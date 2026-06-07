import os
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "print_system.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
LOG_DIR = os.path.join(BASE_DIR, "logs")

for d in [OUTPUT_DIR, TEMPLATE_DIR, LOG_DIR]:
    os.makedirs(d, exist_ok=True)

APPROVAL_SUPERVISOR_THRESHOLD = 1000.0
APPROVAL_DIRECTOR_THRESHOLD = 5000.0
PICKUP_REMINDER_DAYS = 3
COST_WARNING_INCREASE_RATE = 0.20
COST_WARNING_CONSECUTIVE_MONTHS = 2

MATERIAL_CATALOG = {
    "business_card": {
        "name_cn": "名片",
        "unit_price": 50.0,
        "unit": "盒",
        "min_qty": 1,
        "max_qty": 100,
        "need_template": True,
        "description": "标准铜版纸双面彩色名片，每盒100张"
    },
    "flyer": {
        "name_cn": "宣传单页",
        "unit_price": 2.0,
        "unit": "张",
        "min_qty": 50,
        "max_qty": 10000,
        "need_template": True,
        "description": "A4/A5单页彩印，157g铜版纸"
    },
    "brochure": {
        "name_cn": "宣传册",
        "unit_price": 15.0,
        "unit": "本",
        "min_qty": 10,
        "max_qty": 5000,
        "need_template": True,
        "description": "骑马钉/胶装宣传册，封面250g+内页157g"
    },
    "poster": {
        "name_cn": "海报",
        "unit_price": 25.0,
        "unit": "张",
        "min_qty": 1,
        "max_qty": 1000,
        "need_template": True,
        "description": "相纸/灯箱片输出，支持多种尺寸"
    },
    "letterhead": {
        "name_cn": "信纸信封",
        "unit_price": 8.0,
        "unit": "套",
        "min_qty": 10,
        "max_qty": 2000,
        "need_template": True,
        "description": "A4信纸+C5信封套装，100g双胶纸"
    },
    "badge": {
        "name_cn": "工牌",
        "unit_price": 30.0,
        "unit": "个",
        "min_qty": 1,
        "max_qty": 500,
        "need_template": True,
        "description": "PVC卡式工牌，含卡套及挂绳"
    }
}

DEPARTMENT_LIST = [
    "市场部", "销售部", "人力资源部", "财务部", "技术研发部",
    "产品部", "运营部", "行政部", "客户服务部", "法务合规部"
]

DEPARTMENT_POSITION_MAP = {
    "市场部": ["市场总监", "市场经理", "品牌经理", "市场专员", "活动策划", "媒介专员"],
    "销售部": ["销售总监", "销售经理", "大客户经理", "销售主管", "销售代表", "商务专员"],
    "人力资源部": ["HR总监", "HR经理", "招聘主管", "培训经理", "薪酬专员", "HRBP"],
    "财务部": ["财务总监", "财务经理", "会计主管", "出纳", "财务分析师", "税务专员"],
    "技术研发部": ["技术总监", "架构师", "研发经理", "高级工程师", "工程师", "测试工程师", "运维工程师"],
    "产品部": ["产品总监", "高级产品经理", "产品经理", "产品专员", "UI设计师", "交互设计师"],
    "运营部": ["运营总监", "运营经理", "内容运营", "用户运营", "活动运营", "数据运营"],
    "行政部": ["行政总监", "行政经理", "行政主管", "行政专员", "前台接待", "后勤主管"],
    "客户服务部": ["客服总监", "客服经理", "客服主管", "客服专员", "售后工程师", "投诉处理专员"],
    "法务合规部": ["法务总监", "法务经理", "合规专员", "知识产权专员", "合同专员"]
}

COMPANY_INFO = {
    "name": "智联科技有限公司",
    "address": "北京市海淀区中关村科技园创新大厦A座18层",
    "phone": "+86 10 8888 8888",
    "website": "www.zhilian-tech.com",
    "email": "contact@zhilian-tech.com",
    "logo_text": "ZLT"
}

DEFAULT_MONTHLY_BUDGET = 10000.0


def current_month_str(delta_months: int = 0) -> str:
    now = datetime.now()
    target_month = now.month - delta_months
    target_year = now.year
    while target_month <= 0:
        target_month += 12
        target_year -= 1
    return f"{target_year:04d}-{target_month:02d}"


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
