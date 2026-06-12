from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
import pandas as pd

from .storage import DataStore, ForecastRecord, Product


class Forecaster:
    def __init__(self, store: DataStore):
        self.store = store

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

    def _get_promotion_factor(self, store_id: str, sku: str, date_obj: datetime) -> float:
        date_str = date_obj.strftime("%Y-%m-%d")
        for promo in self.store.promotions:
            if promo.sku != sku:
                continue
            if promo.store_id and promo.store_id != store_id:
                continue
            if promo.start_date <= date_str <= promo.end_date:
                return promo.uplift_factor
        return 1.0

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
                           days: Optional[int] = None) -> List[ForecastRecord]:
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

        for store_id, sku in sorted(store_sku_pairs):
            product = self.store.products.get(sku)
            category = product.category if product else ""

            weekday_avg, holiday_avg = self._calculate_daily_avg(df, store_id, sku)

            if weekday_avg == 0 and holiday_avg == 0:
                similar_avg = self._get_similar_sku_avg(df, store_id, sku, category)
                weekday_avg = similar_avg
                holiday_avg = similar_avg * float(self.store.config.get("holiday_factor", 1.3))

            for i in range(days):
                forecast_dt = start_dt + timedelta(days=i)
                forecast_date = forecast_dt.strftime("%Y-%m-%d")

                if self._is_holiday(forecast_dt):
                    base_qty = holiday_avg if holiday_avg > 0 else weekday_avg
                else:
                    base_qty = weekday_avg if weekday_avg > 0 else holiday_avg

                promo_factor = self._get_promotion_factor(store_id, sku, forecast_dt)
                forecast_qty = round(base_qty * promo_factor, 2)

                forecasts.append(ForecastRecord(
                    store_id=store_id,
                    sku=sku,
                    forecast_date=forecast_date,
                    forecast_qty=forecast_qty,
                ))

        self.store.forecasts = forecasts
        self.store.save()
        return forecasts

    def aggregate_forecasts(self, forecasts: List[ForecastRecord],
                             group_by: str) -> pd.DataFrame:
        if not forecasts:
            return pd.DataFrame()

        data = [{
            "store_id": f.store_id,
            "sku": f.sku,
            "forecast_date": f.forecast_date,
            "forecast_qty": f.forecast_qty,
            "category": self.store.products.get(f.sku).category if self.store.products.get(f.sku) else "",
            "product_name": self.store.products.get(f.sku).name if self.store.products.get(f.sku) else f.sku,
            "store_name": self.store.stores.get(f.store_id).name if self.store.stores.get(f.store_id) else f.store_id,
        } for f in forecasts]

        df = pd.DataFrame(data)

        if group_by == "store":
            grouped = df.groupby(["store_id", "store_name", "forecast_date"])["forecast_qty"].sum().reset_index()
        elif group_by == "category":
            grouped = df.groupby(["category", "forecast_date"])["forecast_qty"].sum().reset_index()
        elif group_by == "sku":
            grouped = df.groupby(["store_id", "store_name", "sku", "product_name", "category", "forecast_date"])["forecast_qty"].sum().reset_index()
        else:
            grouped = df

        return grouped

    def get_7day_summary(self, forecasts: List[ForecastRecord]) -> pd.DataFrame:
        if not forecasts:
            return pd.DataFrame()

        data = [{
            "store_id": f.store_id,
            "sku": f.sku,
            "forecast_qty": f.forecast_qty,
        } for f in forecasts]

        df = pd.DataFrame(data)
        summary = df.groupby(["store_id", "sku"])["forecast_qty"].sum().reset_index()
        summary.columns = ["store_id", "sku", "forecast_7d"]
        return summary
