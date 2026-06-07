import csv
import json
import os
from typing import Optional, Dict, Any, List

from config import (
    OUTPUT_DIR, MATERIAL_CATALOG, COMPANY_INFO,
    COST_WARNING_INCREASE_RATE, COST_WARNING_CONSECUTIVE_MONTHS,
    current_month_str, now_str
)
from database import db_conn
from logger import OperationLogger, LogModule, LogAction
from inventory import NotificationService


class CostWarningService:

    SUGGESTIONS = [
        "审查物料使用频率，考虑减少非必要印制",
        "推动电子文档和数字化宣传，降低纸质依赖",
        "合并小批量订单，以量议价获取印刷商折扣",
        "优化设计稿，减少色彩层次和特殊工艺以降低成本",
        "盘点现有库存，优先使用存量避免重复印制",
        "评估内部打印能力，小额物料内部消化",
        "与核心供应商签订年度框架协议，争取优惠价",
        "建立部门物料领用登记制，杜绝浪费"
    ]

    @staticmethod
    def analyze(month: Optional[str] = None) -> List[dict]:
        if not month:
            month = current_month_str()

        months = [
            current_month_str(COST_WARNING_CONSECUTIVE_MONTHS),
            current_month_str(1),
            month
        ]
        warnings = []
        pending_notifs = []

        with db_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT dept_id, dept_name FROM departments")
            depts = [dict(r) for r in c.fetchall()]

            for dept in depts:
                costs = {}
                for m in months:
                    c.execute(
                        "SELECT used FROM department_budgets WHERE dept_id = ? AND month = ?",
                        (dept["dept_id"], m)
                    )
                    row = c.fetchone()
                    costs[m] = row["used"] if row else 0.0

                c0, c1, c2 = costs[months[0]], costs[months[1]], costs[months[2]]
                if c0 > 0 and c1 > 0:
                    r1 = (c1 - c0) / c0
                    r2 = (c2 - c1) / c1 if c1 > 0 else 0.0

                    if r1 > COST_WARNING_INCREASE_RATE and r2 > COST_WARNING_INCREASE_RATE:
                        avg_rate = (r1 + r2) / 2
                        sgs = CostWarningService.SUGGESTIONS[:3]
                        level = "critical" if avg_rate > 0.5 else "warning"

                        c.execute(
                            """INSERT OR IGNORE INTO cost_warnings
                               (dept_id, month, increase_rate, warn_level, suggestions)
                               VALUES (?, ?, ?, ?, ?)""",
                            (dept["dept_id"], month, round(avg_rate, 4),
                             level, json.dumps(sgs, ensure_ascii=False))
                        )

                        w = {
                            "dept_id": dept["dept_id"],
                            "dept_name": dept["dept_name"],
                            "month": month,
                            "avg_rate": round(avg_rate, 4),
                            "rate_1": round(r1, 4),
                            "rate_2": round(r2, 4),
                            f"cost_{months[0]}": c0,
                            f"cost_{months[1]}": c1,
                            f"cost_{months[2]}": c2,
                            "warn_level": level,
                            "suggestions": sgs
                        }
                        warnings.append(w)

                        c.execute(
                            "SELECT supervisor_id, director_id FROM departments WHERE dept_id = ?",
                            (dept["dept_id"],)
                        )
                        heads = c.fetchone()
                        if heads:
                            for aid in [heads["supervisor_id"], heads["director_id"]]:
                                if aid:
                                    pending_notifs.append({
                                        "recipient_id": aid,
                                        "notif_type": NotificationService.TYPE_BUDGET_WARNING,
                                        "title": f"【费用预警】{dept['dept_name']}连续两月费用增长超20%",
                                        "content": (
                                            f"部门: {dept['dept_name']}\n"
                                            f"{months[0]}: ¥{c0:,.2f}\n"
                                            f"{months[1]}: ¥{c1:,.2f} (环比+{r1*100:.1f}%)\n"
                                            f"{months[2]}: ¥{c2:,.2f} (环比+{r2*100:.1f}%)\n"
                                            f"平均增长率: +{avg_rate*100:.1f}%\n\n"
                                            "节约建议:\n" +
                                            "\n".join(f"  • {s}" for s in sgs)
                                        ),
                                        "related_id": dept["dept_id"],
                                        "related_type": "department"
                                    })

        for n in pending_notifs:
            NotificationService.send(**n)
        return warnings


