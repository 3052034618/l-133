from typing import Dict, List, Tuple
from collections import defaultdict
import pandas as pd

from .storage import DataStore, SuggestionRecord, StockRecord


RISK_HIGH = "高"
RISK_MEDIUM = "中"
RISK_LOW = "低"


class SuggestionEngine:
    def __init__(self, store: DataStore):
        self.store = store

    def _get_stock_map(self) -> Dict[Tuple[str, str], StockRecord]:
        result = {}
        for s in self.store.stocks:
            result[(s.store_id, s.sku)] = s
        return result

    def _get_forecast_7d_map(self) -> Dict[Tuple[str, str], float]:
        result = defaultdict(float)
        for f in self.store.forecasts:
            result[(f.store_id, f.sku)] += f.forecast_qty
        return dict(result)

    def _get_daily_forecast_map(self) -> Dict[Tuple[str, str], List[Tuple[str, float]]]:
        result = defaultdict(list)
        for f in self.store.forecasts:
            result[(f.store_id, f.sku)].append((f.forecast_date, f.forecast_qty))
        for key in result:
            result[key].sort(key=lambda x: x[0])
        return dict(result)

    def _assess_risk(self, current_stock: int, in_transit: int,
                     forecast_7d: float, safety_stock: int,
                     daily_forecast: List[Tuple[str, float]]) -> str:
        available = current_stock + in_transit
        avg_daily = forecast_7d / 7.0 if forecast_7d > 0 else 0

        if avg_daily <= 0:
            return RISK_LOW

        days_cover = available / avg_daily if avg_daily > 0 else 999

        cumulative_demand = 0.0
        stockout_day = None
        for day_idx, (_, daily_qty) in enumerate(daily_forecast):
            cumulative_demand += daily_qty
            if cumulative_demand > current_stock:
                stockout_day = day_idx + 1
                break

        if stockout_day is not None and stockout_day <= 3:
            return RISK_HIGH
        if stockout_day is not None and stockout_day <= 7:
            return RISK_MEDIUM
        if available < safety_stock:
            return RISK_MEDIUM
        if days_cover < 3:
            return RISK_HIGH
        if days_cover < 7:
            return RISK_MEDIUM
        return RISK_LOW

    def _check_stagnant(self, current_stock: int, forecast_7d: float,
                         turnover_days: int) -> bool:
        avg_daily = forecast_7d / 7.0 if forecast_7d > 0 else 0
        if avg_daily <= 0:
            return current_stock > 0
        days_cover = current_stock / avg_daily
        return days_cover > turnover_days * 2

    def _find_transferable_stores(self, target_store_id: str, sku: str,
                                   forecast_7d: float) -> List[str]:
        stock_map = self._get_stock_map()
        forecast_map = self._get_forecast_7d_map()

        transferable = []
        avg_daily_needed = forecast_7d / 7.0 if forecast_7d > 0 else 0

        for (store_id, s_sku), stock in stock_map.items():
            if s_sku != sku or store_id == target_store_id:
                continue
            store_forecast = forecast_map.get((store_id, sku), 0.0)
            surplus = stock.current_stock + stock.in_transit - store_forecast
            if surplus > avg_daily_needed * 2:
                transferable.append(store_id)

        return transferable

    def _calculate_suggested_qty(self, current_stock: int, in_transit: int,
                                 forecast_7d: float, safety_stock: int,
                                 min_order_qty: int, turnover_days: int) -> Tuple[int, str]:
        if forecast_7d <= 0:
            return 0, "无需求预测"

        avg_daily = forecast_7d / 7.0
        target_stock = avg_daily * turnover_days + safety_stock
        available = current_stock + in_transit
        needed = max(0, target_stock - available)

        if needed <= 0:
            return 0, "库存充足"

        if min_order_qty > 1:
            suggested = ((int(needed) + min_order_qty - 1) // min_order_qty) * min_order_qty
        else:
            suggested = int(needed) + (1 if needed - int(needed) > 0.5 else 0)

        if suggested < min_order_qty:
            suggested = min_order_qty

        reason_parts = []
        if current_stock < safety_stock:
            reason_parts.append(f"当前库存低于安全库存({safety_stock})")
        if in_transit == 0:
            reason_parts.append("无在途补货")
        reason_parts.append(f"7天预测需求约{forecast_7d:.1f}")

        return suggested, "；".join(reason_parts)

    def generate_suggestions(self) -> List[SuggestionRecord]:
        stock_map = self._get_stock_map()
        forecast_7d_map = self._get_forecast_7d_map()
        daily_forecast_map = self._get_daily_forecast_map()

        all_pairs = set(stock_map.keys()) | set(forecast_7d_map.keys())

        suggestions = []

        for store_id, sku in sorted(all_pairs):
            stock = stock_map.get((store_id, sku))
            forecast_7d = forecast_7d_map.get((store_id, sku), 0.0)
            daily_forecast = daily_forecast_map.get((store_id, sku), [])

            current_stock = stock.current_stock if stock else 0
            in_transit = stock.in_transit if stock else 0
            safety_stock = stock.safety_stock if stock else 0

            product = self.store.products.get(sku)
            min_order_qty = product.min_order_qty if product else int(self.store.config.get("default_min_order_qty", 1))
            turnover_days = product.turnover_days if product else int(self.store.config.get("default_turnover_days", 7))

            risk_level = self._assess_risk(current_stock, in_transit, forecast_7d,
                                           safety_stock, daily_forecast)

            stagnant = self._check_stagnant(current_stock, forecast_7d, turnover_days)

            transferable = self._find_transferable_stores(store_id, sku, forecast_7d)

            if stagnant:
                suggested_qty = 0
                reason = f"库存滞销：当前库存可售约{current_stock / (forecast_7d / 7.0):.0f}天" if forecast_7d > 0 else "库存滞销：无需求预测"
            else:
                suggested_qty, reason = self._calculate_suggested_qty(
                    current_stock, in_transit, forecast_7d, safety_stock,
                    min_order_qty, turnover_days
                )

            suggestions.append(SuggestionRecord(
                store_id=store_id,
                sku=sku,
                suggested_qty=suggested_qty,
                risk_level=risk_level,
                stagnant_warning=stagnant,
                transferable_stores=transferable,
                reason=reason,
                forecast_7d=round(forecast_7d, 2),
                current_stock=current_stock,
                in_transit=in_transit,
                safety_stock=safety_stock,
            ))

        self.store.suggestions = suggestions
        self.store.save()
        return suggestions

    def suggestions_to_dataframe(self, suggestions: List[SuggestionRecord]) -> pd.DataFrame:
        if not suggestions:
            return pd.DataFrame()

        data = []
        for s in suggestions:
            product = self.store.products.get(s.sku)
            store = self.store.stores.get(s.store_id)
            data.append({
                "门店ID": s.store_id,
                "门店名称": store.name if store else s.store_id,
                "商品SKU": s.sku,
                "商品名称": product.name if product else s.sku,
                "品类": product.category if product else "",
                "当前库存": s.current_stock,
                "在途数量": s.in_transit,
                "安全库存": s.safety_stock,
                "7天预测需求": s.forecast_7d,
                "缺货风险等级": s.risk_level,
                "建议补货量": s.suggested_qty,
                "滞销提示": "是" if s.stagnant_warning else "否",
                "可调拨门店": ",".join(s.transferable_stores) if s.transferable_stores else "",
                "建议原因": s.reason,
            })

        return pd.DataFrame(data)

    def filter_suggestions(self, suggestions: List[SuggestionRecord],
                           risk_filter: str = None,
                           need_replenish_only: bool = False,
                           stagnant_only: bool = False) -> List[SuggestionRecord]:
        result = suggestions
        if risk_filter:
            result = [s for s in result if s.risk_level == risk_filter]
        if need_replenish_only:
            result = [s for s in result if s.suggested_qty > 0]
        if stagnant_only:
            result = [s for s in result if s.stagnant_warning]
        return result
