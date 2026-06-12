import os
import sys
import click
import pandas as pd
from tabulate import tabulate
from datetime import datetime
from typing import List, Optional

from .storage import get_store, ReplenishmentStrategy, DataQualityIssue, PurchaseOrder
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
@click.version_option(version="2.1.0", prog_name="retail-replenish")
def cli():
    """智慧零售补货建议命令行工具 v2.1 - 批量分析门店缺货风险"""
    pass


def _ensure_output_dir(filepath: str):
    """确保输出文件的目录存在"""
    dir_path = os.path.dirname(filepath)
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)


@cli.command("import-sales")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option("--products", "products_file", type=click.Path(exists=True, dir_okay=False),
              help="商品主数据文件（可选，包含SKU、名称、品类、起订量、策略ID等）")
@click.option("--stores", "stores_file", type=click.Path(exists=True, dir_okay=False),
              help="门店主数据文件（可选，包含门店ID、名称、区域、类型等）")
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
              help="促销日历文件，包含列：sku, promo_type, start_date, end_date, uplift_factor, store_id（store_id为空表示全门店）")
@click.option("--holiday-factor", type=float, default=None,
              help="节假日需求系数（如1.3表示节假日销量为平日130%）")
@click.option("--min-order-qty", type=int, default=None,
              help="全局默认最低起订量")
@click.option("--turnover-days", type=int, default=None,
              help="全局默认周转天数")
@click.option("--lead-time-days", type=int, default=None,
              help="全局默认补货提前期天数")
@click.option("--service-level", type=float, default=None,
              help="全局默认服务水平（0-1，如0.95表示95%）")
@click.option("--append/--replace", default=False,
              help="追加模式或替换模式（默认替换）")
def import_stock(stock_file, promo_file, holiday_factor, min_order_qty,
                 turnover_days, lead_time_days, service_level, append):
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

        if lead_time_days is not None:
            store.config["default_lead_time_days"] = lead_time_days
            click.echo(f"✓ 设置默认补货提前期: {lead_time_days}天")

        if service_level is not None:
            store.config["default_service_level"] = service_level
            click.echo(f"✓ 设置默认服务水平: {service_level:.0%}")

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
            all_store_count = sum(1 for p in store.promotions if p.is_all_stores)
            click.echo(f"✓ 导入促销日历: {len(df_promo)} 条")
            click.echo(f"  全门店活动: {all_store_count} 条，指定门店活动: {len(df_promo) - all_store_count} 条")

        store.save()

    except Exception as e:
        click.echo(f"✗ 导入失败: {e}", err=True)
        sys.exit(1)


@cli.group("strategy")
def strategy_group():
    """补货策略模板管理"""
    pass


@strategy_group.command("create")
@click.option("--id", "strategy_id", required=True, help="策略ID")
@click.option("--name", required=True, help="策略名称")
@click.option("--service-level", type=float, default=0.95, help="服务水平 (0-1)，默认0.95")
@click.option("--lead-time", type=int, default=3, help="补货提前期天数，默认3")
@click.option("--turnover-days", type=int, default=7, help="目标周转天数，默认7")
@click.option("--min-order-qty", type=int, default=1, help="最低起订量，默认1")
@click.option("--rounding", type=click.Choice(["ceiling", "floor", "round"]), default="ceiling",
              help="起订量取整方式：向上取整/向下取整/四舍五入，默认向上取整")
@click.option("--safety-factor", type=float, default=1.5, help="安全库存系数，默认1.5")
@click.option("--scope-category", default="", help="适用品类（为空表示所有品类）")
@click.option("--scope-sku", default="", help="适用SKU（为空表示所有SKU，优先级高于品类）")
@click.option("--description", default="", help="策略描述")
def strategy_create(strategy_id, name, service_level, lead_time, turnover_days,
                    min_order_qty, rounding, safety_factor, scope_category, scope_sku, description):
    """创建补货策略模板"""
    store = get_store()
    try:
        strategy = ReplenishmentStrategy(
            strategy_id=strategy_id,
            name=name,
            service_level=service_level,
            lead_time_days=lead_time,
            target_turnover_days=turnover_days,
            min_order_qty=min_order_qty,
            order_rounding=rounding,
            safety_stock_factor=safety_factor,
            scope_category=scope_category,
            scope_sku=scope_sku,
            description=description,
        )
        store.add_strategy(strategy)
        store.save()
        click.echo(f"✓ 已创建策略: {strategy_id} - {name}")
        click.echo(f"  服务水平: {service_level:.0%}")
        click.echo(f"  补货提前期: {lead_time}天")
        click.echo(f"  目标周转: {turnover_days}天")
        click.echo(f"  最低起订量: {min_order_qty}")
        click.echo(f"  取整方式: {rounding}")
        if scope_sku:
            click.echo(f"  适用SKU: {scope_sku}")
        elif scope_category:
            click.echo(f"  适用品类: {scope_category}")
        else:
            click.echo(f"  适用范围: 全部商品")
    except Exception as e:
        click.echo(f"✗ 创建失败: {e}", err=True)
        sys.exit(1)


