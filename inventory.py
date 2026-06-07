import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from config import PICKUP_REMINDER_DAYS, MATERIAL_TYPES
from database import get_db_connection
from logger import OperationLogger
from print_order import PrintOrderManager


class NotificationManager:
    TYPE_PICKUP_READY = "pickup_ready"
    TYPE_PICKUP_REMINDER = "pickup_reminder"
    TYPE_APPROVAL_REQUEST = "approval_request"
    TYPE_APPROVAL_RESULT = "approval_result"
    TYPE_BUDGET_WARNING = "budget_warning"
    TYPE_ORDER_STATUS = "order_status"
    TYPE_SYSTEM = "system"

    @staticmethod
    def send(
        recipient_id: int,
        notification_type: str,
        title: str,
        content: Optional[str] = None,
        related_id: Optional[int] = None,
        related_type: Optional[str] = None
    ) -> int:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO notifications
                   (recipient_id, type, title, content, related_id, related_type)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (recipient_id, notification_type, title, content, related_id, related_type)
            )
            notif_id = cursor.lastrowid

            cursor.execute("SELECT name FROM employees WHERE id = ?", (recipient_id,))
            row = cursor.fetchone()
            recipient_name = row["name"] if row else "未知"

            OperationLogger.log(
                operator_id=None,
                operator_name="系统通知",
                action=OperationLogger.ACTION_CREATE,
                module=OperationLogger.MODULE_SYSTEM,
                target_id=notif_id,
                target_type="notification",
                details={
                    "recipient_id": recipient_id,
                    "recipient_name": recipient_name,
                    "type": notification_type,
                    "title": title
                }
            )

            print(f"[通知发送] -> {recipient_name}: {title}")
            return notif_id

    @staticmethod
    def get_unread(recipient_id: int, limit: int = 20) -> List[dict]:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM notifications
                   WHERE recipient_id = ? AND is_read = 0
                   ORDER BY created_at DESC LIMIT ?""",
                (recipient_id, limit)
            )
            return [dict(r) for r in cursor.fetchall()]

    @staticmethod
    def mark_read(notification_id: int, recipient_id: Optional[int] = None):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            sql = "UPDATE notifications SET is_read = 1 WHERE id = ?"
            params = [notification_id]
            if recipient_id:
                sql += " AND recipient_id = ?"
                params.append(recipient_id)
            cursor.execute(sql, params)


class InventoryManager:
    STATUS_IN_STOCK = "in_stock"
    STATUS_PICKED_UP = "picked_up"
    STATUS_OVERDUE = "overdue"

    @staticmethod
    def process_delivered_orders(operator_id: Optional[int] = None) -> Dict[str, Any]:
        delivered = PrintOrderManager.get_delivered_orders_pending_inventory()
        processed = 0

        for order in delivered:
            InventoryManager.add_from_order(order["id"], operator_id)
            processed += 1

        return {"processed": processed, "orders": [o["id"] for o in delivered]}

    @staticmethod
    def add_from_order(order_id: int, operator_id: Optional[int] = None) -> Optional[int]:
        order = PrintOrderManager.get_order(order_id)
        if not order:
            return None

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO inventory
                   (material_type, employee_id, department_id, order_id, quantity, status)
                   VALUES (?, ?, ?, ?, ?, 'in_stock')""",
                (order["material_type"], order["employee_id"], order["department_id"],
                 order_id, order["quantity"])
            )
            inv_id = cursor.lastrowid

            mat_name = MATERIAL_TYPES.get(order["material_type"], {}).get("name", order["material_type"])
            title = f"您的{mat_name}印制已完成，请到行政部领取"
            content = (
                f"订单号: {order['order_no']}\n"
                f"物料: {mat_name}\n"
                f"数量: {order['quantity']}\n"
                f"请在 {PICKUP_REMINDER_DAYS} 天内领取，逾期将发送催领通知。"
            )
            if order["employee_id"]:
                NotificationManager.send(
                    recipient_id=order["employee_id"],
                    notification_type=NotificationManager.TYPE_PICKUP_READY,
                    title=title,
                    content=content,
                    related_id=inv_id,
                    related_type="inventory"
                )

            cursor.execute(
                """UPDATE print_orders
                   SET status = 'completed', updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (order_id,)
            )
            cursor.execute(
                """UPDATE print_requests
                   SET status = 'completed', updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (order["request_id"],)
            )

            OperationLogger.log(
                operator_id=operator_id,
                operator_name="系统",
                action=OperationLogger.ACTION_CREATE,
                module=OperationLogger.MODULE_INVENTORY,
                target_id=inv_id,
                target_type="inventory",
                details={
                    "order_id": order_id,
                    "material_type": order["material_type"],
                    "quantity": order["quantity"],
                    "employee_id": order["employee_id"]
                }
            )

            return inv_id

    @staticmethod
    def pickup(
        inventory_id: int,
        pickup_by: int,
        operator_name: Optional[str] = None
    ) -> Dict[str, Any]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM inventory WHERE id = ?", (inventory_id,))
            inv = cursor.fetchone()

            if not inv:
                return {"success": False, "message": "库存记录不存在"}

            if inv["status"] != "in_stock" and inv["status"] != "overdue":
                return {"success": False, "message": f"当前状态不可领取: {inv['status']}"}

            cursor.execute(
                """UPDATE inventory
                   SET status = 'picked_up', pickup_by = ?, picked_up_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (pickup_by, inventory_id)
            )

            cursor.execute("SELECT name FROM employees WHERE id = ?", (pickup_by,))
            row = cursor.fetchone()
            picker_name = row["name"] if row else "未知"

            OperationLogger.log(
                operator_id=pickup_by,
                operator_name=operator_name or picker_name,
                action=OperationLogger.ACTION_PICKUP,
                module=OperationLogger.MODULE_INVENTORY,
                target_id=inventory_id,
                target_type="inventory",
                details={
                    "material_type": inv["material_type"],
                    "quantity": inv["quantity"]
                }
            )

            return {"success": True, "message": "领取成功"}

    @staticmethod
    def check_overdue_and_remind() -> Dict[str, Any]:
        cutoff = (datetime.now() - timedelta(days=PICKUP_REMINDER_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """SELECT i.*, e.name as employee_name, e.phone as employee_phone
                   FROM inventory i
                   LEFT JOIN employees e ON i.employee_id = e.id
                   WHERE i.status IN ('in_stock', 'overdue')
                   AND i.ready_at < ?
                   AND i.reminder_sent = 0""",
                (cutoff,)
            )
            overdue_items = [dict(r) for r in cursor.fetchall()]

            reminded = 0
            for item in overdue_items:
                cursor.execute(
                    "UPDATE inventory SET status = 'overdue', reminder_sent = 1 WHERE id = ?",
                    (item["id"],)
                )

                mat_name = MATERIAL_TYPES.get(item["material_type"], {}).get("name", item["material_type"])
                if item["employee_id"]:
                    NotificationManager.send(
                        recipient_id=item["employee_id"],
                        notification_type=NotificationManager.TYPE_PICKUP_REMINDER,
                        title=f"【催领通知】您的{mat_name}已超过{PICKUP_REMINDER_DAYS}天未领取",
                        content=(
                            f"物料类型: {mat_name}\n"
                            f"数量: {item['quantity']}\n"
                            f"请尽快到行政部领取，长时间未领取的物料将被回收处理。"
                        ),
                        related_id=item["id"],
                        related_type="inventory"
                    )
                reminded += 1

                OperationLogger.log(
                    operator_id=None,
                    operator_name="系统",
                    action=OperationLogger.ACTION_REMIND,
                    module=OperationLogger.MODULE_INVENTORY,
                    target_id=item["id"],
                    target_type="inventory",
                    details={
                        "employee_id": item["employee_id"],
                        "employee_name": item.get("employee_name"),
                        "material_type": item["material_type"],
                        "quantity": item["quantity"],
                        "days_overdue": PICKUP_REMINDER_DAYS
                    }
                )

        return {"total_overdue": len(overdue_items), "reminded_count": reminded}

    @staticmethod
    def get_inventory(
        status: Optional[str] = None,
        employee_id: Optional[int] = None,
        department_id: Optional[int] = None,
        material_type: Optional[str] = None
    ) -> List[dict]:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            sql = """
                SELECT i.*, e.name as employee_name, e.employee_no,
                       d.name as department_name, po.order_no
                FROM inventory i
                LEFT JOIN employees e ON i.employee_id = e.id
                LEFT JOIN departments d ON i.department_id = d.id
                LEFT JOIN print_orders po ON i.order_id = po.id
                WHERE 1=1
            """
            params = []
            if status:
                sql += " AND i.status = ?"
                params.append(status)
            if employee_id:
                sql += " AND i.employee_id = ?"
                params.append(employee_id)
            if department_id:
                sql += " AND i.department_id = ?"
                params.append(department_id)
            if material_type:
                sql += " AND i.material_type = ?"
                params.append(material_type)
            sql += " ORDER BY i.ready_at DESC"
            cursor.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]

    @staticmethod
    def get_stock_summary() -> List[dict]:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """SELECT material_type,
                           SUM(CASE WHEN status = 'in_stock' THEN quantity ELSE 0 END) as in_stock_qty,
                           SUM(CASE WHEN status = 'overdue' THEN quantity ELSE 0 END) as overdue_qty,
                           SUM(CASE WHEN status = 'picked_up' THEN quantity ELSE 0 END) as picked_qty,
                           COUNT(*) as total_records
                   FROM inventory
                   GROUP BY material_type
                   ORDER BY material_type"""
            )
            results = []
            for r in cursor.fetchall():
                row = dict(r)
                mat_info = MATERIAL_TYPES.get(row["material_type"], {})
                row["material_name"] = mat_info.get("name", row["material_type"])
                row["unit"] = mat_info.get("unit", "")
                results.append(row)
            return results
