import os
import sys
from datetime import datetime, timedelta

from config import OUTPUT_DIR, MATERIAL_CATALOG, now_str
from database import init_db, db_conn
from print_request import PrintRequestService
from approval import ApprovalService, BudgetService
from print_order import PrintOrderService, PrinterMatcher
from inventory import InventoryService, NotificationService
from batch_print import BatchPrintService
from reporting import ReportService
from logger import OperationLogger


def hdr(title: str):
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def sep():
    print("-" * 72)


def _get_random_emp():
    with db_conn() as conn:
        c = conn.cursor()
        c.execute(
            """SELECT e.*, d.dept_name
               FROM employees e LEFT JOIN departments d ON e.dept_id = d.dept_id
               WHERE e.is_active = 1 ORDER BY RANDOM() LIMIT 1"""
        )
        row = c.fetchone()
        return dict(row) if row else None


def demo1_single_request():
    hdr("【演示 1】员工提交印刷申请 → 校验职务/部门 → 生成设计模板")
    emp = _get_random_emp()
    if not emp:
        print("无员工数据")
        return
    print(f"\n测试员工: {emp['emp_name']} (工号 {emp['emp_no']})")
    print(f"所属部门: {emp['dept_name']}  职务: {emp['position']}")

    print("\n▶ 提交名片印刷申请 (5盒 = ¥250)...")
    r = PrintRequestService.submit(
        emp_id=emp["emp_id"],
        material_type="business_card",
        quantity=5,
        custom_info={"备注": "新版VI", "双面覆膜": "是"},
        ip_address="10.0.0.55"
    )
    if not r["success"]:
        print(f"  ✗ 失败: {r['message']}")
        for e in r.get("errors", []):
            print(f"    → {e}")
        return
    print(f"  ✓ 申请提交成功")
    print(f"    申请ID:   {r['req_id']}")
    print(f"    申请单号: {r['req_no']}")
    print(f"    总金额:   ¥{r['total_amount']:,.2f}")
    print(f"    设计模板: {r['template_path']}")

    print("\n▶ 读取模板内容预览 (前10行)...")
    if r["template_path"] and os.path.exists(r["template_path"]):
        with open(r["template_path"], "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= 10:
                    break
                print(f"    {line.rstrip()}")
            print("    ...")

    req_id = r["req_id"]

    print(f"\n▶ 预算检查 + 审批判断 (¥{r['total_amount']})")
    ap = ApprovalService.process(req_id, emp["emp_id"], emp["emp_name"])
    print(f"  预算状态: {'充足' if ap.get('budget', {}).get('sufficient') else '不足'}")
    print(f"  审批级别: {ap.get('approval_level', '无需审批')}")
    print(f"  当前状态: {ap.get('status')}")
    print(f"  消息:     {ap['message']}")

    if ap.get("status") == "pending_approval":
        approver_id = ap.get("approver_id", emp["emp_id"])
        print(f"\n▶ 审批人(ID={approver_id}) 审批通过")
        res = ApprovalService.approve(req_id, approver_id, "同意印制", "审批人")
        print(f"  结果: {res['message']}")

    print(f"\n▶ 匹配评分最高的印刷商并生成订单...")
    orr = PrintOrderService.create_from_request(req_id)
    if orr["success"]:
        print(f"  ✓ {orr['message']}")
        print(f"    订单号:   {orr['order_no']}")
        print(f"    印刷商:   {orr['printer']['printer_name']}")
        print(f"    综合评分: {orr['printer'].get('overall_rating', '-')}")
        print(f"    预计交货: {orr['expected_delivery']}")

        order_id = orr["order_id"]
        for st in ["in_production", "shipped", "delivered"]:
            PrintOrderService.update_status(order_id, st)
            st_cn = {"in_production": "生产中", "shipped": "已发货",
                     "delivered": "已送达"}.get(st, st)
            print(f"\n▶ 订单状态流转: {st_cn}")

        print(f"\n▶ 印刷完成，自动入库并通知员工领取...")
        inv = InventoryService.process_delivered_orders()
        print(f"  处理入库订单数: {inv['processed_count']}")

        print(f"\n▶ 员工凭单领取物料...")
        with db_conn() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT inv_id FROM inventory WHERE emp_id = ? ORDER BY inv_id DESC LIMIT 1",
                (emp["emp_id"],)
            )
            row = c.fetchone()
            if row:
                pk = InventoryService.pickup(row["inv_id"], emp["emp_id"], emp["emp_name"])
                print(f"  {pk['message']}")

    print("\n✅ 单个申请完整流程演示结束")


