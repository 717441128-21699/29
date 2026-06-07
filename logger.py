import csv
import json
import os
from typing import Optional

from database import db_conn
from config import LOG_DIR, now_str


class LogModule:
    PRINT_REQUEST = "print_request"
    APPROVAL = "approval"
    PRINT_ORDER = "print_order"
    INVENTORY = "inventory"
    BUDGET = "budget"
    REPORT = "report"
    BATCH = "batch_print"
    SYSTEM = "system"


class LogAction:
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    SUBMIT = "submit"
    VALIDATE = "validate"
    APPROVE = "approve"
    REJECT = "reject"
    PICKUP = "pickup"
    REMIND = "remind"
    GENERATE = "generate"
    EXPORT = "export"
    MERGE = "merge"
    WARN = "warn"
    DELIVER = "deliver"
    COMPLETE = "complete"


class OperationLogger:

    @staticmethod
    def record(
        operator_id: Optional[int],
        operator_name: Optional[str],
        action: str,
        module: str,
        target_id: Optional[int] = None,
        target_type: Optional[str] = None,
        details: Optional[dict] = None,
        ip_address: Optional[str] = None
    ) -> int:
        details_str = json.dumps(details, ensure_ascii=False) if details else None
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """INSERT INTO operation_logs
                   (operator_id, operator_name, action, module,
                    target_id, target_type, details, ip_address)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (operator_id, operator_name, action, module,
                 target_id, target_type, details_str, ip_address)
            )
            log_id = c.lastrowid

        log_file = os.path.join(LOG_DIR, f"oplog_{now_str()[:10]}.log")
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(
                    f"[{now_str()}] [{module}.{action}] "
                    f"operator={operator_name or 'System'}({operator_id}) "
                    f"target={target_type or '-'}({target_id}) "
                    f"details={details_str or '-'}\n"
                )
        except Exception:
            pass

        return log_id

    @staticmethod
    def query(
        operator_id: Optional[int] = None,
        dept_id: Optional[int] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        module: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 200,
        offset: int = 0
    ) -> list:
        with db_conn() as conn:
            c = conn.cursor()
            sql = """
                SELECT l.*, e.dept_id AS operator_dept
                FROM operation_logs l
                LEFT JOIN employees e ON l.operator_id = e.emp_id
                WHERE 1=1
            """
            params = []
            if operator_id is not None:
                sql += " AND l.operator_id = ?"
                params.append(operator_id)
            if dept_id is not None:
                sql += " AND e.dept_id = ?"
                params.append(dept_id)
            if start_time:
                sql += " AND l.created_at >= ?"
                params.append(start_time)
            if end_time:
                sql += " AND l.created_at <= ?"
                params.append(end_time)
            if module:
                sql += " AND l.module = ?"
                params.append(module)
            if action:
                sql += " AND l.action = ?"
                params.append(action)
            sql += " ORDER BY l.created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            c.execute(sql, params)
            return [dict(r) for r in c.fetchall()]

    @staticmethod
    def export_csv(
        file_path: str,
        operator_id: Optional[int] = None,
        dept_id: Optional[int] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        module: Optional[str] = None,
        action: Optional[str] = None
    ) -> int:
        logs = OperationLogger.query(
            operator_id=operator_id,
            dept_id=dept_id,
            start_time=start_time,
            end_time=end_time,
            module=module,
            action=action,
            limit=999999,
            offset=0
        )
        headers = ["log_id", "operator_id", "operator_name", "module", "action",
                   "target_id", "target_type", "details", "ip_address", "created_at"]
        with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for log in logs:
                writer.writerow([log.get(h, "") for h in headers])
        return len(logs)
