from typing import Optional, Dict, Any, List

from config import (
    APPROVAL_SUPERVISOR_THRESHOLD,
    APPROVAL_DIRECTOR_THRESHOLD,
    DEFAULT_MONTHLY_BUDGET,
    current_month_str, now_str
)
from database import db_conn
from logger import OperationLogger, LogModule, LogAction


class BudgetService:

    @staticmethod
    def get_budget(dept_id: int, month: Optional[str] = None) -> dict:
        if not month:
            month = current_month_str()
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT * FROM department_budgets WHERE dept_id = ? AND month = ?",
                (dept_id, month)
            )
            row = c.fetchone()
            if row:
                return dict(row)

            c.execute("SELECT monthly_budget FROM departments WHERE dept_id = ?", (dept_id,))
            dr = c.fetchone()
            allocated = dr["monthly_budget"] if dr else DEFAULT_MONTHLY_BUDGET

            c.execute(
                """INSERT INTO department_budgets
                   (dept_id, month, allocated, used) VALUES (?, ?, ?, 0)""",
                (dept_id, month, allocated)
            )
            return {
                "dept_id": dept_id,
                "month": month,
                "allocated": allocated,
                "used": 0
            }

    @staticmethod
    def check(dept_id: int, amount: float, month: Optional[str] = None) -> Dict[str, Any]:
        b = BudgetService.get_budget(dept_id, month)
        remaining = b["allocated"] - b["used"]
        return {
            "sufficient": remaining >= amount,
            "allocated": b["allocated"],
            "used": b["used"],
            "remaining": round(remaining, 2),
            "requested": amount,
            "deficit": round(max(0.0, amount - remaining), 2)
        }

    @staticmethod
    def consume(dept_id: int, amount: float, month: Optional[str] = None) -> bool:
        if not month:
            month = current_month_str()
        check = BudgetService.check(dept_id, amount, month)
        if not check["sufficient"]:
            return False
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """UPDATE department_budgets
                   SET used = used + ? WHERE dept_id = ? AND month = ?""",
                (amount, dept_id, month)
            )
        return True

    @staticmethod
    def release(dept_id: int, amount: float, month: Optional[str] = None):
        if not month:
            month = current_month_str()
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """UPDATE department_budgets
                   SET used = MAX(0, used - ?) WHERE dept_id = ? AND month = ?""",
                (amount, dept_id, month)
            )

    @staticmethod
    def summary(dept_id: int, month: Optional[str] = None) -> dict:
        b = BudgetService.get_budget(dept_id, month)
        rate = (b["used"] / b["allocated"] * 100) if b["allocated"] > 0 else 0
        return {
            "dept_id": dept_id,
            "month": b["month"],
            "allocated": b["allocated"],
            "used": round(b["used"], 2),
            "remaining": round(b["allocated"] - b["used"], 2),
            "usage_rate": round(rate, 2)
        }


