from typing import Dict, List, Tuple, Optional
from collections import defaultdict
import pandas as pd
import math
from datetime import datetime

from .storage import DataStore, SuggestionRecord, StockRecord, ReplenishmentStrategy, TransferSuggestion, PurchaseOrder, InboundRecord, ReviewSnapshot


RISK_HIGH = "高"
RISK_MEDIUM = "中"
RISK_LOW = "低"


class SuggestionEngine:
    def __init__(self, store: DataStore):
        self.store = store
        self.current_strategy: str = ""
        self.include_pending_orders: bool = True

    def _get_stock_map(self) -> Dict[Tuple[str, str], StockRecord]:
        result = {}
        for s in self.store.stocks:
            result[(s.store_id, s.sku)] = s
        return result

    def _get_pending_order_qty(self, store_id: str, sku: str) -> int:
        if not self.include_pending_orders:
            return 0
        return self.store.get_total_pending_qty(sku, store_id)

    def _get_available_stock(self, stock: StockRecord, store_id: str, sku: str) -> int:
        base = stock.current_stock + stock.in_transit
        pending = self._get_pending_order_qty(store_id, sku)
        return base + pending

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

    def _assess_risk(self, current_stock: int, in_transit: int, pending_order: int,
                     forecast_7d: float, safety_stock: int,
                     daily_forecast: List[Tuple[str, float]],
                     strategy: Optional[ReplenishmentStrategy] = None) -> str:
        available = current_stock + in_transit + pending_order
        avg_daily = forecast_7d / 7.0 if forecast_7d > 0 else 0

        if avg_daily <= 0:
            return RISK_LOW

        if strategy:
            effective_safety = safety_stock * strategy.get_effective_safety_factor(safety_stock)
            service_level = strategy.service_level
        else:
            effective_safety = safety_stock
            service_level = 0.95

        days_cover = available / avg_daily if avg_daily > 0 else 999

        cumulative_demand = 0.0
        stockout_day = None
        for day_idx, (_, daily_qty) in enumerate(daily_forecast):
            cumulative_demand += daily_qty
            if cumulative_demand > current_stock:
                stockout_day = day_idx + 1
                break

        if stockout_day is not None and stockout_day <= 2:
            return RISK_HIGH
        if stockout_day is not None and stockout_day <= 5:
            return RISK_MEDIUM
        if available < effective_safety:
            if service_level >= 0.98:
                return RISK_HIGH
            elif service_level >= 0.90:
                return RISK_MEDIUM
            else:
                return RISK_LOW
        if days_cover < 3:
            return RISK_HIGH
        if days_cover < 5:
            return RISK_MEDIUM
        return RISK_LOW

    def _check_stagnant(self, current_stock: int, forecast_7d: float,
                         turnover_days: int) -> bool:
        avg_daily = forecast_7d / 7.0 if forecast_7d > 0 else 0
        if avg_daily <= 0:
            return current_stock > 0
        days_cover = current_stock / avg_daily
        return days_cover > turnover_days * 2

    def _calculate_surplus(self, stock: StockRecord, forecast_7d: float,
                           store_id: str, sku: str) -> float:
        available = self._get_available_stock(stock, store_id, sku)
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
            surplus = self._calculate_surplus(stock, store_forecast, store_id, sku)
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

    def _calculate_suggested_qty(self, current_stock: int, in_transit: int, pending_order: int,
                                 forecast_7d: float, safety_stock: int,
                                 strategy: ReplenishmentStrategy) -> Tuple[int, str]:
        if forecast_7d <= 0:
            return 0, "无需求预测"

        min_order_qty = strategy.min_order_qty
        turnover_days = strategy.target_turnover_days
        lead_time_days = strategy.lead_time_days
        safety_factor = strategy.safety_stock_factor
        rounding = strategy.order_rounding
        service_level = strategy.service_level

        effective_safety_factor = strategy.get_effective_safety_factor()

        avg_daily = forecast_7d / 7.0
        cycle_stock = avg_daily * turnover_days
        lead_time_stock = avg_daily * lead_time_days
        safety_stock_calc = safety_stock * effective_safety_factor
        target_stock = cycle_stock + lead_time_stock + safety_stock_calc

        available = current_stock + in_transit + pending_order
        needed = max(0, target_stock - available)

        if needed <= 0:
            return 0, f"库存充足(可用{available}件，目标{int(target_stock)}件)"

        suggested = self._apply_order_rounding(needed, min_order_qty, rounding)

        if suggested < min_order_qty:
            suggested = min_order_qty

        reason_parts = []
        if current_stock < safety_stock:
            reason_parts.append(f"当前库存低于安全库存({safety_stock})")
        if in_transit == 0 and pending_order == 0:
            reason_parts.append("无在途/已下单补货")
        elif pending_order > 0:
            reason_parts.append(f"已下单{pending_order}件在途")
        reason_parts.append(f"7天预测需求约{forecast_7d:.1f}")
        reason_parts.append(f"策略:{strategy.name}(服务水平{service_level:.0%},周转{turnover_days}天,提前期{lead_time_days}天)")
        reason_parts.append(f"有效安全系数:{effective_safety_factor:.2f}x")

        return suggested, "；".join(reason_parts)

    def generate_transfer_suggestions(self, group_by: str = "region",
                                       allow_cross_region: Optional[bool] = None,
                                       max_cost_ratio: Optional[float] = None) -> List[TransferSuggestion]:
        stock_map = self._get_stock_map()
        forecast_map = self._get_forecast_7d_map()

        if allow_cross_region is None:
            allow_cross_region = bool(self.store.config.get("allow_cross_region_transfer", True))
        if max_cost_ratio is None:
            max_cost_ratio = float(self.store.config.get("max_transfer_cost_ratio", 0.1))

        all_stores = list(self.store.stores.keys())
        all_skus = set()
        for (_, sku) in stock_map.keys():
            all_skus.add(sku)

        transfers = []

        store_sku_map = defaultdict(list)
        for store_id, store in self.store.stores.items():
            key = ""
            if group_by == "region":
                key = store.region or "未分组"
            elif group_by == "store_type":
                key = store.store_type or "未分组"
            elif group_by == "group_name":
                key = store.group_name or "未分组"
            else:
                key = "全部"
            store_sku_map[key].append(store_id)

        group_global_surplus = defaultdict(lambda: defaultdict(float))
        group_global_deficit = defaultdict(lambda: defaultdict(list))

        for group_name, store_ids in store_sku_map.items():
            for sku in all_skus:
                deficit_stores = []
                surplus_stores = []

                for store_id in store_ids:
                    stock = stock_map.get((store_id, sku))
                    if not stock:
                        continue
                    forecast = forecast_map.get((store_id, sku), 0.0)
                    surplus = self._calculate_surplus(stock, forecast, store_id, sku)
                    avg_daily = forecast / 7.0 if forecast > 0 else 0
                    daily_forecast = self._get_daily_forecast_map().get((store_id, sku), [])

                    product = self.store.products.get(sku)
                    category = product.category if product else ""
                    strategy = self.store.get_strategy_for(category, sku)

                    if surplus < 0:
                        risk = self._assess_risk(stock.current_stock, stock.in_transit,
                                                 self._get_pending_order_qty(store_id, sku),
                                                 forecast, stock.safety_stock, daily_forecast, strategy)
                        deficit_info = {
                            "store_id": store_id,
                            "deficit": abs(surplus),
                            "risk": risk,
                            "current_stock": stock.current_stock,
                            "group": group_name,
                        }
                        deficit_stores.append(deficit_info)
                        group_global_deficit[sku][group_name].append(deficit_info)
                    elif surplus > avg_daily * 3:
                        surplus_info = {
                            "store_id": store_id,
                            "surplus": surplus,
                            "current_stock": stock.current_stock,
                            "group": group_name,
                        }
                        surplus_stores.append(surplus_info)
                        group_global_surplus[sku][group_name] += surplus

                deficit_stores.sort(key=lambda x: {"高": 0, "中": 1, "低": 2}.get(x["risk"], 3))

                for deficit in deficit_stores:
                    if deficit["deficit"] <= 0:
                        continue
                    remaining_needed = deficit["deficit"]
                    target_store = self.store.stores.get(deficit["store_id"])
                    target_region = target_store.region if target_store else ""

                    product = self.store.products.get(sku)
                    unit_cost = product.unit_cost if product else 0.0

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
                        if transfer_qty <= 0:
                            continue

                        source_store = self.store.stores.get(surplus["store_id"])
                        source_region = source_store.region if source_store else ""
                        is_same_region = source_region == target_region
                        is_cross_region = not is_same_region

                        if is_cross_region and not allow_cross_region:
                            continue

                        if is_cross_region and deficit["risk"] != "高":
                            continue

                        estimated_cost = self._estimate_transfer_cost(
                            surplus["store_id"], deficit["store_id"], transfer_qty
                        )

                        if unit_cost > 0 and is_cross_region:
                            cost_ratio = estimated_cost / (unit_cost * transfer_qty)
                            if cost_ratio > max_cost_ratio:
                                continue

                        priority = self._calculate_transfer_priority(
                            deficit["risk"], transfer_qty, is_same_region
                        )

                        reason_parts = []
                        if is_same_region:
                            reason_parts.append(f"同区域({source_region})调拨")
                        else:
                            reason_parts.append(f"跨区域({source_region}→{target_region})调拨")
                        reason_parts.append(f"{deficit['risk']}风险缺货{int(deficit['deficit'])}件")
                        reason_parts.append(f"{surplus['store_id']}富余{int(surplus['surplus'])}件")
                        if is_cross_region:
                            reason_parts.append(f"成本约{estimated_cost:.1f}元")

                        transfers.append(TransferSuggestion(
                            from_store_id=surplus["store_id"],
                            to_store_id=deficit["store_id"],
                            sku=sku,
                            transfer_qty=transfer_qty,
                            priority=priority,
                            reason="；".join(reason_parts),
                            estimated_cost=estimated_cost,
                            is_cross_region=is_cross_region,
                            same_region=is_same_region,
                        ))
                        surplus["surplus"] -= transfer_qty
                        remaining_needed -= transfer_qty
                        deficit["deficit"] = remaining_needed

        if allow_cross_region and group_by == "region":
            for sku in all_skus:
                sku_deficits = group_global_deficit.get(sku, {})
                sku_surpluses = group_global_surplus.get(sku, {})

                for group_name, deficits in sku_deficits.items():
                    for deficit in deficits:
                        if deficit["deficit"] <= 0 or deficit["risk"] != "高":
                            continue
                        remaining_needed = deficit["deficit"]
                        target_store = self.store.stores.get(deficit["store_id"])
                        target_region = target_store.region if target_store else ""
                        product = self.store.products.get(sku)
                        unit_cost = product.unit_cost if product else 0.0

                        cross_surplus_list = []
                        for other_group, total_surp in sku_surpluses.items():
                            if other_group == group_name or total_surp <= 0:
                                continue
                            for (store_id_check, sku_check), stock in stock_map.items():
                                if sku_check != sku or store_id_check in [d["store_id"] for d in deficits]:
                                    continue
                                s_store = self.store.stores.get(store_id_check)
                                s_region = s_store.region if s_store else ""
                                if s_region and s_region != target_region:
                                    f2 = forecast_map.get((store_id_check, sku), 0.0)
                                    s_surp = self._calculate_surplus(stock, f2, store_id_check, sku)
                                    if s_surp > 0:
                                        cross_surplus_list.append({
                                            "store_id": store_id_check,
                                            "surplus": s_surp,
                                            "region": s_region,
                                        })

                        cross_surplus_list.sort(key=lambda x: -x["surplus"])
                        for surplus in cross_surplus_list:
                            if surplus["surplus"] <= 0 or remaining_needed <= 0:
                                continue
                            transfer_qty = min(int(remaining_needed), int(surplus["surplus"]))
                            if transfer_qty <= 0:
                                continue
                            source_region = surplus["region"]
                            is_cross_region = (source_region != target_region)
                            if not is_cross_region:
                                continue

                            estimated_cost = self._estimate_transfer_cost(
                                surplus["store_id"], deficit["store_id"], transfer_qty
                            )

                            if unit_cost > 0:
                                cost_ratio = estimated_cost / (unit_cost * transfer_qty)
                                if cost_ratio > max_cost_ratio:
                                    continue

                            priority = self._calculate_transfer_priority(
                                deficit["risk"], transfer_qty, False
                            )

                            reason_parts = [f"跨区域候选({source_region}→{target_region})"]
                            reason_parts.append(f"{deficit['risk']}风险缺货{int(remaining_needed)}件")
                            reason_parts.append(f"{surplus['store_id']}富余{int(surplus['surplus'])}件")
                            reason_parts.append(f"预计成本{estimated_cost:.1f}元")
                            cost_ratio_disp = cost_ratio * 100 if unit_cost > 0 else 0
                            reason_parts.append(f"成本占货值{cost_ratio_disp:.1f}%")

                            transfers.append(TransferSuggestion(
                                from_store_id=surplus["store_id"],
                                to_store_id=deficit["store_id"],
                                sku=sku,
                                transfer_qty=transfer_qty,
                                priority=priority,
                                reason="；".join(reason_parts),
                                estimated_cost=estimated_cost,
                                is_cross_region=True,
                                same_region=False,
                            ))
                            surplus["surplus"] -= transfer_qty
                            remaining_needed -= transfer_qty
                            deficit["deficit"] = remaining_needed
                            if remaining_needed <= 0:
                                break

        transfers.sort(key=lambda x: (-x.priority, x.is_cross_region))
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
            pending_order = self._get_pending_order_qty(store_id, sku)

            risk_level = self._assess_risk(current_stock, in_transit, pending_order,
                                           forecast_7d, safety_stock, daily_forecast, strategy)

            stagnant = self._check_stagnant(current_stock, forecast_7d, strategy.target_turnover_days)

            transferable = self._find_transferable_stores(store_id, sku, forecast_7d)

            if stagnant:
                suggested_qty = 0
                reason = f"库存滞销：当前库存可售约{current_stock / (forecast_7d / 7.0):.0f}天" if forecast_7d > 0 else "库存滞销：无需求预测"
            else:
                suggested_qty, reason = self._calculate_suggested_qty(
                    current_stock, in_transit, pending_order, forecast_7d, safety_stock, strategy
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
                pending_order_qty=pending_order,
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
            row = {
                "门店ID": s.store_id,
                "门店名称": store.name if store else s.store_id,
                "区域": store.region if store else "",
                "门店类型": store.store_type if store else "",
                "商品SKU": s.sku,
                "商品名称": product.name if product else s.sku,
                "品类": product.category if product else "",
                "当前库存": s.current_stock,
                "在途数量": s.in_transit,
                "已下单未到": s.pending_order_qty,
                "安全库存": s.safety_stock,
                "7天预测需求": s.forecast_7d,
                "缺货风险等级": s.risk_level,
                "建议补货量": s.suggested_qty,
                "滞销提示": "是" if s.stagnant_warning else "否",
                "可调拨门店": ",".join(s.transferable_stores) if s.transferable_stores else "",
                "使用策略": s.strategy_name,
                "建议原因": s.reason,
            }
            if s.original_qty > 0 or s.budget_gap > 0:
                row["原始建议量"] = s.original_qty
                row["预算缺口(件)"] = s.budget_gap
                row["压缩原因"] = s.budget_reason
                row["全量预计成本(元)"] = s.estimated_cost
                row["实际分配成本(元)"] = s.allocated_cost
            data.append(row)

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
                "是否跨区域": "是" if t.is_cross_region else "否",
                "调出门店ID": t.from_store_id,
                "调出门店名称": from_store.name if from_store else t.from_store_id,
                "调入门店ID": t.to_store_id,
                "调入门店名称": to_store.name if to_store else t.to_store_id,
                "商品SKU": t.sku,
                "商品名称": product.name if product else t.sku,
                "调拨数量": t.transfer_qty,
                "预计调拨成本": round(t.estimated_cost, 2),
                "调拨原因": t.reason,
            })

        return pd.DataFrame(data)

    def _estimate_transfer_cost(self, from_store_id: str, to_store_id: str, qty: int) -> float:
        from_store = self.store.stores.get(from_store_id)
        to_store = self.store.stores.get(to_store_id)
        from_region = from_store.region if from_store else ""
        to_region = to_store.region if to_store else ""

        cost_config = self.store.get_transfer_cost(from_region, to_region)
        total_cost = cost_config.fixed_cost + cost_config.cost_per_unit * qty
        return total_cost

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
            total_pending = 0
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
                    pending = self._get_pending_order_qty(store_id, sku)
                    total_pending += pending
                    total_forecast += forecast
                    available = stock.current_stock + stock.in_transit + pending
                    if available < forecast:
                        deficit_count += 1
                    elif available > forecast * 1.5:
                        surplus_count += 1

                    product = self.store.products.get(sku)
                    category = product.category if product else ""
                    strategy = self.store.get_strategy_for(category, sku)

                    daily_forecast = self._get_daily_forecast_map().get((store_id, sku), [])
                    risk = self._assess_risk(stock.current_stock, stock.in_transit,
                                             pending, forecast, stock.safety_stock,
                                             daily_forecast, strategy)
                    if risk == RISK_HIGH:
                        high_risk_count += 1

            data.append({
                "分组": group_name,
                "门店数": len(store_ids),
                "总库存": total_stock,
                "总在途": total_in_transit,
                "已下单未到": total_pending,
                "7天总预测": round(total_forecast, 1),
                "缺货门店SKU数": deficit_count,
                "富余门店SKU数": surplus_count,
                "高风险数": high_risk_count,
            })

        return pd.DataFrame(data)

    def _is_promotion_sku(self, sku: str) -> bool:
        today = datetime.now().strftime("%Y-%m-%d")
        for promo in self.store.promotions:
            if promo.sku == "*" or promo.sku == sku:
                if promo.start_date <= today <= promo.end_date:
                    return True
        return False

    def _get_priority_score(self, s: SuggestionRecord) -> Tuple[int, int, int, int]:
        risk_priority = {"高": 0, "中": 1, "低": 2}.get(s.risk_level, 3)
        promo_bonus = 0 if self._is_promotion_sku(s.sku) else 1
        stagnant_penalty = 1 if s.stagnant_warning else 0
        qty_desc = -s.suggested_qty
        return (risk_priority, promo_bonus, stagnant_penalty, qty_desc)

    def allocate_budget(self, suggestions: List[SuggestionRecord],
                        budget_limit: float = 0,
                        supplier_filter: str = "",
                        category_filter: str = "",
                        category_priority: Optional[List[str]] = None) -> Dict[str, Any]:
        eligible = []
        for s in suggestions:
            product = self.store.products.get(s.sku)
            category = product.category if product else ""
            if category_filter and category != category_filter:
                continue
            eligible.append(s)

        if category_priority:
            def _category_rank(s):
                product = self.store.products.get(s.sku)
                cat = product.category if product else ""
                try:
                    return category_priority.index(cat)
                except ValueError:
                    return len(category_priority)
            eligible.sort(key=lambda s: (_category_rank(s), self._get_priority_score(s)))
        else:
            eligible.sort(key=lambda s: self._get_priority_score(s))

        result = {"total_cost": 0.0, "compressed_count": 0, "skipped_count": 0,
                  "budget_used": 0.0, "budget_limit": budget_limit, "gap_total": 0}

        for s in eligible:
            if s.suggested_qty <= 0:
                continue
            product = self.store.products.get(s.sku)
            unit_cost = product.unit_cost if product else 0.0
            s.original_qty = s.suggested_qty
            s.estimated_cost = round(s.suggested_qty * unit_cost, 2)

            if supplier_filter:
                supplier_sku_match = (product.strategy_id == supplier_filter) if product else False
                if not supplier_sku_match:
                    s.suggested_qty = 0
                    s.budget_gap = s.original_qty
                    s.budget_reason = f"供应商筛选排除(指定{supplier_filter})"
                    result["skipped_count"] += 1
                    result["gap_total"] += s.budget_gap
                    continue

            if budget_limit > 0:
                remaining_budget = budget_limit - result["budget_used"]
                if remaining_budget <= 0:
                    s.suggested_qty = 0
                    s.budget_gap = s.original_qty
                    s.budget_reason = "预算不足，全量压缩"
                    result["compressed_count"] += 1
                    result["gap_total"] += s.budget_gap
                    continue
                max_affordable_qty = int(remaining_budget // unit_cost) if unit_cost > 0 else s.original_qty
                if max_affordable_qty < s.original_qty:
                    product = self.store.products.get(s.sku)
                    min_qty = product.min_order_qty if product else 1
                    compressed_qty = (max_affordable_qty // min_qty) * min_qty if max_affordable_qty >= min_qty else 0
                    gap = s.original_qty - compressed_qty
                    if compressed_qty <= 0:
                        s.suggested_qty = 0
                        s.budget_gap = s.original_qty
                        s.budget_reason = f"预算不足，全部压缩；剩余预算{remaining_budget:.0f}元，需{s.estimated_cost:.0f}元"
                    else:
                        s.suggested_qty = compressed_qty
                        s.budget_gap = gap
                        s.budget_reason = f"预算压缩：{s.original_qty}→{compressed_qty}件，缺口{gap}件；剩余预算{remaining_budget:.0f}元，全量需{s.estimated_cost:.0f}元"
                    s.allocated_cost = round(s.suggested_qty * unit_cost, 2)
                    result["budget_used"] += s.allocated_cost
                    result["compressed_count"] += 1
                    result["gap_total"] += s.budget_gap
                    continue
            s.allocated_cost = s.estimated_cost
            result["budget_used"] += s.allocated_cost

        result["total_cost"] = result["budget_used"]
        return result

    def save_review_snapshot(self, strategy_id: str = "", strategy_name: str = "") -> ReviewSnapshot:
        snapshot_id = f"SNAP_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        snapshot_date = datetime.now().strftime("%Y-%m-%d")

        records = []
        risk_counts = {"高": 0, "中": 0, "低": 0}
        total_suggested = 0
        total_need_replenish = 0
        stagnant_count = 0
        total_cost = 0.0

        for s in self.store.suggestions:
            product = self.store.products.get(s.sku)
            unit_cost = product.unit_cost if product else 0.0
            records.append({
                "store_id": s.store_id,
                "sku": s.sku,
                "suggested_qty": s.suggested_qty,
                "original_qty": s.original_qty,
                "budget_gap": s.budget_gap,
                "risk_level": s.risk_level,
                "stagnant_warning": s.stagnant_warning,
                "forecast_7d": s.forecast_7d,
                "current_stock": s.current_stock,
                "pending_order_qty": s.pending_order_qty,
                "strategy_id": s.strategy_id,
                "strategy_name": s.strategy_name,
                "estimated_cost": round(s.suggested_qty * unit_cost, 2),
            })
            risk_counts[s.risk_level] = risk_counts.get(s.risk_level, 0) + 1
            total_suggested += s.suggested_qty
            if s.suggested_qty > 0:
                total_need_replenish += 1
            if s.stagnant_warning:
                stagnant_count += 1
            total_cost += s.suggested_qty * unit_cost

        summary = {
            "total_records": len(self.store.suggestions),
            "total_suggested_qty": total_suggested,
            "need_replenish_count": total_need_replenish,
            "stagnant_count": stagnant_count,
            "risk_high": risk_counts.get("高", 0),
            "risk_medium": risk_counts.get("中", 0),
            "risk_low": risk_counts.get("低", 0),
            "total_estimated_cost": round(total_cost, 2),
        }

        snap = ReviewSnapshot(
            snapshot_id=snapshot_id,
            snapshot_date=snapshot_date,
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            records=records,
            summary=summary,
        )
        self.store.add_review_snapshot(snap)
        self.store.save()
        return snap

    def generate_review_analysis(self, snapshot: Optional[ReviewSnapshot] = None) -> Dict[str, Any]:
        if snapshot is None:
            snapshot = self.store.get_latest_snapshot()
        if not snapshot:
            return {"error": "没有可用的复盘快照，请先运行 suggest 并保存快照"}

        snap_sku_store = {(r["store_id"], r["sku"]): r for r in snapshot.records}
        snapshot_date = snapshot.snapshot_date

        ordered_by_sku_store = defaultdict(int)
        for order in self.store.purchase_orders:
            if order.ordered_date >= snapshot_date:
                ordered_by_sku_store[(order.store_id, order.sku)] += order.qty

        arrived_by_sku_store = defaultdict(int)
        for inbound in self.store.inbound_records:
            if inbound.inbound_date >= snapshot_date:
                arrived_by_sku_store[(inbound.store_id, inbound.sku)] += inbound.qty

        sales_by_sku_store = defaultdict(int)
        for sale in self.store.sales:
            if sale.sale_date >= snapshot_date:
                sales_by_sku_store[(sale.store_id, sale.sku)] += sale.quantity

        stock_before_map = {}
        for r in snapshot.records:
            stock_before_map[(r["store_id"], r["sku"])] = r["current_stock"]

        stock_after_map = {(s.store_id, s.sku): s.current_stock for s in self.store.stocks}

        review_records = []
        total_suggested = 0
        total_ordered = 0
        total_arrived = 0
        total_sold = 0
        hit_count = 0
        overstock_count = 0
        stockout_improved = 0
        stockout_worse = 0

        for key, snap_rec in snap_sku_store.items():
            store_id, sku = key
            suggested = snap_rec["suggested_qty"]
            original_qty = snap_rec.get("original_qty", suggested)
            risk_before = snap_rec["risk_level"]
            stock_before = snap_rec["current_stock"]
            forecast_7d = snap_rec["forecast_7d"]

            ordered = ordered_by_sku_store.get(key, 0)
            arrived = arrived_by_sku_store.get(key, 0)
            sold = sales_by_sku_store.get(key, 0)
            stock_after = stock_after_map.get(key, stock_before)

            total_suggested += suggested
            total_ordered += ordered
            total_arrived += arrived
            total_sold += sold

            order_hit = ordered >= original_qty * 0.8 if original_qty > 0 else True
            if order_hit and suggested > 0:
                hit_count += 1

            if suggested == 0 and snap_rec.get("stagnant_warning", False):
                if ordered > 0:
                    overstock_count += 1

            stock_after_risk = "低"
            if forecast_7d > 0:
                avg_daily = forecast_7d / 7.0
                days_cover = stock_after / avg_daily if avg_daily > 0 else 999
                if days_cover < 3:
                    stock_after_risk = "高"
                elif days_cover < 5:
                    stock_after_risk = "中"

            if risk_before == "高" and stock_after_risk != "高":
                stockout_improved += 1
            elif risk_before != "高" and stock_after_risk == "高":
                stockout_worse += 1

            product = self.store.products.get(sku)
            store = self.store.stores.get(store_id)
            review_records.append({
                "门店ID": store_id,
                "门店名称": store.name if store else store_id,
                "商品SKU": sku,
                "商品名称": product.name if product else sku,
                "品类": product.category if product else "",
                "建议量": suggested,
                "原始建议量": original_qty,
                "实际下单": ordered,
                "下单命中率%": round(ordered / original_qty * 100, 1) if original_qty > 0 else 100.0,
                "实际到货": arrived,
                "到货完成率%": round(arrived / ordered * 100, 1) if ordered > 0 else 100.0,
                "后续实际销量": sold,
                "建议前库存": stock_before,
                "当前库存": stock_after,
                "建议前风险": risk_before,
                "当前风险": stock_after_risk,
                "建议7天预测": forecast_7d,
            })

        total_need = sum(1 for r in snapshot.records if r["suggested_qty"] > 0)
        hit_rate = round(hit_count / total_need * 100, 1) if total_need > 0 else 100.0
        improvement_rate = round(stockout_improved / max(1, snapshot.summary.get("risk_high", 1)) * 100, 1)

        summary = {
            "复盘快照ID": snapshot.snapshot_id,
            "快照日期": snapshot_date,
            "复盘记录数": len(review_records),
            "建议补货总件数": total_suggested,
            "实际下单总件数": total_ordered,
            "实际到货总件数": total_arrived,
            "后续实际销量": total_sold,
            "需补货SKU数": total_need,
            "下单命中数(≥80%)": hit_count,
            "下单命中率%": hit_rate,
            "高风险改善数": stockout_improved,
            "高风险新增数": stockout_worse,
            "缺货改善率%": improvement_rate,
            "滞销转积压数": overstock_count,
            "策略": snapshot.strategy_name or "默认",
        }

        return {
            "summary": summary,
            "records": review_records,
            "snapshot_id": snapshot.snapshot_id,
        }
