import csv
import json
import sqlite3
from datetime import datetime
from typing import Optional

from database import get_db_connection


class OperationLogger:
    MODULE_REQUEST = "print_request"
    MODULE_APPROVAL = "approval"
    MODULE_ORDER = "print_order"
    MODULE_INVENTORY = "inventory"
    MODULE_BUDGET = "budget"
    MODULE_REPORT = "report"
    MODULE_SYSTEM = "system"

    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_APPROVE = "approve"
    ACTION_REJECT = "reject"
    ACTION_SUBMIT = "submit"
    ACTION_VALIDATE = "validate"
    ACTION_PICKUP = "pickup"
    ACTION_REMIND = "remind"
    ACTION_GENERATE = "generate"
    ACTION_EXPORT = "export"
    ACTION_MERGE = "merge"
    ACTION_WARN = "warn"

    @staticmethod
    def log(
        operator_id: Optional[int],
        operator_name: Optional[str],
        action: str,
        module: str,
        target_id: Optional[int] = None,
        target_type: Optional[str] = None,
        details: Optional[dict] = None,
        ip_address: Optional[str] = None
    ):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            details_str = json.dumps(details, ensure_ascii=False) if details else None
            cursor.execute(
                """INSERT INTO operation_logs
                   (operator_id, operator_name, action, module, target_id, target_type, details, ip_address)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (operator_id, operator_name, action, module, target_id, target_type, details_str, ip_address)
            )

    @staticmethod
    def query_logs(
        operator_id: Optional[int] = None,
        department_id: Optional[int] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        module: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ):
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            sql = """
                SELECT l.*, e.department_id as operator_department
                FROM operation_logs l
                LEFT JOIN employees e ON l.operator_id = e.id
                WHERE 1=1
            """
            params = []

            if operator_id is not None:
                sql += " AND l.operator_id = ?"
                params.append(operator_id)

            if department_id is not None:
                sql += " AND e.department_id = ?"
                params.append(department_id)

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

            cursor.execute(sql, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def export_logs_to_csv(
        file_path: str,
        operator_id: Optional[int] = None,
        department_id: Optional[int] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        module: Optional[str] = None,
        action: Optional[str] = None
    ):
        import csv
        logs = OperationLogger.query_logs(
            operator_id=operator_id,
            department_id=department_id,
            start_time=start_time,
            end_time=end_time,
            module=module,
            action=action,
            limit=100000,
            offset=0
        )

        fieldnames = [
            "id", "operator_id", "operator_name", "action", "module",
            "target_id", "target_type", "details", "ip_address", "created_at"
        ]

        with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for log in logs:
                row = {k: log.get(k, "") for k in fieldnames}
                writer.writerow(row)

        return len(logs)