@strategy_group.command("list")
def strategy_list():
    """列出所有补货策略模板"""
    store = get_store()
    if not store.strategies:
        click.echo("⚠ 暂无策略模板")
        return

    data = []
    for s in store.strategies.values():
        sku_scope = s.scope_sku if isinstance(s.scope_sku, str) and s.scope_sku.strip() else ""
        cat_scope = s.scope_category if isinstance(s.scope_category, str) and s.scope_category.strip() else ""
        scope = sku_scope if sku_scope else (cat_scope if cat_scope else "全部")
        data.append({
            "策略ID": s.strategy_id,
            "策略名称": s.name,
            "服务水平": f"{s.service_level:.0%}",
            "提前期(天)": s.lead_time_days,
            "周转天数": s.target_turnover_days,
            "起订量": s.min_order_qty,
            "取整方式": s.order_rounding,
            "适用范围": scope,
            "描述": s.description,
        })

    df = pd.DataFrame(data)
    click.echo(tabulate(df, headers="keys", tablefmt="grid", showindex=False))


@strategy_group.command("delete")
@click.argument("strategy_id")
def strategy_delete(strategy_id):
    """删除指定补货策略模板"""
    store = get_store()
    if strategy_id not in store.strategies:
        click.echo(f"✗ 策略不存在: {strategy_id}", err=True)
        sys.exit(1)

    store.remove_strategy(strategy_id)
    store.save()
    click.echo(f"✓ 已删除策略: {strategy_id}")


@strategy_group.command("import")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option("--append/--replace", default=False, help="追加或替换现有策略")
def strategy_import(filepath, append):
    """从文件批量导入补货策略模板"""
    store = get_store()
    try:
        df = _read_csv_or_excel(filepath)
        required_cols = {"strategy_id", "name"}
        missing = required_cols - set(df.columns.str.lower())
        if missing:
            raise click.BadParameter(f"策略文件缺少必要列: {', '.join(missing)}")

        df.columns = df.columns.str.lower()

        if not append:
            store.strategies = {}

        count = 0
        for _, row in df.iterrows():
            def _safe_str(val):
                s = str(val) if val is not None else ""
                return "" if s.lower() == "nan" else s.strip()

            def _safe_float(val, default):
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return default

            def _safe_int(val, default):
                try:
                    return int(float(val))
                except (ValueError, TypeError):
                    return default

            strategy = ReplenishmentStrategy(
                strategy_id=_safe_str(row["strategy_id"]),
                name=_safe_str(row["name"]),
                service_level=_safe_float(row.get("service_level"), 0.95),
                lead_time_days=_safe_int(row.get("lead_time_days"), 3),
                target_turnover_days=_safe_int(row.get("target_turnover_days"), 7),
                min_order_qty=_safe_int(row.get("min_order_qty"), 1),
                order_rounding=_safe_str(row.get("order_rounding", "ceiling")),
                safety_stock_factor=_safe_float(row.get("safety_stock_factor"), 1.5),
                scope_category=_safe_str(row.get("scope_category", "")),
                scope_sku=_safe_str(row.get("scope_sku", "")),
                description=_safe_str(row.get("description", "")),
            )
            store.add_strategy(strategy)
            count += 1

        store.save()
        click.echo(f"✓ 已导入 {count} 条策略模板")

    except Exception as e:
        click.echo(f"✗ 导入失败: {e}", err=True)
        sys.exit(1)


@cli.group("order")
def order_group():
    """采购订单管理 - 回填补货状态，纳入后续补货建议"""
    pass


@order_group.command("list")
@click.option("--status", type=click.Choice(["已下单", "部分到货", "已到货", "已取消"]), default=None,
              help="按状态筛选")
@click.option("--sku", type=str, default=None, help="按商品SKU筛选")
@click.option("--store", "store_id", type=str, default=None, help="按门店ID筛选")
@click.option("--limit", type=int, default=50, help="显示前N条")
def order_list(status, sku, store_id, limit):
    """列出采购订单"""
    store = get_store()
    orders = store.purchase_orders

    if not orders:
        click.echo("⚠ 暂无采购订单")
        return

    if status:
        orders = [o for o in orders if o.status == status]
    if sku:
        orders = [o for o in orders if o.sku == sku]
    if store_id:
        orders = [o for o in orders if o.store_id == store_id]

    click.echo(f"✓ 共 {len(orders)} 条采购订单")

    if not orders:
        return

    data = []
    for o in orders[:limit]:
        product = store.products.get(o.sku)
        s_obj = store.stores.get(o.store_id) if o.store_id else None
        data.append({
            "订单ID": o.order_id,
            "商品SKU": o.sku,
            "商品名称": product.name if product else "",
            "门店": s_obj.name if s_obj else (o.store_id or "总仓"),
            "订购数量": o.qty,
            "已到货数量": o.arrived_qty,
            "待到货": o.qty - o.arrived_qty,
            "状态": o.status,
            "下单日期": o.ordered_date,
            "预计到货": o.expected_arrival_date,
            "备注": o.remark,
        })

    df = pd.DataFrame(data)
    click.echo(tabulate(df, headers="keys", tablefmt="grid", showindex=False))


