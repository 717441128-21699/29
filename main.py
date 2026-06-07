import os
import sys
import json
from datetime import datetime

from database import initialize, get_db_connection
from config import MATERIAL_TYPES, DEPARTMENTS, OUTPUT_DIR
from print_request import PrintRequestManager
from approval import ApprovalManager, BudgetManager
from print_order import PrintOrderManager
from inventory import InventoryManager, NotificationManager
from batch_print import BatchPrintManager
from reporting import MonthlyReportGenerator, CostWarningSystem
from logger import OperationLogger


def print_header(title: str):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_separator():
    print("-" * 70)


def demo_single_request_workflow():
    """演示：单个员工印刷申请完整流程"""
    print_header("【演示1】单个印刷申请完整流程")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM employees WHERE is_active = 1 LIMIT 1")
        emp = dict(cursor.fetchone())

    print(f"\n测试员工: {emp['name']} (工号: {emp['employee_no']}, 部门ID: {emp['department_id']})")

    print("\n1. 提交印刷申请（名片 5盒 = 250元）...")
    result = PrintRequestManager.create_request(
        employee_id=emp["id"],
        material_type="business_card",
        quantity=5,
        custom_info={"备注": "最新版名片"},
        ip_address="192.168.1.100"
    )
    print(f"   结果: {result['message']}")
    if not result["success"]:
        print(f"   错误: {result.get('validation_errors')}")
        return

    request_id = result["request_id"]
    print(f"   申请ID: {request_id}, 申请单号: {result['request_no']}")
    print(f"   总金额: {result['total_amount']}元")
    print(f"   模板路径: {result['template_path']}")

    print("\n2. 预算检查与审批流程...")
    approval_result = ApprovalManager.process_request_budget_and_approval(request_id)
    print(f"   结果: {approval_result['message']}")
    print(f"   当前状态: {approval_result.get('status')}")
    print(f"   审批级别: {approval_result.get('approval_level', '无需审批')}")

    if approval_result.get("status") == "pending_approval":
        approver_id = approval_result.get("approver_id", emp["id"])
        print(f"\n3. 审批人审批 (ID: {approver_id})...")
        approve_result = ApprovalManager.approve_request(
            request_id=request_id,
            approver_id=approver_id,
            comments="同意印制",
            operator_name="主管审批"
        )
        print(f"   结果: {approve_result['message']}")

    print("\n4. 匹配最优印刷商并生成订单...")
    order_result = PrintOrderManager.create_order_for_request(request_id)
    print(f"   结果: {order_result.get('message')}")
    if order_result.get("success"):
        print(f"   订单ID: {order_result['order_id']}")
        print(f"   订单号: {order_result['order_no']}")
        print(f"   印刷商: {order_result['printer'].get('name')}")
        print(f"   预计交货: {order_result['expected_delivery']}")

        order_id = order_result["order_id"]
        print("\n5. 模拟印刷完成并交付...")
        PrintOrderManager.update_order_status(order_id, "in_production")
        print("   状态: 生产中")
        PrintOrderManager.update_order_status(order_id, "shipped")
        print("   状态: 已发货")
        PrintOrderManager.update_order_status(order_id, "delivered", quality_rating=5)
        print("   状态: 已送达")

        print("\n6. 入库并发送领取通知...")
        inv_result = InventoryManager.process_delivered_orders()
        print(f"   处理入库订单数: {inv_result['processed']}")

        print("\n7. 员工领取物料...")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM inventory WHERE employee_id = ? ORDER BY id DESC LIMIT 1",
                (emp["id"],)
            )
            inv = cursor.fetchone()
            if inv:
                pickup_result = InventoryManager.pickup(inv["id"], emp["id"], emp["name"])
                print(f"   领取结果: {pickup_result['message']}")

    print("\n✅ 单申请流程演示完成！")


def demo_multi_level_approval():
    """演示：多级审批流程（超5000元需总监审批）"""
    print_header("【演示2】多级审批流程")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM employees WHERE is_active = 1 LIMIT 1")
        emp = dict(cursor.fetchone())

    print(f"\n测试员工: {emp['name']}")
    print("\n1. 提交大额印刷申请（宣传册 400本 = 6000元，超5000需总监审批）...")
    result = PrintRequestManager.create_request(
        employee_id=emp["id"],
        material_type="brochure",
        quantity=400
    )
    if not result["success"]:
        print(f"   失败: {result['message']}")
        return
    request_id = result["request_id"]
    print(f"   申请ID: {request_id}, 金额: {result['total_amount']}元")

    print("\n2. 预算与审批级别判断...")
    ap_result = ApprovalManager.process_request_budget_and_approval(request_id)
    print(f"   审批级别: {ap_result.get('approval_level')}")
    print(f"   当前状态: {ap_result.get('status')}")
    print(f"   消息: {ap_result['message']}")

    if ap_result.get("status") == "pending_approval":
        approver_id = ap_result.get("approver_id", emp["id"])
        print(f"\n3. 总监审批通过 (审批人ID: {approver_id})...")
        appr = ApprovalManager.approve_request(request_id, approver_id, "同意，注意控制成本", "总监")
        print(f"   结果: {appr['message']}")

    print("\n✅ 多级审批流程演示完成！")


