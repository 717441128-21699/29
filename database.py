import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
import random

from config import (
    DB_PATH, MATERIAL_TYPES, DEPARTMENTS, VALID_POSITIONS,
    DEFAULT_DEPARTMENT_BUDGET, COMPANY_NAME
)


@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database():
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS departments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                monthly_budget REAL DEFAULT 10000,
                supervisor_id INTEGER,
                director_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_no TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                department_id INTEGER,
                position TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (department_id) REFERENCES departments(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS printers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                contact_person TEXT,
                phone TEXT,
                email TEXT,
                address TEXT,
                overall_rating REAL DEFAULT 5.0,
                total_orders INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS printer_material_ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                printer_id INTEGER NOT NULL,
                material_type TEXT NOT NULL,
                rating REAL DEFAULT 5.0,
                order_count INTEGER DEFAULT 0,
                avg_delivery_days INTEGER DEFAULT 3,
                FOREIGN KEY (printer_id) REFERENCES printers(id),
                UNIQUE(printer_id, material_type)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS print_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_no TEXT UNIQUE NOT NULL,
                employee_id INTEGER NOT NULL,
                department_id INTEGER NOT NULL,
                material_type TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                total_amount REAL NOT NULL,
                template_path TEXT,
                custom_info TEXT,
                status TEXT DEFAULT 'pending_validation',
                rejection_reason TEXT,
                valid_employee_info INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (employee_id) REFERENCES employees(id),
                FOREIGN KEY (department_id) REFERENCES departments(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                approval_level TEXT NOT NULL,
                approver_id INTEGER,
                status TEXT DEFAULT 'pending',
                comments TEXT,
                approved_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (request_id) REFERENCES print_requests(id),
                FOREIGN KEY (approver_id) REFERENCES employees(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS print_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT UNIQUE NOT NULL,
                request_id INTEGER,
                batch_id TEXT,
                printer_id INTEGER NOT NULL,
                material_type TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                total_amount REAL NOT NULL,
                status TEXT DEFAULT 'placed',
                expected_delivery_date DATE,
                actual_delivery_date DATE,
                quality_rating INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (request_id) REFERENCES print_requests(id),
                FOREIGN KEY (printer_id) REFERENCES printers(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                material_type TEXT NOT NULL,
                employee_id INTEGER,
                department_id INTEGER,
                order_id INTEGER,
                quantity INTEGER NOT NULL,
                status TEXT DEFAULT 'in_stock',
                pickup_by INTEGER,
                picked_up_at TIMESTAMP,
                ready_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reminder_sent INTEGER DEFAULT 0,
                FOREIGN KEY (employee_id) REFERENCES employees(id),
                FOREIGN KEY (department_id) REFERENCES departments(id),
                FOREIGN KEY (order_id) REFERENCES print_orders(id),
                FOREIGN KEY (pickup_by) REFERENCES employees(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS department_budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                department_id INTEGER NOT NULL,
                month TEXT NOT NULL,
                allocated_budget REAL NOT NULL,
                used_budget REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(department_id, month),
                FOREIGN KEY (department_id) REFERENCES departments(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT,
                related_id INTEGER,
                related_type TEXT,
                is_read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (recipient_id) REFERENCES employees(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS operation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operator_id INTEGER,
                operator_name TEXT,
                action TEXT NOT NULL,
                module TEXT NOT NULL,
                target_id INTEGER,
                target_type TEXT,
                details TEXT,
                ip_address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (operator_id) REFERENCES employees(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monthly_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT UNIQUE NOT NULL,
                total_cost REAL DEFAULT 0,
                total_orders INTEGER DEFAULT 0,
                report_data TEXT,
                pdf_path TEXT,
                excel_path TEXT,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cost_warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                department_id INTEGER NOT NULL,
                month TEXT NOT NULL,
                increase_rate REAL NOT NULL,
                warning_level TEXT DEFAULT 'warning',
                suggestions TEXT,
                is_resolved INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (department_id) REFERENCES departments(id)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_requests_employee ON print_requests(employee_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_requests_department ON print_requests(department_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_requests_status ON print_requests(status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_printer ON print_orders(printer_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_status ON print_orders(status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_logs_operator ON operation_logs(operator_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_logs_action ON operation_logs(action)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_logs_created ON operation_logs(created_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_inventory_status ON inventory(status)
        """)


def seed_demo_data():
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM departments")
        if cursor.fetchone()[0] > 0:
            return

        print("正在初始化演示数据...")

        dept_ids = {}
        for dept_name in DEPARTMENTS:
            cursor.execute(
                "INSERT INTO departments (name, monthly_budget) VALUES (?, ?)",
                (dept_name, DEFAULT_DEPARTMENT_BUDGET)
            )
            dept_ids[dept_name] = cursor.lastrowid

        first_names = ["张", "李", "王", "刘", "陈", "杨", "赵", "黄", "周", "吴",
                       "徐", "孙", "胡", "朱", "高", "林", "何", "郭", "马", "罗"]
        last_names = ["伟", "芳", "娜", "敏", "静", "丽", "强", "磊", "洋", "艳",
                      "勇", "军", "杰", "娟", "涛", "明", "超", "秀英", "霞", "平"]

        employee_counter = 1
        for dept_name, dept_id in dept_ids.items():
            positions = VALID_POSITIONS.get(dept_name, ["职员"])
            for i, pos in enumerate(positions[:3]):
                name = random.choice(first_names) + random.choice(last_names)
                emp_no = f"EMP{employee_counter:04d}"
                phone = f"138{random.randint(10000000, 99999999)}"
                email = f"emp{employee_counter:04d}@zhilian-tech.com"
                cursor.execute(
                    """INSERT INTO employees (employee_no, name, department_id, position, phone, email)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (emp_no, name, dept_id, pos, phone, email)
                )
                employee_counter += 1

        for dept_name, dept_id in dept_ids.items():
            cursor.execute(
                "SELECT id FROM employees WHERE department_id = ? ORDER BY id LIMIT 1",
                (dept_id,)
            )
            row = cursor.fetchone()
            if row:
                cursor.execute(
                    "UPDATE departments SET supervisor_id = ?, director_id = ? WHERE id = ?",
                    (row[0], row[0], dept_id)
                )

        printer_names = [
            ("华盛印刷有限公司", "王经理", "13900001111"),
            ("金印达图文制作", "李主管", "13900002222"),
            ("优品彩印科技", "张总监", "13900003333"),
            ("恒信印务中心", "赵经理", "13900004444"),
            ("宏图印刷集团", "刘总", "13900005555"),
        ]

        for pname, contact, phone in printer_names:
            rating = round(random.uniform(4.0, 5.0), 1)
            cursor.execute(
                """INSERT INTO printers (name, contact_person, phone, email, address, overall_rating, total_orders)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (pname, contact, phone, f"contact@{pname[:4]}.com",
                 f"北京市印刷园区{random.randint(1, 100)}号", rating, random.randint(50, 500))
            )
            printer_id = cursor.lastrowid

            for mtype in MATERIAL_TYPES.keys():
                m_rating = round(random.uniform(3.8, 5.0), 1)
                cursor.execute(
                    """INSERT INTO printer_material_ratings
                       (printer_id, material_type, rating, order_count, avg_delivery_days)
                       VALUES (?, ?, ?, ?, ?)""",
                    (printer_id, mtype, m_rating, random.randint(10, 200), random.randint(2, 7))
                )

        now = datetime.now()
        for month_offset in range(3):
            target_month = (now.replace(day=1) - timedelta(days=month_offset * 30)).strftime("%Y-%m")
            for dept_name, dept_id in dept_ids.items():
                used = round(random.uniform(2000, 9000), 2)
                cursor.execute(
                    """INSERT OR IGNORE INTO department_budgets
                       (department_id, month, allocated_budget, used_budget)
                       VALUES (?, ?, ?, ?)""",
                    (dept_id, target_month, DEFAULT_DEPARTMENT_BUDGET, used)
                )

        print("演示数据初始化完成！")


def initialize():
    if not os.path.exists(DB_PATH):
        print(f"数据库文件不存在，正在创建: {DB_PATH}")
    init_database()
    seed_demo_data()