@order_group.command("import")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option("--append/--replace", default=True, help="追加或替换（默认追加）")
def order_import(filepath, append):
    """导入采购订单（回填补货执行状态）

    FILEPATH: 订单文件，需包含列：order_id, sku, qty, status, ordered_date, expected_arrival_date, arrived_qty, store_id, remark
    """
    store = get_store()
    try:
        df = _read_csv_or_excel(filepath)
        df.columns = df.columns.str.lower()

        required_cols = {"order_id", "sku", "qty"}
        missing = required_cols - set(df.columns)
        if missing:
            raise click.BadParameter(f"订单文件缺少必要列: {', '.join(missing)}")

        if not append:
            store.purchase_orders = []

        count = 0
        for _, row in df.iterrows():
            def _safe_str(val, default=""):
                s = str(val) if val is not None else default
                return "" if s.lower() == "nan" else s.strip()

            def _safe_int(val, default=0):
                try:
                    return int(float(val))
                except (ValueError, TypeError):
                    return default

            order = PurchaseOrder(
                order_id=_safe_str(row["order_id"]),
                sku=_safe_str(row["sku"]),
                qty=_safe_int(row.get("qty", 0)),
                status=_safe_str(row.get("status", "已下单")) or "已下单",
                ordered_date=_safe_str(row.get("ordered_date", "")),
                expected_arrival_date=_safe_str(row.get("expected_arrival_date", "")),
                arrived_qty=_safe_int(row.get("arrived_qty", 0)),
                unit_cost=float(row.get("unit_cost", 0.0) or 0),
                supplier=_safe_str(row.get("supplier", "")),
                store_id=_safe_str(row.get("store_id", "")),
                remark=_safe_str(row.get("remark", "")),
            )
            store.add_purchase_order(order)
            count += 1

        store.save()
        click.echo(f"✓ 已导入 {count} 条采购订单")

        pending_qty = sum(o.qty - o.arrived_qty for o in store.purchase_orders if o.status in ("已下单", "部分到货"))
        click.echo(f"  待到货总数量: {pending_qty} 件")

    except Exception as e:
        click.echo(f"✗ 导入失败: {e}", err=True)
        sys.exit(1)


@order_group.command("update")
@click.argument("order_id")
@click.option("--status", type=click.Choice(["已下单", "部分到货", "已到货", "已取消"]), default=None,
              help="更新订单状态")
@click.option("--arrived-qty", type=int, default=None, help="更新已到货数量")
def order_update(order_id, status, arrived_qty):
    """更新采购订单状态"""
    store = get_store()

    order = None
    for o in store.purchase_orders:
        if o.order_id == order_id:
            order = o
            break

    if not order:
        click.echo(f"✗ 未找到订单: {order_id}", err=True)
        sys.exit(1)

    if status:
        order.status = status
        if status == "已到货":
            order.arrived_qty = order.qty
    if arrived_qty is not None:
        order.arrived_qty = min(arrived_qty, order.qty)
        if order.arrived_qty >= order.qty:
            order.status = "已到货"
        elif order.arrived_qty > 0:
            if order.status == "已下单":
                order.status = "部分到货"

    store.save()
    click.echo(f"✓ 订单 {order_id} 已更新")
    click.echo(f"  状态: {order.status}, 已到货: {order.arrived_qty}/{order.qty}")


@order_group.command("delete")
@click.argument("order_id")
def order_delete(order_id):
    """删除采购订单"""
    store = get_store()

    original_len = len(store.purchase_orders)
    store.purchase_orders = [o for o in store.purchase_orders if o.order_id != order_id]

    if len(store.purchase_orders) == original_len:
        click.echo(f"✗ 未找到订单: {order_id}", err=True)
        sys.exit(1)

    store.save()
    click.echo(f"✓ 订单 {order_id} 已删除")


@cli.command("forecast")
@click.option("--group-by", type=click.Choice(["sku", "store", "category"]),
              default="sku", help="聚合维度：商品(sku)-跨门店合并、门店(store)-按门店合并、品类(category)")
@click.option("--start-date", type=str, default=None,
              help="预测起始日期 (YYYY-MM-DD)，默认为今天")
@click.option("--days", type=int, default=None,
              help="预测天数，默认7天")
@click.option("--limit", type=int, default=50,
              help="显示前N条结果")
@click.option("--summary", is_flag=True, default=False,
              help="仅显示7天汇总（按选择的维度汇总）")
@click.option("--show-increment", is_flag=True, default=True,
              help="显示促销增量和假日增量（默认开启）")
def forecast_cmd(group_by, start_date, days, limit, summary, show_increment):
    """生成需求预测，明细和汇总维度一致

    维度说明:
    - sku: 商品视角，跨门店合并，显示商品级汇总
    - store: 门店视角，按门店合并，显示门店级汇总
    - category: 品类视角，按品类合并
    """
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

        dim_name = {"sku": "商品(跨门店)", "store": "门店", "category": "品类"}.get(group_by, group_by)
        click.echo(f"✓ 已生成 {len(forecasts)} 条原始预测 (维度: {dim_name})")

        active_promos = sum(1 for f in forecasts if f.promo_increment > 0)
        holiday_effect = sum(1 for f in forecasts if f.holiday_increment > 0)
        if active_promos > 0:
            click.echo(f"  含活动增量的预测: {active_promos} 条")
        if holiday_effect > 0:
            click.echo(f"  含假日增量的预测: {holiday_effect} 条")

        if summary:
            summary_df = forecaster.get_7day_summary(forecasts, group_by=group_by)
            if not show_increment:
                cols_to_keep = [c for c in summary_df.columns if "增量" not in c and "基准" not in c]
                summary_df = summary_df[cols_to_keep]
            summary_df = summary_df.sort_values("7天预测需求", ascending=False).head(limit) if "7天预测需求" in summary_df.columns else summary_df.head(limit)
            click.echo(tabulate(summary_df, headers="keys", tablefmt="grid", showindex=False, floatfmt=".1f"))
        else:
            df = forecaster.aggregate_forecasts(forecasts, group_by)
            if not show_increment:
                cols_to_keep = [c for c in df.columns if "增量" not in c and "基准" not in c]
                df = df[cols_to_keep]
            if limit:
                df = df.head(limit)
            click.echo(tabulate(df, headers="keys", tablefmt="grid", showindex=False, floatfmt=".1f"))

    except Exception as e:
        click.echo(f"✗ 预测失败: {e}", err=True)
        sys.exit(1)