class ReportService:

    @staticmethod
    def _collect(month: str) -> Dict[str, Any]:
        with db_conn() as conn:
            c = conn.cursor()

            c.execute(
                """SELECT SUM(o.total_amount) AS cost, COUNT(*) AS orders
                   FROM print_orders o
                   WHERE strftime('%Y-%m', o.created_at) = ?
                     AND o.status != 'cancelled'""",
                (month,)
            )
            t = dict(c.fetchone() or {})
            total_cost = t["cost"] or 0.0
            total_orders = t["orders"] or 0

            c.execute(
                """SELECT d.dept_id, d.dept_name,
                           SUM(o.total_amount) AS cost,
                           COUNT(*) AS orders,
                           SUM(o.quantity) AS qty
                   FROM print_orders o
                   LEFT JOIN print_requests r ON o.req_id = r.req_id
                   LEFT JOIN departments d ON r.dept_id = d.dept_id
                   WHERE strftime('%Y-%m', o.created_at) = ?
                     AND o.status != 'cancelled'
                   GROUP BY d.dept_id, d.dept_name
                   ORDER BY cost DESC""",
                (month,)
            )
            by_dept = []
            for r in c.fetchall():
                row = dict(r)
                row["avg_per_order"] = (
                    round(row["cost"] / row["orders"], 2) if row["orders"] else 0.0
                )
                by_dept.append(row)

            c.execute(
                """SELECT o.material_type,
                           SUM(o.total_amount) AS cost,
                           COUNT(*) AS orders,
                           SUM(o.quantity) AS qty
                   FROM print_orders o
                   WHERE strftime('%Y-%m', o.created_at) = ?
                     AND o.status != 'cancelled'
                   GROUP BY o.material_type
                   ORDER BY cost DESC""",
                (month,)
            )
            by_mat = []
            for r in c.fetchall():
                row = dict(r)
                info = MATERIAL_CATALOG.get(row["material_type"], {})
                row["name_cn"] = info.get("name_cn", row["material_type"])
                row["unit"] = info.get("unit", "")
                row["avg_unit_cost"] = (
                    round(row["cost"] / row["qty"], 2) if row["qty"] else 0.0
                )
                by_mat.append(row)

            c.execute(
                """SELECT e.emp_id, e.emp_name, d.dept_name,
                           SUM(o.total_amount) AS cost,
                           COUNT(*) AS orders
                   FROM print_orders o
                   LEFT JOIN print_requests r ON o.req_id = r.req_id
                   LEFT JOIN employees e ON r.emp_id = e.emp_id
                   LEFT JOIN departments d ON r.dept_id = d.dept_id
                   WHERE strftime('%Y-%m', o.created_at) = ?
                     AND o.status != 'cancelled'
                   GROUP BY e.emp_id, e.emp_name
                   ORDER BY cost DESC
                   LIMIT 10""",
                (month,)
            )
            top_emp = [dict(r) for r in c.fetchall()]

            prev_month = current_month_str(1)
            c.execute(
                """SELECT SUM(o.total_amount) AS cost
                   FROM print_orders o
                   WHERE strftime('%Y-%m', o.created_at) = ?
                     AND o.status != 'cancelled'""",
                (prev_month,)
            )
            pr = c.fetchone()
            prev_cost = float(pr["cost"]) if pr and pr["cost"] is not None else 0.0
            if prev_cost > 0:
                yoy = (total_cost - prev_cost) / prev_cost
            else:
                yoy = 0.0 if total_cost == 0 else 1.0

            c.execute(
                """SELECT strftime('%Y-%m', o.created_at) AS m,
                           SUM(o.total_amount) AS cost
                   FROM print_orders o
                   WHERE o.created_at >= date('now', '-12 months')
                     AND o.status != 'cancelled'
                   GROUP BY strftime('%Y-%m', o.created_at)
                   ORDER BY m"""
            )
            trend = [dict(r) for r in c.fetchall()]

            warnings = CostWarningService.analyze(month)

            return {
                "month": month,
                "company": COMPANY_INFO["name"],
                "generated_at": now_str(),
                "total_cost": round(total_cost, 2),
                "total_orders": total_orders,
                "prev_month": prev_month,
                "prev_cost": round(prev_cost, 2),
                "yoy_rate": round(yoy, 4),
                "by_department": by_dept,
                "by_material": by_mat,
                "top_employees": top_emp,
                "trend_12m": trend,
                "warnings": warnings,
                "warning_count": len(warnings)
            }

    @staticmethod
    def _bar_chart(data: list, value_key: str, label_key: str,
                   title: str, width: int = 36) -> str:
        if not data:
            return f"\n{title}\n  (无数据)\n"
        lines = [f"\n{title}", "-" * (width + 24)]
        mx = max((d.get(value_key) or 0) for d in data) or 1
        for d in data:
            v = d.get(value_key) or 0
            label = str(d.get(label_key, ""))[:14]
            bar = "█" * int((v / mx) * width)
            lines.append(f"  {label:<14} | {bar:<{width}} ¥{v:>12,.2f}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def generate(month: Optional[str] = None, export: bool = True) -> Dict[str, Any]:
        if not month:
            month = current_month_str()
        data = ReportService._collect(month)
        pdf_path = None
        excel_path = None
        if export:
            pdf_path = ReportService.export_pdf(data)
            excel_path = ReportService.export_excel(data)

        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                """INSERT OR REPLACE INTO monthly_reports
                   (month, total_cost, total_orders, report_data, pdf_path, excel_path, generated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (month, data["total_cost"], data["total_orders"],
                 json.dumps(data, ensure_ascii=False), pdf_path, excel_path, now_str())
            )

        OperationLogger.record(
            operator_id=None,
            operator_name="系统",
            action=LogAction.GENERATE,
            module=LogModule.REPORT,
            details={
                "month": month,
                "total_cost": data["total_cost"],
                "total_orders": data["total_orders"],
                "warning_count": data["warning_count"],
                "pdf_path": pdf_path,
                "excel_path": excel_path
            }
        )
        data["pdf_path"] = pdf_path
        data["excel_path"] = excel_path
        return data

    @staticmethod
    def export_pdf(data: Dict[str, Any]) -> str:
        month = data["month"]
        filepath = os.path.join(OUTPUT_DIR, f"印刷费用分析报告_{month}.txt")
        D = data
        L = "=" * 72
        S = "-" * 72
        yoy = D["yoy_rate"] * 100
        yoy_sign = "+" if yoy >= 0 else ""

        lines = [
            L,
            f"  {D['company']} - 印刷费用月度分析报告",
            L,
            f"  报告月份: {month}",
            f"  生成时间: {D['generated_at']}",
            "",
            S,
            "一、总体费用概览",
            S,
            f"  本月总费用:       ¥{D['total_cost']:>14,.2f}",
            f"  本月订单总数:     {D['total_orders']:>14}",
            f"  上月总费用:       ¥{D['prev_cost']:>14,.2f}",
            f"  环比变化率:       {yoy_sign}{yoy:>12.2f}%",
            "",
            ReportService._bar_chart(
                D["trend_12m"], "cost", "m",
                "二、近12个月费用趋势图",
                width=40
            ),
            S,
            "三、按部门统计",
            S,
        ]
        if D["by_department"]:
            lines.append(f"  {'部门':<14} {'费用(¥)':>14} {'订单数':>8} {'平均单费(¥)':>14}")
            lines.append("  " + "-" * 54)
            for d in D["by_department"]:
                lines.append(
                    f"  {(d['dept_name'] or '未知'):<14} "
                    f"{d['cost']:>14,.2f} {d['orders']:>8} {d['avg_per_order']:>14,.2f}"
                )
        else:
            lines.append("  (本月无数据)")
        lines.append("")
        lines.append(ReportService._bar_chart(
            D["by_department"], "cost", "dept_name",
            "部门费用分布", width=34
        ))
        lines.extend([
            S,
            "四、按物料类型统计",
            S,
        ])
        if D["by_material"]:
            lines.append(f"  {'物料类型':<12} {'费用(¥)':>14} {'数量':>10} {'平均单价(¥)':>14}")
            lines.append("  " + "-" * 54)
            for m in D["by_material"]:
                lines.append(
                    f"  {m['name_cn']:<12} "
                    f"{m['cost']:>14,.2f} {m['qty']:>6}{m['unit']:<4} {m['avg_unit_cost']:>14,.2f}"
                )
        else:
            lines.append("  (本月无数据)")
        lines.append("")
        lines.extend([
            S,
            f"五、费用 TOP10 员工",
            S,
        ])
        if D["top_employees"]:
            lines.append(f"  {'#':<3} {'姓名':<10} {'部门':<14} {'费用(¥)':>14} {'订单数':>8}")
            lines.append("  " + "-" * 54)
            for i, e in enumerate(D["top_employees"], 1):
                lines.append(
                    f"  {i:<3} {(e['emp_name'] or '未知'):<10} "
                    f"{(e['dept_name'] or '-'):<14} {e['cost']:>14,.2f} {e['orders']:>8}"
                )
        else:
            lines.append("  (本月无数据)")
        lines.append("")
        lines.extend([
            S,
            f"六、费用预警 ({D['warning_count']}个部门触发)",
            S,
        ])
        if D["warnings"]:
            for w in D["warnings"]:
                tag = "【严重】" if w["warn_level"] == "critical" else "【警告】"
                lines.append(f"  {tag} {w['dept_name']}")
                lines.append(f"    平均月增长率: +{w['avg_rate'] * 100:.1f}%")
                lines.append(f"    节约建议:")
                for s in w["suggestions"]:
                    lines.append(f"      • {s}")
                lines.append("")
        else:
            lines.append("  本月无部门触发费用预警，各部门费用控制良好。")
        lines.append("")
        lines.extend([
            L,
            "  本报告由印刷自动化管理系统自动生成",
            L,
            ""
        ])
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return filepath

    @staticmethod
    def export_excel(data: Dict[str, Any]) -> str:
        month = data["month"]
        out_dir = os.path.join(OUTPUT_DIR, f"印刷费用报告_{month}_CSV")
        os.makedirs(out_dir, exist_ok=True)

        def _w(fn, headers, rows):
            path = os.path.join(out_dir, fn)
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(headers)
                for row in rows:
                    w.writerow(row)

        _w("1_总体概览.csv",
           ["指标", "数值"],
           [
               ["报告月份", month],
               ["本月总费用(¥)", data["total_cost"]],
               ["本月订单总数", data["total_orders"]],
               ["上月总费用(¥)", data["prev_cost"]],
               ["环比变化率(%)", f"{data['yoy_rate'] * 100:.2f}"],
               ["预警部门数", data["warning_count"]],
           ])

        _w("2_部门统计.csv",
           ["部门", "总费用(¥)", "订单数", "数量", "平均单费(¥)"],
           [[d.get("dept_name", "未知"), d.get("cost", 0), d.get("orders", 0),
             d.get("qty", 0), d.get("avg_per_order", 0)]
            for d in data["by_department"]])

        _w("3_物料统计.csv",
           ["物料类型", "单位", "总费用(¥)", "订单数", "数量", "平均单价(¥)"],
           [[m.get("name_cn", ""), m.get("unit", ""), m.get("cost", 0),
             m.get("orders", 0), m.get("qty", 0), m.get("avg_unit_cost", 0)]
            for m in data["by_material"]])

        _w("4_员工TOP10.csv",
           ["排名", "姓名", "部门", "总费用(¥)", "订单数"],
           [[i + 1, e.get("emp_name", ""), e.get("dept_name", ""),
             e.get("cost", 0), e.get("orders", 0)]
            for i, e in enumerate(data["top_employees"])])

        _w("5_近12月趋势.csv",
           ["月份", "总费用(¥)"],
           [[t.get("m", ""), t.get("cost", 0)] for t in data["trend_12m"]])

        _w("6_费用预警.csv",
           ["部门", "级别", "平均增长率(%)", "节约建议"],
           [[w.get("dept_name", ""),
             "严重" if w.get("warn_level") == "critical" else "警告",
             f"{w.get('avg_rate', 0) * 100:.2f}",
             "；".join(w.get("suggestions", []))]
            for w in data["warnings"]])

        return out_dir
