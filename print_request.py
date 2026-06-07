import os
import json
import uuid
from typing import Optional, Tuple, Dict, Any

from config import (
    MATERIAL_CATALOG, DEPARTMENT_POSITION_MAP, TEMPLATE_DIR,
    COMPANY_INFO, now_str
)
from database import db_conn
from logger import OperationLogger, LogModule, LogAction


class EmployeeValidator:

    @staticmethod
    def validate(emp_id: int) -> Tuple[bool, str, Optional[dict]]:
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """SELECT e.*, d.dept_name
                   FROM employees e
                   LEFT JOIN departments d ON e.dept_id = d.dept_id
                   WHERE e.emp_id = ? AND e.is_active = 1""",
                (emp_id,)
            )
            row = c.fetchone()

            if not row:
                return False, "员工不存在或已离职", None

            emp = dict(row)
            dept_name = emp["dept_name"]
            position = emp["position"]

            if dept_name not in DEPARTMENT_POSITION_MAP:
                return False, f"部门 '{dept_name}' 不在有效部门列表中", emp

            valid_positions = DEPARTMENT_POSITION_MAP.get(dept_name, [])
            if position not in valid_positions:
                return (
                    False,
                    f"职务 '{position}' 与部门 '{dept_name}' 不匹配。"
                    f"该部门允许职务: {', '.join(valid_positions)}",
                    emp
                )

            return True, "员工信息校验通过", emp


class MaterialValidator:

    @staticmethod
    def validate(material_type: str, quantity: int) -> Tuple[bool, str]:
        if material_type not in MATERIAL_CATALOG:
            return False, f"无效物料类型: {material_type}，" \
                          f"支持: {', '.join(MATERIAL_CATALOG.keys())}"

        info = MATERIAL_CATALOG[material_type]
        if quantity < info["min_qty"]:
            return False, f"{info['name_cn']} 最小起印量为 {info['min_qty']}{info['unit']}"
        if quantity > info["max_qty"]:
            return False, f"{info['name_cn']} 单次最大印量为 {info['max_qty']}{info['unit']}"

        return True, "物料校验通过"

    @staticmethod
    def calc_cost(material_type: str, quantity: int) -> Tuple[float, float]:
        info = MATERIAL_CATALOG[material_type]
        unit_price = info["unit_price"]
        total = round(unit_price * quantity, 2)
        return unit_price, total