def demo2_multi_approval():
    hdr("【演示 2】预算分级审批: <1000自动过 / ≥1000主管 / ≥5000总监")
    emp = _get_random_emp()
    if not emp:
        print("无员工数据")
        return
    dept_id = emp["dept_id"]
    print(f"测试部门: {emp['dept_name']}  审批阈值: <1000/≥1000主管/≥5000总监")

    cases = [
        ("500元 (5盒名片)", "business_card", 10),
        ("3000元 (200盒名片)", "business_card", 60),
        ("7500元 (500本宣传册)", "brochure", 500),
    ]
    for label, mtype, qty in cases:
        print(f"\n▶ 申请金额: {label}")
        r = PrintRequestService.submit(emp["emp_id"], mtype, qty)
        if not r["success"]:
            print(f"  ✗ {r['message']}")
            continue
        ap = ApprovalService.process(r["req_id"])
        level = ap.get("approval_level", "none")
        level_cn = {"none": "无需审批(自动通过)",
                    "supervisor": "需【主管】审批",
                    "director": "需【总监】审批"}.get(level, level)
        print(f"  审批判定: {level_cn}")
        print(f"  状态: {ap.get('status')} - {ap['message']}")

        if ap.get("status") == "pending_approval":
            aid = ap.get("approver_id", emp["emp_id"])
            res = ApprovalService.approve(r["req_id"], aid, "批量演示通过")
            print(f"  审批结果: {res['message']}")
    print("\n✅ 分级审批演示结束")


def demo3_budget():
    hdr("【演示 3】部门月度预算管理")
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT dept_id, dept_name FROM departments LIMIT 5")
        depts = [dict(r) for r in c.fetchall()]
    for d in depts:
        s = BudgetService.summary(d["dept_id"])
        bar = "█" * int(s["usage_rate"] / 5) if s["usage_rate"] <= 100 else "█" * 20
        print(f"  {d['dept_name']:<10} "
              f"已用¥{s['used']:>8,.2f} / ¥{s['allocated']:>8,.2f} "
              f"[{bar:<20}] {s['usage_rate']:>5.1f}%  "
              f"剩余¥{s['remaining']:>8,.2f}")

        check = BudgetService.check(d["dept_id"], 3000)
        tag = "✓ 预算充足" if check["sufficient"] else "✗ 预算不足"
        print(f"    申请¥3,000: {tag} (缺口¥{check['deficit']:,.2f})")
    print("\n✅ 预算演示结束")


def demo4_printer_match():
    hdr("【演示 4】按物料类型匹配历史评分最高的印刷商")
    for mtype, info in list(MATERIAL_CATALOG.items())[:6]:
        name = info["name_cn"]
        top = PrinterMatcher.list_ranked(mtype, 3)
        best = PrinterMatcher.best_match(mtype)
        print(f"\n▶ {name} (最佳匹配): {best['printer_name'] if best else '无'}")
        if best:
            print(f"    综合评分 {best.get('overall_rating', 0)}  "
                  f"物料评分 {best.get('mat_rating', 0)}  "
                  f"交货期 {best.get('avg_delivery_days', 0)}天")
        if top:
            print(f"    TOP3 候选:")
            for i, p in enumerate(top, 1):
                print(f"      {i}. {p['printer_name']:<20} "
                      f"物料分{p['mat_rating']:.1f} 综合{p['overall_rating']:.1f} "
                      f"交货{p['avg_delivery_days']}天")
    print("\n✅ 印刷商智能匹配演示结束")


def demo5_inventory_reminder():
    hdr("【演示 5】库存自动入库 + 3天未领催领")
    print("\n▶ 当前库存概览:")
    sm = InventoryService.summary()
    for s in sm:
        print(f"    {s['name_cn']:<8} 在库{s['in_stock']:>4}{s['unit']}  "
              f"逾期{s['overdue']:>4}{s['unit']}  已领{s['picked']:>4}{s['unit']}")
    print(f"\n▶ 运行逾期催领定时任务 (>3天未领自动催领)...")
    r = InventoryService.run_reminder_cron()
    print(f"    逾期物料: {r['total_overdue']} 项")
    print(f"    已发送催领通知: {r['reminded']} 条")

    emp = _get_random_emp()
    if emp:
        unread = NotificationService.unread(emp["emp_id"], 5)
        print(f"\n▶ {emp['emp_name']} 的最新通知 (共{len(unread)}条未读):")
        for n in unread:
            print(f"    [{n['notif_type']}] {n['title']}")
    print("\n✅ 库存与催领演示结束")


