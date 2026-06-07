import csv
import json
import os
import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

from config import (
    OUTPUT_DIR, MATERIAL_TYPES, COST_INCREASE_WARNING_RATE,
    CONSECUTIVE_MONTHS_FOR_WARNING, COMPANY_NAME,
    get_current_month_str, get_previous_month_str
)
from database import get_db_connection
from logger import OperationLogger
from inventory import NotificationManager


class CostWarningSystem:
    SUGGESTIONS_POOL = [
        "建议审查物料使用频率，考虑减少非必要印制",
        "建议推动电子文档使用，减少纸质物料依赖",
        "建议合并小批量订单，享受印刷商批量折扣",
        "建议优化设计减少色彩和特殊工艺，降低单位成本",
        "建议盘点现有库存，优先使用存量物料",
        "建议评估是否有替代方案，如内部打印或数字化",
        "建议与供应商谈判，争取更优惠的长期合作价格",
        "建议建立部门物料领用审批机制，避免浪费"
    ]

    @staticmethod
    def analyze_monthly_cost_trends(month: Optional[str] = None) -> List[dict]:
        """分析各部门连续月度费用增长情况"""
        if not month:
            month = get_current_month_str()

        months_to_check = [
            get_previous_month_str(CONSECUTIVE_MONTHS_FOR_WARNING),
            get_previous_month_str(1),
            month
        ]

        warnings = []
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT id, name FROM departments")
            departments = [dict(r) for r in cursor.fetchall()]

            for dept in departments:
                dept_costs = {}
                for m in months_to_check:
                    cursor.execute(
                        "SELECT used_budget FROM department_budgets WHERE department_id = ? AND month = ?",
                        (dept["id"], m)
                    )
                    row = cursor.fetchone()
                    dept_costs[m] = row["used_budget"] if row else 0

                m0 = months_to_check[0]
                m1 = months_to_check[1]
                m2 = months_to_check[2]
                c0, c1, c2 = dept_costs[m0], dept_costs[m1], dept_costs[m2]

                if c0 > 0 and c1 > 0:
                    rate1 = (c1 - c0) / c0
                    rate2 = (c2 - c1) / c1 if c1 > 0 else 0

                    if (rate1 > COST_INCREASE_WARNING_RATE and
                            rate2 > COST_INCREASE_WARNING_RATE):
                        avg_rate = (rate1 + rate2) / 2
                        suggestions = CostWarningSystem.SUGGESTIONS_POOL[:3]

                        warning_level = "critical" if avg_rate > 0.5 else "warning"

                        cursor.execute(
                            """INSERT OR IGNORE INTO cost_warnings
                               (department_id, month, increase_rate, warning_level, suggestions)
                               VALUES (?, ?, ?, ?, ?)""",
                            (dept["id"], month, round(avg_rate, 4),
                             warning_level, json.dumps(suggestions, ensure_ascii=False))
                        )

                        warning_data = {
                            "department_id": dept["id"],
                            "department_name": dept["name"],
                            "month": month,
                            f"cost_{m0}": c0,
                            f"cost_{m1}": c1,
                            f"cost_{m2}": c2,
                            "increase_rate_1": round(rate1, 4),
                            "increase_rate_2": round(rate2, 4),
                            "avg_increase_rate": round(avg_rate, 4),
                            "warning_level": warning_level,
                            "suggestions": suggestions
                        }
                        warnings.append(warning_data)

                        cursor.execute(
                            "SELECT supervisor_id, director_id FROM departments WHERE id = ?",
                            (dept["id"],)
                        )
                        approvers = cursor.fetchone()
                        if approvers:
                            for approver_id in [approvers["supervisor_id"], approvers["director_id"]]:
                                if approver_id:
                                    NotificationManager.send(
                                        recipient_id=approver_id,
                                        notification_type=NotificationManager.TYPE_BUDGET_WARNING,
                                        title=f"【费用预警】{dept['name']}连续两月费用增长超20%",
                                        content=(
                                            f"部门: {dept['name']}\n"
                                            f"{m0}费用: {c0:.2f}元\n"
                                            f"{m1}费用: {c1:.2f}元 (增长{rate1*100:.1f}%)\n"
                                            f"{m2}费用: {c2:.2f}元 (增长{rate2*100:.1f}%)\n"
                                            f"平均增长率: {avg_rate*100:.1f}%\n\n"
                                            f"节约建议:\n" +
                                            "\n".join([f"  - {s}" for s in suggestions])
                                        ),
                                        related_id=dept["id"],
                                        related_type="department"
                                    )

        return warnings


