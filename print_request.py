import os
import json
import sqlite3
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

from config import (
    MATERIAL_TYPES, VALID_POSITIONS, TEMPLATE_DIR,
    COMPANY_NAME, COMPANY_ADDRESS, COMPANY_PHONE, COMPANY_WEBSITE
)
from database import get_db_connection
from logger import OperationLogger


class PrintRequestValidator:
    @staticmethod
    def validate_employee_info(employee_id: int) -> Tuple[bool, str, Optional[dict]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT e.*, d.name as department_name
                   FROM employees e
                   LEFT JOIN departments d ON e.department_id = d.id
                   WHERE e.id = ? AND e.is_active = 1""",
                (employee_id,)
            )
            row = cursor.fetchone()
            if not row:
                return False, "员工不存在或已离职", None

            emp = dict(row)
            dept_name = emp.get("department_name")
            position = emp.get("position")

            if dept_name not in VALID_POSITIONS:
                return False, f"部门'{dept_name}'不在有效部门列表中", emp

            valid_positions = VALID_POSITIONS.get(dept_name, [])
            if position not in valid_positions:
                return False, (
                    f"职务'{position}'与部门'{dept_name}'不匹配。"
                    f"该部门有效职务: {', '.join(valid_positions)}"
                ), emp

            return True, "员工信息校验通过", emp

    @staticmethod
    def validate_material(material_type: str, quantity: int) -> Tuple[bool, str]:
        if material_type not in MATERIAL_TYPES:
            return False, f"无效的物料类型: {material_type}"
        if quantity <= 0:
            return False, "数量必须大于0"
        if quantity > 10000:
            return False, "单次申请数量不能超过10000"
        return True, "物料信息校验通过"

    @staticmethod
    def calculate_cost(material_type: str, quantity: int) -> Tuple[float, float]:
        info = MATERIAL_TYPES[material_type]
        unit_price = info["unit_price"]
        total = round(unit_price * quantity, 2)
        return unit_price, total


class DesignTemplateGenerator:
    @staticmethod
    def generate_template(
        employee: dict,
        material_type: str,
        custom_info: Optional[dict] = None
    ) -> str:
        material_info = MATERIAL_TYPES[material_type]
        material_name = material_info["name"]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = employee.get("name", "unknown").replace(" ", "_")
        filename = f"{material_type}_{safe_name}_{timestamp}.txt"
        filepath = os.path.join(TEMPLATE_DIR, filename)

        template_content = DesignTemplateGenerator._build_template_content(
            employee, material_type, material_name, custom_info
        )

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(template_content)

        return filepath

    @staticmethod
    def _build_template_content(
        employee: dict,
        material_type: str,
        material_name: str,
        custom_info: Optional[dict]
    ) -> str:
        lines = []
        lines.append("=" * 60)
        lines.append(f"  {COMPANY_NAME} - {material_name}设计稿")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"【物料类型】{material_name}")
        lines.append(f"【生成时间】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        lines.append("-" * 40)
        lines.append("员工信息:")
        lines.append(f"  姓名: {employee.get('name', '')}")
        lines.append(f"  工号: {employee.get('employee_no', '')}")
        lines.append(f"  部门: {employee.get('department_name', '')}")
        lines.append(f"  职务: {employee.get('position', '')}")
        lines.append(f"  电话: {employee.get('phone', '')}")
        lines.append(f"  邮箱: {employee.get('email', '')}")
        lines.append("")
        lines.append("-" * 40)
        lines.append("公司信息:")
        lines.append(f"  公司名称: {COMPANY_NAME}")
        lines.append(f"  公司地址: {COMPANY_ADDRESS}")
        lines.append(f"  公司电话: {COMPANY_PHONE}")
        lines.append(f"  公司网站: {COMPANY_WEBSITE}")
        lines.append("")

        if material_type == "business_card":
            lines.append("-" * 40)
            lines.append("名片布局规范:")
            lines.append("  [正面]")
            lines.append(f"    左上角: {COMPANY_NAME} LOGO")
            lines.append(f"    中间偏上: {employee.get('name', '')}")
            lines.append(f"    中间: {employee.get('position', '')}")
            lines.append(f"    下方: {employee.get('department_name', '')}")
            lines.append("  [背面]")
            lines.append(f"    电话: {employee.get('phone', '')}")
            lines.append(f"    邮箱: {employee.get('email', '')}")
            lines.append(f"    地址: {COMPANY_ADDRESS}")
            lines.append(f"    网站: {COMPANY_WEBSITE}")
        elif material_type == "letterhead":
            lines.append("-" * 40)
            lines.append("信纸规范:")
            lines.append(f"  页眉: {COMPANY_NAME} - {employee.get('department_name', '')}")
            lines.append(f"  页脚: {COMPANY_ADDRESS} | {COMPANY_PHONE} | {COMPANY_WEBSITE}")
        elif material_type == "badge":
            lines.append("-" * 40)
            lines.append("工牌布局:")
            lines.append(f"  [正面]")
            lines.append(f"    顶部: {COMPANY_NAME}")
            lines.append(f"    中间: [照片]")
            lines.append(f"    姓名: {employee.get('name', '')}")
            lines.append(f"    职务: {employee.get('position', '')}")
            lines.append(f"    工号: {employee.get('employee_no', '')}")
            lines.append(f"  [背面]")
            lines.append(f"    注意事项...")

        if custom_info:
            lines.append("")
            lines.append("-" * 40)
            lines.append("自定义信息:")
            for k, v in custom_info.items():
                lines.append(f"  {k}: {v}")

        lines.append("")
        lines.append("=" * 60)
        lines.append("  此为标准模板，设计部将根据此稿进行专业设计")
        lines.append("=" * 60)

        return "\n".join(lines)


class PrintRequestManager:
    @staticmethod
    def create_request(
        employee_id: int,
        material_type: str,
        quantity: int,
        custom_info: Optional[dict] = None,
        ip_address: Optional[str] = None
    ) -> Dict[str, Any]:
        result = {
            "success": False,
            "request_id": None,
            "request_no": None,
            "status": None,
            "message": "",
            "total_amount": 0,
            "template_path": None,
            "validation_errors": []
        }

        valid_emp, emp_msg, employee = PrintRequestValidator.validate_employee_info(employee_id)
        if not valid_emp:
            result["message"] = emp_msg
            result["validation_errors"].append(emp_msg)
            OperationLogger.log(
                operator_id=employee_id,
                operator_name=employee.get("name") if employee else None,
                action=OperationLogger.ACTION_SUBMIT,
                module=OperationLogger.MODULE_REQUEST,
                details={"error": emp_msg, "material_type": material_type, "quantity": quantity},
                ip_address=ip_address
            )
            return result

        valid_mat, mat_msg = PrintRequestValidator.validate_material(material_type, quantity)
        if not valid_mat:
            result["message"] = mat_msg
            result["validation_errors"].append(mat_msg)
            OperationLogger.log(
                operator_id=employee_id,
                operator_name=employee["name"],
                action=OperationLogger.ACTION_SUBMIT,
                module=OperationLogger.MODULE_REQUEST,
                details={"error": mat_msg, "material_type": material_type, "quantity": quantity},
                ip_address=ip_address
            )
            return result

        unit_price, total_amount = PrintRequestValidator.calculate_cost(material_type, quantity)

        template_path = None
        mat_info = MATERIAL_TYPES[material_type]
        if mat_info.get("template_required", False):
            template_path = DesignTemplateGenerator.generate_template(
                employee, material_type, custom_info
            )

        request_no = f"PR{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"

        custom_info_str = json.dumps(custom_info, ensure_ascii=False) if custom_info else None

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO print_requests
                   (request_no, employee_id, department_id, material_type, quantity,
                    unit_price, total_amount, template_path, custom_info,
                    status, valid_employee_info)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_validation', 1)""",
                (request_no, employee_id, employee["department_id"], material_type,
                 quantity, unit_price, total_amount, template_path, custom_info_str)
            )
            request_id = cursor.lastrowid

        result["success"] = True
        result["request_id"] = request_id
        result["request_no"] = request_no
        result["status"] = "pending_validation"
        result["message"] = "申请已提交，等待预算与审批流程"
        result["total_amount"] = total_amount
        result["template_path"] = template_path
        result["employee"] = employee

        OperationLogger.log(
            operator_id=employee_id,
            operator_name=employee["name"],
            action=OperationLogger.ACTION_CREATE,
            module=OperationLogger.MODULE_REQUEST,
            target_id=request_id,
            target_type="print_request",
            details={
                "request_no": request_no,
                "material_type": material_type,
                "material_name": mat_info["name"],
                "quantity": quantity,
                "total_amount": total_amount,
                "template_path": template_path
            },
            ip_address=ip_address
        )

        return result

    @staticmethod
    def get_request(request_id: int) -> Optional[dict]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT pr.*, e.name as employee_name, e.employee_no,
                          d.name as department_name
                   FROM print_requests pr
                   LEFT JOIN employees e ON pr.employee_id = e.id
                   LEFT JOIN departments d ON pr.department_id = d.id
                   WHERE pr.id = ?""",
                (request_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_requests(
        employee_id: Optional[int] = None,
        department_id: Optional[int] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ):
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            sql = """
                SELECT pr.*, e.name as employee_name, e.employee_no,
                       d.name as department_name
                FROM print_requests pr
                LEFT JOIN employees e ON pr.employee_id = e.id
                LEFT JOIN departments d ON pr.department_id = d.id
                WHERE 1=1
            """
            params = []
            if employee_id:
                sql += " AND pr.employee_id = ?"
                params.append(employee_id)
            if department_id:
                sql += " AND pr.department_id = ?"
                params.append(department_id)
            if status:
                sql += " AND pr.status = ?"
                params.append(status)
            sql += " ORDER BY pr.created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            cursor.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]
