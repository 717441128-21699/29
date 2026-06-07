import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any, List

from config import (
    APPROVAL_THRESHOLD_SUPERVISOR, APPROVAL_THRESHOLD_DIRECTOR,
    DEFAULT_DEPARTMENT_BUDGET, get_current_month_str
)
from database import get_db_connection
from logger import OperationLogger


class BudgetManager:
    @staticmethod
    def get_or_create_monthly_budget(department_id: int, month: Optional[str] = None) -> dict:
        if not month:
            month = get_current_month_str()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM department_budgets WHERE department_id = ? AND month = ?",
                (department_id, month)
            )
            row = cursor.fetchone()

            if row:
                return dict(row)

            cursor.execute(
                "SELECT monthly_budget FROM departments WHERE id = ?",
                (department_id,)
            )
            dept_row = cursor.fetchone()
            allocated = dept_row["monthly_budget"] if dept_row else DEFAULT_DEPARTMENT_BUDGET

            cursor.execute(
                """INSERT INTO department_budgets
                   (department_id, month, allocated_budget, used_budget)
                   VALUES (?, ?, ?, 0)""",
                (department_id, month, allocated)
            )
            budget_id = cursor.lastrowid

            return {
                "id": budget_id,
                "department_id": department_id,
                "month": month,
                "allocated_budget": allocated,
                "used_budget": 0
            }

    @staticmethod
    def check_budget_sufficient(department_id: int, amount: float, month: Optional[str] = None) -> Dict[str, Any]:
        budget = BudgetManager.get_or_create_monthly_budget(department_id, month)
        remaining = budget["allocated_budget"] - budget["used_budget"]

        result = {
            "sufficient": remaining >= amount,
            "allocated": budget["allocated_budget"],
            "used": budget["used_budget"],
            "remaining": remaining,
            "requested": amount,
            "deficit": max(0, amount - remaining)
        }
        return result

    @staticmethod
    def consume_budget(department_id: int, amount: float, month: Optional[str] = None) -> bool:
        if not month:
            month = get_current_month_str()

        budget = BudgetManager.get_or_create_monthly_budget(department_id, month)
        remaining = budget["allocated_budget"] - budget["used_budget"]

        if remaining < amount:
            return False

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE department_budgets
                   SET used_budget = used_budget + ?
                   WHERE department_id = ? AND month = ?""",
                (amount, department_id, month)
            )
        return True

    @staticmethod
    def release_budget(department_id: int, amount: float, month: Optional[str] = None):
        if not month:
            month = get_current_month_str()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE department_budgets
                   SET used_budget = MAX(0, used_budget - ?)
                   WHERE department_id = ? AND month = ?""",
                (amount, department_id, month)
            )

    @staticmethod
    def get_department_budget_summary(department_id: int, month: Optional[str] = None) -> dict:
        budget = BudgetManager.get_or_create_monthly_budget(department_id, month)
        usage_rate = (budget["used_budget"] / budget["allocated_budget"] * 100) if budget["allocated_budget"] > 0 else 0

        return {
            "department_id": department_id,
            "month": budget["month"],
            "allocated": budget["allocated_budget"],
            "used": budget["used_budget"],
            "remaining": budget["allocated_budget"] - budget["used_budget"],
            "usage_rate": round(usage_rate, 2)
        }