class TemplateGenerator:

    @staticmethod
    def generate(employee: dict, material_type: str,
                 custom_info: Optional[dict] = None) -> str:
        info = MATERIAL_CATALOG[material_type]
        mat_name = info["name_cn"]
        ts = now_str().replace(":", "").replace("-", "").replace(" ", "_")
        safe_name = employee.get("emp_name", "unknown").replace(" ", "")
        filename = f"{material_type}_{safe_name}_{ts}.txt"
        filepath = os.path.join(TEMPLATE_DIR, filename)
        content = TemplateGenerator._build(employee, material_type,
                                            mat_name, info, custom_info)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath

    @staticmethod
    def _build(emp: dict, mtype: str, mname: str,
               info: dict, custom: Optional[dict]) -> str:
        ci = COMPANY_INFO
        L = "=" * 64
        S = "-" * 64
        lines = [
            L,
            f"  {ci['name']} - {mname} 标准设计稿模板",
            L,
            f"  生成时间: {now_str()}",
            f"  物料类型: {mname} ({mtype})",
            f"  规格说明: {info['description']}",
            "",
            S,
            "【员工信息】",
            f"  姓名:     {emp.get('emp_name', '')}",
            f"  工号:     {emp.get('emp_no', '')}",
            f"  部门:     {emp.get('dept_name', '')}",
            f"  职务:     {emp.get('position', '')}",
            f"  电话:     {emp.get('phone', '')}",
            f"  邮箱:     {emp.get('email', '')}",
            "",
            S,
            "【公司信息】",
            f"  公司名称: {ci['name']}",
            f"  公司地址: {ci['address']}",
            f"  总机:     {ci['phone']}",
            f"  官网:     {ci['website']}",
            f"  邮箱:     {ci['email']}",
            "",
        ]
        lines.extend(TemplateGenerator._spec(mtype, emp))
        if custom:
            lines.extend([
                S,
                "【自定义要求】",
            ])
            for k, v in custom.items():
                lines.append(f"  {k}: {v}")
            lines.append("")
        lines.extend([
            S,
            "设计要求:",
            "  - 色彩模式: CMYK",
            "  - 分辨率: 300dpi",
            "  - 出血: 3mm",
            "  - 请使用本公司 VI 规范中的字体与色值",
            "",
            L,
            "  本模板由系统自动生成，设计部将据此输出正式设计稿",
            L,
            ""
        ])
        return "\n".join(lines)

    @staticmethod
    def _spec(mtype: str, emp: dict) -> list:
        ci = COMPANY_INFO
        name = emp.get("emp_name", "")
        pos = emp.get("position", "")
        dept = emp.get("dept_name", "")
        phone = emp.get("phone", "")
        email = emp.get("email", "")

        if mtype == "business_card":
            return [
                S,
                "【名片设计规范】",
                "  尺寸: 90mm × 54mm (含出血 94mm × 58mm)",
                "",
                "  [正面]",
                f"    左上角 LOGO: {ci['logo_text']} / {ci['name']}",
                f"    中部:  {name}",
                f"           {pos}",
                f"           {dept}",
                "",
                "  [背面]",
                f"    电话: {phone}",
                f"    邮箱: {email}",
                f"    地址: {ci['address']}",
                f"    网站: {ci['website']}",
                "",
            ]
        if mtype == "flyer":
            return [
                S,
                "【宣传单页设计规范】",
                "  尺寸: A4 (210mm × 285mm) 或 A5 (148mm × 210mm)",
                "  纸张: 157g 铜版纸，双面彩色印刷",
                "",
                "  [正面]",
                "    顶部: 公司 LOGO + 活动主标题",
                "    中部: 核心宣传图 + 卖点文案",
                "    底部: 联系方式 + 二维码",
                "",
                "  [背面]",
                "    详细产品/活动介绍",
                "    参数表格",
                "    底部统一: 公司地址、电话、官网",
                "",
            ]
        if mtype == "brochure":
            return [
                S,
                "【宣传册设计规范】",
                "  规格: 16P / 20P / 24P (骑马钉或胶装)",
                "  尺寸: 成品 210mm × 285mm",
                "  纸张: 封面 250g 铜版纸覆亚膜，内页 157g 铜版纸",
                "",
                "  P1 封面:  LOGO + 企业名称 + 主视觉",
                "  P2-P3:  企业介绍",
                "  P4-P5:  核心产品/服务",
                "  P6-P13: 详细案例 / 解决方案",
                "  P14-P15: 团队实力 / 合作伙伴",
                "  P16 封底: 联系我们 + 地址 + 地图",
                "",
            ]
        if mtype == "poster":
            return [
                S,
                "【海报设计规范】",
                "  常用尺寸:",
                "    - 写真海报: 60cm × 90cm",
                "    - 展架海报: 80cm × 180cm (X展架/易拉宝)",
                "    - 户外喷绘: 按实际场景定制",
                "",
                "  版式要求:",
                "    主标题 (醒目大字)",
                "    副标题/活动时间地点",
                "    主体图像 / 产品图",
                "    辅助说明文案",
                "    底部: 公司 LOGO + 联系方式",
                "",
            ]
        if mtype == "letterhead":
            return [
                S,
                "【信纸信封设计规范】",
                "  信纸: A4 (210mm × 297mm)，100g 双胶纸",
                "  信封: C5 (229mm × 162mm)，120g 双胶纸",
                "",
                "  信纸页眉:",
                f"    左侧: {ci['logo_text']} LOGO + {ci['name']}",
                f"    右侧: {dept}",
                "  信纸页脚:",
                f"    {ci['address']}",
                f"    TEL: {ci['phone']}  FAX: {ci['phone'].replace('8888', '8889')}",
                f"    {ci['website']}",
                "",
                "  信封正面:",
                "    左上角: 公司 LOGO (小)",
                "    右下角: 公司全称 + 地址 + 邮编",
                "    开窗位置 (若为开窗信封): 左中部",
                "",
            ]
        if mtype == "badge":
            return [
                S,
                "【工牌设计规范】",
                "  尺寸: 85.5mm × 54mm (标准信用卡尺寸)",
                "  材质: PVC 卡 + 配套卡套及挂绳",
                "",
                "  [正面]",
                f"    顶部: {ci['logo_text']}  {ci['name']}",
                "    中部: [员工照片 25mm × 32mm]",
                f"    姓名: {name}",
                f"    职务: {pos}",
                f"    部门: {dept}",
                f"    工号: {emp.get('emp_no', '')}",
                "",
                "  [背面]",
                "    公司愿景 / Slogan",
                "    使用须知:",
                "      1. 本卡仅限本人使用",
                "      2. 出入请出示本卡",
                "      3. 遗失请及时到行政部补办",
                f"    行政部联系: {ci['phone']}",
                "",
            ]
        return []


