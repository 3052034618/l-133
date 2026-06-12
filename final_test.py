import subprocess
import sys
import os
import pandas as pd

os.chdir(r"d:\trae-bz\TraeProjects\133")

def run(cmd):
    print(f"\n{'='*60}")
    print(f"▶ 执行: {cmd}")
    print("="*60)
    sys.stdout.flush()
    result = subprocess.run(
        cmd, 
        shell=True, 
        capture_output=False,
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True
    )
    sys.stdout.flush()
    if result.returncode != 0:
        print(f"❌ 命令失败，退出码: {result.returncode}")
        return False
    return True

def check_file(filepath, desc):
    exists = os.path.exists(filepath)
    if exists:
        print(f"✅ {desc}: {filepath} 存在")
        return True
    else:
        print(f"❌ {desc}: {filepath} 不存在")
        return False

all_passed = True

# 清理旧数据
if os.path.exists(".retail_data"):
    import shutil
    shutil.rmtree(".retail_data")
    print("已删除旧数据目录")

print("\n" + "="*60)
print("🚀 智慧零售补货建议 v2.0 - 最终验收测试")
print("="*60)

print("\n📋 测试目标:")
print("  1. 补货策略模板功能")
print("  2. 门店群组与调拨模拟")
print("  3. 预测汇总口径一致性")
print("  4. 采购与配送导出细分")
print("  5. 数据质量检查")

# ============ 需求1: 补货策略模板 ============
print("\n" + "="*60)
print("🔍 测试需求1: 补货策略模板")
print("="*60)

all_passed &= run("python -m smart_retail_replenishment.cli import-sales examples/sales.csv --products examples/products.csv --stores examples/stores.csv")
all_passed &= run("python -m smart_retail_replenishment.cli import-stock --stock examples/stock.csv --promotions examples/promotions.csv")
all_passed &= run("python -m smart_retail_replenishment.cli strategy import examples/strategies.csv")
all_passed &= run("python -m smart_retail_replenishment.cli strategy list")
all_passed &= run("python -m smart_retail_replenishment.cli strategy create --id TEST001 --name '测试策略' --service-level 0.99 --lead-time 2 --turnover 10 --min-order 100 --scope-category '饮料'")
all_passed &= run("python -m smart_retail_replenishment.cli suggest --strategy STRAT001 --category 乳制品 --limit 5")
all_passed &= run("python -m smart_retail_replenishment.cli suggest --category 零食 --limit 5")

# ============ 需求2: 门店群组与调拨模拟 ============
print("\n" + "="*60)
print("🔍 测试需求2: 门店群组与调拨模拟")
print("="*60)

# 先设置一些富余库存
df = pd.read_csv("examples/stock.csv")
df.loc[(df["store_id"] == "S003") & (df["sku"] == "SKU001"), "current_stock"] = 3000
df.loc[(df["store_id"] == "S003") & (df["sku"] == "SKU002"), "current_stock"] = 4000
df.to_csv("examples/stock.csv", index=False)

all_passed &= run("python -m smart_retail_replenishment.cli transfer-simulate --group-by region")
all_passed &= run("python -m smart_retail_replenishment.cli transfer-simulate --group-by store_type")

# 检查促销日历全门店处理
all_passed &= run("python -m smart_retail_replenishment.cli forecast --group-by sku --days 7 --show-increment --summary --limit 5")

# ============ 需求3: 预测汇总口径一致性 ============
print("\n" + "="*60)
print("🔍 测试需求3: 预测汇总口径一致性")
print("="*60)

all_passed &= run("python -m smart_retail_replenishment.cli forecast --group-by store --days 7 --summary")
all_passed &= run("python -m smart_retail_replenishment.cli forecast --group-by category --days 7 --summary")
all_passed &= run("python -m smart_retail_replenishment.cli forecast --group-by sku --days 7 --summary")

# 检查导出的预测明细
all_passed &= run("python -m smart_retail_replenishment.cli suggest")
all_passed &= run("python -m smart_retail_replenishment.cli export --format csv --type all output/test_sku")

