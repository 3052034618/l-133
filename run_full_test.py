import subprocess
import sys
import os

os.chdir(r"d:\trae-bz\TraeProjects\133")

def run(cmd):
    print(f"\n=== 执行: {cmd} ===")
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
        print(f"命令失败，退出码: {result.returncode}")
    return result.returncode == 0

if __name__ == "__main__":
    # 1. 清理旧数据
    if os.path.exists(".retail_data"):
        import shutil
        shutil.rmtree(".retail_data")
        print("已删除旧数据目录")

    # 2. 导入数据
    if not run("python -m smart_retail_replenishment.cli import-sales examples/sales.csv --products examples/products.csv --stores examples/stores.csv"):
        sys.exit(1)

    if not run("python -m smart_retail_replenishment.cli import-stock --stock examples/stock.csv --promotions examples/promotions.csv"):
        sys.exit(1)

    if not run("python -m smart_retail_replenishment.cli strategy import examples/strategies.csv"):
        sys.exit(1)

    # 3. 测试策略列表
    if not run("python -m smart_retail_replenishment.cli strategy list"):
        sys.exit(1)

    # 4. 测试预测 - 三个维度
    if not run("python -m smart_retail_replenishment.cli forecast --group-by sku --days 7 --summary --show-increment --limit 10"):
        sys.exit(1)

    if not run("python -m smart_retail_replenishment.cli forecast --group-by store --days 7 --summary"):
        sys.exit(1)

    if not run("python -m smart_retail_replenishment.cli forecast --group-by category --days 7 --summary"):
        sys.exit(1)

    # 5. 测试建议 - 自动策略匹配
    if not run("python -m smart_retail_replenishment.cli suggest --category 乳制品 --limit 10"):
        sys.exit(1)

    if not run("python -m smart_retail_replenishment.cli suggest --strategy STRAT003 --limit 10"):
        sys.exit(1)

    # 6. 测试调拨模拟
    if not run("python -m smart_retail_replenishment.cli transfer-simulate --group-by region --limit 15"):
        sys.exit(1)

    if not run("python -m smart_retail_replenishment.cli transfer-simulate --group-by store_type"):
        sys.exit(1)

    # 7. 测试导出 - 多文件CSV
    if not run('python -m smart_retail_replenishment.cli export --format csv --type all output/retail_plan'):
        sys.exit(1)

    # 8. 测试导出 - Excel
    if not run('python -m smart_retail_replenishment.cli export --format xlsx --type all output/retail_plan'):
        sys.exit(1)

    print("\n" + "="*50)
    print("✅ 所有测试通过！")
    print("="*50)
