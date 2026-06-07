import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from config import MATERIAL_CATALOG, now_str
from database import db_conn
from logger import OperationLogger, LogModule, LogAction
from print_request import PrintRequestService


class PrinterMatcher:

    @staticmethod
    def best_match(material_type: str) -> Optional[dict]:
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """SELECT p.*, pmr.rating AS mat_rating, pmr.order_count AS mat_orders,
                          pmr.avg_delivery_days
                   FROM printers p
                   JOIN printer_material_ratings pmr ON p.printer_id = pmr.printer_id
                   WHERE p.is_active = 1 AND pmr.material_type = ?
                   ORDER BY pmr.rating DESC, p.overall_rating DESC, pmr.order_count DESC
                   LIMIT 10""",
                (material_type,)
            )
            rows = [dict(r) for r in c.fetchall()]
            if not rows:
                c.execute(
                    """SELECT * FROM printers WHERE is_active = 1
                       ORDER BY overall_rating DESC, total_orders DESC LIMIT 1""",
                )
                r = c.fetchone()
                return dict(r) if r else None

            def score(r):
                s = (r.get("mat_rating", 5.0) * 0.55
                     + r.get("overall_rating", 5.0) * 0.30
                     + min(r.get("mat_orders", 0), 200) / 200 * 0.15)
                return s

            rows.sort(key=score, reverse=True)
            return rows[0]

    @staticmethod
    def list_ranked(material_type: str, top_n: int = 10) -> List[dict]:
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """SELECT p.*, pmr.rating AS mat_rating, pmr.order_count AS mat_orders,
                          pmr.avg_delivery_days
                   FROM printers p
                   JOIN printer_material_ratings pmr ON p.printer_id = pmr.printer_id
                   WHERE p.is_active = 1 AND pmr.material_type = ?
                   ORDER BY pmr.rating DESC, p.overall_rating DESC
                   LIMIT ?""",
                (material_type, top_n)
            )
            return [dict(r) for r in c.fetchall()]

    @staticmethod
    def update_rating(printer_id: int, material_type: str, quality: int):
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """UPDATE printer_material_ratings
                   SET rating = (rating * order_count + ?) / (order_count + 1),
                       order_count = order_count + 1
                   WHERE printer_id = ? AND material_type = ?""",
                (quality, printer_id, material_type)
            )
            c.execute(
                """UPDATE printers
                   SET overall_rating = (
                       SELECT AVG(rating) FROM printer_material_ratings WHERE printer_id = ?
                   ),
                   total_orders = total_orders + 1
                   WHERE printer_id = ?""",
                (printer_id, printer_id)
            )


