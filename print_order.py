import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

from config import MATERIAL_TYPES
from database import get_db_connection
from logger import OperationLogger


class PrinterMatcher:
    @staticmethod
    def find_best_printer(material_type: str, quantity: Optional[int] = None) -> Optional[dict]:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(
                """SELECT p.*, pmr.rating as material_rating, pmr.order_count, pmr.avg_delivery_days
                   FROM printers p
                   JOIN printer_material_ratings pmr ON p.id = pmr.printer_id
                   WHERE p.is_active = 1 AND pmr.material_type = ?
                   ORDER BY pmr.rating DESC, p.overall_rating DESC, pmr.order_count DESC
                   LIMIT 5""",
                (material_type,)
            )
            rows = cursor.fetchall()
            if not rows:
                cursor.execute(
                    """SELECT * FROM printers
                       WHERE is_active = 1
                       ORDER BY overall_rating DESC, total_orders DESC
                       LIMIT 1"""
                )
                row = cursor.fetchone()
                return dict(row) if row else None

            best = max(
                rows,
                key=lambda r: (
                    r["material_rating"] * 0.5 +
                    r["overall_rating"] * 0.3 +
                    min(r["order_count"], 100) / 100 * 0.2
                )
            )
            return dict(best)

    @staticmethod
    def list_printers_by_material(material_type: str, top_n: int = 10) -> List[dict]:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """SELECT p.*, pmr.rating as material_rating, pmr.order_count, pmr.avg_delivery_days
                   FROM printers p
                   JOIN printer_material_ratings pmr ON p.id = pmr.printer_id
                   WHERE p.is_active = 1 AND pmr.material_type = ?
                   ORDER BY pmr.rating DESC, p.overall_rating DESC
                   LIMIT ?""",
                (material_type, top_n)
            )
            return [dict(r) for r in cursor.fetchall()]

    @staticmethod
    def update_printer_rating(printer_id: int, material_type: str, quality_rating: int):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE printer_material_ratings
                   SET rating = (rating * order_count + ?) / (order_count + 1),
                       order_count = order_count + 1
                   WHERE printer_id = ? AND material_type = ?""",
                (quality_rating, printer_id, material_type)
            )
            cursor.execute(
                """UPDATE printers
                   SET overall_rating = (
                       SELECT AVG(rating) FROM printer_material_ratings WHERE printer_id = ?
                   ),
                   total_orders = total_orders + 1
                   WHERE id = ?""",
                (printer_id, printer_id)
            )


class PrintOrderManager:
    STATUS_PLACED = "placed"
    STATUS_IN_PRODUCTION = "in_production"
    STATUS_SHIPPED = "shipped"
    STATUS_DELIVERED = "delivered"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    @staticmethod
    def create_order_for_request(
        request_id: int,
        operator_id: Optional[int] = None,
        operator_name: Optional[str] = None,
        batch_id: Optional[str] = None
    ) -> Dict[str, Any]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT pr.*, e.name as employee_name, d.name as department_name
                   FROM print_requests pr
                   LEFT JOIN employees e ON pr.employee_id = e.id
                   LEFT JOIN departments d ON pr.department_id = d.id
                   WHERE pr.id = ?""",
                (request_id,)
            )
            request = cursor.fetchone()

            if not request:
                return {"success": False, "message": "申请不存在"}

            req = dict(request)
            if req["status"] != "approved":
                return {"success": False, "message": f"申请未通过审批，当前状态: {req['status']}"}

            printer = PrinterMatcher.find_best_printer(req["material_type"], req["quantity"])
            if not printer:
                return {"success": False, "message": "无可用印刷商"}

            delivery_days = printer.get("avg_delivery_days", 3)
            expected_delivery = (datetime.now() + timedelta(days=delivery_days)).strftime("%Y-%m-%d")
            order_no = f"PO{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"

            cursor.execute(
                """INSERT INTO print_orders
                   (order_no, request_id, batch_id, printer_id, material_type, quantity,
                    unit_price, total_amount, status, expected_delivery_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'placed', ?)""",
                (order_no, request_id, batch_id, printer["id"], req["material_type"],
                 req["quantity"], req["unit_price"], req["total_amount"], expected_delivery)
            )
            order_id = cursor.lastrowid

            cursor.execute(
                """UPDATE print_requests
                   SET status = 'in_production', updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (request_id,)
            )

            OperationLogger.log(
                operator_id=operator_id,
                operator_name=operator_name or "系统",
                action=OperationLogger.ACTION_CREATE,
                module=OperationLogger.MODULE_ORDER,
                target_id=order_id,
                target_type="print_order",
                details={
                    "order_no": order_no,
                    "request_id": request_id,
                    "request_no": req["request_no"],
                    "printer_id": printer["id"],
                    "printer_name": printer["name"],
                    "material_type": req["material_type"],
                    "quantity": req["quantity"],
                    "total_amount": req["total_amount"],
                    "expected_delivery": expected_delivery,
                    "batch_id": batch_id
                }
            )

            return {
                "success": True,
                "order_id": order_id,
                "order_no": order_no,
                "printer": printer,
                "expected_delivery": expected_delivery,
                "message": f"订单已生成并推送至{printer['name']}"
            }

    @staticmethod
    def create_orders_for_batch(
        request_ids: List[int],
        operator_id: Optional[int] = None,
        operator_name: Optional[str] = None
    ) -> Dict[str, Any]:
        batch_id = f"BATCH{datetime.now().strftime('%Y%m%d%H%M%S')}"
        results = []
        success_count = 0

        for req_id in request_ids:
            result = PrintOrderManager.create_order_for_request(
                req_id, operator_id, operator_name, batch_id
            )
            results.append(result)
            if result.get("success"):
                success_count += 1

        OperationLogger.log(
            operator_id=operator_id,
            operator_name=operator_name or "系统",
            action=OperationLogger.ACTION_MERGE,
            module=OperationLogger.MODULE_ORDER,
            details={
                "batch_id": batch_id,
                "total_requests": len(request_ids),
                "success_count": success_count,
                "request_ids": request_ids
            }
        )

        return {
            "success": success_count > 0,
            "batch_id": batch_id,
            "total": len(request_ids),
            "success_count": success_count,
            "results": results
        }

    @staticmethod
    def update_order_status(
        order_id: int,
        status: str,
        quality_rating: Optional[int] = None,
        operator_id: Optional[int] = None,
        operator_name: Optional[str] = None
    ) -> Dict[str, Any]:
        valid_statuses = [
            PrintOrderManager.STATUS_PLACED,
            PrintOrderManager.STATUS_IN_PRODUCTION,
            PrintOrderManager.STATUS_SHIPPED,
            PrintOrderManager.STATUS_DELIVERED,
            PrintOrderManager.STATUS_COMPLETED,
            PrintOrderManager.STATUS_CANCELLED
        ]
        if status not in valid_statuses:
            return {"success": False, "message": f"无效状态: {status}"}

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM print_orders WHERE id = ?", (order_id,))
            order = cursor.fetchone()

            if not order:
                return {"success": False, "message": "订单不存在"}

            updates = {"status": status}
            if status == PrintOrderManager.STATUS_DELIVERED:
                updates["actual_delivery_date"] = datetime.now().strftime("%Y-%m-%d")
            if quality_rating is not None:
                updates["quality_rating"] = quality_rating
                PrinterMatcher.update_printer_rating(
                    order["printer_id"], order["material_type"], quality_rating
                )

            set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
            set_clause += ", updated_at = CURRENT_TIMESTAMP"
            params = list(updates.values()) + [order_id]

            cursor.execute(f"UPDATE print_orders SET {set_clause} WHERE id = ?", params)

            if status == PrintOrderManager.STATUS_DELIVERED:
                cursor.execute(
                    """UPDATE print_requests
                       SET status = 'delivered', updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (order["request_id"],)
                )

            OperationLogger.log(
                operator_id=operator_id,
                operator_name=operator_name or "系统",
                action=OperationLogger.ACTION_UPDATE,
                module=OperationLogger.MODULE_ORDER,
                target_id=order_id,
                target_type="print_order",
                details={"new_status": status, "quality_rating": quality_rating}
            )

            return {"success": True, "message": f"订单状态已更新为: {status}"}

    @staticmethod
    def get_order(order_id: int) -> Optional[dict]:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """SELECT po.*, pr.request_no, pr.employee_id, pr.department_id,
                          p.name as printer_name, p.phone as printer_phone,
                          e.name as employee_name, d.name as department_name
                   FROM print_orders po
                   LEFT JOIN print_requests pr ON po.request_id = pr.id
                   LEFT JOIN printers p ON po.printer_id = p.id
                   LEFT JOIN employees e ON pr.employee_id = e.id
                   LEFT JOIN departments d ON pr.department_id = d.id
                   WHERE po.id = ?""",
                (order_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_orders(
        status: Optional[str] = None,
        printer_id: Optional[int] = None,
        department_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[dict]:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            sql = """
                SELECT po.*, pr.request_no, pr.employee_id,
                       p.name as printer_name,
                       e.name as employee_name, d.name as department_name
                FROM print_orders po
                LEFT JOIN print_requests pr ON po.request_id = pr.id
                LEFT JOIN printers p ON po.printer_id = p.id
                LEFT JOIN employees e ON pr.employee_id = e.id
                LEFT JOIN departments d ON pr.department_id = d.id
                WHERE 1=1
            """
            params = []
            if status:
                sql += " AND po.status = ?"
                params.append(status)
            if printer_id:
                sql += " AND po.printer_id = ?"
                params.append(printer_id)
            if department_id:
                sql += " AND pr.department_id = ?"
                params.append(department_id)
            if start_date:
                sql += " AND po.created_at >= ?"
                params.append(start_date)
            if end_date:
                sql += " AND po.created_at <= ?"
                params.append(end_date)
            sql += " ORDER BY po.created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            cursor.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]

    @staticmethod
    def get_delivered_orders_pending_inventory() -> List[dict]:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """SELECT po.*, pr.request_no, pr.employee_id, pr.department_id
                   FROM print_orders po
                   JOIN print_requests pr ON po.request_id = pr.id
                   WHERE po.status = 'delivered'
                   AND po.id NOT IN (SELECT DISTINCT order_id FROM inventory WHERE order_id IS NOT NULL)"""
            )
            return [dict(r) for r in cursor.fetchall()]