def demo6_batch_print():
    hdr("【演示 6】手动批量印制 → 校验库存 → 合并订单")
    with db_conn() as conn:
        c = conn.cursor()
        c.execute(
            """SELECT e.emp_id, e.emp_name, e.dept_id, d.dept_name
               FROM employees e LEFT JOIN departments d ON e.dept_id = d.dept_id
               WHERE e.is_active = 1 LIMIT 10"""
        )
        emps = [dict(r) for r in c.fetchall()]
    if not emps:
        print("无员工数据")
        return
    target_dept = emps[0]
    dept_id = target_dept["dept_id"]
    operator_id = target_dept["emp_id"]
    operator_name = target_dept["emp_name"]

    items = []
    print(f"\n发起部门: {target_dept['dept_name']}  操作人: {operator_name}")
    print("批量印制项目:")
    types_qtys = [("business_card", 10), ("flyer", 200), ("badge", 5),
                  ("letterhead", 50), ("poster", 10)]
    i = 0
    for e in emps:
        if e["dept_id"] != dept_id or i >= len(types_qtys):
            continue
        mt, q = types_qtys[i]
        items.append({"emp_id": e["emp_id"], "material_type": mt, "quantity": q})
        name = MATERIAL_CATALOG[mt]["name_cn"]
        print(f"  - {e['emp_name']:<8}: {name} x{q}")
        i += 1

    print(f"\n▶ 一键全流程: 创建申请 → 预算审批 → 库存校验 → 合并下单...")
    r = BatchPrintService.full_workflow(items, dept_id, operator_id,
                                        approver_id=operator_id,
                                        operator_name=operator_name)
    print(f"  执行阶段: {r['stage']}")
    if r["success"]:
        s1 = r["step_create"]
        s2 = r["step_approve"]
        s3 = r["step_order"]
        print(f"  申请创建: {s1['created_count']}/{s1['total_submitted']} 成功, "
              f"总金额¥{s1['total_amount']:,.2f}")
        print(f"  通过审批: {s2['approved_count']}/{s2['total']}")
        print(f"  从库存扣减: {s3['fulfilled_from_inventory']} 项")
        print(f"  合并下单: {s3['sent_to_print']} 个申请")
        if s3.get("order_result", {}).get("batch_id"):
            print(f"  批次号: {s3['order_result']['batch_id']}")
    else:
        print(f"  失败信息: {r.get('message')}")
    print("\n✅ 批量印制演示结束")


def demo7_monthly_report():
    hdr("【演示 7】月度费用分析报告 + 20%增长预警 + PDF/Excel导出")
    print("\n▶ 生成月度报告...")
    report = ReportService.generate()
    print(f"  报告月份:     {report['month']}")
    print(f"  总费用:       ¥{report['total_cost']:>12,.2f}")
    print(f"  总订单数:     {report['total_orders']:>12}")
    print(f"  上月费用:     ¥{report['prev_cost']:>12,.2f}")
    yoy = report["yoy_rate"] * 100
    sign = "+" if yoy >= 0 else ""
    print(f"  环比变化:     {sign}{yoy:>10.2f}%")
    print(f"  预警部门数:   {report['warning_count']:>12}")

    print("\n▶ 按部门统计 TOP5:")
    for i, d in enumerate(report["by_department"][:5], 1):
        print(f"    {i}. {d.get('dept_name', '未知'):<12} "
              f"¥{d.get('cost', 0):>10,.2f} ({d.get('orders', 0)}单, "
              f"均费¥{d.get('avg_per_order', 0):,.2f})")

    print("\n▶ 按物料统计:")
    for m in report["by_material"]:
        print(f"    - {m['name_cn']:<8} "
              f"¥{m.get('cost', 0):>10,.2f} ({m.get('qty', 0)}{m['unit']}, "
              f"均价¥{m.get('avg_unit_cost', 0):,.2f})")

    if report["warnings"]:
        print(f"\n⚠️  费用预警 ({report['warning_count']}个部门):")
        for w in report["warnings"]:
            tag = "严重" if w["warn_level"] == "critical" else "警告"
            print(f"    [{tag}] {w['dept_name']} 月均增长 +{w['avg_rate'] * 100:.1f}%")
            print(f"      建议: {'; '.join(w['suggestions'])}")
    else:
        print("\n  ✓ 本月无部门触发费用预警")

    print(f"\n▶ 报告文件已导出:")
    print(f"    文本报告(PDF格式): {report.get('pdf_path')}")
    print(f"    Excel报表 (6表):  {report.get('excel_path')}")
    print("\n✅ 月度报告演示结束")