def demo_budget_check():
    """演示：预算检查"""
    print_header("【演示3】部门预算管理")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM departments LIMIT 3")
        depts = [dict(r) for r in cursor.fetchall()]

    for dept in depts:
        print(f"\n部门: {dept['name']}")
        summary = BudgetManager.get_department_budget_summary(dept["id"])
        print(f"  月度预算: {summary['allocated']:,.2f}元")
        print(f"  已使用:   {summary['used']:,.2f}元")
        print(f"  剩余:     {summary['remaining']:,.2f}元")
        print(f"  使用率:   {summary['usage_rate']}%")

        check_result = BudgetManager.check_budget_sufficient(dept["id"], 3000)
        print(f"  申请3000元: {'充足' if check_result['sufficient'] else '不足'}")

    print("\n✅ 预算管理演示完成！")


def demo_batch_print():
    """演示：批量印制"""
    print_header("【演示4】批量印制与订单合并")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT e.id, e.name, e.department_id, d.name as dept_name
               FROM employees e
               LEFT JOIN departments d ON e.department_id = d.id
               WHERE e.is_active = 1 LIMIT 5"""
        )
        emps = [dict(r) for r in cursor.fetchall()]

    if not emps:
        print("无员工数据")
        return

    target_dept_id = emps[0]["department_id"]
    target_dept_name = emps[0]["dept_name"]
    operator_id = emps[0]["id"]
    operator_name = emps[0]["name"]

    print(f"\n发起部门: {target_dept_name}")
    print("批量项目:")
    items = []
    for i, emp in enumerate(emps[:3]):
        if emp["department_id"] != target_dept_id:
            continue
        qty = [10, 50, 5][i % 3]
        mtype = ["business_card", "flyer", "badge"][i % 3]
        items.append({
            "employee_id": emp["id"],
            "material_type": mtype,
            "quantity": qty
        })
        mname = MATERIAL_TYPES[mtype]["name"]
        print(f"  - {emp['name']}: {mname} x{qty}")

    print("\n一键批量处理（创建申请→审批→匹配库存→生成订单）...")
    result = BatchPrintManager.process_entire_workflow(
        items=items,
        department_id=target_dept_id,
        operator_id=operator_id,
        operator_name=operator_name
    )
    print(f"最终阶段: {result['stage']}")
    if result["success"]:
        req_step = result["request_step"]
        print(f"  创建申请: {req_step['created_count']}/{req_step['total_submitted']} 成功")
        appr_step = result["approval_step"]
        print(f"  通过审批: {appr_step['approved_count']}/{appr_step['total']}")
        order_step = result["order_step"]
        print(f"  库存扣减: {order_step.get('fulfilled_from_inventory', 0)} 项")
        print(f"  新增印刷: {order_step.get('sent_to_print', 0)} 项")

    print("\n✅ 批量印制演示完成！")


def demo_monthly_report():
    """演示：月度费用分析报告"""
    print_header("【演示5】月度费用分析报告生成")

    print("\n正在生成月度报告...")
    report = MonthlyReportGenerator.generate_report()

    print(f"\n报告月份: {report['month']}")
    print(f"总费用: ¥{report['total_cost']:,.2f}")
    print(f"订单总数: {report['total_orders']}")
    print(f"上月费用: ¥{report['prev_total_cost']:,.2f}")
    change_pct = report['yoy_change_rate'] * 100
    print(f"环比变化: {'+' if change_pct >= 0 else ''}{change_pct:.1f}%")
    print(f"触发预警部门数: {report['warning_count']}")

    print("\n按部门统计 TOP5:")
    for i, dept in enumerate(report["by_department"][:5], 1):
        print(f"  {i}. {dept.get('department_name', '未知')}: "
              f"¥{dept.get('total_cost', 0):,.2f} ({dept.get('order_count', 0)}单)")

    print("\n按物料统计:")
    for mat in report["by_material"]:
        print(f"  - {mat.get('material_name')}: "
              f"¥{mat.get('total_cost', 0):,.2f} "
              f"({mat.get('total_quantity', 0)}{mat.get('unit', '')})")

    if report["cost_warnings"]:
        print("\n⚠️  费用预警:")
        for w in report["cost_warnings"]:
            level = "严重" if w["warning_level"] == "critical" else "警告"
            print(f"  [{level}] {w['department_name']}: "
                  f"月均增长{w['avg_increase_rate'] * 100:.1f}%")

    print(f"\n报告已导出:")
    print(f"  文本报告: {report.get('pdf_path')}")
    print(f"  CSV报表目录: {report.get('excel_path')}")

    print("\n✅ 月度报告演示完成！")


def demo_operation_logs():
    """演示：操作日志查询与导出"""
    print_header("【演示6】操作日志查询与导出")

    print("\n最近10条操作日志:")
    logs = OperationLogger.query_logs(limit=10)
    for log in logs:
        print(f"  [{log['created_at']}] {log.get('operator_name', '系统')} "
              f"- {log['module']}.{log['action']}"
              f"{f' (目标ID: {log.get('target_id')})' if log.get('target_id') else ''}")

    export_path = os.path.join(OUTPUT_DIR, f"操作日志导出_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    count = OperationLogger.export_logs_to_csv(export_path)
    print(f"\n已导出 {count} 条日志到: {export_path}")

    print("\n✅ 日志系统演示完成！")


def demo_inventory_and_reminder():
    """演示：库存管理与催领"""
    print_header("【演示7】库存管理与逾期催领")

    print("\n当前库存概览:")
    summary = InventoryManager.get_stock_summary()
    for s in summary:
        print(f"  {s['material_name']}: "
              f"在库{s['in_stock_qty']}{s['unit']}, "
              f"逾期{s['overdue_qty']}{s['unit']}, "
              f"已领{s['picked_qty']}{s['unit']}")

    print("\n检查逾期未领并发送催领通知...")
    remind_result = InventoryManager.check_overdue_and_remind()
    print(f"  逾期总数: {remind_result['total_overdue']}")
    print(f"  已发送催领: {remind_result['reminded_count']}")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM employees WHERE is_active = 1 LIMIT 1")
        emp = cursor.fetchone()
        if emp:
            unread = NotificationManager.get_unread(emp["id"])
            print(f"\n员工未读通知数: {len(unread)}")
            for n in unread[:3]:
                print(f"  - [{n['type']}] {n['title']}")

    print("\n✅ 库存与通知演示完成！")


def demo_printer_matching():
    """演示：印刷商智能匹配"""
    print_header("【演示8】印刷商智能匹配")

    material_list = list(MATERIAL_TYPES.keys())[:4]
    for mtype in material_list:
        mname = MATERIAL_TYPES[mtype]["name"]
        printer = BatchPrintManager if False else None
        from print_order import PrinterMatcher
        best = PrinterMatcher.find_best_printer(mtype)
        if best:
            rating = best.get("material_rating", best.get("overall_rating", 0))
            print(f"  {mname} -> 最佳印刷商: {best.get('name')} "
                  f"(评分: {rating}, 总订单: {best.get('total_orders', 0)})")

        print(f"    排名前3:")
        top3 = PrinterMatcher.list_printers_by_material(mtype, 3)
        for i, p in enumerate(top3, 1):
            print(f"      {i}. {p['name']} - 物料评分{p.get('material_rating', 0)}, "
                  f"综合{p['overall_rating']}, 交货{p.get('avg_delivery_days', 0)}天")

    print("\n✅ 印刷商匹配演示完成！")


def main():
    print_header(f"{ '智联科技 - 企业级印刷自动化管理系统' }")
    print(f"  数据库路径: {os.path.abspath('print_management.db')}")
    print(f"  输出目录:   {os.path.abspath(OUTPUT_DIR)}")
    print(f"  启动时间:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    print("\n正在初始化数据库...")
    initialize()
    print("数据库初始化完成！")

    demos = [
        ("单个印刷申请完整流程", demo_single_request_workflow),
        ("多级审批流程（总监审批）", demo_multi_level_approval),
        ("部门预算管理", demo_budget_check),
        ("批量印制与订单合并", demo_batch_print),
        ("月度费用分析报告", demo_monthly_report),
        ("操作日志查询与导出", demo_operation_logs),
        ("库存管理与逾期催领", demo_inventory_and_reminder),
        ("印刷商智能匹配", demo_printer_matching),
    ]

    while True:
        print()
        print("=" * 70)
        print("  功能菜单")
        print("=" * 70)
        for i, (name, _) in enumerate(demos, 1):
            print(f"  {i}. 演示: {name}")
        print(f"  {len(demos) + 1}. 运行全部演示")
        print(f"  0. 退出")
        print("-" * 70)

        try:
            choice = input("请选择功能 (0-9): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not choice:
            continue

        if choice == "0":
            print("\n感谢使用，再见！")
            break

        if choice == str(len(demos) + 1):
            for _, func in demos:
                try:
                    func()
                except Exception as e:
                    print(f"  ❌ 演示出错: {e}")
                    import traceback
                    traceback.print_exc()
            continue

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(demos):
                try:
                    demos[idx][1]()
                except Exception as e:
                    print(f"  ❌ 执行出错: {e}")
                    import traceback
                    traceback.print_exc()
            else:
                print("  无效选项")
        except ValueError:
            print("  请输入数字")


if __name__ == "__main__":
    main()