class PrintRequestService:

    @staticmethod
    def submit(
        emp_id: int,
        material_type: str,
        quantity: int,
        custom_info: Optional[dict] = None,
        ip_address: Optional[str] = None
    ) -> Dict[str, Any]:
        result = {
            "success": False,
            "req_id": None,
            "req_no": None,
            "status": None,
            "message": "",
            "total_amount": 0,
            "template_path": None,
            "errors": []
        }

        ok, msg, emp = EmployeeValidator.validate(emp_id)
        if not ok:
            result["message"] = msg
            result["errors"].append(msg)
            OperationLogger.record(
                operator_id=emp_id,
                operator_name=emp["emp_name"] if emp else None,
                action=LogAction.SUBMIT,
                module=LogModule.PRINT_REQUEST,
                details={"error": msg, "material_type": material_type,
                         "quantity": quantity},
                ip_address=ip_address
            )
            return result

        ok, msg = MaterialValidator.validate(material_type, quantity)
        if not ok:
            result["message"] = msg
            result["errors"].append(msg)
            OperationLogger.record(
                operator_id=emp_id,
                operator_name=emp["emp_name"],
                action=LogAction.SUBMIT,
                module=LogModule.PRINT_REQUEST,
                details={"error": msg, "material_type": material_type,
                         "quantity": quantity},
                ip_address=ip_address
            )
            return result

        unit_price, total = MaterialValidator.calc_cost(material_type, quantity)
        template_path = None
        if MATERIAL_CATALOG[material_type].get("need_template", True):
            template_path = TemplateGenerator.generate(emp, material_type, custom_info)

        req_no = f"REQ{now_str().replace(':', '').replace('-', '').replace(' ', '')}" \
                 f"{uuid.uuid4().hex[:5].upper()}"
        custom_str = json.dumps(custom_info, ensure_ascii=False) if custom_info else None

        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """INSERT INTO print_requests
                   (req_no, emp_id, dept_id, material_type, quantity,
                    unit_price, total_amount, template_path, custom_info, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_validate')""",
                (req_no, emp_id, emp["dept_id"], material_type, quantity,
                 unit_price, total, template_path, custom_str)
            )
            req_id = c.lastrowid

        result.update({
            "success": True,
            "req_id": req_id,
            "req_no": req_no,
            "status": "pending_validate",
            "message": "申请提交成功，已进入预算与审批流程",
            "total_amount": total,
            "template_path": template_path,
            "employee": emp
        })

        OperationLogger.record(
            operator_id=emp_id,
            operator_name=emp["emp_name"],
            action=LogAction.CREATE,
            module=LogModule.PRINT_REQUEST,
            target_id=req_id,
            target_type="print_request",
            details={
                "req_no": req_no,
                "material_type": material_type,
                "material_name": MATERIAL_CATALOG[material_type]["name_cn"],
                "quantity": quantity,
                "total_amount": total,
                "template_path": template_path
            },
            ip_address=ip_address
        )
        return result

    @staticmethod
    def get(req_id: int) -> Optional[dict]:
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """SELECT r.*, e.emp_name, e.emp_no, d.dept_name
                   FROM print_requests r
                   LEFT JOIN employees e ON r.emp_id = e.emp_id
                   LEFT JOIN departments d ON r.dept_id = d.dept_id
                   WHERE r.req_id = ?""",
                (req_id,)
            )
            row = c.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list(emp_id: Optional[int] = None, dept_id: Optional[int] = None,
             status: Optional[str] = None, limit: int = 100, offset: int = 0) -> list:
        with db_conn() as conn:
            c = conn.cursor()
            sql = """
                SELECT r.*, e.emp_name, e.emp_no, d.dept_name
                FROM print_requests r
                LEFT JOIN employees e ON r.emp_id = e.emp_id
                LEFT JOIN departments d ON r.dept_id = d.dept_id
                WHERE 1=1
            """
            params = []
            if emp_id:
                sql += " AND r.emp_id = ?"
                params.append(emp_id)
            if dept_id:
                sql += " AND r.dept_id = ?"
                params.append(dept_id)
            if status:
                sql += " AND r.status = ?"
                params.append(status)
            sql += " ORDER BY r.created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            c.execute(sql, params)
            return [dict(r) for r in c.fetchall()]