class ApprovalService:
    LEVEL_NONE = "none"
    LEVEL_SUPERVISOR = "supervisor"
    LEVEL_DIRECTOR = "director"

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"

    @staticmethod
    def required_level(amount: float) -> str:
        if amount >= APPROVAL_DIRECTOR_THRESHOLD:
            return ApprovalService.LEVEL_DIRECTOR
        if amount >= APPROVAL_SUPERVISOR_THRESHOLD:
            return ApprovalService.LEVEL_SUPERVISOR
        return ApprovalService.LEVEL_NONE

    @staticmethod
    def get_approver_id(dept_id: int, level: str) -> Optional[int]:
        with db_conn() as conn:
            c = conn.cursor()
            col = "director_id" if level == ApprovalService.LEVEL_DIRECTOR else "supervisor_id"
            c.execute(f"SELECT {col} FROM departments WHERE dept_id = ?", (dept_id,))
            r = c.fetchone()
            return r[0] if r and r[0] else None

    @staticmethod
    def process(req_id: int, operator_id: Optional[int] = None,
                operator_name: Optional[str] = None) -> Dict[str, Any]:
        with db_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM print_requests WHERE req_id = ?", (req_id,))
            row = c.fetchone()
            if not row:
                return {"success": False, "message": "申请不存在"}
            req = dict(row)

            if req["status"] != "pending_validate":
                return {"success": False, "message": f"申请状态异常: {req['status']}"}

            amount = req["total_amount"]
            dept_id = req["dept_id"]

            check = BudgetService.check(dept_id, amount)
            if not check["sufficient"]:
                reason = (f"部门月度预算不足。"
                          f"剩余 {check['remaining']:.2f} 元，"
                          f"申请 {check['requested']:.2f} 元，"
                          f"缺口 {check['deficit']:.2f} 元")
                c.execute(
                    """UPDATE print_requests
                       SET status = 'rejected', rejection_reason = ?,
                           updated_at = ? WHERE req_id = ?""",
                    (reason, now_str(), req_id)
                )
                OperationLogger.record(
                    operator_id=operator_id,
                    operator_name=operator_name or "系统",
                    action=LogAction.REJECT,
                    module=LogModule.BUDGET,
                    target_id=req_id,
                    target_type="print_request",
                    details={"reason": "预算不足", **check}
                )
                return {"success": False, "message": reason, "status": "rejected",
                        "budget": check}

            level = ApprovalService.required_level(amount)

            if level == ApprovalService.LEVEL_NONE:
                BudgetService.consume(dept_id, amount)
                c.execute(
                    """UPDATE print_requests
                       SET status = 'approved', updated_at = ? WHERE req_id = ?""",
                    (now_str(), req_id)
                )
                OperationLogger.record(
                    operator_id=operator_id,
                    operator_name=operator_name or "系统",
                    action=LogAction.APPROVE,
                    module=LogModule.APPROVAL,
                    target_id=req_id,
                    target_type="print_request",
                    details={"level": "auto", "amount": amount}
                )
                return {"success": True, "message": "预算充足，金额低于审批阈值，自动通过",
                        "budget": check, "approval_level": level, "status": "approved"}

            approver_id = ApprovalService.get_approver_id(dept_id, level)
            c.execute(
                """UPDATE print_requests
                   SET status = 'pending_approval', updated_at = ? WHERE req_id = ?""",
                (now_str(), req_id)
            )
            c.execute(
                """INSERT INTO approvals
                   (req_id, approval_level, approver_id, status)
                   VALUES (?, ?, ?, 'pending')""",
                (req_id, level, approver_id)
            )

            level_name = "主管" if level == ApprovalService.LEVEL_SUPERVISOR else "总监"
            OperationLogger.record(
                operator_id=operator_id,
                operator_name=operator_name or "系统",
                action=LogAction.VALIDATE,
                module=LogModule.APPROVAL,
                target_id=req_id,
                target_type="print_request",
                details={"level": level, "level_name": level_name,
                         "approver_id": approver_id, "amount": amount}
            )
            return {
                "success": True,
                "message": f"预算充足，需{level_name}审批 (≥{APPROVAL_SUPERVISOR_THRESHOLD if level == ApprovalService.LEVEL_SUPERVISOR else APPROVAL_DIRECTOR_THRESHOLD:.0f}元)",
                "budget": check,
                "approval_level": level,
                "approver_id": approver_id,
                "status": "pending_approval"
            }

    @staticmethod
    def approve(req_id: int, approver_id: int, comments: Optional[str] = None,
                operator_name: Optional[str] = None) -> Dict[str, Any]:
        with db_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM print_requests WHERE req_id = ?", (req_id,))
            row = c.fetchone()
            if not row:
                return {"success": False, "message": "申请不存在"}
            req = dict(row)
            if req["status"] != "pending_approval":
                return {"success": False, "message": f"当前状态无需审批: {req['status']}"}

            c.execute(
                """SELECT * FROM approvals
                   WHERE req_id = ? AND status = 'pending' ORDER BY approval_id DESC LIMIT 1""",
                (req_id,)
            )
            ap = c.fetchone()
            if not ap:
                return {"success": False, "message": "无待审批记录"}
            if ap["approver_id"] and ap["approver_id"] != approver_id:
                return {"success": False, "message": "您不是该申请的审批人"}

            BudgetService.consume(req["dept_id"], req["total_amount"])

            c.execute(
                """UPDATE approvals
                   SET status = 'approved', comments = ?, approved_at = ?
                   WHERE approval_id = ?""",
                (comments, now_str(), ap["approval_id"])
            )
            c.execute(
                """UPDATE print_requests
                   SET status = 'approved', updated_at = ? WHERE req_id = ?""",
                (now_str(), req_id)
            )
            OperationLogger.record(
                operator_id=approver_id,
                operator_name=operator_name,
                action=LogAction.APPROVE,
                module=LogModule.APPROVAL,
                target_id=req_id,
                target_type="print_request",
                details={"comments": comments, "amount": req["total_amount"]}
            )
            return {"success": True, "message": "审批通过"}

    @staticmethod
    def reject(req_id: int, approver_id: int, reason: str,
               operator_name: Optional[str] = None) -> Dict[str, Any]:
        with db_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM print_requests WHERE req_id = ?", (req_id,))
            row = c.fetchone()
            if not row:
                return {"success": False, "message": "申请不存在"}
            req = dict(row)
            if req["status"] != "pending_approval":
                return {"success": False, "message": f"当前状态无法拒绝: {req['status']}"}

            c.execute(
                """SELECT * FROM approvals
                   WHERE req_id = ? AND status = 'pending' ORDER BY approval_id DESC LIMIT 1""",
                (req_id,)
            )
            ap = c.fetchone()
            if not ap:
                return {"success": False, "message": "无待审批记录"}
            if ap["approver_id"] and ap["approver_id"] != approver_id:
                return {"success": False, "message": "您不是该申请的审批人"}

            c.execute(
                """UPDATE approvals
                   SET status = 'rejected', comments = ?, approved_at = ?
                   WHERE approval_id = ?""",
                (reason, now_str(), ap["approval_id"])
            )
            c.execute(
                """UPDATE print_requests
                   SET status = 'rejected', rejection_reason = ?, updated_at = ?
                   WHERE req_id = ?""",
                (reason, now_str(), req_id)
            )
            OperationLogger.record(
                operator_id=approver_id,
                operator_name=operator_name,
                action=LogAction.REJECT,
                module=LogModule.APPROVAL,
                target_id=req_id,
                target_type="print_request",
                details={"reason": reason}
            )
            return {"success": True, "message": "已拒绝申请"}

    @staticmethod
    def pending(approver_id: Optional[int] = None,
                dept_id: Optional[int] = None) -> List[dict]:
        with db_conn() as conn:
            c = conn.cursor()
            sql = """
                SELECT a.*, r.req_no, r.material_type, r.quantity,
                       r.total_amount, r.emp_id, r.dept_id,
                       e.emp_name, d.dept_name
                FROM approvals a
                JOIN print_requests r ON a.req_id = r.req_id
                LEFT JOIN employees e ON r.emp_id = e.emp_id
                LEFT JOIN departments d ON r.dept_id = d.dept_id
                WHERE a.status = 'pending'
            """
            params = []
            if approver_id:
                sql += " AND a.approver_id = ?"
                params.append(approver_id)
            if dept_id:
                sql += " AND r.dept_id = ?"
                params.append(dept_id)
            sql += " ORDER BY a.created_at DESC"
            c.execute(sql, params)
            return [dict(r) for r in c.fetchall()]
