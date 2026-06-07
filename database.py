import os
import random
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from config import (
    DB_PATH, DEPARTMENT_LIST, DEPARTMENT_POSITION_MAP,
    DEFAULT_MONTHLY_BUDGET, MATERIAL_CATALOG, current_month_str
)


@contextmanager
def db_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def create_tables():
    with db_conn() as conn:
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS departments (
                dept_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                dept_name     TEXT UNIQUE NOT NULL,
                monthly_budget REAL DEFAULT 10000,
                supervisor_id INTEGER,
                director_id   INTEGER,
                created_at    TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                emp_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_no        TEXT UNIQUE NOT NULL,
                emp_name      TEXT NOT NULL,
                dept_id       INTEGER NOT NULL,
                position      TEXT NOT NULL,
                phone         TEXT,
                email         TEXT,
                is_active     INTEGER DEFAULT 1,
                created_at    TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS printers (
                printer_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                printer_name  TEXT NOT NULL,
                contact_person TEXT,
                contact_phone TEXT,
                contact_email TEXT,
                address       TEXT,
                overall_rating REAL DEFAULT 5.0,
                total_orders  INTEGER DEFAULT 0,
                is_active     INTEGER DEFAULT 1,
                created_at    TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS printer_material_ratings (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                printer_id    INTEGER NOT NULL,
                material_type TEXT NOT NULL,
                rating        REAL DEFAULT 5.0,
                order_count   INTEGER DEFAULT 0,
                avg_delivery_days INTEGER DEFAULT 3,
                FOREIGN KEY (printer_id) REFERENCES printers(printer_id),
                UNIQUE(printer_id, material_type)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS print_requests (
                req_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                req_no         TEXT UNIQUE NOT NULL,
                emp_id         INTEGER NOT NULL,
                dept_id        INTEGER NOT NULL,
                material_type  TEXT NOT NULL,
                quantity       INTEGER NOT NULL,
                unit_price     REAL NOT NULL,
                total_amount   REAL NOT NULL,
                template_path  TEXT,
                custom_info    TEXT,
                status         TEXT DEFAULT 'pending_validate',
                rejection_reason TEXT,
                created_at     TEXT DEFAULT (datetime('now','localtime')),
                updated_at     TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (emp_id) REFERENCES employees(emp_id),
                FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                req_id        INTEGER NOT NULL,
                approval_level TEXT NOT NULL,
                approver_id   INTEGER,
                status        TEXT DEFAULT 'pending',
                comments      TEXT,
                approved_at   TEXT,
                created_at    TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (req_id) REFERENCES print_requests(req_id),
                FOREIGN KEY (approver_id) REFERENCES employees(emp_id)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS print_orders (
                order_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no       TEXT UNIQUE NOT NULL,
                req_id         INTEGER,
                batch_id       TEXT,
                printer_id     INTEGER NOT NULL,
                material_type  TEXT NOT NULL,
                quantity       INTEGER NOT NULL,
                unit_price     REAL NOT NULL,
                total_amount   REAL NOT NULL,
                status         TEXT DEFAULT 'placed',
                expected_delivery TEXT,
                actual_delivery   TEXT,
                quality_rating INTEGER,
                created_at     TEXT DEFAULT (datetime('now','localtime')),
                updated_at     TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (req_id) REFERENCES print_requests(req_id),
                FOREIGN KEY (printer_id) REFERENCES printers(printer_id)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                inv_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                material_type  TEXT NOT NULL,
                emp_id         INTEGER,
                dept_id        INTEGER,
                order_id       INTEGER,
                quantity       INTEGER NOT NULL,
                status         TEXT DEFAULT 'in_stock',
                pickup_by      INTEGER,
                picked_up_at   TEXT,
                ready_at       TEXT DEFAULT (datetime('now','localtime')),
                reminder_sent  INTEGER DEFAULT 0,
                FOREIGN KEY (emp_id) REFERENCES employees(emp_id),
                FOREIGN KEY (dept_id) REFERENCES departments(dept_id),
                FOREIGN KEY (order_id) REFERENCES print_orders(order_id)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS department_budgets (
                budget_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                dept_id       INTEGER NOT NULL,
                month         TEXT NOT NULL,
                allocated     REAL NOT NULL,
                used          REAL DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(dept_id, month),
                FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                notif_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_id  INTEGER NOT NULL,
                notif_type    TEXT NOT NULL,
                title         TEXT NOT NULL,
                content       TEXT,
                related_id    INTEGER,
                related_type  TEXT,
                is_read       INTEGER DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (recipient_id) REFERENCES employees(emp_id)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS operation_logs (
                log_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                operator_id   INTEGER,
                operator_name TEXT,
                action        TEXT NOT NULL,
                module        TEXT NOT NULL,
                target_id     INTEGER,
                target_type   TEXT,
                details       TEXT,
                ip_address    TEXT,
                created_at    TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (operator_id) REFERENCES employees(emp_id)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS monthly_reports (
                report_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                month         TEXT UNIQUE NOT NULL,
                total_cost    REAL DEFAULT 0,
                total_orders  INTEGER DEFAULT 0,
                report_data   TEXT,
                pdf_path      TEXT,
                excel_path    TEXT,
                generated_at  TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS cost_warnings (
                warn_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                dept_id       INTEGER NOT NULL,
                month         TEXT NOT NULL,
                increase_rate REAL NOT NULL,
                warn_level    TEXT DEFAULT 'warning',
                suggestions   TEXT,
                is_resolved   INTEGER DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
            )
        """)

        for idx_name, tbl, cols in [
            ("idx_req_emp", "print_requests", "emp_id"),
            ("idx_req_dept", "print_requests", "dept_id"),
            ("idx_req_status", "print_requests", "status"),
            ("idx_ord_printer", "print_orders", "printer_id"),
            ("idx_ord_status", "print_orders", "status"),
            ("idx_log_op", "operation_logs", "operator_id"),
            ("idx_log_time", "operation_logs", "created_at"),
            ("idx_log_module", "operation_logs", "module"),
            ("idx_inv_status", "inventory", "status"),
            ("idx_notif_recv", "notifications", "recipient_id"),
        ]:
            c.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {tbl}({cols})")


def seed_demo_data():
    with db_conn() as conn:
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM departments")
        if c.fetchone()[0] > 0:
            return

        print("[初始化] 正在创建演示数据...")

        dept_map = {}
        for dname in DEPARTMENT_LIST:
            c.execute(
                "INSERT INTO departments (dept_name, monthly_budget) VALUES (?, ?)",
                (dname, DEFAULT_MONTHLY_BUDGET)
            )
            dept_map[dname] = c.lastrowid

        first_names = ["张", "李", "王", "刘", "陈", "杨", "赵", "黄", "周", "吴",
                       "徐", "孙", "胡", "朱", "高", "林", "何", "郭", "马", "罗"]
        given_names = ["伟", "芳", "娜", "敏", "静", "丽", "强", "磊", "洋", "艳",
                       "勇", "军", "杰", "娟", "涛", "明", "超", "秀英", "霞", "平"]

        emp_counter = 1
        dept_heads = {}
        for dname, did in dept_map.items():
            positions = DEPARTMENT_POSITION_MAP.get(dname, ["职员"])
            head_emp_id = None
            for i, pos in enumerate(positions[:4]):
                name = random.choice(first_names) + random.choice(given_names)
                emp_no = f"E{emp_counter:04d}"
                phone = f"138{random.randint(10000000, 99999999)}"
                email = f"e{emp_counter:04d}@zhilian-tech.com"
                c.execute(
                    """INSERT INTO employees
                       (emp_no, emp_name, dept_id, position, phone, email)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (emp_no, name, did, pos, phone, email)
                )
                if i == 0:
                    head_emp_id = c.lastrowid
                emp_counter += 1
            dept_heads[did] = head_emp_id
            c.execute(
                "UPDATE departments SET supervisor_id = ?, director_id = ? WHERE dept_id = ?",
                (head_emp_id, head_emp_id, did)
            )

        printers_info = [
            ("华盛印刷有限公司", "王建国", "13900001111"),
            ("金印达图文制作中心", "李秀英", "13900002222"),
            ("优品彩印科技", "张志强", "13900003333"),
            ("恒信印务集团", "赵明辉", "13900004444"),
            ("宏图快印连锁", "刘美玲", "13900005555"),
        ]
        for pname, contact, phone in printers_info:
            rating = round(random.uniform(4.0, 5.0), 1)
            c.execute(
                """INSERT INTO printers
                   (printer_name, contact_person, contact_phone, contact_email,
                    address, overall_rating, total_orders)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (pname, contact, phone, f"service@{pname[:4]}.com",
                 f"北京市大兴区印刷产业园{random.randint(1, 50)}号",
                 rating, random.randint(80, 600))
            )
            pid = c.lastrowid
            for mtype in MATERIAL_CATALOG.keys():
                mr = round(random.uniform(3.7, 5.0), 1)
                c.execute(
                    """INSERT INTO printer_material_ratings
                       (printer_id, material_type, rating, order_count, avg_delivery_days)
                       VALUES (?, ?, ?, ?, ?)""",
                    (pid, mtype, mr, random.randint(15, 250), random.randint(2, 7))
                )

        dept_ids = list(dept_map.values())
        for offset in range(4):
            month = current_month_str(offset)
            for idx, did in enumerate(dept_ids):
                if idx == 0 and offset <= 2:
                    base = {2: 3000.0, 1: 4000.0, 0: 5600.0}
                    used = base.get(offset, round(random.uniform(2500, 9500), 2))
                elif idx == 1 and offset <= 2:
                    base = {2: 2500.0, 1: 3200.0, 0: 4500.0}
                    used = base.get(offset, round(random.uniform(2500, 9500), 2))
                else:
                    used = round(random.uniform(2500, 9500), 2)
                c.execute(
                    """INSERT OR IGNORE INTO department_budgets
                       (dept_id, month, allocated, used)
                       VALUES (?, ?, ?, ?)""",
                    (did, month, DEFAULT_MONTHLY_BUDGET, used)
                )

        print(f"[初始化] 演示数据创建完成：{len(dept_map)}个部门，"
              f"{emp_counter - 1}名员工，{len(printers_info)}家印刷商。")


def init_db():
    need_seed = not os.path.exists(DB_PATH)
    if need_seed:
        print(f"[系统] 数据库不存在，正在创建: {DB_PATH}")
    create_tables()
    seed_demo_data()
