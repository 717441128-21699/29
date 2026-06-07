import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "print_management.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMPLATE_DIR, exist_ok=True)

APPROVAL_THRESHOLD_SUPERVISOR = 1000
APPROVAL_THRESHOLD_DIRECTOR = 5000
PICKUP_REMINDER_DAYS = 3
COST_INCREASE_WARNING_RATE = 0.20
CONSECUTIVE_MONTHS_FOR_WARNING = 2

MATERIAL_TYPES = {
    "business_card": {"name": "名片", "unit_price": 50, "unit": "盒", "template_required": True},
    "flyer": {"name": "宣传单页", "unit_price": 2, "unit": "张", "template_required": True},
    "brochure": {"name": "宣传册", "unit_price": 15, "unit": "本", "template_required": True},
    "poster": {"name": "海报", "unit_price": 25, "unit": "张", "template_required": True},
    "letterhead": {"name": "信纸信封", "unit_price": 8, "unit": "套", "template_required": True},
    "badge": {"name": "工牌", "unit_price": 30, "unit": "个", "template_required": True},
}

DEPARTMENTS = [
    "市场部", "销售部", "人力资源部", "财务部", "技术部",
    "产品部", "运营部", "行政部", "客户服务部", "法务部"
]

VALID_POSITIONS = {
    "市场部": ["市场总监", "市场经理", "市场专员", "品牌经理", "活动策划"],
    "销售部": ["销售总监", "销售经理", "销售主管", "销售代表", "大客户专员"],
    "人力资源部": ["HR总监", "HR经理", "招聘主管", "培训专员", "薪酬专员"],
    "财务部": ["财务总监", "财务经理", "会计主管", "出纳", "财务分析师"],
    "技术部": ["技术总监", "架构师", "技术经理", "高级工程师", "工程师", "测试工程师"],
    "产品部": ["产品总监", "产品经理", "产品专员", "UI设计师", "UX研究员"],
    "运营部": ["运营总监", "运营经理", "内容运营", "用户运营", "数据运营"],
    "行政部": ["行政总监", "行政经理", "行政主管", "行政专员", "前台"],
    "客户服务部": ["客服总监", "客服经理", "客服主管", "客服专员", "售后工程师"],
    "法务部": ["法务总监", "法务经理", "法务专员", "知识产权顾问"],
}

DEFAULT_DEPARTMENT_BUDGET = 10000
COMPANY_NAME = "智联科技有限公司"
COMPANY_ADDRESS = "北京市海淀区中关村科技园创新大厦A座18层"
COMPANY_PHONE = "+86 10 8888 8888"
COMPANY_WEBSITE = "www.zhilian-tech.com"


def get_current_month_str():
    return datetime.now().strftime("%Y-%m")


def get_previous_month_str(months_ago=1):
    now = datetime.now()
    month = now.month - months_ago
    year = now.year
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}-{month:02d}"
