import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any, List

from config import MATERIAL_TYPES
from database import get_db_connection
from logger import OperationLogger
from print_request import PrintRequestManager, PrintRequestValidator
from approval import ApprovalManager, BudgetManager
from print_order import PrintOrderManager
from inventory import InventoryManager


class BatchPrintManager:
    @staticmethod
    def create_batch_request(
        department_id: int,
        operator_id: int,
        items: List[Dict[str, Any]],
        operator_name: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        手动发起批量印制需求
        items: [{"employee_id": int, "material_type": str, "quantity": int, "custom_info": dict}, ...]
        """
        if not items:
            return {"success": False, "message": "批量印制项目不能为空"}

        results = []
        created_requests = []
        total_amount = 0
        validation_errors = []

        for idx, item in enumerate(items):
            employee_id = item.get("employee_id")
            material_type = item.get("material_type")
            quantity = item.get("quantity", 0)
            custom_info = item.get("custom_info")

            if not employee_id:
                validation_errors.append(f"第{idx + 1}项: 缺少员工ID")
                continue

            valid_emp, emp_msg, employee = PrintRequestValidator.validate_employee_info(employee_id)
            if not valid_emp:
                validation_errors.append(f"第{idx + 1}项: {emp_msg}")
                continue

            if employee["department_id"] != department_id:
                validation_errors.append(
                    f"第{idx + 1}项: 员工{employee['name']}不属于该部门，已跳过"
                )
                continue

            valid_mat, mat_msg = PrintRequestValidator.validate_material(material_type, quantity)
            if not valid_mat:
                validation_errors.append(f"第{idx + 1}项: {mat_msg}")
                continue

            result = PrintRequestManager.create_request(
                employee_id=employee_id,
                material_type=material_type,
                quantity=quantity,
                custom_info=custom_info,
                ip_address=ip_address
            )

            results.append(result)
            if result.get("success"):
                created_requests.append(result)
                total_amount += result["total_amount"]

        budget_check = BudgetManager.check_budget_sufficient(department_id, total_amount)

        OperationLogger.log(
            operator_id=operator_id,
            operator_name=operator_name or "批量操作",
            action=OperationLogger.ACTION_MERGE,
            module=OperationLogger.MODULE_REQUEST,
            details={
                "department_id": department_id,
                "total_items": len(items),
                "created_count": len(created_requests),
                "error_count": len(validation_errors),
                "total_amount": total_amount,
                "budget_sufficient": budget_check["sufficient"]
            }
        )

        return {
            "success": len(created_requests) > 0,
            "total_submitted": len(items),
            "created_count": len(created_requests),
            "error_count": len(validation_errors),
            "total_amount": total_amount,
            "validation_errors": validation_errors,
            "budget_check": budget_check,
            "created_requests": created_requests,
            "results": results
        }

    @staticmethod
    def validate_and_approve_batch(request_ids: List[int], approver_id: int) -> Dict[str, Any]:
        """批量审批多个申请"""
        approved = []
        failed = []

        for req_id in request_ids:
            result = ApprovalManager.process_request_budget_and_approval(req_id, approver_id)
            if not result.get("success"):
                failed.append({"request_id": req_id, "reason": result.get("message")})
                continue

            if result.get("status") == "pending_approval":
                approve_result = ApprovalManager.approve_request(req_id, approver_id)
                if approve_result.get("success"):
                    approved.append(req_id)
                else:
                    failed.append({"request_id": req_id, "reason": approve_result.get("message")})
            elif result.get("status") == "approved":
                approved.append(req_id)
            else:
                failed.append({"request_id": req_id, "reason": result.get("message")})

        OperationLogger.log(
            operator_id=approver_id,
            operator_name="批量审批",
            action=OperationLogger.ACTION_APPROVE,
            module=OperationLogger.MODULE_APPROVAL,
            details={
                "total_requests": len(request_ids),
                "approved_count": len(approved),
                "failed_count": len(failed),
                "approved_ids": approved,
                "failed": failed
            }
        )

        return {
            "success": len(approved) > 0,
            "total": len(request_ids),
            "approved_count": len(approved),
            "failed_count": len(failed),
            "approved_ids": approved,
            "failed": failed
        }

    @staticmethod
    def process_and_merge_orders(
        request_ids: List[int],
        operator_id: Optional[int] = None,
        operator_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """校验库存并合并生成订单"""
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            inventory_reduced = []
            need_print = []

            for req_id in request_ids:
                req = PrintRequestManager.get_request(req_id)
                if not req:
                    continue

                if req["status"] != "approved":
                    continue

                cursor.execute(
                    """SELECT * FROM inventory
                       WHERE material_type = ? AND department_id = ?
                       AND status = 'in_stock' AND employee_id IS NULL
                       ORDER BY ready_at ASC""",
                    (req["material_type"], req["department_id"])
                )
                stock_items = [dict(r) for r in cursor.fetchall()]

                remaining_qty = req["quantity"]
                for stock in stock_items:
                    if remaining_qty <= 0:
                        break
                    take_qty = min(stock["quantity"], remaining_qty)
                    stock["quantity"] -= take_qty
                    remaining_qty -= take_qty

                    if stock["quantity"] == 0:
                        cursor.execute(
                            "UPDATE inventory SET status = 'used_in_batch' WHERE id = ?",
                            (stock["id"],)
                        )
                    else:
                        cursor.execute(
                            "UPDATE inventory SET quantity = ? WHERE id = ?",
                            (stock["quantity"], stock["id"])
                        )

                    inventory_reduced.append({
                        "inventory_id": stock["id"],
                        "material_type": req["material_type"],
                        "reduced_by": take_qty,
                        "for_request_id": req_id
                    })

                if remaining_qty > 0:
                    if remaining_qty < req["quantity"]:
                        cursor.execute(
                            """UPDATE print_requests
                               SET quantity = ?, total_amount = ?
                               WHERE id = ?""",
                            (remaining_qty,
                             round(remaining_qty * MATERIAL_TYPES[req["material_type"]]["unit_price"], 2),
                             req_id)
                        )
                    need_print.append(req_id)

        order_result = {"success": True, "batch_id": None, "results": []}
        if need_print:
            order_result = PrintOrderManager.create_orders_for_batch(
                need_print, operator_id, operator_name
            )

        OperationLogger.log(
            operator_id=operator_id,
            operator_name=operator_name or "系统",
            action=OperationLogger.ACTION_MERGE,
            module=OperationLogger.MODULE_ORDER,
            details={
                "total_requests": len(request_ids),
                "from_inventory_count": len(inventory_reduced),
                "need_print_count": len(need_print),
                "need_print_request_ids": need_print,
                "inventory_details": inventory_reduced,
                "order_result": order_result
            }
        )

        return {
            "success": True,
            "total_requests": len(request_ids),
            "fulfilled_from_inventory": len(inventory_reduced),
            "sent_to_print": len(need_print),
            "inventory_usage": inventory_reduced,
            "order_result": order_result
        }

    @staticmethod
    def get_batch_print_candidates(department_id: Optional[int] = None) -> List[dict]:
        """获取可合并印制的已通过申请"""
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            sql = """
                SELECT pr.*, e.name as employee_name, e.employee_no,
                       d.name as department_name
                FROM print_requests pr
                LEFT JOIN employees e ON pr.employee_id = e.id
                LEFT JOIN departments d ON pr.department_id = d.id
                WHERE pr.status = 'approved'
                AND pr.id NOT IN (SELECT DISTINCT request_id FROM print_orders WHERE request_id IS NOT NULL)
            """
            params = []
            if department_id:
                sql += " AND pr.department_id = ?"
                params.append(department_id)
            sql += " ORDER BY pr.material_type, pr.created_at"
            cursor.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]

    @staticmethod
    def process_entire_workflow(
        items: List[Dict[str, Any]],
        department_id: int,
        operator_id: int,
        approver_id: Optional[int] = None,
        operator_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """一键完成批量印制全流程：创建申请 -> 审批 -> 匹配库存 -> 生成订单"""
        request_step = BatchPrintManager.create_batch_request(
            department_id=department_id,
            operator_id=operator_id,
            items=items,
            operator_name=operator_name
        )

        if not request_step.get("created_requests"):
            return {
                "stage": "create_requests",
                "success": False,
                "message": "没有成功创建的申请",
                "request_step": request_step
            }

        created_ids = [r["request_id"] for r in request_step["created_requests"]]

        approval_step = BatchPrintManager.validate_and_approve_batch(
            request_ids=created_ids,
            approver_id=approver_id or operator_id
        )

        if not approval_step.get("approved_ids"):
            return {
                "stage": "approval",
                "success": False,
                "message": "没有通过审批的申请",
                "request_step": request_step,
                "approval_step": approval_step
            }

        order_step = BatchPrintManager.process_and_merge_orders(
            request_ids=approval_step["approved_ids"],
            operator_id=operator_id,
            operator_name=operator_name
        )

        return {
            "success": True,
            "stage": "completed",
            "request_step": request_step,
            "approval_step": approval_step,
            "order_step": order_step
        }