class ApprovalManager:
    LEVEL_NONE = "none"
    LEVEL_SUPERVISOR = "supervisor"
    LEVEL_DIRECTOR = "director"

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"

    @staticmethod
    def determine_approval_level(amount: float) -> str:
        if amount >= APPROVAL_THRESHOLD_DIRECTOR:
            return ApprovalManager.LEVEL_DIRECTOR
        elif amount >= APPROVAL_THRESHOLD_SUPERVISOR:
            return ApprovalManager.LEVEL_SUPERVISOR
        return ApprovalManager.LEVEL_NONE

    @staticmethod
    def get_approver(department_id: int, level: str) -> Optional[int]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if level == ApprovalManager.LEVEL_DIRECTOR:
                cursor.execute(
                    "SELECT director_id FROM departments WHERE id = ?",
                    (department_id,)
                )
            else:
                cursor.execute(
                    "SELECT supervisor_id FROM departments WHERE id = ?",
                    (department_id,)
                )
            row = cursor.fetchone()
            return row[0] if row and row[0] else None

    @staticmethod
    def process_request_budget_and_approval(request_id: int, operator_id: Optional[int] = None) -> Dict[str, Any]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM print_requests WHERE id = ?", (request_id,))
            request = cursor.fetchone()

            if not request:
                return {"success": False, "message": "申请不存在"}

            req = dict(request)
            if req["status"] != "pending_validation":
                return {"success": False, "message": f"申请状态异常: {req['status']}"}

            total_amount = req["total_amount"]
            department_id = req["department_id"]

            budget_check = BudgetManager.check_budget_sufficient(department_id, total_amount)

            if not budget_check["sufficient"]:
                cursor.execute(
                    """UPDATE print_requests
                       SET status = 'rejected', rejection_reason = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (f"部门月度预算不足，剩余{budget_check['remaining']:.2f}元，需{total_amount:.2f}元", request_id)
                )
                OperationLogger.log(
                    operator_id=operator_id,
                    operator_name="系统",
                    action=OperationLogger.ACTION_REJECT,
                    module=OperationLogger.MODULE_BUDGET,
                    target_id=request_id,
                    target_type="print_request",
                    details={
                        "reason": "预算不足",
                        "budget_remaining": budget_check["remaining"],
                        "requested_amount": total_amount
                    }
                )
                return {
                    "success": False,
                    "message": "部门月度预算不足，申请被拒绝",
                    "budget_check": budget_check,
                    "status": "rejected"
                }

            approval_level = ApprovalManager.determine_approval_level(total_amount)

            if approval_level == ApprovalManager.LEVEL_NONE:
                BudgetManager.consume_budget(department_id, total_amount)
                cursor.execute(
                    """UPDATE print_requests
                       SET status = 'approved', updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (request_id,)
                )
                OperationLogger.log(
                    operator_id=operator_id,
                    operator_name="系统",
                    action=OperationLogger.ACTION_APPROVE,
                    module=OperationLogger.MODULE_APPROVAL,
                    target_id=request_id,
                    target_type="print_request",
                    details={"level": "auto", "amount": total_amount}
                )
                return {
                    "success": True,
                    "message": "预算充足，金额低于审批阈值，自动通过",
                    "budget_check": budget_check,
                    "approval_level": approval_level,
                    "status": "approved"
                }

            approver_id = ApprovalManager.get_approver(department_id, approval_level)

            cursor.execute(
                """UPDATE print_requests
                   SET status = 'pending_approval', updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (request_id,)
            )

            cursor.execute(
                """INSERT INTO approvals
                   (request_id, approval_level, approver_id, status)
                   VALUES (?, ?, ?, 'pending')""",
                (request_id, approval_level, approver_id)
            )

            level_name = "主管" if approval_level == ApprovalManager.LEVEL_SUPERVISOR else "总监"
            OperationLogger.log(
                operator_id=operator_id,
                operator_name="系统",
                action=OperationLogger.ACTION_VALIDATE,
                module=OperationLogger.MODULE_APPROVAL,
                target_id=request_id,
                target_type="print_request",
                details={
                    "level": approval_level,
                    "level_name": level_name,
                    "approver_id": approver_id,
                    "amount": total_amount
                }
            )

            return {
                "success": True,
                "message": f"预算充足，需{level_name}审批",
                "budget_check": budget_check,
                "approval_level": approval_level,
                "approver_id": approver_id,
                "status": "pending_approval"
            }

    @staticmethod
    def approve_request(
        request_id: int,
        approver_id: int,
        comments: Optional[str] = None,
        operator_name: Optional[str] = None
    ) -> Dict[str, Any]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM print_requests WHERE id = ?", (request_id,))
            request = cursor.fetchone()

            if not request:
                return {"success": False, "message": "申请不存在"}

            req = dict(request)
            if req["status"] != "pending_approval":
                return {"success": False, "message": f"当前状态无需审批: {req['status']}"}

            cursor.execute(
                "SELECT * FROM approvals WHERE request_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
                (request_id,)
            )
            approval = cursor.fetchone()

            if not approval:
                return {"success": False, "message": "无待审批记录"}

            if approval["approver_id"] and approval["approver_id"] != approver_id:
                return {"success": False, "message": "您不是该申请的审批人"}

            BudgetManager.consume_budget(req["department_id"], req["total_amount"])

            cursor.execute(
                """UPDATE approvals
                   SET status = 'approved', comments = ?, approved_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (comments, approval["id"])
            )

            cursor.execute(
                """UPDATE print_requests
                   SET status = 'approved', updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (request_id,)
            )

            OperationLogger.log(
                operator_id=approver_id,
                operator_name=operator_name,
                action=OperationLogger.ACTION_APPROVE,
                module=OperationLogger.MODULE_APPROVAL,
                target_id=request_id,
                target_type="print_request",
                details={"comments": comments, "amount": req["total_amount"]}
            )

            return {"success": True, "message": "审批通过"}

    @staticmethod
    def reject_request(
        request_id: int,
        approver_id: int,
        reason: str,
        operator_name: Optional[str] = None
    ) -> Dict[str, Any]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM print_requests WHERE id = ?", (request_id,))
            request = cursor.fetchone()

            if not request:
                return {"success": False, "message": "申请不存在"}

            req = dict(request)
            if req["status"] != "pending_approval":
                return {"success": False, "message": f"当前状态无法拒绝: {req['status']}"}

            cursor.execute(
                "SELECT * FROM approvals WHERE request_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
                (request_id,)
            )
            approval = cursor.fetchone()

            if not approval:
                return {"success": False, "message": "无待审批记录"}

            if approval["approver_id"] and approval["approver_id"] != approver_id:
                return {"success": False, "message": "您不是该申请的审批人"}

            cursor.execute(
                """UPDATE approvals
                   SET status = 'rejected', comments = ?, approved_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (reason, approval["id"])
            )

            cursor.execute(
                """UPDATE print_requests
                   SET status = 'rejected', rejection_reason = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (reason, request_id)
            )

            OperationLogger.log(
                operator_id=approver_id,
                operator_name=operator_name,
                action=OperationLogger.ACTION_REJECT,
                module=OperationLogger.MODULE_APPROVAL,
                target_id=request_id,
                target_type="print_request",
                details={"reason": reason}
            )

            return {"success": True, "message": "已拒绝申请"}

    @staticmethod
    def list_pending_approvals(approver_id: Optional[int] = None, department_id: Optional[int] = None) -> List[dict]:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            sql = """
                SELECT a.*, pr.request_no, pr.material_type, pr.quantity,
                       pr.total_amount, pr.employee_id, pr.department_id,
                       e.name as employee_name, d.name as department_name
                FROM approvals a
                JOIN print_requests pr ON a.request_id = pr.id
                LEFT JOIN employees e ON pr.employee_id = e.id
                LEFT JOIN departments d ON pr.department_id = d.id
                WHERE a.status = 'pending'
            """
            params = []
            if approver_id:
                sql += " AND a.approver_id = ?"
                params.append(approver_id)
            if department_id:
                sql += " AND pr.department_id = ?"
                params.append(department_id)
            sql += " ORDER BY a.created_at DESC"
            cursor.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]