@cli.command("transfer-simulate")
@click.option("--group-by", type=click.Choice(["region", "store_type", "group_name", "all"]),
              default="region", help="门店群组维度：区域、门店类型、自定义组、全部")
@click.option("--limit", type=int, default=30, help="显示前N条调拨建议")
@click.option("--cross-region/--no-cross-region", default=True,
              help="是否允许跨区域调拨（默认允许）")
@click.option("--max-cost-ratio", type=float, default=None,
              help="跨区域调拨时，调拨成本占货值的最大比例（默认0.1即10%）")
@click.option("--show-cost", is_flag=True, default=True,
              help="显示预计调拨成本（默认开启）")
def transfer_simulate(group_by, limit, cross_region, max_cost_ratio, show_cost):
    """门店群组调拨模拟 - 按区域/类型查看缺货和富余，生成调拨优先级

    调拨规则:
    - 优先同区域内调拨
    - 跨区域仅高风险才能调拨
    - 跨区域调拨成本不得超过货值的指定比例
    """
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

        click.echo("=== 门店群组分析 ===")
        analysis_df = engine.get_group_analysis(group_by)
        click.echo(tabulate(analysis_df, headers="keys", tablefmt="grid", showindex=False, floatfmt=".1f"))
        click.echo("")

        click.echo("=== 调拨建议（按优先级） ===")
        transfers = engine.generate_transfer_suggestions(
            group_by=group_by,
            allow_cross_region=cross_region,
            max_cost_ratio=max_cost_ratio,
        )

        if not transfers:
            click.echo("⚠ 未发现可调拨机会")
            return

        cross_count = sum(1 for t in transfers if t.is_cross_region)
        same_count = sum(1 for t in transfers if not t.is_cross_region)
        click.echo(f"✓ 共生成 {len(transfers)} 条调拨建议（同区域 {same_count} 条，跨区域 {cross_count} 条")
        if not cross_region:
            click.echo(f"  (已禁用跨区域调拨)")

        df = engine.transfers_to_dataframe(transfers)
        if limit:
            df = df.head(limit)

        priority_summary = pd.DataFrame([
            {"优先级区间": "≥15", "数量": len([t for t in transfers if t.priority >= 15])},
            {"优先级区间": "10-14", "数量": len([t for t in transfers if 10 <= t.priority < 15])},
            {"优先级区间": "5-9", "数量": len([t for t in transfers if 5 <= t.priority < 10])},
            {"优先级区间": "<5", "数量": len([t for t in transfers if t.priority < 5])},
        ])
        click.echo(tabulate(priority_summary, headers="keys", tablefmt="grid", showindex=False))
        click.echo("")

        cols_to_show = list(df.columns)
        if not show_cost and "预计调拨成本" in cols_to_show:
            cols_to_show.remove("预计调拨成本")
        if not show_cost and "是否跨区域" in cols_to_show:
            cols_to_show.remove("是否跨区域")
        available_cols = [c for c in cols_to_show if c in df.columns]
        df = df[available_cols]

        click.echo(tabulate(df, headers="keys", tablefmt="grid", showindex=False))

    except Exception as e:
        click.echo(f"✗ 调拨模拟失败: {e}", err=True)
        sys.exit(1)


@cli.command("suggest")
@click.option("--strategy", "strategy_id", default=None,
              help="指定策略ID（不指定则自动匹配商品/品类绑定的策略）")
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
@click.option("--category", type=str, default=None,
              help="按品类筛选")
@click.option("--region", type=str, default=None,
              help="按区域筛选")
@click.option("--limit", type=int, default=50,
              help="显示前N条结果")
@click.option("--show-strategy", is_flag=True, default=True,
              help="显示使用的策略名称（默认开启）")
@click.option("--ignore-pending", is_flag=True, default=False,
              help="忽略已下单未到货的采购订单（默认纳入计算）")
def suggest_cmd(strategy_id, risk, need_replenish, stagnant, store_id, sku,
                category, region, limit, show_strategy, ignore_pending):
    """输出建议补货量、缺货风险等级、滞销提示和可调拨门店，可选择策略模板"""
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
        engine.include_pending_orders = not ignore_pending
        suggestions = engine.generate_suggestions(strategy_id=strategy_id)

        if not suggestions:
            click.echo("⚠ 未生成有效建议，请检查数据")
            return

        suggestions = engine.filter_suggestions(
            suggestions, risk_filter=risk,
            need_replenish_only=need_replenish,
            stagnant_only=stagnant,
            store_id=store_id,
            sku=sku,
            category=category,
            region=region,
        )

        click.echo(f"✓ 共生成 {len(suggestions)} 条补货建议")
        click.echo(f"  使用策略: {engine.current_strategy}")
        if not ignore_pending and store.purchase_orders:
            click.echo(f"  已纳入采购订单: {len(store.purchase_orders)} 单")

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

        strategy_stats = {}
        for s in suggestions:
            key = s.strategy_name or "默认策略"
            strategy_stats[key] = strategy_stats.get(key, 0) + 1
        if len(strategy_stats) > 1:
            click.echo(f"  策略分布: {', '.join([f'{k}={v}' for k, v in strategy_stats.items()])}")

        df = engine.suggestions_to_dataframe(suggestions)
        if limit:
            df = df.head(limit)

        columns = ["门店名称", "区域", "商品名称", "品类", "当前库存", "在途数量", "已下单未到", "安全库存",
                   "7天预测需求", "缺货风险等级", "建议补货量", "滞销提示", "可调拨门店"]
        if show_strategy:
            columns.insert(len(columns) - 1, "使用策略")
        columns.append("建议原因")

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
@click.option("--need-replenish/--all-items", default=True,
              help="仅导出需要补货的商品（默认开启）")
