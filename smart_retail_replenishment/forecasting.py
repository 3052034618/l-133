from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
import pandas as pd
import math

from .storage import DataStore, ForecastRecord, Product, ReplenishmentStrategy


class Forecaster:
    def __init__(self, store: DataStore):
        self.store = store
        self._last_group_by = store.config.get("last_forecast_group_by", "sku")

    @property
    def last_group_by(self) -> str:
        return self._last_group_by

    def _get_sales_dataframe(self) -> pd.DataFrame:
        if not self.store.sales:
            return pd.DataFrame(columns=["store_id", "sku", "sale_date", "quantity"])
        data = [{
            "store_id": s.store_id,
            "sku": s.sku,
            "sale_date": s.sale_date,
            "quantity": s.quantity,
            "amount": s.amount,
        } for s in self.store.sales]
        df = pd.DataFrame(data)
        df["sale_date"] = pd.to_datetime(df["sale_date"])
        return df

    def _is_holiday(self, date_obj: datetime) -> bool:
        return date_obj.weekday() >= 5

    def _get_holiday_factor(self, date_obj: datetime) -> float:
        if self._is_holiday(date_obj):
            return float(self.store.config.get("holiday_factor", 1.3))
        return 1.0

    def _get_promotion_factor(self, store_id: str, sku: str, date_obj: datetime) -> Tuple[float, bool]:
        date_str = date_obj.strftime("%Y-%m-%d")
        max_factor = 1.0
        has_promo = False
        for promo in self.store.promotions:
            if promo.sku != sku and promo.sku != "*":
                continue
            if promo.is_all_stores:
                pass
            elif promo.store_id and promo.store_id != store_id:
                continue
            if promo.start_date <= date_str <= promo.end_date:
                if promo.uplift_factor > max_factor:
                    max_factor = promo.uplift_factor
                    has_promo = True
        return max_factor, has_promo

    def _calculate_daily_avg(self, df: pd.DataFrame, store_id: str, sku: str,
                              days: int = 28) -> Tuple[float, float]:
        mask = (df["store_id"] == store_id) & (df["sku"] == sku)
        sku_df = df[mask].copy()
        if sku_df.empty:
            return 0.0, 0.0

        max_date = sku_df["sale_date"].max()
        min_date = max_date - timedelta(days=days)
        recent_df = sku_df[sku_df["sale_date"] >= min_date]

        if recent_df.empty:
            return 0.0, 0.0

        daily = recent_df.groupby("sale_date")["quantity"].sum().reset_index()
        date_range = pd.date_range(start=min_date, end=max_date, freq="D")
        daily = daily.set_index("sale_date").reindex(date_range, fill_value=0).reset_index()
        daily.columns = ["sale_date", "quantity"]

        daily["is_holiday"] = daily["sale_date"].dt.weekday >= 5
        weekday_avg = daily[~daily["is_holiday"]]["quantity"].mean() if not daily[~daily["is_holiday"]].empty else 0.0
        holiday_avg = daily[daily["is_holiday"]]["quantity"].mean() if not daily[daily["is_holiday"]].empty else 0.0

        if pd.isna(weekday_avg):
            weekday_avg = 0.0
        if pd.isna(holiday_avg):
            holiday_avg = 0.0

        return weekday_avg, holiday_avg

    def _get_similar_sku_avg(self, df: pd.DataFrame, store_id: str, sku: str,
                              category: str) -> float:
        if not category:
            return 0.0
        skus_in_category = [p.sku for p in self.store.products.values() if p.category == category and p.sku != sku]
        if not skus_in_category:
            return 0.0
        mask = (df["store_id"] == store_id) & (df["sku"].isin(skus_in_category))
        similar_df = df[mask]
        if similar_df.empty:
            return 0.0
        return float(similar_df.groupby("sku")["quantity"].sum().mean() / 28.0)

    def generate_forecast(self, group_by: str = "sku",
                           start_date: Optional[str] = None,
                           days: Optional[int] = None,
                           strategy: Optional[ReplenishmentStrategy] = None) -> List[ForecastRecord]:
        self._last_group_by = group_by
        self.store.config["last_forecast_group_by"] = group_by
        self.store.save()

        if days is None:
            days = int(self.store.config.get("forecast_days", 7))

        if start_date is None:
            start_dt = datetime.now()
        else:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")

        df = self._get_sales_dataframe()

        store_sku_pairs = set()
        if self.store.stocks:
            for s in self.store.stocks:
                store_sku_pairs.add((s.store_id, s.sku))
        if self.store.sales:
            for s in self.store.sales:
                store_sku_pairs.add((s.store_id, s.sku))

        forecasts = []
        holiday_factor_val = float(self.store.config.get("holiday_factor", 1.3))

        for store_id, sku in sorted(store_sku_pairs):
            product = self.store.products.get(sku)
            category = product.category if product else ""

            weekday_avg, holiday_avg = self._calculate_daily_avg(df, store_id, sku)

            if weekday_avg == 0 and holiday_avg == 0:
                similar_avg = self._get_similar_sku_avg(df, store_id, sku, category)
                weekday_avg = similar_avg
                holiday_avg = similar_avg * holiday_factor_val

            for i in range(days):
                forecast_dt = start_dt + timedelta(days=i)
                forecast_date = forecast_dt.strftime("%Y-%m-%d")

                if self._is_holiday(forecast_dt):
                    base_qty = holiday_avg if holiday_avg > 0 else weekday_avg
                else:
                    base_qty = weekday_avg if weekday_avg > 0 else holiday_avg

                baseline_qty = round(base_qty, 2)

                promo_factor, has_promo = self._get_promotion_factor(store_id, sku, forecast_dt)

                holiday_increment = 0.0
                if self._is_holiday(forecast_dt) and base_qty > 0:
                    weekday_equiv = base_qty / holiday_factor_val
                    holiday_increment = round(base_qty - weekday_equiv, 2)

                promo_increment = 0.0
                if has_promo and promo_factor > 1.0:
                    non_promo_qty = base_qty
                    promo_qty = base_qty * promo_factor
                    promo_increment = round(promo_qty - non_promo_qty, 2)

                forecast_qty = round(base_qty * promo_factor, 2)

                forecasts.append(ForecastRecord(
                    store_id=store_id,
                    sku=sku,
                    forecast_date=forecast_date,
                    forecast_qty=forecast_qty,
                    baseline_qty=baseline_qty,
                    promo_increment=promo_increment,
                    holiday_increment=holiday_increment,
                ))

        self.store.forecasts = forecasts
        self.store.save()
        return forecasts

    def aggregate_forecasts(self, forecasts: List[ForecastRecord],
                             group_by: str) -> pd.DataFrame:
        if not forecasts:
            return pd.DataFrame()

        data = []
        for f in forecasts:
            product = self.store.products.get(f.sku)
            store = self.store.stores.get(f.store_id)
            data.append({
                "store_id": f.store_id,
                "store_name": store.name if store else f.store_id,
                "region": store.region if store else "",
                "store_type": store.store_type if store else "",
                "sku": f.sku,
                "product_name": product.name if product else f.sku,
                "category": product.category if product else "",
                "forecast_date": f.forecast_date,
                "预测数量": f.forecast_qty,
                "基准销量": f.baseline_qty,
                "促销增量": f.promo_increment,
                "假日增量": f.holiday_increment,
            })

        df = pd.DataFrame(data)

        value_cols = ["预测数量", "基准销量", "促销增量", "假日增量"]

        if group_by == "store":
            grouped = df.groupby(["store_id", "store_name", "region", "store_type", "forecast_date"], as_index=False)[value_cols].sum()
            grouped = grouped.rename(columns={"store_id": "门店ID", "store_name": "门店名称", "region": "区域", "store_type": "门店类型", "forecast_date": "预测日期"})
        elif group_by == "category":
            grouped = df.groupby(["category", "forecast_date"], as_index=False)[value_cols].sum()
            grouped = grouped.rename(columns={"category": "品类", "forecast_date": "预测日期"})
        elif group_by == "sku":
            grouped = df.groupby(["sku", "product_name", "category", "forecast_date"], as_index=False)[value_cols].sum()
            grouped = grouped.rename(columns={"sku": "商品SKU", "product_name": "商品名称", "category": "品类", "forecast_date": "预测日期"})
        else:
            grouped = df

        return grouped

    def get_7day_summary(self, forecasts: List[ForecastRecord],
                         group_by: str = "sku") -> pd.DataFrame:
        if not forecasts:
            return pd.DataFrame()

        data = []
        for f in forecasts:
            product = self.store.products.get(f.sku)
            store = self.store.stores.get(f.store_id)
            data.append({
                "store_id": f.store_id,
                "store_name": store.name if store else f.store_id,
                "region": store.region if store else "",
                "store_type": store.store_type if store else "",
                "sku": f.sku,
                "product_name": product.name if product else f.sku,
                "category": product.category if product else "",
                "forecast_qty": f.forecast_qty,
                "baseline_qty": f.baseline_qty,
                "promo_increment": f.promo_increment,
                "holiday_increment": f.holiday_increment,
            })

        df = pd.DataFrame(data)

        value_cols = ["forecast_qty", "baseline_qty", "promo_increment", "holiday_increment"]

        if group_by == "store":
            summary = df.groupby(["store_id", "store_name", "region", "store_type"], as_index=False)[value_cols].sum()
            summary = summary.rename(columns={
                "store_id": "门店ID", "store_name": "门店名称",
                "region": "区域", "store_type": "门店类型",
                "forecast_qty": "7天预测需求", "baseline_qty": "7天基准销量",
                "promo_increment": "7天促销增量", "holiday_increment": "7天假日增量"
            })
        elif group_by == "category":
            summary = df.groupby(["category"], as_index=False)[value_cols].sum()
            summary = summary.rename(columns={
                "category": "品类",
                "forecast_qty": "7天预测需求", "baseline_qty": "7天基准销量",
                "promo_increment": "7天促销增量", "holiday_increment": "7天假日增量"
            })
        else:
            summary = df.groupby(["sku", "product_name", "category"], as_index=False)[value_cols].sum()
            summary = summary.rename(columns={
                "sku": "商品SKU", "product_name": "商品名称", "category": "品类",
                "forecast_qty": "7天预测需求", "baseline_qty": "7天基准销量",
                "promo_increment": "7天促销增量", "holiday_increment": "7天假日增量"
            })

        return summary

    def get_detailed_forecast_export(self, forecasts: List[ForecastRecord],
                                     group_by: str = "sku") -> pd.DataFrame:
        if not forecasts:
            return pd.DataFrame()

        df = self.aggregate_forecasts(forecasts, group_by)

        value_cols_map = {
            "store": ["门店ID", "门店名称", "区域", "门店类型", "预测日期", "预测数量", "基准销量", "促销增量", "假日增量"],
            "category": ["品类", "预测日期", "预测数量", "基准销量", "促销增量", "假日增量"],
            "sku": ["商品SKU", "商品名称", "品类", "预测日期", "预测数量", "基准销量", "促销增量", "假日增量"],
        }

        cols = value_cols_map.get(group_by, list(df.columns))
        available_cols = [c for c in cols if c in df.columns]
        return df[available_cols]
