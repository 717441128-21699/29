import sys
import traceback

print("=" * 60)
print("企业级印刷自动化管理系统 - 模块自检")
print("=" * 60)

errors = []

def check(name, desc):
    global errors
    try:
        print(f"[检查] {name}... ", end="")
        desc()
        print("OK")
    except Exception as e:
        errors.append((name, str(e)))
        print(f"FAIL: {e}")
        traceback.print_exc()

check("1. 导入所有模块", lambda: __import__("config"))

def t2():
    import config
    assert hasattr(config, "MATERIAL_CATALOG")
    assert len(config.MATERIAL_CATALOG) == 6, f"物料类型数应为6，实际{len(config.MATERIAL_CATALOG)}"
    assert config.APPROVAL_SUPERVISOR_THRESHOLD == 1000
    assert config.APPROVAL_DIRECTOR_THRESHOLD == 5000
    assert config.PICKUP_REMINDER_DAYS == 3
    assert config.COST_WARNING_INCREASE_RATE == 0.20
check("2. 配置参数", t2)

def t3():
    from database import init_db, db_conn
    init_db()
    with db_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        required = ["departments","employees","printers","printer_material_ratings",
                    "print_requests","approvals","print_orders","inventory",
                    "department_budgets","notifications","operation_logs",
                    "monthly_reports","cost_warnings"]
        for t in required:
            assert t in tables, f"缺少表: {t}"
        cur.execute("SELECT COUNT(*) FROM departments")
        assert cur.fetchone()[0] > 0, "部门表为空"
        cur.execute("SELECT COUNT(*) FROM employees")
        assert cur.fetchone()[0] > 0, "员工表为空"
check("3. 数据库初始化", t3)

def t4():
    from print_request import EmployeeValidator, MaterialValidator, TemplateGenerator
    from database import db_conn
    with db_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT emp_id FROM employees LIMIT 1")
        emp_id = cur.fetchone()[0]
    ok, msg, emp = EmployeeValidator.validate(emp_id)
    assert ok, f"员工校验失败: {msg}"
    assert emp is not None
    ok, msg = MaterialValidator.validate("business_card", 10)
    assert ok, f"物料校验失败: {msg}"
    ok, msg = MaterialValidator.validate("xxx", 10)
    assert not ok, "无效物料类型应该返回False"
    up, total = MaterialValidator.calc_cost("business_card", 5)
    assert total == 250.0, f"5盒名片应为250元，实际{total}"
    path = TemplateGenerator.generate(emp, "business_card")
    import os
    assert os.path.exists(path), f"模板未生成: {path}"
check("4. 员工/物料校验 + 模板生成", t4)

def t5():
    from print_request import PrintRequestService
    from approval import ApprovalService, BudgetService
    from database import db_conn
    with db_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT emp_id, dept_id FROM employees LIMIT 1")
        r = cur.fetchone()
        emp_id, dept_id = r[0], r[1]
    result = PrintRequestService.submit(emp_id, "business_card", 5)
    assert result["success"], f"申请失败: {result['message']}"
    req_id = result["req_id"]
    ap = ApprovalService.process(req_id)
    assert ap["success"] or ap["status"] in ("approved", "pending_approval", "rejected")
    bs = BudgetService.summary(dept_id)
    assert "allocated" in bs and "used" in bs and "remaining" in bs
check("5. 申请提交 + 预算 + 审批", t5)

def t6():
    from print_order import PrinterMatcher
    top = PrinterMatcher.best_match("business_card")
    assert top is not None and "printer_id" in top
    ranked = PrinterMatcher.list_ranked("business_card", 3)
    assert len(ranked) > 0
check("6. 印刷商智能匹配", t6)

def t7():
    from print_request import PrintRequestService
    from approval import ApprovalService
    from print_order import PrintOrderService
    from database import db_conn
    with db_conn() as c:
        cur = c.cursor()
        cur.execute("SELECT emp_id FROM employees LIMIT 1")
        emp_id = cur.fetchone()[0]
    r = PrintRequestService.submit(emp_id, "flyer", 100)
    if r["success"]:
        ap = ApprovalService.process(r["req_id"])
        if ap.get("status") == "pending_approval":
            ApprovalService.approve(r["req_id"], ap["approver_id"])
        r2 = PrintOrderService.create_from_request(r["req_id"])
        assert r2["success"], f"订单创建失败: {r2.get('message')}"
        assert "order_no" in r2 and "printer" in r2
check("7. 订单创建", t7)

def t8():
    from inventory import InventoryService
    s = InventoryService.summary()
    assert isinstance(s, list)
    r = InventoryService.run_reminder_cron()
    assert "total_overdue" in r and "reminded" in r
check("8. 库存 + 催领", t8)

def t9():
    from reporting import ReportService
    report = ReportService.generate()
    assert "total_cost" in report
    assert "by_department" in report
    assert "by_material" in report
    assert "warnings" in report
    import os
    assert os.path.exists(report["pdf_path"]), "PDF未生成"
    assert os.path.exists(report["excel_path"]), "Excel目录未生成"
check("9. 月度报告 + PDF/Excel导出", t9)

def t10():
    from logger import OperationLogger
    OperationLogger.record(None, "测试员", "test_action", "test_module",
                           details={"key": "value"})
    logs = OperationLogger.query(limit=5)
    assert len(logs) > 0
    import os, tempfile
    out = os.path.join(tempfile.gettempdir(), "test_logs.csv")
    n = OperationLogger.export_csv(out)
    assert n > 0 and os.path.exists(out)
check("10. 操作日志 + 导出", t10)

def t11():
    from batch_print import BatchPrintService
    from database import db_conn
    with db_conn() as c:
        cur = c.cursor()
        cur.execute("""SELECT e.emp_id, e.dept_id FROM employees e
                       INNER JOIN departments d ON e.dept_id=d.dept_id
                       LIMIT 5""")
        rows = cur.fetchall()
    if rows:
        dept_id = rows[0][1]
        operator_id = rows[0][0]
        items = [{"emp_id": r[0], "material_type": "business_card", "quantity": 5}
                 for r in rows]
        r = BatchPrintService.create_requests(dept_id, operator_id, items)
        assert "created_count" in r
check("11. 批量印制", t11)

print()
print("=" * 60)
if errors:
    print(f"❌ 发现 {len(errors)} 个错误:")
    for n, e in errors:
        print(f"  - {n}: {e}")
    sys.exit(1)
else:
    print("✅ 所有检查通过！系统运行正常。")