class MonthlyReportGenerator:
    @staticmethod
    def _get_monthly_data(month: str) -> Dict[str, Any]:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(
                """SELECT SUM(po.total_amount) as total_cost, COUNT(*) as total_orders
                   FROM print_orders po
                   WHERE strftime('%Y-%m', po.created_at) = ?
                   AND po.status != 'cancelled'""",
                (month,)
            )
            totals = dict(cursor.fetchone() or {})

            cursor.execute(
                """SELECT d.id as department_id, d.name as department_name,
                           SUM(po.total_amount) as total_cost,
                           COUNT(*) as order_count,
                           SUM(po.quantity) as total_quantity
                   FROM print_orders po
                   LEFT JOIN print_requests pr ON po.request_id = pr.id
                   LEFT JOIN departments d ON pr.department_id = d.id
                   WHERE strftime('%Y-%m', po.created_at) = ?
                   AND po.status != 'cancelled'
                   GROUP BY d.id, d.name
                   ORDER BY total_cost DESC""",
                (month,)
            )
            by_department = [dict(r) for r in cursor.fetchall()]

            for dept in by_department:
                if dept["order_count"] and dept["order_count"] > 0:
                    dept["avg_cost_per_order"] = round(dept["total_cost"] / dept["order_count"], 2)
                else:
                    dept["avg_cost_per_order"] = 0

            cursor.execute(
                """SELECT po.material_type,
                           SUM(po.total_amount) as total_cost,
                           COUNT(*) as order_count,
                           SUM(po.quantity) as total_quantity
                   FROM print_orders po
                   WHERE strftime('%Y-%m', po.created_at) = ?
                   AND po.status != 'cancelled'
                   GROUP BY po.material_type
                   ORDER BY total_cost DESC""",
                (month,)
            )
            by_material = [dict(r) for r in cursor.fetchall()]

            for mat in by_material:
                mat_info = MATERIAL_TYPES.get(mat["material_type"], {})
                mat["material_name"] = mat_info.get("name", mat["material_type"])
                mat["unit"] = mat_info.get("unit", "")
                if mat["total_quantity"] and mat["total_quantity"] > 0:
                    mat["avg_unit_cost"] = round(mat["total_cost"] / mat["total_quantity"], 2)
                else:
                    mat["avg_unit_cost"] = 0

            cursor.execute(
                """SELECT e.id as employee_id, e.name as employee_name,
                           d.name as department_name,
                           SUM(po.total_amount) as total_cost,
                           COUNT(*) as order_count
                   FROM print_orders po
                   LEFT JOIN print_requests pr ON po.request_id = pr.id
                   LEFT JOIN employees e ON pr.employee_id = e.id
                   LEFT JOIN departments d ON pr.department_id = d.id
                   WHERE strftime('%Y-%m', po.created_at) = ?
                   AND po.status != 'cancelled'
                   GROUP BY e.id, e.name
                   ORDER BY total_cost DESC
                   LIMIT 10""",
                (month,)
            )
            top_employees = [dict(r) for r in cursor.fetchall()]

            prev_month = get_previous_month_str(1)
            cursor.execute(
                """SELECT SUM(po.total_amount) as prev_total_cost
                   FROM print_orders po
                   WHERE strftime('%Y-%m', po.created_at) = ?
                   AND po.status != 'cancelled'""",
                (prev_month,)
            )
            prev_row = cursor.fetchone()
            prev_total = prev_row["prev_total_cost"] if prev_row else 0
            current_total = totals.get("total_cost") or 0

            if prev_total > 0:
                yoy_change = (current_total - prev_total) / prev_total
            else:
                yoy_change = 0 if current_total == 0 else 1.0

            cursor.execute(
                """SELECT strftime('%Y-%m', po.created_at) as m,
                           SUM(po.total_amount) as cost
                   FROM print_orders po
                   WHERE po.created_at >= date('now', '-12 months')
                   AND po.status != 'cancelled'
                   GROUP BY strftime('%Y-%m', po.created_at)
                   ORDER BY m"""
            )
            last_12_months = [dict(r) for r in cursor.fetchall()]

            return {
                "month": month,
                "company_name": COMPANY_NAME,
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_cost": current_total,
                "total_orders": totals.get("total_orders") or 0,
                "prev_month": prev_month,
                "prev_total_cost": prev_total,
                "yoy_change_rate": round(yoy_change, 4),
                "by_department": by_department,
                "by_material": by_material,
                "top_employees": top_employees,
                "last_12_months": last_12_months
            }

    @staticmethod
    def _generate_text_chart(data: List[dict], value_key: str, label_key: str,
                             title: str, max_width: int = 40) -> str:
        if not data:
            return f"{title}\n  (无数据)\n"

        lines = [f"\n{title}", "-" * (max_width + 20)]
        max_val = max((d.get(value_key) or 0) for d in data) or 1

        for d in data:
            val = d.get(value_key) or 0
            label = str(d.get(label_key, ""))[:15]
            bar_len = int((val / max_val) * max_width)
            bar = "█" * bar_len
            lines.append(f"  {label:<15} | {bar:<{max_width}} {val:>10,.2f}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def generate_report(month: Optional[str] = None,
                        auto_export: bool = True) -> Dict[str, Any]:
        if not month:
            month = get_current_month_str()

        data = MonthlyReportGenerator._get_monthly_data(month)
        warnings = CostWarningSystem.analyze_monthly_cost_trends(month)

        report_data = {
            **data,
            "cost_warnings": warnings,
            "warning_count": len(warnings)
        }

        pdf_path = None
        excel_path = None

        if auto_export:
            pdf_path = MonthlyReportGenerator.export_to_pdf(report_data)
            excel_path = MonthlyReportGenerator.export_to_csv(report_data)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO monthly_reports
                   (month, total_cost, total_orders, report_data, pdf_path, excel_path, generated_at)
                   VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (month, data["total_cost"], data["total_orders"],
                 json.dumps(report_data, ensure_ascii=False), pdf_path, excel_path)
            )

        OperationLogger.log(
            operator_id=None,
            operator_name="系统",
            action=OperationLogger.ACTION_GENERATE,
            module=OperationLogger.MODULE_REPORT,
            details={
                "month": month,
                "total_cost": data["total_cost"],
                "total_orders": data["total_orders"],
                "warning_count": len(warnings),
                "pdf_path": pdf_path,
                "excel_path": excel_path
            }
        )

        report_data["pdf_path"] = pdf_path
        report_data["excel_path"] = excel_path
        return report_data

    @staticmethod
    def export_to_pdf(data: Dict[str, Any]) -> str:
        """使用文本格式生成PDF报告（不依赖第三方PDF库）"""
        month = data["month"]
        filename = f"印刷费用分析报告_{month}.txt"
        filepath = os.path.join(OUTPUT_DIR, filename)

        lines = []
        lines.append("=" * 70)
        lines.append(f"  {data['company_name']} - 印刷费用月度分析报告")
        lines.append("=" * 70)
        lines.append(f"  报告月份: {month}")
        lines.append(f"  生成时间: {data['generated_at']}")
        lines.append("")

        lines.append("-" * 70)
        lines.append("一、总体费用概览")
        lines.append("-" * 70)
        lines.append(f"  总费用:       ¥{data['total_cost']:,.2f}")
        lines.append(f"  订单总数:     {data['total_orders']}")
        lines.append(f"  上月费用:     ¥{data['prev_total_cost']:,.2f}")
        change_pct = data['yoy_change_rate'] * 100
        change_sign = "+" if change_pct >= 0 else ""
        lines.append(f"  环比变化:     {change_sign}{change_pct:.1f}%")
        lines.append("")

        lines.append(MonthlyReportGenerator._generate_text_chart(
            data["last_12_months"], "cost", "m",
            "近12个月费用趋势 (单位: 元)", max_width=40
        ))

        lines.append("-" * 70)
        lines.append("二、按部门统计")
        lines.append("-" * 70)
        if data["by_department"]:
            lines.append(f"  {'部门':<12} {'费用(元)':>12} {'订单数':>8} {'平均单费(元)':>12}")
            lines.append("  " + "-" * 46)
            for dept in data["by_department"]:
                lines.append(
                    f"  {dept['department_name'] or '未知':<12} "
                    f"{dept['total_cost']:>12,.2f} "
                    f"{dept['order_count']:>8} "
                    f"{dept['avg_cost_per_order']:>12,.2f}"
                )
        else:
            lines.append("  (无数据)")
        lines.append("")

        lines.append(MonthlyReportGenerator._generate_text_chart(
            data["by_department"], "total_cost", "department_name",
            "各部门费用分布图 (单位: 元)", max_width=35
        ))

        lines.append("-" * 70)
        lines.append("三、按物料类型统计")
        lines.append("-" * 70)
        if data["by_material"]:
            lines.append(f"  {'物料类型':<12} {'费用(元)':>12} {'数量':>8} {'平均单价(元)':>12}")
            lines.append("  " + "-" * 46)
            for mat in data["by_material"]:
                lines.append(
                    f"  {mat['material_name']:<12} "
                    f"{mat['total_cost']:>12,.2f} "
                    f"{mat['total_quantity']:>8} "
                    f"{mat['avg_unit_cost']:>12,.2f}"
                )
        else:
            lines.append("  (无数据)")
        lines.append("")

        lines.append("-" * 70)
        lines.append("四、费用 TOP10 员工")
        lines.append("-" * 70)
        if data["top_employees"]:
            lines.append(f"  {'排名':<4} {'姓名':<10} {'部门':<12} {'费用(元)':>12} {'订单数':>8}")
            lines.append("  " + "-" * 50)
            for i, emp in enumerate(data["top_employees"], 1):
                lines.append(
                    f"  {i:<4} {emp['employee_name'] or '未知':<10} "
                    f"{emp['department_name'] or '-':<12} "
                    f"{emp['total_cost']:>12,.2f} "
                    f"{emp['order_count']:>8}"
                )
        else:
            lines.append("  (无数据)")
        lines.append("")

        lines.append("-" * 70)
        lines.append(f"五、费用预警 ({data['warning_count']}个部门触发预警)")
        lines.append("-" * 70)
        if data["cost_warnings"]:
            for w in data["cost_warnings"]:
                level = "【严重】" if w["warning_level"] == "critical" else "【警告】"
                lines.append(f"  {level} {w['department_name']}")
                lines.append(f"    平均增长率: {w['avg_increase_rate'] * 100:.1f}%")
                lines.append(f"    节约建议:")
                for s in w["suggestions"]:
                    lines.append(f"      • {s}")
                lines.append("")
        else:
            lines.append("  本月无部门触发费用预警，所有部门费用控制良好。")
        lines.append("")

        lines.append("=" * 70)
        lines.append("  报告结束 - 本报告由印刷管理系统自动生成")
        lines.append("=" * 70)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return filepath

    @staticmethod
    def export_to_csv(data: Dict[str, Any]) -> str:
        """导出Excel兼容的CSV格式报告（多个sheet逻辑通过多文件实现）"""
        month = data["month"]
        dir_path = os.path.join(OUTPUT_DIR, f"印刷费用报告_{month}_CSV")
        os.makedirs(dir_path, exist_ok=True)

        summary_path = os.path.join(dir_path, "1_总体概览.csv")
        with open(summary_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["指标", "数值"])
            writer.writerow(["报告月份", month])
            writer.writerow(["总费用(元)", data["total_cost"]])
            writer.writerow(["订单总数", data["total_orders"]])
            writer.writerow(["上月费用(元)", data["prev_total_cost"]])
            writer.writerow(["环比变化率", f"{data['yoy_change_rate'] * 100:.2f}%"])
            writer.writerow(["预警部门数", data["warning_count"]])

        dept_path = os.path.join(dir_path, "2_部门统计.csv")
        with open(dept_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["部门", "总费用(元)", "订单数", "数量", "平均单费(元)"])
            for dept in data["by_department"]:
                writer.writerow([
                    dept.get("department_name", "未知"),
                    dept.get("total_cost", 0),
                    dept.get("order_count", 0),
                    dept.get("total_quantity", 0),
                    dept.get("avg_cost_per_order", 0)
                ])

        mat_path = os.path.join(dir_path, "3_物料统计.csv")
        with open(mat_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["物料类型", "单位", "总费用(元)", "订单数", "数量", "平均单价(元)"])
            for mat in data["by_material"]:
                writer.writerow([
                    mat.get("material_name", mat.get("material_type", "")),
                    mat.get("unit", ""),
                    mat.get("total_cost", 0),
                    mat.get("order_count", 0),
                    mat.get("total_quantity", 0),
                    mat.get("avg_unit_cost", 0)
                ])

        emp_path = os.path.join(dir_path, "4_员工TOP10.csv")
        with open(emp_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["排名", "姓名", "部门", "总费用(元)", "订单数"])
            for i, emp in enumerate(data["top_employees"], 1):
                writer.writerow([
                    i,
                    emp.get("employee_name", "未知"),
                    emp.get("department_name", ""),
                    emp.get("total_cost", 0),
                    emp.get("order_count", 0)
                ])

        trend_path = os.path.join(dir_path, "5_近12月趋势.csv")
        with open(trend_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["月份", "总费用(元)"])
            for m in data["last_12_months"]:
                writer.writerow([m.get("m", ""), m.get("cost", 0)])

        warn_path = os.path.join(dir_path, "6_费用预警.csv")
        with open(warn_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["部门", "预警级别", "平均增长率", "节约建议"])
            for w in data["cost_warnings"]:
                writer.writerow([
                    w.get("department_name", ""),
                    "严重" if w.get("warning_level") == "critical" else "警告",
                    f"{w.get('avg_increase_rate', 0) * 100:.2f}%",
                    "；".join(w.get("suggestions", []))
                ])

        index_path = os.path.join(OUTPUT_DIR, f"印刷费用报告_{month}_CSV.zip")
        return dir_path
