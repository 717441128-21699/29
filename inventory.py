from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from config import MATERIAL_CATALOG, PICKUP_REMINDER_DAYS, now_str
from database import db_conn
from logger import OperationLogger, LogModule, LogAction
from print_order import PrintOrderService


class NotificationService:
    TYPE_PICKUP_READY = "pickup_ready"
    TYPE_PICKUP_REMINDER = "pickup_reminder"
    TYPE_APPROVAL_NEEDED = "approval_needed"
    TYPE_APPROVAL_DONE = "approval_done"
    TYPE_BUDGET_WARNING = "budget_warning"
    TYPE_ORDER_STATUS = "order_status"

    @staticmethod
    def send(recipient_id: int, notif_type: str, title: str,
             content: Optional[str] = None, related_id: Optional[int] = None,
             related_type: Optional[str] = None) -> int:
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """INSERT INTO notifications
                   (recipient_id, notif_type, title, content, related_id, related_type)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (recipient_id, notif_type, title, content, related_id, related_type)
            )
            notif_id = c.lastrowid

            c.execute("SELECT emp_name FROM employees WHERE emp_id = ?", (recipient_id,))
            row = c.fetchone()
            name = row["emp_name"] if row else "未知"

        OperationLogger.record(
            operator_id=None,
            operator_name="系统通知",
            action=LogAction.CREATE,
            module=LogModule.SYSTEM,
            target_id=notif_id,
            target_type="notification",
            details={"recipient": name, "type": notif_type, "title": title}
        )
        print(f"  [通知] → {name}: {title}")
        return notif_id

    @staticmethod
    def unread(recipient_id: int, limit: int = 50) -> List[dict]:
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """SELECT * FROM notifications
                   WHERE recipient_id = ? AND is_read = 0
                   ORDER BY created_at DESC LIMIT ?""",
                (recipient_id, limit)
            )
            return [dict(r) for r in c.fetchall()]

    @staticmethod
    def mark_read(notif_id: int, recipient_id: Optional[int] = None):
        with db_conn() as conn:
            c = conn.cursor()
            sql = "UPDATE notifications SET is_read = 1 WHERE notif_id = ?"
            params = [notif_id]
            if recipient_id:
                sql += " AND recipient_id = ?"
                params.append(recipient_id)
            c.execute(sql, params)


class InventoryService:
    STATUS_IN_STOCK = "in_stock"
    STATUS_PICKED_UP = "picked_up"
    STATUS_OVERDUE = "overdue"
    STATUS_CONSUMED = "consumed"

    @staticmethod
    def process_delivered_orders(operator_id: Optional[int] = None) -> Dict[str, Any]:
        delivered = PrintOrderService.delivered_pending_inventory()
        processed = []
        for order in delivered:
            inv_id = InventoryService.receive_order(order["order_id"], operator_id)
            if inv_id:
                processed.append(inv_id)
        return {"processed_count": len(processed), "inv_ids": processed}

    @staticmethod
    def receive_order(order_id: int, operator_id: Optional[int] = None) -> Optional[int]:
        order = PrintOrderService.get(order_id)
        if not order:
            return None
        inv_id = None
        notif_params = None
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """INSERT INTO inventory
                   (material_type, emp_id, dept_id, order_id, quantity, status)
                   VALUES (?, ?, ?, ?, ?, 'in_stock')""",
                (order["material_type"], order["emp_id"], order["dept_id"],
                 order_id, order["quantity"])
            )
            inv_id = c.lastrowid

            mat_info = MATERIAL_CATALOG.get(order["material_type"], {})
            mat_name = mat_info.get("name_cn", order["material_type"])
            unit = mat_info.get("unit", "")

            if order["emp_id"]:
                notif_params = {
                    "recipient_id": order["emp_id"],
                    "notif_type": NotificationService.TYPE_PICKUP_READY,
                    "title": f"您的{mat_name}已印制完成，请到行政部领取",
                    "content": (
                        f"订单号: {order['order_no']}\n"
                        f"物料: {mat_name}\n"
                        f"数量: {order['quantity']}{unit}\n"
                        f"请在 {PICKUP_REMINDER_DAYS} 天内领取，逾期将发送催领通知。"
                    ),
                    "related_id": inv_id,
                    "related_type": "inventory"
                }

            c.execute(
                """UPDATE print_orders
                   SET status = 'completed', updated_at = ? WHERE order_id = ?""",
                (now_str(), order_id)
            )
            if order.get("req_id"):
                c.execute(
                    """UPDATE print_requests
                       SET status = 'completed', updated_at = ? WHERE req_id = ?""",
                    (now_str(), order["req_id"])
                )

        if notif_params:
            NotificationService.send(**notif_params)

        if inv_id:
            OperationLogger.record(
                operator_id=operator_id,
                operator_name="系统",
                action=LogAction.CREATE,
                module=LogModule.INVENTORY,
                target_id=inv_id,
                target_type="inventory",
                details={
                    "order_id": order_id,
                    "material_type": order["material_type"],
                    "quantity": order["quantity"],
                    "emp_id": order["emp_id"]
                }
            )
        return inv_id

    @staticmethod
    def pickup(inv_id: int, pickup_by: int,
               operator_name: Optional[str] = None) -> Dict[str, Any]:
        with db_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM inventory WHERE inv_id = ?", (inv_id,))
            row = c.fetchone()
            if not row:
                return {"success": False, "message": "库存记录不存在"}
            inv = dict(row)
            if inv["status"] not in (InventoryService.STATUS_IN_STOCK,
                                     InventoryService.STATUS_OVERDUE):
                return {"success": False,
                        "message": f"当前状态不可领取: {inv['status']}"}

            c.execute(
                """UPDATE inventory
                   SET status = 'picked_up', pickup_by = ?, picked_up_at = ?
                   WHERE inv_id = ?""",
                (pickup_by, now_str(), inv_id)
            )
            c.execute("SELECT emp_name FROM employees WHERE emp_id = ?", (pickup_by,))
            row = c.fetchone()
            name = row["emp_name"] if row else "未知"

        OperationLogger.record(
            operator_id=pickup_by,
            operator_name=operator_name or name,
            action=LogAction.PICKUP,
            module=LogModule.INVENTORY,
            target_id=inv_id,
            target_type="inventory",
            details={
                "material_type": inv["material_type"],
                "quantity": inv["quantity"]
            }
        )
        return {"success": True, "message": "领取成功"}

    @staticmethod
    def run_reminder_cron() -> Dict[str, Any]:
        cutoff = (datetime.now() - timedelta(days=PICKUP_REMINDER_DAYS))
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        pending_notifs = []
        pending_logs = []
        overdue = []
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """SELECT i.*, e.emp_name, e.phone
                   FROM inventory i
                   LEFT JOIN employees e ON i.emp_id = e.emp_id
                   WHERE i.status IN ('in_stock', 'overdue')
                     AND i.ready_at < ?
                     AND i.reminder_sent = 0""",
                (cutoff_str,)
            )
            overdue = [dict(r) for r in c.fetchall()]

            reminded = 0
            for inv in overdue:
                c.execute(
                    "UPDATE inventory SET status = 'overdue', reminder_sent = 1 WHERE inv_id = ?",
                    (inv["inv_id"],)
                )
                mat_name = MATERIAL_CATALOG.get(inv["material_type"], {}).get(
                    "name_cn", inv["material_type"])
                unit = MATERIAL_CATALOG.get(inv["material_type"], {}).get("unit", "")
                if inv["emp_id"]:
                    pending_notifs.append({
                        "recipient_id": inv["emp_id"],
                        "notif_type": NotificationService.TYPE_PICKUP_REMINDER,
                        "title": f"【催领通知】您的{mat_name}已超过{PICKUP_REMINDER_DAYS}天未领取",
                        "content": (
                            f"物料类型: {mat_name}\n"
                            f"数量: {inv['quantity']}{unit}\n"
                            f"请尽快到行政部领取，超过7天未领取将作回收处理。"
                        ),
                        "related_id": inv["inv_id"],
                        "related_type": "inventory"
                    })
                reminded += 1
                pending_logs.append({
                    "target_id": inv["inv_id"],
                    "details": {
                        "emp_id": inv["emp_id"],
                        "emp_name": inv.get("emp_name"),
                        "material_type": inv["material_type"],
                        "quantity": inv["quantity"],
                        "days_overdue": PICKUP_REMINDER_DAYS
                    }
                })

        for n in pending_notifs:
            NotificationService.send(**n)
        for lg in pending_logs:
            OperationLogger.record(
                operator_id=None,
                operator_name="系统",
                action=LogAction.REMIND,
                module=LogModule.INVENTORY,
                target_id=lg["target_id"],
                target_type="inventory",
                details=lg["details"]
            )
        return {"total_overdue": len(overdue), "reminded": len(pending_logs)}

    @staticmethod
    def list(status: Optional[str] = None, emp_id: Optional[int] = None,
             dept_id: Optional[int] = None, material_type: Optional[str] = None) -> list:
        with db_conn() as conn:
            c = conn.cursor()
            sql = """
                SELECT i.*, e.emp_name, e.emp_no, d.dept_name, o.order_no
                FROM inventory i
                LEFT JOIN employees e ON i.emp_id = e.emp_id
                LEFT JOIN departments d ON i.dept_id = d.dept_id
                LEFT JOIN print_orders o ON i.order_id = o.order_id
                WHERE 1=1
            """
            params = []
            if status:
                sql += " AND i.status = ?"
                params.append(status)
            if emp_id:
                sql += " AND i.emp_id = ?"
                params.append(emp_id)
            if dept_id:
                sql += " AND i.dept_id = ?"
                params.append(dept_id)
            if material_type:
                sql += " AND i.material_type = ?"
                params.append(material_type)
            sql += " ORDER BY i.ready_at DESC"
            c.execute(sql, params)
            return [dict(r) for r in c.fetchall()]

    @staticmethod
    def summary() -> List[dict]:
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """SELECT material_type,
                           SUM(CASE WHEN status = 'in_stock' THEN quantity ELSE 0 END) AS in_stock,
                           SUM(CASE WHEN status = 'overdue'   THEN quantity ELSE 0 END) AS overdue,
                           SUM(CASE WHEN status = 'picked_up' THEN quantity ELSE 0 END) AS picked,
                           COUNT(*) AS records
                   FROM inventory GROUP BY material_type ORDER BY material_type"""
            )
            result = []
            for r in c.fetchall():
                row = dict(r)
                info = MATERIAL_CATALOG.get(row["material_type"], {})
                row["name_cn"] = info.get("name_cn", row["material_type"])
                row["unit"] = info.get("unit", "")
                result.append(row)
            return result
