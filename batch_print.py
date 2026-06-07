from typing import Optional, Dict, Any, List

from config import MATERIAL_CATALOG
from database import db_conn
from logger import OperationLogger, LogModule, LogAction
from print_request import (
    PrintRequestService, EmployeeValidator, MaterialValidator
)
from approval import ApprovalService, BudgetService
from print_order import PrintOrderService
from inventory import InventoryService


class BatchPrintService:

    @staticmethod
    def create_requests(
        dept_id: int,
        operator_id: int,
        items: List[Dict[str, Any]],
        operator_name: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Dict[str, Any]:
        if not items:
            return {"success": False, "message": "批量印制项目不能为空"}

        results = []
        created = []
        total_amount = 0.0
        errors = []

        for idx, item in enumerate(items):
            emp_id = item.get("emp_id")
            mtype = item.get("material_type")
            qty = int(item.get("quantity", 0))
            custom = item.get("custom_info")

            if not emp_id:
                errors.append(f"第{idx + 1}项: 缺少员工ID (emp_id)")
                continue

            ok, msg, emp = EmployeeValidator.validate(emp_id)
            if not ok:
                errors.append(f"第{idx + 1}项: {msg}")
                continue
            if emp["dept_id"] != dept_id:
                errors.append(
                    f"第{idx + 1}项: 员工 {emp['emp_name']} 不属于本部门，已跳过"
                )
                continue

            ok, msg = MaterialValidator.validate(mtype, qty)
            if not ok:
                errors.append(f"第{idx + 1}项: {msg}")
                continue

            r = PrintRequestService.submit(
                emp_id=emp_id,
                material_type=mtype,
                quantity=qty,
                custom_info=custom,
                ip_address=ip_address
            )
            results.append(r)
            if r.get("success"):
                created.append(r)
                total_amount += r["total_amount"]

        budget_check = BudgetService.check(dept_id, total_amount)

        OperationLogger.record(
            operator_id=operator_id,
            operator_name=operator_name or "批量操作",
            action=LogAction.MERGE,
            module=LogModule.BATCH,
            details={
                "dept_id": dept_id,
                "total_items": len(items),
                "created_count": len(created),
                "error_count": len(errors),
                "total_amount": round(total_amount, 2),
                "budget_sufficient": budget_check["sufficient"]
            }
        )
        return {
            "success": len(created) > 0,
            "total_submitted": len(items),
            "created_count": len(created),
            "error_count": len(errors),
            "total_amount": round(total_amount, 2),
            "errors": errors,
            "budget_check": budget_check,
            "created_requests": created,
            "raw_results": results
        }

    @staticmethod
    def batch_approve(req_ids: List[int], approver_id: int,
                      operator_name: Optional[str] = None) -> Dict[str, Any]:
        approved = []
        failed = []
        for rid in req_ids:
            r = ApprovalService.process(rid, approver_id, operator_name)
            if not r.get("success"):
                failed.append({"req_id": rid, "reason": r.get("message")})
                continue

            if r.get("status") == "pending_approval":
                ar = ApprovalService.approve(rid, approver_id,
                                             "批量审批通过", operator_name)
                if ar.get("success"):
                    approved.append(rid)
                else:
                    failed.append({"req_id": rid, "reason": ar.get("message")})
            elif r.get("status") == "approved":
                approved.append(rid)
            else:
                failed.append({"req_id": rid, "reason": r.get("message")})

        OperationLogger.record(
            operator_id=approver_id,
            operator_name=operator_name or "批量审批",
            action=LogAction.APPROVE,
            module=LogModule.APPROVAL,
            details={
                "total_requests": len(req_ids),
                "approved_count": len(approved),
                "failed_count": len(failed),
                "approved_ids": approved,
                "failed": failed
            }
        )
        return {
            "success": len(approved) > 0,
            "total": len(req_ids),
            "approved_count": len(approved),
            "failed_count": len(failed),
            "approved_ids": approved,
            "failed": failed
        }

    @staticmethod
    def merge_and_create_orders(
        req_ids: List[int],
        operator_id: Optional[int] = None,
        operator_name: Optional[str] = None
    ) -> Dict[str, Any]:
        with db_conn() as conn:
            c = conn.cursor()

            from_stock = []
            need_print = []

            for rid in req_ids:
                req = PrintRequestService.get(rid)
                if not req or req["status"] != "approved":
                    continue

                mtype = req["material_type"]
                dept_id = req["dept_id"]
                remaining = req["quantity"]

                c.execute(
                    """SELECT * FROM inventory
                       WHERE material_type = ? AND dept_id = ?
                         AND status = 'in_stock' AND emp_id IS NULL
                       ORDER BY ready_at ASC""",
                    (mtype, dept_id)
                )
                stock = [dict(r) for r in c.fetchall()]

                for s in stock:
                    if remaining <= 0:
                        break
                    take = min(s["quantity"], remaining)
                    new_qty = s["quantity"] - take
                    remaining -= take
                    if new_qty == 0:
                        c.execute(
                            "UPDATE inventory SET status = 'consumed' WHERE inv_id = ?",
                            (s["inv_id"],)
                        )
                    else:
                        c.execute(
                            "UPDATE inventory SET quantity = ? WHERE inv_id = ?",
                            (new_qty, s["inv_id"])
                        )
                    from_stock.append({
                        "inv_id": s["inv_id"],
                        "material_type": mtype,
                        "taken": take,
                        "req_id": rid
                    })

                if remaining > 0:
                    if remaining < req["quantity"]:
                        up = MATERIAL_CATALOG[mtype]["unit_price"]
                        c.execute(
                            """UPDATE print_requests
                               SET quantity = ?, total_amount = ? WHERE req_id = ?""",
                            (remaining, round(remaining * up, 2), rid)
                        )
                    need_print.append(rid)

        order_result = {"success": True, "batch_id": None, "results": []}
        if need_print:
            order_result = PrintOrderService.create_batch(
                need_print, operator_id, operator_name
            )

        OperationLogger.record(
            operator_id=operator_id,
            operator_name=operator_name or "系统",
            action=LogAction.MERGE,
            module=LogModule.PRINT_ORDER,
            details={
                "total_requests": len(req_ids),
                "from_stock_count": len(from_stock),
                "need_print_count": len(need_print),
                "need_print_ids": need_print,
                "stock_usage": from_stock,
                "order_result_summary": {
                    "batch_id": order_result.get("batch_id"),
                    "success_count": order_result.get("success_count", 0)
                }
            }
        )
        return {
            "success": True,
            "total_requests": len(req_ids),
            "fulfilled_from_inventory": len(from_stock),
            "sent_to_print": len(need_print),
            "inventory_usage": from_stock,
            "order_result": order_result
        }

    @staticmethod
    def candidates(dept_id: Optional[int] = None) -> List[dict]:
        with db_conn() as conn:
            c = conn.cursor()
            sql = """
                SELECT r.*, e.emp_name, e.emp_no, d.dept_name
                FROM print_requests r
                LEFT JOIN employees e ON r.emp_id = e.emp_id
                LEFT JOIN departments d ON r.dept_id = d.dept_id
                WHERE r.status = 'approved'
                  AND r.req_id NOT IN
                      (SELECT DISTINCT req_id FROM print_orders WHERE req_id IS NOT NULL)
            """
            params = []
            if dept_id:
                sql += " AND r.dept_id = ?"
                params.append(dept_id)
            sql += " ORDER BY r.material_type, r.created_at"
            c.execute(sql, params)
            return [dict(r) for r in c.fetchall()]

    @staticmethod
    def full_workflow(
        items: List[Dict[str, Any]],
        dept_id: int,
        operator_id: int,
        approver_id: Optional[int] = None,
        operator_name: Optional[str] = None
    ) -> Dict[str, Any]:
        step1 = BatchPrintService.create_requests(
            dept_id=dept_id, operator_id=operator_id,
            items=items, operator_name=operator_name
        )
        if not step1.get("created_requests"):
            return {
                "stage": "create_requests",
                "success": False,
                "message": "没有成功创建的申请",
                "step_create": step1
            }
        created_ids = [r["req_id"] for r in step1["created_requests"]]

        step2 = BatchPrintService.batch_approve(
            req_ids=created_ids,
            approver_id=approver_id or operator_id,
            operator_name=operator_name
        )
        if not step2.get("approved_ids"):
            return {
                "stage": "approval",
                "success": False,
                "message": "没有通过审批的申请",
                "step_create": step1,
                "step_approve": step2
            }

        step3 = BatchPrintService.merge_and_create_orders(
            req_ids=step2["approved_ids"],
            operator_id=operator_id,
            operator_name=operator_name
        )
        return {
            "success": True,
            "stage": "completed",
            "step_create": step1,
            "step_approve": step2,
            "step_order": step3
        }
