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
    ("S001", "南京路旗舰店", "华东区", "旗舰店", "华东核心店群", "上海市黄浦区南京路100号"),
    ("S002", "陆家嘴店", "华东区", "标准店", "华东核心店群", "上海市浦东新区陆家嘴环路200号"),
    ("S003", "中关村店", "华北区", "旗舰店", "华北核心店群", "北京市海淀区中关村大街300号"),
    ("S004", "国贸店", "华北区", "标准店", "华北核心店群", "北京市朝阳区建国门外大街1号"),
    ("S005", "天河城店", "华南区", "旗舰店", "华南核心店群", "广州市天河区天河路208号"),
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
        writer.writerow(["store_id", "name", "region", "store_type", "group_name", "address"])
        for s in STORES:
            writer.writerow(s)
    print(f"生成: {path}")


def write_strategies():
    path = os.path.join(EXAMPLES_DIR, "strategies.csv")
    strategies = [
        ("STRAT001", "乳制品快周转策略", 0.98, 2, 5, 24, "ceiling", 2.0, "乳制品", "", "短保质期商品，高服务水平"),
        ("STRAT002", "烘焙新鲜策略", 0.95, 1, 3, 6, "floor", 1.5, "烘焙", "", "当日新鲜，控制周转"),
        ("STRAT003", "饮料促销策略", 0.90, 3, 14, 24, "ceiling", 1.8, "饮料", "", "大促期间高备货"),
        ("STRAT004", "日化标准策略", 0.95, 5, 60, 6, "round", 1.5, "日化", "", "长保质期，标准周转"),
        ("STRAT005", "零食标准策略", 0.95, 3, 30, 12, "ceiling", 1.5, "零食", "", "标准库存策略"),
        ("STRAT006", "蛋品标准策略", 0.95, 2, 7, 1, "ceiling", 1.5, "蛋品", "", "短保质期"),
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["strategy_id", "name", "service_level", "lead_time_days",
                        "target_turnover_days", "min_order_qty", "order_rounding",
                        "safety_stock_factor", "scope_category", "scope_sku", "description"])
        for s in strategies:
            writer.writerow(s)
    print(f"生成: {path}")


def write_bad_data_samples():
    bad_sales_path = os.path.join(EXAMPLES_DIR, "bad_sales_sample.csv")
    with open(bad_sales_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["store_id", "sku", "sale_date", "quantity", "amount"])
        writer.writerow(["S001", "SKU001", "2026-06-01", -3, 120.0])  # 负销量
        writer.writerow(["S001", "SKU001", "2026-06-01", 15, 600.0])  # 与上一行重复
        writer.writerow(["S001", "SKU001", "2026-06-01", 15, 600.0])  # 重复销量
        writer.writerow(["S999", "SKU001", "2026-06-02", 10, 400.0])  # 缺少门店主数据
        writer.writerow(["S001", "SKU999", "2026-06-02", 10, 400.0])  # 缺少商品主数据
        writer.writerow(["S001", "SKU002", "2026/06/03", 10, 400.0])  # 日期格式错误
        writer.writerow(["S001", "SKU003", "06-03-2026", 10, 400.0])  # 日期格式错误2
    print(f"生成: {bad_sales_path} (含销量问题数据)")

    bad_stock_path = os.path.join(EXAMPLES_DIR, "bad_stock_sample.csv")
    with open(bad_stock_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["store_id", "sku", "current_stock", "in_transit", "safety_stock"])
        writer.writerow(["S001", "SKU001", -5, 10, 50])  # 负库存
        writer.writerow(["S002", "SKU002", 50, -3, 30])  # 负在途
        writer.writerow(["S999", "SKU003", 20, 0, 10])  # 缺少门店主数据
        writer.writerow(["S001", "SKU999", 15, 5, 20])  # 缺少商品主数据
    print(f"生成: {bad_stock_path} (含库存问题数据)")


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


def write_purchase_orders():
    path = os.path.join(EXAMPLES_DIR, "purchase_orders.csv")
    today = datetime.now()

    orders = [
        ("PO20260101001", "SKU001", 500, "已下单", "2026-06-25", "2026-06-28", 0, 3.5, "供应商A", "S001", "常规补货"),
        ("PO20260101002", "SKU005", 1000, "部分到货", "2026-06-24", "2026-06-27", 600, 1.5, "供应商B", "S002", "促销备货"),
        ("PO20260101003", "SKU003", 200, "已到货", "2026-06-20", "2026-06-23", 200, 8.0, "供应商C", "S003", "新品上架"),
        ("PO20260101004", "SKU008", 800, "已下单", "2026-06-26", "2026-06-30", 0, 5.0, "供应商A", "S001", "补货"),
    ]

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["order_id", "sku", "qty", "status", "ordered_date",
                        "expected_arrival_date", "arrived_qty", "unit_cost",
                        "supplier", "store_id", "remark"])
        for o in orders:
            writer.writerow(o)
    print(f"生成: {path}")


def write_transfer_costs():
    path = os.path.join(EXAMPLES_DIR, "transfer_costs.csv")

    costs = [
        ("华东区", "华东区", 0.5, 50, 1),
        ("华北区", "华北区", 0.6, 60, 1),
        ("华南区", "华南区", 0.5, 55, 1),
        ("华东区", "华北区", 2.0, 200, 3),
        ("华东区", "华南区", 2.5, 250, 4),
        ("华北区", "华南区", 2.2, 220, 3),
        ("华北区", "华东区", 2.0, 200, 3),
        ("华南区", "华东区", 2.5, 250, 4),
        ("华南区", "华北区", 2.2, 220, 3),
    ]

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["from_region", "to_region", "cost_per_unit", "fixed_cost", "lead_time_days"])
        for c in costs:
            writer.writerow(c)
    print(f"生成: {path}")


if __name__ == "__main__":
    write_products()
    write_stores()
    write_strategies()
    write_sales()
    write_stock()
    write_promotions()
    write_purchase_orders()
    write_transfer_costs()
    write_bad_data_samples()
    print(f"\n示例数据已生成到: {EXAMPLES_DIR}")