class PrintOrderService:
    STATUS_PLACED = "placed"
    STATUS_IN_PRODUCTION = "in_production"
    STATUS_SHIPPED = "shipped"
    STATUS_DELIVERED = "delivered"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    @staticmethod
    def _new_order_no() -> str:
        return f"PO{now_str().replace(':', '').replace('-', '').replace(' ', '')}" \
               f"{uuid.uuid4().hex[:5].upper()}"

    @staticmethod
    def create_from_request(req_id: int, operator_id: Optional[int] = None,
                            operator_name: Optional[str] = None,
                            batch_id: Optional[str] = None) -> Dict[str, Any]:
        req = PrintRequestService.get(req_id)
        if not req:
            return {"success": False, "message": "申请不存在"}
        if req["status"] != "approved":
            return {"success": False,
                    "message": f"申请未通过审批，当前状态: {req['status']}"}

        printer = PrinterMatcher.best_match(req["material_type"])
        if not printer:
            return {"success": False, "message": "无可用印刷商"}

        order_id = None
        order_no = None
        expected = None
        with db_conn() as conn:
            c = conn.cursor()
            delivery_days = printer.get("avg_delivery_days", 3)
            expected = (datetime.now() + timedelta(days=delivery_days)).strftime("%Y-%m-%d")
            order_no = PrintOrderService._new_order_no()

            c.execute(
                """INSERT INTO print_orders
                   (order_no, req_id, batch_id, printer_id, material_type, quantity,
                    unit_price, total_amount, status, expected_delivery)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'placed', ?)""",
                (order_no, req_id, batch_id, printer["printer_id"], req["material_type"],
                 req["quantity"], req["unit_price"], req["total_amount"], expected)
            )
            order_id = c.lastrowid

            c.execute(
                """UPDATE print_requests
                   SET status = 'in_production', updated_at = ? WHERE req_id = ?""",
                (now_str(), req_id)
            )

        if order_id is None:
            return {"success": False, "message": "创建订单失败"}

        OperationLogger.record(
            operator_id=operator_id,
            operator_name=operator_name or "系统",
            action=LogAction.CREATE,
            module=LogModule.PRINT_ORDER,
            target_id=order_id,
            target_type="print_order",
            details={
                "order_no": order_no,
                "req_id": req_id,
                "req_no": req["req_no"],
                "printer_id": printer["printer_id"],
                "printer_name": printer["printer_name"],
                "material_type": req["material_type"],
                "material_name": MATERIAL_CATALOG.get(req["material_type"], {}).get("name_cn", ""),
                "quantity": req["quantity"],
                "total_amount": req["total_amount"],
                "expected_delivery": expected,
                "batch_id": batch_id
            }
        )
        return {
            "success": True,
            "order_id": order_id,
            "order_no": order_no,
            "printer": printer,
            "expected_delivery": expected,
            "message": f"订单已生成并推送至【{printer['printer_name']}】，"
                       f"预计交货日期: {expected}"
        }

    @staticmethod
    def create_batch(req_ids: List[int], operator_id: Optional[int] = None,
                     operator_name: Optional[str] = None) -> Dict[str, Any]:
        batch_id = f"B{now_str().replace(':', '').replace('-', '').replace(' ', '')}"
        results = []
        success_count = 0
        for rid in req_ids:
            r = PrintOrderService.create_from_request(rid, operator_id, operator_name, batch_id)
            results.append(r)
            if r.get("success"):
                success_count += 1

        OperationLogger.record(
            operator_id=operator_id,
            operator_name=operator_name or "系统",
            action=LogAction.MERGE,
            module=LogModule.PRINT_ORDER,
            details={
                "batch_id": batch_id,
                "total_requests": len(req_ids),
                "success_count": success_count,
                "request_ids": req_ids
            }
        )
        return {
            "success": success_count > 0,
            "batch_id": batch_id,
            "total": len(req_ids),
            "success_count": success_count,
            "results": results
        }

    @staticmethod
    def update_status(order_id: int, status: str, quality: Optional[int] = None,
                      operator_id: Optional[int] = None,
                      operator_name: Optional[str] = None) -> Dict[str, Any]:
        valid = [PrintOrderService.STATUS_PLACED, PrintOrderService.STATUS_IN_PRODUCTION,
                 PrintOrderService.STATUS_SHIPPED, PrintOrderService.STATUS_DELIVERED,
                 PrintOrderService.STATUS_COMPLETED, PrintOrderService.STATUS_CANCELLED]
        if status not in valid:
            return {"success": False, "message": f"无效状态: {status}"}

        with db_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM print_orders WHERE order_id = ?", (order_id,))
            row = c.fetchone()
            if not row:
                return {"success": False, "message": "订单不存在"}
            order = dict(row)

            fields = {"status": status}
            if status == PrintOrderService.STATUS_DELIVERED:
                fields["actual_delivery"] = now_str()[:10]
            if quality is not None and 1 <= quality <= 5:
                fields["quality_rating"] = quality
                PrinterMatcher.update_rating(order["printer_id"],
                                             order["material_type"], quality)

            sets = ", ".join(f"{k} = ?" for k in fields.keys())
            sets += ", updated_at = ?"
            params = list(fields.values()) + [now_str(), order_id]
            c.execute(f"UPDATE print_orders SET {sets} WHERE order_id = ?", params)

            if status == PrintOrderService.STATUS_DELIVERED:
                c.execute(
                    """UPDATE print_requests
                       SET status = 'delivered', updated_at = ? WHERE req_id = ?""",
                    (now_str(), order["req_id"])
                )

        OperationLogger.record(
            operator_id=operator_id,
            operator_name=operator_name or "系统",
            action=LogAction.UPDATE,
            module=LogModule.PRINT_ORDER,
            target_id=order_id,
            target_type="print_order",
            details={"new_status": status, "quality_rating": quality}
        )
        return {"success": True, "message": f"订单状态已更新为: {status}"}

    @staticmethod
    def get(order_id: int) -> Optional[dict]:
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """SELECT o.*, r.req_no, r.emp_id, r.dept_id,
                          p.printer_name, p.contact_phone AS printer_phone,
                          e.emp_name, d.dept_name
                   FROM print_orders o
                   LEFT JOIN print_requests r ON o.req_id = r.req_id
                   LEFT JOIN printers p ON o.printer_id = p.printer_id
                   LEFT JOIN employees e ON r.emp_id = e.emp_id
                   LEFT JOIN departments d ON r.dept_id = d.dept_id
                   WHERE o.order_id = ?""",
                (order_id,)
            )
            r = c.fetchone()
            return dict(r) if r else None

    @staticmethod
    def list(status: Optional[str] = None, printer_id: Optional[int] = None,
             dept_id: Optional[int] = None, start: Optional[str] = None,
             end: Optional[str] = None, limit: int = 200, offset: int = 0) -> list:
        with db_conn() as conn:
            c = conn.cursor()
            sql = """
                SELECT o.*, r.req_no, r.emp_id, p.printer_name,
                       e.emp_name, d.dept_name
                FROM print_orders o
                LEFT JOIN print_requests r ON o.req_id = r.req_id
                LEFT JOIN printers p ON o.printer_id = p.printer_id
                LEFT JOIN employees e ON r.emp_id = e.emp_id
                LEFT JOIN departments d ON r.dept_id = d.dept_id
                WHERE 1=1
            """
            params = []
            if status:
                sql += " AND o.status = ?"
                params.append(status)
            if printer_id:
                sql += " AND o.printer_id = ?"
                params.append(printer_id)
            if dept_id:
                sql += " AND r.dept_id = ?"
                params.append(dept_id)
            if start:
                sql += " AND o.created_at >= ?"
                params.append(start)
            if end:
                sql += " AND o.created_at <= ?"
                params.append(end)
            sql += " ORDER BY o.created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            c.execute(sql, params)
            return [dict(r) for r in c.fetchall()]

    @staticmethod
    def delivered_pending_inventory() -> list:
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """SELECT o.*, r.req_no, r.emp_id, r.dept_id
                   FROM print_orders o
                   JOIN print_requests r ON o.req_id = r.req_id
                   WHERE o.status = 'delivered'
                   AND o.order_id NOT IN
                       (SELECT DISTINCT order_id FROM inventory WHERE order_id IS NOT NULL)"""
            )
            return [dict(r) for r in c.fetchall()]