def demo8_logger():
    hdr("【演示 8】操作日志组合查询 + 批量导出")
    print("\n▶ 最近 15 条操作日志:")
    logs = OperationLogger.query(limit=15)
    for l in logs:
        op = l.get("operator_name") or "系统"
        tgt = f" → {l.get('target_type') or ''}(ID={l.get('target_id')})" if l.get("target_id") else ""
        print(f"  [{l['created_at']}] {op:<8} "
              f"{l['module']}.{l['action']:<16}{tgt}")

    print("\n▶ 按条件组合查询 (部门=随机部门, 近24小时)...")
    emp = _get_random_emp()
    if emp:
        filtered = OperationLogger.query(
            dept_id=emp["dept_id"],
            start_time=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
            limit=10
        )
        print(f"  部门 {emp['dept_name']} 共 {len(filtered)} 条记录")

    out = os.path.join(OUTPUT_DIR, f"操作日志导出_{now_str()[:10]}.csv")
    print(f"\n▶ 全量日志批量导出到: {out}")
    n = OperationLogger.export_csv(out)
    print(f"  已导出 {n} 条记录")
    print("\n✅ 日志系统演示结束")


DEMOS = [
    ("单个印刷申请完整流程 (校验→模板→审批→下单→入库→领取)", demo1_single_request),
    ("预算分级审批 (<1000/≥1000主管/≥5000总监)", demo2_multi_approval),
    ("部门月度预算管理与使用率看板", demo3_budget),
    ("按物料匹配历史评分最高印刷商", demo4_printer_match),
    ("库存入库通知 + 3天逾期催领", demo5_inventory_reminder),
    ("手动批量印制 + 库存校验 + 订单合并", demo6_batch_print),
    ("月度费用报告 + 20%预警 + PDF/Excel导出", demo7_monthly_report),
    ("操作日志组合查询 + 批量导出", demo8_logger),
]


_AUTO_RUN = False


def _pause(msg: str = "\n按回车继续下一个演示..."):
    global _AUTO_RUN
    if _AUTO_RUN:
        print(msg + " [自动模式，继续]")
        return
    try:
        if sys.stdin.isatty():
            input(msg)
        else:
            print(msg + " [非交互模式，自动继续]")
    except (EOFError, KeyboardInterrupt):
        pass


def main():
    global _AUTO_RUN
    hdr(f"{ '智联科技 · 企业级印刷自动化管理系统' }")
    print(f"  数据库: {os.path.abspath('print_system.db')}")
    print(f"  输出目录: {os.path.abspath(OUTPUT_DIR)}")
    print(f"  启动时间: {now_str()}")

    print("\n[初始化] 正在检查并初始化数据库...")
    init_db()
    print("[初始化] 完成！\n")

    auto_run = None
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--all":
            auto_run = "all"
        elif arg.startswith("--demo="):
            try:
                auto_run = int(arg.split("=", 1)[1])
            except ValueError:
                pass
        elif arg.isdigit():
            auto_run = int(arg)

    if auto_run is not None:
        _AUTO_RUN = True

    if auto_run == "all":
        for i, (_, func) in enumerate(DEMOS, 1):
            try:
                func()
            except Exception as e:
                print(f"\n  ❌ 演示{i}执行出错: {e}")
                import traceback
                traceback.print_exc()
            _pause()
        print("\n🎉 全部演示完成！")
        return

    if isinstance(auto_run, int) and 1 <= auto_run <= len(DEMOS):
        try:
            DEMOS[auto_run - 1][1]()
        except Exception as e:
            print(f"\n  ❌ 执行出错: {e}")
            import traceback
            traceback.print_exc()
        return

    while True:
        print()
        print("=" * 72)
        print("  功能菜单")
        print("=" * 72)
        for i, (name, _) in enumerate(DEMOS, 1):
            print(f"  {i:>2}. 演示: {name}")
        print(f"  {len(DEMOS) + 1:>2}. 依次运行全部演示")
        print(f"   0. 退出")
        print("-" * 72)

        try:
            choice = input("请选择 (0-9): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not choice:
            continue
        if choice == "0":
            print("\n感谢使用，再见！")
            break
        if choice == str(len(DEMOS) + 1):
            for i, (_, func) in enumerate(DEMOS, 1):
                try:
                    func()
                except Exception as e:
                    print(f"\n  ❌ 演示{i}执行出错: {e}")
                    import traceback
                    traceback.print_exc()
                _pause()
            continue

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(DEMOS):
                try:
                    DEMOS[idx][1]()
                except Exception as e:
                    print(f"\n  ❌ 执行出错: {e}")
                    import traceback
                    traceback.print_exc()
            else:
                print("  无效选项")
        except ValueError:
            print("  请输入数字")


if __name__ == "__main__":
    main()
