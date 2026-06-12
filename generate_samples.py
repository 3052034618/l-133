import os
import random
from datetime import datetime, timedelta
import csv


EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "examples")
os.makedirs(EXAMPLES_DIR, exist_ok=True)


PRODUCTS = [
    ("SKU001", "经典牛奶250ml", "乳制品", "盒", 12, 7, 3.5),
    ("SKU002", "原味酸奶100g", "乳制品", "杯", 24, 5, 2.0),
    ("SKU003", "全麦面包500g", "烘焙", "袋", 6, 3, 8.0),
    ("SKU004", "鸡蛋30枚装", "蛋品", "盒", 1, 7, 25.0),
    ("SKU005", "矿泉水550ml", "饮料", "瓶", 24, 14, 1.5),
    ("SKU006", "可乐330ml罐", "饮料", "罐", 24, 14, 2.5),
    ("SKU007", "薯片原味", "零食", "袋", 12, 30, 6.0),
    ("SKU008", "巧克力棒", "零食", "条", 24, 60, 5.0),
    ("SKU009", "洗衣液2kg", "日化", "瓶", 6, 90, 35.0),
    ("SKU010", "纸巾抽纸3包", "日化", "提", 12, 60, 15.0),
]


STORES = [
    ("S001", "南京路旗舰店", "华东区", "上海市黄浦区南京路100号"),
    ("S002", "陆家嘴店", "华东区", "上海市浦东新区陆家嘴环路200号"),
    ("S003", "中关村店", "华北区", "北京市海淀区中关村大街300号"),
    ("S004", "国贸店", "华北区", "北京市朝阳区建国门外大街1号"),
    ("S005", "天河城店", "华南区", "广州市天河区天河路208号"),
]


def write_products():
    path = os.path.join(EXAMPLES_DIR, "products.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "name", "category", "unit", "min_order_qty", "turnover_days", "unit_cost"])
        for p in PRODUCTS:
            writer.writerow(p)
    print(f"生成: {path}")


def write_stores():
    path = os.path.join(EXAMPLES_DIR, "stores.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["store_id", "name", "region", "address"])
        for s in STORES:
            writer.writerow(s)
    print(f"生成: {path}")


def write_sales():
    path = os.path.join(EXAMPLES_DIR, "sales.csv")
    random.seed(42)
    today = datetime.now()
    start_date = today - timedelta(days=28)

    store_base = {
        "S001": 1.5,
        "S002": 1.2,
        "S003": 1.3,
        "S004": 1.0,
        "S005": 1.4,
    }

    product_base = {
        "SKU001": 80,
        "SKU002": 120,
        "SKU003": 50,
        "SKU004": 30,
        "SKU005": 150,
        "SKU006": 100,
        "SKU007": 60,
        "SKU008": 40,
        "SKU009": 15,
        "SKU010": 25,
    }

    product_cost = {p[0]: p[6] for p in PRODUCTS}

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["store_id", "sku", "sale_date", "quantity", "amount"])

        current = start_date
        while current <= today:
            date_str = current.strftime("%Y-%m-%d")
            is_weekend = current.weekday() >= 5
            holiday_mult = 1.3 if is_weekend else 1.0

            for store_id, store_mult in store_base.items():
                for sku, base_qty in product_base.items():
                    variation = random.uniform(0.7, 1.3)
                    qty = int(base_qty * store_mult * holiday_mult * variation)
                    qty = max(0, qty)
                    if qty > 0:
                        amount = round(qty * product_cost[sku] * 1.4, 2)
                        writer.writerow([store_id, sku, date_str, qty, amount])

            current += timedelta(days=1)

    print(f"生成: {path}")


def write_stock():
    path = os.path.join(EXAMPLES_DIR, "stock.csv")
    random.seed(99)

    stock_levels = {
        "S001": {"low": 5, "normal": 30, "high": 100},
        "S002": {"low": 3, "normal": 25, "high": 80},
        "S003": {"low": 4, "normal": 28, "high": 90},
        "S004": {"low": 2, "normal": 20, "high": 70},
        "S005": {"low": 6, "normal": 32, "high": 110},
    }

    safety_map = {
        "SKU001": 50, "SKU002": 80, "SKU003": 30, "SKU004": 20, "SKU005": 100,
        "SKU006": 70, "SKU007": 40, "SKU008": 25, "SKU009": 10, "SKU010": 18,
    }

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["store_id", "sku", "current_stock", "in_transit", "safety_stock"])

        for store_id, levels in stock_levels.items():
            for i, (sku, safety) in enumerate(safety_map.items()):
                scenario = i % 3
                if scenario == 0:
                    current = random.randint(levels["low"], levels["low"] + 10)
                    in_transit = 0
                elif scenario == 1:
                    current = random.randint(levels["normal"], levels["normal"] + 20)
                    in_transit = random.randint(0, safety // 2)
                else:
                    current = random.randint(levels["high"], levels["high"] + 50)
                    in_transit = random.randint(safety // 2, safety)

                writer.writerow([store_id, sku, current, in_transit, safety])

    print(f"生成: {path}")


def write_promotions():
    path = os.path.join(EXAMPLES_DIR, "promotions.csv")
    today = datetime.now()
    promo_start = (today + timedelta(days=2)).strftime("%Y-%m-%d")
    promo_end = (today + timedelta(days=6)).strftime("%Y-%m-%d")

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "promo_type", "start_date", "end_date", "uplift_factor", "store_id"])
        writer.writerow(["SKU005", "周末促销", promo_start, promo_end, 1.8, ""])
        writer.writerow(["SKU006", "满减活动", promo_start, promo_end, 1.6, "S001"])
        writer.writerow(["SKU007", "新品推广", promo_start, promo_end, 2.0, ""])

    print(f"生成: {path}")


if __name__ == "__main__":
    write_products()
    write_stores()
    write_sales()
    write_stock()
    write_promotions()
    print(f"\n示例数据已生成到: {EXAMPLES_DIR}")
