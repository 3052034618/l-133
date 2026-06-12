import os
import sys
import click
import pandas as pd
from tabulate import tabulate
from datetime import datetime

from .storage import get_store
from .forecasting import Forecaster
from .suggestion import SuggestionEngine


def _read_csv_or_excel(filepath: str) -> pd.DataFrame:
    ext = os.path.splitext(filepath)[1].lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(filepath)
    elif ext == ".csv":
        encodings = ["utf-8-sig", "utf-8", "gbk"]
        for enc in encodings:
            try:
                return pd.read_csv(filepath, encoding=enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
        return pd.read_csv(filepath, encoding="utf-8", errors="replace")
    else:
        raise click.BadParameter(f"不支持的文件格式: {ext}，请使用 CSV 或 Excel")


@click.group()
@click.version_option(version="1.0.0", prog_name="retail-replenish")
def cli():
    """智慧零售补货建议命令行工具 - 批量分析门店缺货风险"""
    pass


@cli.command("import-sales")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option("--products", "products_file", type=click.Path(exists=True, dir_okay=False),
              help="商品主数据文件（可选，包含SKU、名称、品类、起订量等）")
@click.option("--stores", "stores_file", type=click.Path(exists=True, dir_okay=False),
              help="门店主数据文件（可选）")
@click.option("--append/--replace", default=False,
              help="追加模式或替换模式（默认替换）")
def import_sales(filepath, products_file, stores_file, append):
    """导入开票销量数据

    FILEPATH: 销量数据文件路径 (CSV或Excel)，需要包含列：store_id, sku, sale_date, quantity, amount
    """
    store = get_store()

    try:
        if products_file:
            df_products = _read_csv_or_excel(products_file)
            store.add_products_from_dataframe(df_products)
            click.echo(f"✓ 导入商品主数据: {len(df_products)} 条")

        if stores_file:
            df_stores = _read_csv_or_excel(stores_file)
            store.add_stores_from_dataframe(df_stores)
            click.echo(f"✓ 导入门店主数据: {len(df_stores)} 条")

        df_sales = _read_csv_or_excel(filepath)
        required_cols = {"store_id", "sku", "sale_date", "quantity"}
        missing = required_cols - set(df_sales.columns.str.lower())
        if missing:
            raise click.BadParameter(f"销量数据缺少必要列: {', '.join(missing)}")

        df_sales.columns = df_sales.columns.str.lower()
        if "amount" not in df_sales.columns:
            df_sales["amount"] = 0.0

        if not append:
            store.clear_sales()

        store.add_sales_from_dataframe(df_sales)
        store.save()

        click.echo(f"✓ 导入销量数据: {len(df_sales)} 条")
        click.echo(f"  数据时间范围: {df_sales['sale_date'].min()} ~ {df_sales['sale_date'].max()}")
        click.echo(f"  涉及门店数: {df_sales['store_id'].nunique()}")
        click.echo(f"  涉及商品数: {df_sales['sku'].nunique()}")

    except Exception as e:
        click.echo(f"✗ 导入失败: {e}", err=True)
        sys.exit(1)


@cli.command("import-stock")
@click.option("--stock", "stock_file", type=click.Path(exists=True, dir_okay=False),
              help="库存数据文件，包含列：store_id, sku, current_stock, in_transit, safety_stock")
@click.option("--promotions", "promo_file", type=click.Path(exists=True, dir_okay=False),
              help="促销日历文件，包含列：sku, promo_type, start_date, end_date, uplift_factor, store_id")
@click.option("--holiday-factor", type=float, default=None,
              help="节假日需求系数（如1.3表示节假日销量为平日130%）")
@click.option("--min-order-qty", type=int, default=None,
              help="全局默认最低起订量")
@click.option("--turnover-days", type=int, default=None,
              help="全局默认周转天数")
@click.option("--append/--replace", default=False,
              help="追加模式或替换模式（默认替换）")
def import_stock(stock_file, promo_file, holiday_factor, min_order_qty,
                 turnover_days, append):
    """导入库存、在途、安全库存、促销日历及参数配置"""
    store = get_store()

    try:
        if holiday_factor is not None:
            store.config["holiday_factor"] = holiday_factor
            click.echo(f"✓ 设置节假日系数: {holiday_factor}")

        if min_order_qty is not None:
            store.config["default_min_order_qty"] = min_order_qty
            click.echo(f"✓ 设置默认最低起订量: {min_order_qty}")

        if turnover_days is not None:
            store.config["default_turnover_days"] = turnover_days
            click.echo(f"✓ 设置默认周转天数: {turnover_days}")

        if not append:
            store.clear_stock_data()

        if stock_file:
            df_stock = _read_csv_or_excel(stock_file)
            required_cols = {"store_id", "sku", "current_stock"}
            missing = required_cols - set(df_stock.columns.str.lower())
            if missing:
                raise click.BadParameter(f"库存数据缺少必要列: {', '.join(missing)}")

            df_stock.columns = df_stock.columns.str.lower()
            for col in ["in_transit", "safety_stock"]:
                if col not in df_stock.columns:
                    df_stock[col] = 0

            store.add_stocks_from_dataframe(df_stock)
            click.echo(f"✓ 导入库存数据: {len(df_stock)} 条")
            click.echo(f"  涉及门店数: {df_stock['store_id'].nunique()}")
            click.echo(f"  涉及商品数: {df_stock['sku'].nunique()}")

        if promo_file:
            df_promo = _read_csv_or_excel(promo_file)
            required_cols = {"sku", "start_date", "end_date"}
            missing = required_cols - set(df_promo.columns.str.lower())
            if missing:
                raise click.BadParameter(f"促销数据缺少必要列: {', '.join(missing)}")

            df_promo.columns = df_promo.columns.str.lower()
            if "promo_type" not in df_promo.columns:
                df_promo["promo_type"] = "促销"
            if "uplift_factor" not in df_promo.columns:
                df_promo["uplift_factor"] = 1.5
            if "store_id" not in df_promo.columns:
                df_promo["store_id"] = ""

            store.add_promotions_from_dataframe(df_promo)
            click.echo(f"✓ 导入促销日历: {len(df_promo)} 条")

        store.save()

    except Exception as e:
        click.echo(f"✗ 导入失败: {e}", err=True)
        sys.exit(1)


@cli.command("forecast")
@click.option("--group-by", type=click.Choice(["sku", "store", "category"]),
              default="sku", help="聚合维度：商品(sku)、门店(store)、品类(category)")
@click.option("--start-date", type=str, default=None,
              help="预测起始日期 (YYYY-MM-DD)，默认为今天")
@click.option("--days", type=int, default=None,
              help="预测天数，默认7天")
@click.option("--limit", type=int, default=50,
              help="显示前N条结果")
@click.option("--summary", is_flag=True, default=False,
              help="仅显示7天汇总")
def forecast_cmd(group_by, start_date, days, limit, summary):
    """生成未来七天需求预测，按门店、品类或商品维度"""
    store = get_store()

    if not store.sales:
        click.echo("✗ 请先导入销量数据 (import-sales)", err=True)
        sys.exit(1)

    try:
        forecaster = Forecaster(store)
        forecasts = forecaster.generate_forecast(
            group_by=group_by, start_date=start_date, days=days
        )

        if not forecasts:
            click.echo("⚠ 未生成有效预测，请检查数据")
            return

        click.echo(f"✓ 已生成 {len(forecasts)} 条预测记录")

        if summary:
            summary_df = forecaster.get_7day_summary(forecasts)
            summary_df = summary_df.merge(
                pd.DataFrame([{
                    "sku": p.sku,
                    "商品名称": p.name,
                    "品类": p.category,
                } for p in store.products.values()]),
                on="sku", how="left"
            )
            summary_df = summary_df.merge(
                pd.DataFrame([{
                    "store_id": s.store_id,
                    "门店名称": s.name,
                } for s in store.stores.values()]),
                on="store_id", how="left"
            )
            summary_df = summary_df.rename(columns={
                "store_id": "门店ID", "sku": "商品SKU", "forecast_7d": "7天预测需求"
            })
            summary_df = summary_df[["门店ID", "门店名称", "商品SKU", "商品名称", "品类", "7天预测需求"]]
            summary_df = summary_df.sort_values("7天预测需求", ascending=False).head(limit)
            click.echo(tabulate(summary_df, headers="keys", tablefmt="grid", showindex=False, floatfmt=".1f"))
        else:
            df = forecaster.aggregate_forecasts(forecasts, group_by)
            if limit:
                df = df.head(limit)
            click.echo(tabulate(df, headers="keys", tablefmt="grid", showindex=False, floatfmt=".1f"))

    except Exception as e:
        click.echo(f"✗ 预测失败: {e}", err=True)
        sys.exit(1)


@cli.command("suggest")
@click.option("--risk", type=click.Choice(["高", "中", "低"]), default=None,
              help="按缺货风险等级筛选")
@click.option("--need-replenish", is_flag=True, default=False,
              help="仅显示需要补货的商品")
@click.option("--stagnant", is_flag=True, default=False,
              help="仅显示有滞销风险的商品")
@click.option("--store", "store_id", type=str, default=None,
              help="按门店ID筛选")
@click.option("--sku", type=str, default=None,
              help="按商品SKU筛选")
@click.option("--limit", type=int, default=50,
              help="显示前N条结果")
def suggest_cmd(risk, need_replenish, stagnant, store_id, sku, limit):
    """输出建议补货量、缺货风险等级、滞销提示和可调拨门店"""
    store = get_store()

    if not store.forecasts:
        click.echo("⚠ 未检测到预测数据，自动执行预测...")
        forecaster = Forecaster(store)
        forecaster.generate_forecast()

    if not store.stocks:
        click.echo("✗ 请先导入库存数据 (import-stock)", err=True)
        sys.exit(1)

    try:
        engine = SuggestionEngine(store)
        suggestions = engine.generate_suggestions()

        if not suggestions:
            click.echo("⚠ 未生成有效建议，请检查数据")
            return

        suggestions = engine.filter_suggestions(
            suggestions, risk_filter=risk,
            need_replenish_only=need_replenish,
            stagnant_only=stagnant
        )

        if store_id:
            suggestions = [s for s in suggestions if s.store_id == store_id]
        if sku:
            suggestions = [s for s in suggestions if s.sku == sku]

        click.echo(f"✓ 共生成 {len(suggestions)} 条补货建议")

        if not suggestions:
            return

        stats = {
            "高风险": sum(1 for s in suggestions if s.risk_level == "高"),
            "中风险": sum(1 for s in suggestions if s.risk_level == "中"),
            "低风险": sum(1 for s in suggestions if s.risk_level == "低"),
            "需补货": sum(1 for s in suggestions if s.suggested_qty > 0),
            "滞销预警": sum(1 for s in suggestions if s.stagnant_warning),
        }
        click.echo(f"  风险分布: 高={stats['高风险']} 中={stats['中风险']} 低={stats['低风险']}")
        click.echo(f"  需补货: {stats['需补货']} 条，滞销预警: {stats['滞销预警']} 条")

        df = engine.suggestions_to_dataframe(suggestions)
        if limit:
            df = df.head(limit)

        columns = ["门店名称", "商品名称", "品类", "当前库存", "在途数量", "安全库存",
                   "7天预测需求", "缺货风险等级", "建议补货量", "滞销提示", "可调拨门店", "建议原因"]
        available_cols = [c for c in columns if c in df.columns]
        df = df[available_cols]

        click.echo(tabulate(df, headers="keys", tablefmt="grid", showindex=False, floatfmt=".1f"))

    except Exception as e:
        click.echo(f"✗ 生成建议失败: {e}", err=True)
        sys.exit(1)


@cli.command("export")
@click.argument("output", type=click.Path(dir_okay=False))
@click.option("--format", "fmt", type=click.Choice(["xlsx", "csv"]), default=None,
              help="导出格式，默认根据文件扩展名自动判断")
@click.option("--type", "export_type", type=click.Choice(["purchase", "delivery", "all"]),
              default="all", help="导出类型：采购清单(purchase)、配送清单(delivery)、全部(all)")
@click.option("--risk", type=click.Choice(["高", "中", "低", "all"]), default="all",
              help="按风险等级筛选")
@click.option("--need-replenish", is_flag=True, default=True,
              help="仅导出需要补货的商品（默认开启）")
@click.option("--include-forecast", is_flag=True, default=True,
              help="包含预测数据")
def export_cmd(output, fmt, export_type, risk, need_replenish, include_forecast):
    """导出给采购或配送团队使用的清单

    OUTPUT: 输出文件路径
    """
    store = get_store()

    if not store.suggestions:
        click.echo("⚠ 未检测到建议数据，自动执行建议生成...")
        if not store.forecasts:
            forecaster = Forecaster(store)
            forecaster.generate_forecast()
        engine = SuggestionEngine(store)
        engine.generate_suggestions()

    if not store.suggestions:
        click.echo("✗ 无可导出的数据", err=True)
        sys.exit(1)

    try:
        output_dir = os.path.dirname(os.path.abspath(output))
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        if fmt is None:
            ext = os.path.splitext(output)[1].lower().lstrip(".")
            if ext in ("xlsx", "xls"):
                fmt = "xlsx"
            else:
                fmt = "csv"

        engine = SuggestionEngine(store)
        suggestions = store.suggestions

        risk_filter = None if risk == "all" else risk
        suggestions = engine.filter_suggestions(
            suggestions, risk_filter=risk_filter,
            need_replenish_only=need_replenish
        )

        df = engine.suggestions_to_dataframe(suggestions)

        if include_forecast and store.forecasts:
            forecaster = Forecaster(store)
            forecast_df = forecaster.get_7day_summary(store.forecasts)
            if not forecast_df.empty:
                click.echo(f"✓ 包含 {len(store.forecasts)} 条预测明细")

        if fmt == "xlsx":
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                if export_type in ("purchase", "all"):
                    purchase_cols = ["商品SKU", "商品名称", "品类", "建议补货量"]
                    purchase_df = df.groupby(["商品SKU", "商品名称", "品类"], as_index=False)["建议补货量"].sum()
                    purchase_df = purchase_df[purchase_df["建议补货量"] > 0]
                    purchase_df = purchase_df.rename(columns={"建议补货量": "建议采购量"})
                    purchase_df.to_excel(writer, sheet_name="采购清单", index=False)

                if export_type in ("delivery", "all"):
                    delivery_cols = ["门店ID", "门店名称", "商品SKU", "商品名称", "品类",
                                     "当前库存", "在途数量", "安全库存", "7天预测需求",
                                     "缺货风险等级", "建议补货量", "可调拨门店", "建议原因"]
                    delivery_df = df[[c for c in delivery_cols if c in df.columns]]
                    delivery_df = delivery_df.sort_values(["缺货风险等级", "门店名称"], ascending=[False, True])
                    delivery_df.to_excel(writer, sheet_name="配送清单", index=False)

                if include_forecast and store.forecasts:
                    forecast_data = [{
                        "门店ID": f.store_id,
                        "门店名称": store.stores.get(f.store_id).name if store.stores.get(f.store_id) else f.store_id,
                        "商品SKU": f.sku,
                        "商品名称": store.products.get(f.sku).name if store.products.get(f.sku) else f.sku,
                        "预测日期": f.forecast_date,
                        "预测数量": f.forecast_qty,
                    } for f in store.forecasts]
                    forecast_df = pd.DataFrame(forecast_data)
                    forecast_df.to_excel(writer, sheet_name="需求预测明细", index=False)

                summary_data = []
                for risk_lvl in ["高", "中", "低"]:
                    count = len(df[df["缺货风险等级"] == risk_lvl])
                    qty = int(df[df["缺货风险等级"] == risk_lvl]["建议补货量"].sum())
                    summary_data.append({"风险等级": risk_lvl, "SKU数量": count, "建议补货总量": qty})
                pd.DataFrame(summary_data).to_excel(writer, sheet_name="汇总概览", index=False)
        else:
            df.to_csv(output, index=False, encoding="utf-8-sig")

        click.echo(f"✓ 已导出到: {output}")
        click.echo(f"  共 {len(df)} 条记录")
        if not df.empty:
            click.echo(f"  建议补货总量: {int(df['建议补货量'].sum())}")

    except Exception as e:
        click.echo(f"✗ 导出失败: {e}", err=True)
        sys.exit(1)


@cli.command("status")
def status_cmd():
    """查看当前数据状态"""
    store = get_store()
    click.echo("=== 当前数据状态 ===")
    click.echo(f"商品主数据: {len(store.products)} 个SKU")
    click.echo(f"门店主数据: {len(store.stores)} 个门店")
    click.echo(f"销量数据: {len(store.sales)} 条记录")
    click.echo(f"库存数据: {len(store.stocks)} 条记录")
    click.echo(f"促销日历: {len(store.promotions)} 条记录")
    click.echo(f"预测数据: {len(store.forecasts)} 条记录")
    click.echo(f"补货建议: {len(store.suggestions)} 条记录")
    click.echo("")
    click.echo("=== 参数配置 ===")
    for k, v in store.config.items():
        click.echo(f"  {k}: {v}")


if __name__ == "__main__":
    cli()