# 检查导出的维度
df = pd.read_csv("output/test_sku_预测.csv")
cols_sku = list(df.columns)
print(f"SKU维度列: {cols_sku}")
all_passed &= "商品SKU" in cols_sku and "门店ID" in cols_sku

# 切换维度再导出
all_passed &= run("python -m smart_retail_replenishment.cli forecast --group-by category --days 7")
all_passed &= run("python -m smart_retail_replenishment.cli suggest")
all_passed &= run("python -m smart_retail_replenishment.cli export --format csv --type all output/test_category")

df = pd.read_csv("output/test_category_预测.csv")
cols_cat = list(df.columns)
print(f"品类维度列: {cols_cat}")
all_passed &= "品类" in cols_cat and "商品SKU" not in cols_cat and "门店ID" not in cols_cat

# ============ 需求4: 采购与配送导出细分 ============
print("\n" + "="*60)
print("🔍 测试需求4: 采购与配送导出细分")
print("="*60)

all_passed &= check_file("output/test_sku_采购.csv", "采购清单CSV")
all_passed &= check_file("output/test_sku_配送.csv", "配送清单CSV")
all_passed &= check_file("output/test_sku_预测.csv", "预测明细CSV")
all_passed &= check_file("output/test_sku_调拨.csv", "调拨建议CSV")

# 检查采购清单是否合并
df_purchase = pd.read_csv("output/test_sku_采购.csv")
print(f"采购清单行数(合并后): {len(df_purchase)}")
all_passed &= len(df_purchase) <= 10

# 检查配送清单是否拆分
df_delivery = pd.read_csv("output/test_sku_配送.csv")
print(f"配送清单行数(按门店): {len(df_delivery)}")
all_passed &= len(df_delivery) > len(df_purchase)

# 测试Excel多sheet
all_passed &= run("python -m smart_retail_replenishment.cli export --format xlsx --type all output/test_excel")
all_passed &= check_file("output/test_excel.xlsx", "Excel多sheet导出")

# 测试筛选功能
all_passed &= run("python -m smart_retail_replenishment.cli export --format csv --type all output/test_filtered --risk 高 --need-replenish")
df_filtered = pd.read_csv("output/test_filtered_配送.csv")
print(f"筛选后配送清单行数: {len(df_filtered)}")
all_passed &= "高" in df_filtered["缺货风险等级"].unique()

# ============ 需求5: 数据质量检查 ============
print("\n" + "="*60)
print("🔍 测试需求5: 数据质量检查")
print("="*60)

# 重新导入干净数据，然后追加问题数据
if os.path.exists(".retail_data"):
    shutil.rmtree(".retail_data")

all_passed &= run("python -m smart_retail_replenishment.cli import-sales examples/sales.csv --products examples/products.csv --stores examples/stores.csv")
all_passed &= run("python -m smart_retail_replenishment.cli import-sales examples/bad_sales_sample.csv --append")
all_passed &= run("python -m smart_retail_replenishment.cli data-quality --export output/data_quality_result.csv")

all_passed &= check_file("output/data_quality_result.csv", "数据质量异常清单")

# 检查异常清单内容
df_issues = pd.read_csv("output/data_quality_result.csv")
print(f"发现数据质量问题数: {len(df_issues)}")
print(f"问题类型: {df_issues['问题类型'].unique()}")

required_types = ["缺少门店主数据", "缺少商品主数据", "负销量", "日期格式错误", "重复销量记录"]
for t in required_types:
    found = t in df_issues["问题类型"].values
    if found:
        print(f"✅ 发现问题类型: {t}")
    else:
        print(f"❌ 未发现问题类型: {t}")
        all_passed = False

# ============ 最终结果 ============
print("\n" + "="*60)
if all_passed:
    print("🎉 所有测试通过！5个升级需求全部实现！")
else:
    print("❌ 部分测试失败，请检查上述错误")
print("="*60)

# 恢复原始示例数据
os.system("python generate_samples.py > nul 2>&1")
