from typing import Dict, List, Tuple
from collections import defaultdict
import pandas as pd
import math

from .storage import DataStore, SuggestionRecord, StockRecord, ReplenishmentStrategy, TransferSuggestion


RISK_HIGH = "高"
RISK_MEDIUM = "中"
RISK_LOW = "低"


class SuggestionEngine:
    def __init__(self, store: DataStore):
        self.store = store
        self.current_strategy: str = ""

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

    def _get_group_stores(self, group_by: str) -> Dict[str, List[str]]:
        groups = defaultdict(list)
        for store_id, store in self.store.stores.items():
            if group_by == "region":
                key = store.region or "未分组"
            elif group_by == "store_type":
                key = store.store_type or "未分组"
            elif group_by == "group_name":
                key = store.group_name or "未分组"
            else:
                key = "全部"
            groups[key].append(store_id)
        return dict(groups)

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

    def _calculate_surplus(self, stock: StockRecord, forecast_7d: float) -> float:
        available = stock.current_stock + stock.in_transit
        return available - forecast_7d

    def _find_transferable_stores(self, target_store_id: str, sku: str,
                                   forecast_7d: float) -> List[str]:
        stock_map = self._get_stock_map()
        forecast_map = self._get_forecast_7d_map()

        transferable = []
        avg_daily_needed = forecast_7d / 7.0 if forecast_7d > 0 else 0

        target_store = self.store.stores.get(target_store_id)
        target_region = target_store.region if target_store else ""

        for (store_id, s_sku), stock in stock_map.items():
            if s_sku != sku or store_id == target_store_id:
                continue
            store_forecast = forecast_map.get((store_id, sku), 0.0)
            surplus = self._calculate_surplus(stock, store_forecast)
            if surplus > avg_daily_needed * 2:
                source_store = self.store.stores.get(store_id)
                if source_store and source_store.region == target_region:
                    transferable.insert(0, store_id)
                else:
                    transferable.append(store_id)

        return transferable

    def _calculate_transfer_priority(self, risk_level: str,
                                      transfer_qty: int,
                                      is_same_region: bool) -> int:
        base_priority = {"高": 10, "中": 5, "低": 1}.get(risk_level, 1)
        region_bonus = 3 if is_same_region else 0
        qty_factor = min(transfer_qty // 10, 5)
        return base_priority + region_bonus + qty_factor

    def _apply_order_rounding(self, qty: float, min_order_qty: int,
                               rounding: str) -> int:
        if rounding == "floor":
            return (int(qty) // min_order_qty) * min_order_qty
        elif rounding == "round":
            return round(qty / min_order_qty) * min_order_qty
        else:
            return math.ceil(qty / min_order_qty) * min_order_qty

    def _calculate_suggested_qty(self, current_stock: int, in_transit: int,
                                 forecast_7d: float, safety_stock: int,
                                 strategy: ReplenishmentStrategy) -> Tuple[int, str]:
        if forecast_7d <= 0:
            return 0, "无需求预测"

        min_order_qty = strategy.min_order_qty
        turnover_days = strategy.target_turnover_days
        lead_time_days = strategy.lead_time_days
        safety_factor = strategy.safety_stock_factor
        rounding = strategy.order_rounding

        avg_daily = forecast_7d / 7.0
        cycle_stock = avg_daily * turnover_days
        lead_time_stock = avg_daily * lead_time_days
        safety_stock_calc = safety_stock * safety_factor
        target_stock = cycle_stock + lead_time_stock + safety_stock_calc

        available = current_stock + in_transit
        needed = max(0, target_stock - available)

        if needed <= 0:
            return 0, "库存充足"

        suggested = self._apply_order_rounding(needed, min_order_qty, rounding)

        if suggested < min_order_qty:
            suggested = min_order_qty

        reason_parts = []
        if current_stock < safety_stock:
            reason_parts.append(f"当前库存低于安全库存({safety_stock})")
        if in_transit == 0:
            reason_parts.append("无在途补货")
        reason_parts.append(f"7天预测需求约{forecast_7d:.1f}")
        reason_parts.append(f"策略:{strategy.name}(周转{turnover_days}天,提前期{lead_time_days}天)")

        return suggested, "；".join(reason_parts)

    def generate_transfer_suggestions(self, group_by: str = "region") -> List[TransferSuggestion]:
        stock_map = self._get_stock_map()
        forecast_map = self._get_forecast_7d_map()
        groups = self._get_group_stores(group_by)

        transfers = []
        all_skus = set()
        for (_, sku) in stock_map.keys():
            all_skus.add(sku)

        for group_name, store_ids in groups.items():
            for sku in all_skus:
                deficit_stores = []
                surplus_stores = []

                for store_id in store_ids:
                    stock = stock_map.get((store_id, sku))
                    if not stock:
                        continue
                    forecast = forecast_map.get((store_id, sku), 0.0)
                    surplus = self._calculate_surplus(stock, forecast)
                    avg_daily = forecast / 7.0 if forecast > 0 else 0
                    daily_forecast = self._get_daily_forecast_map().get((store_id, sku), [])

                    if surplus < 0:
                        risk = self._assess_risk(stock.current_stock, stock.in_transit,
                                                 forecast, stock.safety_stock, daily_forecast)
                        deficit_stores.append({
                            "store_id": store_id,
                            "deficit": abs(surplus),
                            "risk": risk,
                            "current_stock": stock.current_stock,
                        })
                    elif surplus > avg_daily * 3:
                        surplus_stores.append({
                            "store_id": store_id,
                            "surplus": surplus,
                            "current_stock": stock.current_stock,
                        })

                deficit_stores.sort(key=lambda x: {"高": 0, "中": 1, "低": 2}.get(x["risk"], 3))

                for deficit in deficit_stores:
                    if deficit["deficit"] <= 0:
                        continue
                    remaining_needed = deficit["deficit"]
                    target_region = self.store.stores.get(deficit["store_id"]).region if self.store.stores.get(deficit["store_id"]) else ""

                    sorted_surplus = sorted(
                        surplus_stores,
                        key=lambda x: (
                            0 if (self.store.stores.get(x["store_id"]).region if self.store.stores.get(x["store_id"]) else "") == target_region else 1,
                            -x["surplus"]
                        )
                    )

                    for surplus in sorted_surplus:
                        if surplus["surplus"] <= 0 or remaining_needed <= 0:
                            continue
                        transfer_qty = min(int(remaining_needed), int(surplus["surplus"]))
                        if transfer_qty > 0:
                            is_same_region = (
                                (self.store.stores.get(surplus["store_id"]).region if self.store.stores.get(surplus["store_id"]) else "") == target_region
                            )
                            priority = self._calculate_transfer_priority(
                                deficit["risk"], transfer_qty, is_same_region
                            )
                            transfers.append(TransferSuggestion(
                                from_store_id=surplus["store_id"],
                                to_store_id=deficit["store_id"],
                                sku=sku,
                                transfer_qty=transfer_qty,
                                priority=priority,
                                reason=f"{group_name}内调拨：{deficit['risk']}风险缺货{int(remaining_needed)}件，{surplus['store_id']}富余{int(surplus['surplus'])}件"
                            ))
                            surplus["surplus"] -= transfer_qty
                            remaining_needed -= transfer_qty

        transfers.sort(key=lambda x: -x.priority)
        self.store.transfer_suggestions = transfers
        self.store.save()
        return transfers

    def generate_suggestions(self, strategy_id: Optional[str] = None) -> List[SuggestionRecord]:
        stock_map = self._get_stock_map()
        forecast_7d_map = self._get_forecast_7d_map()
        daily_forecast_map = self._get_daily_forecast_map()

        all_pairs = set(stock_map.keys()) | set(forecast_7d_map.keys())

        suggestions = []
        used_strategy_name = "默认策略"
        used_strategy_id = ""

        for store_id, sku in sorted(all_pairs):
            product = self.store.products.get(sku)
            category = product.category if product else ""

            if strategy_id and strategy_id in self.store.strategies:
                strategy = self.store.strategies[strategy_id]
                used_strategy_name = strategy.name
                used_strategy_id = strategy.strategy_id
            else:
                strategy = self.store.get_strategy_for(category, sku)
                if strategy:
                    used_strategy_name = strategy.name
                    used_strategy_id = strategy.strategy_id
                else:
                    strategy = ReplenishmentStrategy(
                        strategy_id="DEFAULT",
                        name="默认策略",
                        service_level=float(self.store.config.get("default_service_level", 0.95)),
                        lead_time_days=int(self.store.config.get("default_lead_time_days", 3)),
                        target_turnover_days=int(self.store.config.get("default_turnover_days", 7)),
                        min_order_qty=product.min_order_qty if product else int(self.store.config.get("default_min_order_qty", 1)),
                    )
                    used_strategy_name = "默认策略"
                    used_strategy_id = "DEFAULT"

            self.current_strategy = used_strategy_name

            stock = stock_map.get((store_id, sku))
            forecast_7d = forecast_7d_map.get((store_id, sku), 0.0)
            daily_forecast = daily_forecast_map.get((store_id, sku), [])

            current_stock = stock.current_stock if stock else 0
            in_transit = stock.in_transit if stock else 0
            safety_stock = stock.safety_stock if stock else 0

            risk_level = self._assess_risk(current_stock, in_transit, forecast_7d,
                                           safety_stock, daily_forecast)

            stagnant = self._check_stagnant(current_stock, forecast_7d, strategy.target_turnover_days)

            transferable = self._find_transferable_stores(store_id, sku, forecast_7d)

            if stagnant:
                suggested_qty = 0
                reason = f"库存滞销：当前库存可售约{current_stock / (forecast_7d / 7.0):.0f}天" if forecast_7d > 0 else "库存滞销：无需求预测"
            else:
                suggested_qty, reason = self._calculate_suggested_qty(
                    current_stock, in_transit, forecast_7d, safety_stock, strategy
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
                strategy_id=used_strategy_id,
                strategy_name=used_strategy_name,
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
                "区域": store.region if store else "",
                "门店类型": store.store_type if store else "",
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
                "使用策略": s.strategy_name,
                "建议原因": s.reason,
            })

        return pd.DataFrame(data)

    def transfers_to_dataframe(self, transfers: List[TransferSuggestion]) -> pd.DataFrame:
        if not transfers:
            return pd.DataFrame()

        data = []
        for t in transfers:
            from_store = self.store.stores.get(t.from_store_id)
            to_store = self.store.stores.get(t.to_store_id)
            product = self.store.products.get(t.sku)
            data.append({
                "优先级": t.priority,
                "调出门店ID": t.from_store_id,
                "调出门店名称": from_store.name if from_store else t.from_store_id,
                "调入门店ID": t.to_store_id,
                "调入门店名称": to_store.name if to_store else t.to_store_id,
                "商品SKU": t.sku,
                "商品名称": product.name if product else t.sku,
                "调拨数量": t.transfer_qty,
                "调拨原因": t.reason,
            })

        return pd.DataFrame(data)

    def filter_suggestions(self, suggestions: List[SuggestionRecord],
                           risk_filter: str = None,
                           need_replenish_only: bool = False,
                           stagnant_only: bool = False,
                           store_id: str = None,
                           sku: str = None,
                           category: str = None,
                           region: str = None) -> List[SuggestionRecord]:
        result = suggestions
        if risk_filter:
            result = [s for s in result if s.risk_level == risk_filter]
        if need_replenish_only:
            result = [s for s in result if s.suggested_qty > 0]
        if stagnant_only:
            result = [s for s in result if s.stagnant_warning]
        if store_id:
            result = [s for s in result if s.store_id == store_id]
        if sku:
            result = [s for s in result if s.sku == sku]
        if category:
            result = [s for s in result if self.store.products.get(s.sku) and self.store.products.get(s.sku).category == category]
        if region:
            result = [s for s in result if self.store.stores.get(s.store_id) and self.store.stores.get(s.store_id).region == region]
        return result

    def get_group_analysis(self, group_by: str = "region") -> pd.DataFrame:
        groups = self._get_group_stores(group_by)
        stock_map = self._get_stock_map()
        forecast_map = self._get_forecast_7d_map()

        data = []
        for group_name, store_ids in groups.items():
            total_stock = 0
            total_in_transit = 0
            total_forecast = 0
            deficit_count = 0
            surplus_count = 0
            high_risk_count = 0

            for store_id in store_ids:
                for (s_store_id, sku), stock in stock_map.items():
                    if s_store_id != store_id:
                        continue
                    forecast = forecast_map.get((store_id, sku), 0.0)
                    total_stock += stock.current_stock
                    total_in_transit += stock.in_transit
                    total_forecast += forecast
                    available = stock.current_stock + stock.in_transit
                    if available < forecast:
                        deficit_count += 1
                    elif available > forecast * 1.5:
                        surplus_count += 1

                    daily_forecast = self._get_daily_forecast_map().get((store_id, sku), [])
                    risk = self._assess_risk(stock.current_stock, stock.in_transit,
                                             forecast, stock.safety_stock, daily_forecast)
                    if risk == RISK_HIGH:
                        high_risk_count += 1

            data.append({
                "分组": group_name,
                "门店数": len(store_ids),
                "总库存": total_stock,
                "总在途": total_in_transit,
                "7天总预测": round(total_forecast, 1),
                "缺货门店SKU数": deficit_count,
                "富余门店SKU数": surplus_count,
                "高风险数": high_risk_count,
            })

        return pd.DataFrame(data)