@click.option("--include-forecast", is_flag=True, default=True,
              help="包含预测数据（按forecast最后使用的维度）")
@click.option("--include-transfer", is_flag=True, default=True,
              help="包含调拨建议")
@click.option("--forecast-group-by", type=click.Choice(["sku", "store", "category"]), default=None,
              help="强制指定预测明细的汇总维度（不指定则使用最近一次forecast的维度）")
def export_cmd(output, fmt, export_type, risk, need_replenish, include_forecast,
               include_transfer, forecast_group_by):
    """导出给采购或配送团队使用的清单，CSV和Excel都支持三种口径

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
        else:
            if fmt == "xlsx":
                ext = os.path.splitext(output)[1].lower().lstrip(".")
                if ext not in ("xlsx", "xls"):
                    output = output + ".xlsx"

        engine = SuggestionEngine(store)
        suggestions = store.suggestions

        risk_filter = None if risk == "all" else risk
        suggestions = engine.filter_suggestions(
            suggestions, risk_filter=risk_filter,
            need_replenish_only=need_replenish
        )

        df = engine.suggestions_to_dataframe(suggestions)

        if df.empty:
            click.echo("⚠ 筛选后无数据可导出")
            return

        if include_transfer and store.transfer_suggestions:
            transfer_df = engine.transfers_to_dataframe(store.transfer_suggestions)
            click.echo(f"✓ 包含 {len(store.transfer_suggestions)} 条调拨建议")

        forecaster = Forecaster(store)
        group_by = forecast_group_by or forecaster.last_group_by

        if include_forecast and store.forecasts:
            forecast_export_df = forecaster.get_detailed_forecast_export(store.forecasts, group_by=group_by)
            if not forecast_export_df.empty:
                click.echo(f"✓ 包含 {len(store.forecasts)} 条预测明细 (维度: {group_by})")

        base, ext = os.path.splitext(output)

        if fmt == "xlsx":
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                if export_type in ("purchase", "all"):
                    purchase_cols = ["商品SKU", "商品名称", "品类", "建议补货量"]
                    purchase_df = df.groupby(["商品SKU", "商品名称", "品类"], as_index=False)["建议补货量"].sum()
                    purchase_df = purchase_df[purchase_df["建议补货量"] > 0]
                    purchase_df = purchase_df.rename(columns={"建议补货量": "建议采购量"})
                    purchase_df = purchase_df.sort_values("建议采购量", ascending=False)
                    purchase_df.to_excel(writer, sheet_name="采购清单", index=False)
                    click.echo(f"  采购清单: {len(purchase_df)} 个SKU，采购总量: {int(purchase_df['建议采购量'].sum())}")

                if export_type in ("delivery", "all"):
                    delivery_cols = ["门店ID", "门店名称", "区域", "门店类型", "商品SKU", "商品名称", "品类",
                                     "当前库存", "在途数量", "安全库存", "7天预测需求",
                                     "缺货风险等级", "建议补货量", "使用策略", "可调拨门店", "建议原因"]
                    delivery_df = df[[c for c in delivery_cols if c in df.columns]]
                    delivery_df = delivery_df.sort_values(
                        ["缺货风险等级", "区域", "门店名称"],
                        ascending=[False, True, True],
                        key=lambda x: x.map({"高": 0, "中": 1, "低": 2}) if x.name == "缺货风险等级" else x
                    )
                    delivery_df.to_excel(writer, sheet_name="配送清单", index=False)
                    click.echo(f"  配送清单: {len(delivery_df)} 条记录")

                if include_forecast and store.forecasts:
                    forecast_export_df.to_excel(writer, sheet_name=f"需求预测({group_by})", index=False)

                if include_transfer and store.transfer_suggestions:
                    transfer_df.to_excel(writer, sheet_name="调拨建议", index=False)

                summary_data = []
                for risk_lvl in ["高", "中", "低"]:
                    count = len(df[df["缺货风险等级"] == risk_lvl])
                    qty = int(df[df["缺货风险等级"] == risk_lvl]["建议补货量"].sum())
                    summary_data.append({"风险等级": risk_lvl, "SKU数量": count, "建议补货总量": qty})
                pd.DataFrame(summary_data).to_excel(writer, sheet_name="汇总概览", index=False)

                if export_type == "all":
                    strategy_summary = df.groupby(["使用策略"], as_index=False).agg({
                        "商品SKU": "count",
                        "建议补货量": "sum"
                    })
                    strategy_summary = strategy_summary.rename(columns={"商品SKU": "SKU数量", "建议补货量": "补货总量"})
                    strategy_summary.to_excel(writer, sheet_name="策略汇总", index=False)
        else:
            if export_type == "all":
                if export_type in ("purchase", "all"):
                    purchase_file = f"{base}_采购.{fmt}"
                    purchase_cols = ["商品SKU", "商品名称", "品类", "建议补货量"]
                    purchase_df = df.groupby(["商品SKU", "商品名称", "品类"], as_index=False)["建议补货量"].sum()
                    purchase_df = purchase_df[purchase_df["建议补货量"] > 0]
                    purchase_df = purchase_df.rename(columns={"建议补货量": "建议采购量"})
                    purchase_df = purchase_df.sort_values("建议采购量", ascending=False)
                    purchase_df.to_csv(purchase_file, index=False, encoding="utf-8-sig")
                    click.echo(f"✓ 采购清单已导出到: {purchase_file} ({len(purchase_df)} 个SKU)")

                if export_type in ("delivery", "all"):
                    delivery_file = f"{base}_配送.{fmt}"
                    delivery_cols = ["门店ID", "门店名称", "区域", "门店类型", "商品SKU", "商品名称", "品类",
                                     "当前库存", "在途数量", "安全库存", "7天预测需求",
                                     "缺货风险等级", "建议补货量", "使用策略", "可调拨门店", "建议原因"]
                    delivery_df = df[[c for c in delivery_cols if c in df.columns]]
                    delivery_df = delivery_df.sort_values(
                        ["缺货风险等级", "区域", "门店名称"],
                        ascending=[False, True, True],
                        key=lambda x: x.map({"高": 0, "中": 1, "低": 2}) if x.name == "缺货风险等级" else x
                    )
                    delivery_df.to_csv(delivery_file, index=False, encoding="utf-8-sig")
                    click.echo(f"✓ 配送清单已导出到: {delivery_file} ({len(delivery_df)} 条记录)")

                if include_forecast and store.forecasts:
                    forecast_file = f"{base}_预测.{fmt}"
                    forecast_export_df.to_csv(forecast_file, index=False, encoding="utf-8-sig")
                    click.echo(f"✓ 预测明细已导出到: {forecast_file} ({len(forecast_export_df)} 条记录)")

                if include_transfer and store.transfer_suggestions:
                    transfer_file = f"{base}_调拨.{fmt}"
                    transfer_df.to_csv(transfer_file, index=False, encoding="utf-8-sig")
                    click.echo(f"✓ 调拨建议已导出到: {transfer_file} ({len(transfer_df)} 条记录)")
            else:
                if export_type == "purchase":
                    purchase_cols = ["商品SKU", "商品名称", "品类", "建议补货量"]
                    purchase_df = df.groupby(["商品SKU", "商品名称", "品类"], as_index=False)["建议补货量"].sum()
                    purchase_df = purchase_df[purchase_df["建议补货量"] > 0]
                    purchase_df = purchase_df.rename(columns={"建议补货量": "建议采购量"})
                    purchase_df = purchase_df.sort_values("建议采购量", ascending=False)
                    purchase_df.to_csv(output, index=False, encoding="utf-8-sig")
                    click.echo(f"✓ 已导出到: {output}")
                    click.echo(f"  共 {len(purchase_df)} 个SKU")
                    click.echo(f"  建议采购总量: {int(purchase_df['建议采购量'].sum())}")
                elif export_type == "delivery":
                    delivery_cols = ["门店ID", "门店名称", "区域", "门店类型", "商品SKU", "商品名称", "品类",
                                     "当前库存", "在途数量", "安全库存", "7天预测需求",
                                     "缺货风险等级", "建议补货量", "使用策略", "可调拨门店", "建议原因"]
                    delivery_df = df[[c for c in delivery_cols if c in df.columns]]
                    delivery_df = delivery_df.sort_values(
                        ["缺货风险等级", "区域", "门店名称"],
                        ascending=[False, True, True],
                        key=lambda x: x.map({"高": 0, "中": 1, "低": 2}) if x.name == "缺货风险等级" else x
                    )
                    delivery_df.to_csv(output, index=False, encoding="utf-8-sig")
                    click.echo(f"✓ 已导出到: {output}")
                    click.echo(f"  共 {len(delivery_df)} 条记录")

        if fmt == "xlsx":
            click.echo(f"✓ 已导出到: {output}")
            click.echo(f"  共 {len(df)} 条记录")
            if not df.empty:
                click.echo(f"  建议补货总量: {int(df['建议补货量'].sum())}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        click.echo(f"✗ 导出失败: {e}", err=True)
        sys.exit(1)


@cli.command("data-quality")
@click.option("--export", "export_path", type=click.Path(dir_okay=False), default=None,
              help="导出异常清单到文件")
@click.option("--fix/--no-fix", default=False,
              help="自动修复可修正的问题（如负库存置零）")
@click.option("--severity", type=click.Choice(["高", "中", "低"]), default=None,
              help="按严重程度筛选后导出（仅影响导出和显示）")
@click.option("--limit", type=int, default=20, help="显示前N条问题明细")
def data_quality_cmd(export_path, fix, severity, limit):
    """数据质量检查 - 列出缺少主数据、负库存、重复销量、日期格式等问题"""
    store = get_store()
    issues: List[DataQualityIssue] = []

    try:
        click.echo("=== 开始数据质量检查 ===")

        sku_set = set(store.products.keys())
        store_set = set(store.stores.keys())

        for idx, sale in enumerate(store.sales):
            if sale.sku not in sku_set:
                issues.append(DataQualityIssue(
                    issue_type="缺少商品主数据",
                    severity="中",
                    table_name="销量数据",
                    record_key=f"{sale.store_id}-{sale.sku}-{sale.sale_date}",
                    description=f"销量第{idx+1}条：SKU {sale.sku} 不存在于商品主数据",
                ))
            if sale.store_id not in store_set:
                issues.append(DataQualityIssue(
                    issue_type="缺少门店主数据",
                    severity="中",
                    table_name="销量数据",
                    record_key=f"{sale.store_id}-{sale.sku}-{sale.sale_date}",
                    description=f"销量第{idx+1}条：门店 {sale.store_id} 不存在于门店主数据",
                ))
            if sale.quantity < 0:
                issues.append(DataQualityIssue(
                    issue_type="负销量",
                    severity="高",
                    table_name="销量数据",
                    record_key=f"{sale.store_id}-{sale.sku}-{sale.sale_date}",
                    description=f"销量第{idx+1}条：销售数量 {sale.quantity} 为负数",
                ))
            try:
                datetime.strptime(sale.sale_date, "%Y-%m-%d")
            except ValueError:
                issues.append(DataQualityIssue(
                    issue_type="日期格式错误",
                    severity="高",
                    table_name="销量数据",
                    record_key=f"{sale.store_id}-{sale.sku}-{sale.sale_date}",
                    description=f"销量第{idx+1}条：日期 {sale.sale_date} 格式不正确，应为 YYYY-MM-DD",
                ))

        seen_sales = {}
        for idx, sale in enumerate(store.sales):
            key = (sale.store_id, sale.sku, sale.sale_date)
            if key in seen_sales:
                issues.append(DataQualityIssue(
                    issue_type="重复销量记录",
                    severity="中",
                    table_name="销量数据",
                    record_key=f"{sale.store_id}-{sale.sku}-{sale.sale_date}",
                    description=f"销量第{idx+1}条：与第{seen_sales[key]+1}条重复 (同门店同SKU同日期)",
                ))
            else:
                seen_sales[key] = idx

        for idx, stock in enumerate(store.stocks):
            if stock.sku not in sku_set:
                issues.append(DataQualityIssue(
                    issue_type="缺少商品主数据",
                    severity="中",
                    table_name="库存数据",
                    record_key=f"{stock.store_id}-{stock.sku}",
                    description=f"库存第{idx+1}条：SKU {stock.sku} 不存在于商品主数据",
                ))
            if stock.store_id not in store_set:
                issues.append(DataQualityIssue(
                    issue_type="缺少门店主数据",
                    severity="中",
                    table_name="库存数据",
                    record_key=f"{stock.store_id}-{stock.sku}",
                    description=f"库存第{idx+1}条：门店 {stock.store_id} 不存在于门店主数据",
                ))
            if stock.current_stock < 0:
                issues.append(DataQualityIssue(
                    issue_type="负库存",
                    severity="高",
                    table_name="库存数据",
                    record_key=f"{stock.store_id}-{stock.sku}",
                    description=f"库存第{idx+1}条：当前库存 {stock.current_stock} 为负数",
                ))
            if stock.in_transit < 0:
                issues.append(DataQualityIssue(
                    issue_type="负在途",
                    severity="中",
                    table_name="库存数据",
                    record_key=f"{stock.store_id}-{stock.sku}",
                    description=f"库存第{idx+1}条：在途数量 {stock.in_transit} 为负数",
                ))
            if stock.safety_stock < 0:
                issues.append(DataQualityIssue(
                    issue_type="负安全库存",
                    severity="中",
                    table_name="库存数据",
                    record_key=f"{stock.store_id}-{stock.sku}",
                    description=f"库存第{idx+1}条：安全库存 {stock.safety_stock} 为负数",
                ))

        for idx, promo in enumerate(store.promotions):
            if promo.sku != "*" and promo.sku not in sku_set:
                issues.append(DataQualityIssue(
                    issue_type="缺少商品主数据",
                    severity="低",
                    table_name="促销日历",
                    record_key=f"{promo.sku}-{promo.start_date}",
                    description=f"促销第{idx+1}条：SKU {promo.sku} 不存在于商品主数据",
                ))
            if promo.store_id and promo.store_id not in store_set:
                issues.append(DataQualityIssue(
                    issue_type="缺少门店主数据",
                    severity="低",
                    table_name="促销日历",
                    record_key=f"{promo.sku}-{promo.start_date}",
                    description=f"促销第{idx+1}条：门店 {promo.store_id} 不存在于门店主数据",
                ))
            try:
                start = datetime.strptime(promo.start_date, "%Y-%m-%d")
                end = datetime.strptime(promo.end_date, "%Y-%m-%d")
                if start > end:
                    issues.append(DataQualityIssue(
                        issue_type="日期逻辑错误",
                        severity="高",
                        table_name="促销日历",
                        record_key=f"{promo.sku}-{promo.start_date}",
                        description=f"促销第{idx+1}条：开始日期 {promo.start_date} 晚于结束日期 {promo.end_date}",
                    ))
            except ValueError:
                issues.append(DataQualityIssue(
                    issue_type="日期格式错误",
                    severity="高",
                    table_name="促销日历",
                    record_key=f"{promo.sku}-{promo.start_date}",
                    description=f"促销第{idx+1}条：日期格式不正确，应为 YYYY-MM-DD",
                ))
            if promo.uplift_factor < 1.0:
                issues.append(DataQualityIssue(
                    issue_type="促销系数异常",
                    severity="中",
                    table_name="促销日历",
                    record_key=f"{promo.sku}-{promo.start_date}",
                    description=f"促销第{idx+1}条：提升系数 {promo.uplift_factor} 小于1.0",
                ))

        for strategy_id, strategy in store.strategies.items():
            if strategy.scope_category and not any(p.category == strategy.scope_category for p in store.products.values()):
                issues.append(DataQualityIssue(
                    issue_type="策略无匹配品类",
                    severity="低",
                    table_name="补货策略",
                    record_key=strategy_id,
                    description=f"策略 {strategy.name} 指定品类 {strategy.scope_category}，但无商品属于该品类",
                ))
            if strategy.scope_sku and strategy.scope_sku not in sku_set:
                issues.append(DataQualityIssue(
                    issue_type="策略无匹配SKU",
                    severity="低",
                    table_name="补货策略",
                    record_key=strategy_id,
                    description=f"策略 {strategy.name} 指定SKU {strategy.scope_sku}，但该SKU不存在",
                ))

        store.data_quality_issues = issues

        severity_count = {"高": 0, "中": 0, "低": 0}
        type_count = {}
        for issue in issues:
            severity_count[issue.severity] = severity_count.get(issue.severity, 0) + 1
            type_count[issue.issue_type] = type_count.get(issue.issue_type, 0) + 1

        click.echo(f"\n✓ 检查完成，共发现 {len(issues)} 个问题")
        click.echo(f"  严重程度: 高={severity_count.get('高', 0)} 中={severity_count.get('中', 0)} 低={severity_count.get('低', 0)}")
        click.echo("")
        click.echo("=== 问题类型统计 ===")
        type_df = pd.DataFrame([{"问题类型": k, "数量": v} for k, v in type_count.items()])
        click.echo(tabulate(type_df, headers="keys", tablefmt="grid", showindex=False))

        if issues:
            display_issues_list = issues
            if severity:
                display_issues_list = [i for i in issues if i.severity == severity]

            click.echo("")
            click.echo(f"=== 问题明细（前{limit}条{f', {severity}严重程度' if severity else ''}） ===")
            display_issues = display_issues_list[:limit]
            issue_df = pd.DataFrame([{
                "严重程度": i.severity,
                "问题类型": i.issue_type,
                "数据来源": i.table_name,
                "记录标识": i.record_key,
                "详细描述": i.description,
            } for i in display_issues])
            click.echo(tabulate(issue_df, headers="keys", tablefmt="grid", showindex=False))

            if len(display_issues_list) > limit:
                click.echo(f"  ... 还有 {len(display_issues_list) - limit} 条问题，请通过 --export 导出完整清单")

        if fix:
            fixed_count = 0
            for stock in store.stocks:
                if stock.current_stock < 0:
                    stock.current_stock = 0
                    fixed_count += 1
                if stock.in_transit < 0:
                    stock.in_transit = 0
                    fixed_count += 1
                if stock.safety_stock < 0:
                    stock.safety_stock = 0
                    fixed_count += 1
            if fixed_count > 0:
                store.save()
                click.echo(f"\n✓ 已自动修复 {fixed_count} 个可修正问题（负库存置零）")

        if export_path:
            export_issues = issues
            if severity:
                export_issues = [i for i in issues if i.severity == severity]

            _ensure_output_dir(export_path)

            issue_df = pd.DataFrame([{
                "严重程度": i.severity,
                "问题类型": i.issue_type,
                "数据来源": i.table_name,
                "记录标识": i.record_key,
                "详细描述": i.description,
            } for i in export_issues])
            issue_df.to_csv(export_path, index=False, encoding="utf-8-sig")
            click.echo(f"\n✓ 异常清单已导出到: {export_path} ({len(export_issues)} 条)")
            if severity:
                click.echo(f"  (已筛选 {severity} 严重程度)")

        if severity_count.get("高", 0) > 0:
            click.echo(f"\n⚠ 存在 {severity_count.get('高', 0)} 个高严重程度问题，建议先修复后再进行分析")

    except Exception as e:
        import traceback
        traceback.print_exc()
        click.echo(f"✗ 数据质量检查失败: {e}", err=True)
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
    all_store_promos = sum(1 for p in store.promotions if p.is_all_stores)
    if all_store_promos > 0:
        click.echo(f"  其中全门店活动: {all_store_promos} 条")
    click.echo(f"补货策略: {len(store.strategies)} 个模板")
    click.echo(f"预测数据: {len(store.forecasts)} 条记录")
    click.echo(f"补货建议: {len(store.suggestions)} 条记录")
    click.echo(f"调拨建议: {len(store.transfer_suggestions)} 条记录")
    click.echo(f"采购订单: {len(store.purchase_orders)} 单")
    if store.purchase_orders:
        pending = sum(1 for o in store.purchase_orders if o.status in ("已下单", "部分到货"))
        pending_qty = sum(o.qty - o.arrived_qty for o in store.purchase_orders if o.status in ("已下单", "部分到货"))
        click.echo(f"  待到货: {pending} 单，{pending_qty} 件")
    click.echo("")
    click.echo("=== 参数配置 ===")
    for k, v in store.config.items():
        display_v = f"{v:.0%}" if k.endswith("service_level") and isinstance(v, float) else v
        click.echo(f"  {k}: {display_v}")

    if store.strategies:
        click.echo("")
        click.echo("=== 已配置策略 ===")
        for s in store.strategies.values():
            scope = s.scope_sku if s.scope_sku else (s.scope_category if s.scope_category else "全部")
            click.echo(f"  {s.strategy_id}: {s.name} (服务水平{s.service_level:.0%}, 周转{s.target_turnover_days}天, 适用:{scope})")


if __name__ == "__main__":
    cli()
