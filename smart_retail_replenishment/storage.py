import os
import json
from datetime import datetime, date
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict, field
import pandas as pd
import numpy as np


def _convert_numpy_types(obj):
    if isinstance(obj, dict):
        return {k: _convert_numpy_types(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_numpy_types(item) for item in obj]
    elif isinstance(obj, (np.bool_, np.bool)):
        return bool(obj)
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.str_):
        return str(obj)
    else:
        return obj


DATA_DIR = os.path.join(os.getcwd(), ".retail_data")


def ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)


def _data_path(filename: str) -> str:
    ensure_data_dir()
    return os.path.join(DATA_DIR, filename)


@dataclass
class ReplenishmentStrategy:
    """补货策略模板"""
    strategy_id: str
    name: str
    service_level: float = 0.95
    lead_time_days: int = 3
    target_turnover_days: int = 7
    min_order_qty: int = 1
    order_rounding: str = "ceiling"
    safety_stock_factor: float = 1.5
    description: str = ""
    scope_category: str = ""
    scope_sku: str = ""

    def matches(self, category: str, sku: str) -> bool:
        sku_val = self.scope_sku if isinstance(self.scope_sku, str) else ""
        cat_val = self.scope_category if isinstance(self.scope_category, str) else ""
        if sku_val and sku_val == sku:
            return True
        if cat_val and cat_val == category:
            return True
        if not cat_val and not sku_val:
            return True
        return False

    def get_safety_factor_from_service_level(self) -> float:
        service_level_factors = {
            0.50: 0.0, 0.60: 0.25, 0.70: 0.52, 0.75: 0.67,
            0.80: 0.84, 0.85: 1.04, 0.90: 1.28, 0.95: 1.65,
            0.97: 1.88, 0.98: 2.05, 0.99: 2.33, 0.995: 2.58,
            0.999: 3.09,
        }
        closest = min(service_level_factors.keys(), key=lambda x: abs(x - self.service_level))
        z_score = service_level_factors[closest]
        return z_score

    def get_effective_safety_factor(self, base_safety_stock: int = 0) -> float:
        z_factor = self.get_safety_factor_from_service_level()
        base_factor = self.safety_stock_factor
        return max(0.5, base_factor * (0.7 + z_factor * 0.3))


@dataclass
class TransferSuggestion:
    """调拨建议记录"""
    from_store_id: str
    to_store_id: str
    sku: str
    transfer_qty: int
    priority: int
    reason: str
    estimated_cost: float = 0.0
    is_cross_region: bool = False
    same_region: bool = True


@dataclass
class Product:
    sku: str
    name: str
    category: str = ""
    unit: str = "件"
    min_order_qty: int = 1
    turnover_days: int = 7
    unit_cost: float = 0.0
    strategy_id: str = ""


@dataclass
class Store:
    store_id: str
    name: str
    region: str = ""
    store_type: str = ""
    group_name: str = ""
    address: str = ""


@dataclass
class SalesRecord:
    store_id: str
    sku: str
    sale_date: str
    quantity: int
    amount: float = 0.0


@dataclass
class StockRecord:
    store_id: str
    sku: str
    current_stock: int
    in_transit: int = 0
    safety_stock: int = 0


@dataclass
class PromotionRecord:
    sku: str
    promo_type: str
    start_date: str
    end_date: str
    uplift_factor: float = 1.5
    store_id: str = ""
    is_all_stores: bool = False


@dataclass
class ForecastRecord:
    store_id: str
    sku: str
    forecast_date: str
    forecast_qty: float
    baseline_qty: float = 0.0
    promo_increment: float = 0.0
    holiday_increment: float = 0.0


@dataclass
class SuggestionRecord:
    store_id: str
    sku: str
    suggested_qty: int
    risk_level: str
    stagnant_warning: bool
    transferable_stores: List[str]
    reason: str
    forecast_7d: float
    current_stock: int
    in_transit: int
    safety_stock: int
    pending_order_qty: int = 0
    strategy_id: str = ""
    strategy_name: str = ""


@dataclass
class DataQualityIssue:
    """数据质量问题记录"""
    issue_type: str
    severity: str
    table_name: str
    record_key: str
    description: str


@dataclass
class PurchaseOrder:
    """采购订单记录"""
    order_id: str
    sku: str
    qty: int
    status: str = "已下单"
    ordered_date: str = ""
    expected_arrival_date: str = ""
    arrived_qty: int = 0
    unit_cost: float = 0.0
    supplier: str = ""
    store_id: str = ""
    remark: str = ""


@dataclass
class TransferCost:
    """调拨成本配置"""
    from_region: str
    to_region: str
    cost_per_unit: float = 1.0
    fixed_cost: float = 0.0
    lead_time_days: int = 1


class DataStore:
    def __init__(self):
        self.products: Dict[str, Product] = {}
        self.stores: Dict[str, Store] = {}
        self.sales: List[SalesRecord] = []
        self.stocks: List[StockRecord] = []
        self.promotions: List[PromotionRecord] = []
        self.forecasts: List[ForecastRecord] = []
        self.suggestions: List[SuggestionRecord] = []
        self.strategies: Dict[str, ReplenishmentStrategy] = {}
        self.transfer_suggestions: List[TransferSuggestion] = []
        self.data_quality_issues: List[DataQualityIssue] = []
        self.purchase_orders: List[PurchaseOrder] = []
        self.transfer_costs: List[TransferCost] = []
        self.config: Dict[str, Any] = {
            "holiday_factor": 1.3,
            "default_min_order_qty": 1,
            "default_turnover_days": 7,
            "forecast_days": 7,
            "default_lead_time_days": 3,
            "default_service_level": 0.95,
            "last_forecast_group_by": "sku",
            "allow_cross_region_transfer": True,
            "max_transfer_cost_ratio": 0.1,
            "default_transfer_cost_per_unit": 2.0,
        }

    def save(self):
        ensure_data_dir()
        data = {
            "products": [asdict(p) for p in self.products.values()],
            "stores": [asdict(s) for s in self.stores.values()],
            "sales": [asdict(s) for s in self.sales],
            "stocks": [asdict(s) for s in self.stocks],
            "promotions": [asdict(p) for p in self.promotions],
            "forecasts": [asdict(f) for f in self.forecasts],
            "suggestions": [asdict(s) for s in self.suggestions],
            "strategies": [asdict(s) for s in self.strategies.values()],
            "transfer_suggestions": [asdict(s) for s in self.transfer_suggestions],
            "purchase_orders": [asdict(o) for o in self.purchase_orders],
            "transfer_costs": [asdict(c) for c in self.transfer_costs],
            "config": self.config,
        }
        data = _convert_numpy_types(data)
        with open(_data_path("datastore.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self):
        path = _data_path("datastore.json")
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.products = {p["sku"]: Product(**p) for p in data.get("products", [])}
        self.stores = {s["store_id"]: Store(**s) for s in data.get("stores", [])}
        self.sales = [SalesRecord(**s) for s in data.get("sales", [])]
        self.stocks = [StockRecord(**s) for s in data.get("stocks", [])]
        self.promotions = [PromotionRecord(**p) for p in data.get("promotions", [])]
        self.forecasts = [ForecastRecord(**f) for f in data.get("forecasts", [])]
        self.suggestions = [SuggestionRecord(**s) for s in data.get("suggestions", [])]
        self.strategies = {s["strategy_id"]: ReplenishmentStrategy(**s) for s in data.get("strategies", [])}
        self.transfer_suggestions = [TransferSuggestion(**s) for s in data.get("transfer_suggestions", [])]
        self.purchase_orders = [PurchaseOrder(**o) for o in data.get("purchase_orders", [])]
        self.transfer_costs = [TransferCost(**c) for c in data.get("transfer_costs", [])]
        self.config.update(data.get("config", {}))

    def clear_sales(self):
        self.sales = []

    def clear_stock_data(self):
        self.stocks = []
        self.promotions = []

    def add_strategy(self, strategy: ReplenishmentStrategy):
        self.strategies[strategy.strategy_id] = strategy

    def remove_strategy(self, strategy_id: str):
        if strategy_id in self.strategies:
            del self.strategies[strategy_id]

    def add_purchase_order(self, order: PurchaseOrder):
        self.purchase_orders.append(order)

    def update_purchase_order_status(self, order_id: str, status: str, arrived_qty: Optional[int] = None):
        for order in self.purchase_orders:
            if order.order_id == order_id:
                order.status = status
                if arrived_qty is not None:
                    order.arrived_qty = arrived_qty
                break

    def get_pending_orders_for_sku(self, sku: str, store_id: str = "") -> List[PurchaseOrder]:
        result = []
        for order in self.purchase_orders:
            if order.sku != sku:
                continue
            if store_id and order.store_id != store_id:
                continue
            if order.status in ("已下单", "部分到货"):
                result.append(order)
        return result

    def get_total_pending_qty(self, sku: str, store_id: str = "") -> int:
        orders = self.get_pending_orders_for_sku(sku, store_id)
        total = 0
        for order in orders:
            total += (order.qty - order.arrived_qty)
        return total

    def add_transfer_cost(self, cost: TransferCost):
        self.transfer_costs.append(cost)

    def get_transfer_cost(self, from_region: str, to_region: str) -> TransferCost:
        for cost in self.transfer_costs:
            if cost.from_region == from_region and cost.to_region == to_region:
                return cost
        default_cost = float(self.config.get("default_transfer_cost_per_unit", 2.0))
        is_same = from_region == to_region
        if is_same:
            return TransferCost(from_region=from_region, to_region=to_region, cost_per_unit=default_cost * 0.5, fixed_cost=0.0, lead_time_days=1)
        else:
            return TransferCost(from_region=from_region, to_region=to_region, cost_per_unit=default_cost, fixed_cost=5.0, lead_time_days=2)

    def get_strategy_for(self, category: str, sku: str) -> Optional[ReplenishmentStrategy]:
        product = self.products.get(sku)
        if product and product.strategy_id and product.strategy_id in self.strategies:
            return self.strategies[product.strategy_id]
        sku_specific = [s for s in self.strategies.values() if (s.scope_sku and isinstance(s.scope_sku, str) and s.scope_sku.strip() == sku)]
        if sku_specific:
            return sku_specific[0]
        category_specific = [s for s in self.strategies.values() if (s.scope_category and isinstance(s.scope_category, str) and s.scope_category.strip() == category)]
        if category_specific:
            return category_specific[0]
        default = [s for s in self.strategies.values() if not (s.scope_category and isinstance(s.scope_category, str) and s.scope_category.strip()) and not (s.scope_sku and isinstance(s.scope_sku, str) and s.scope_sku.strip())]
        if default:
            return default[0]
        return None

    def add_products_from_dataframe(self, df: pd.DataFrame):
        for _, row in df.iterrows():
            sku = str(row.get("sku", "")).strip()
            if not sku:
                continue
            product = Product(
                sku=sku,
                name=str(row.get("name", sku)),
                category=str(row.get("category", "")),
                unit=str(row.get("unit", "件")),
                min_order_qty=int(row.get("min_order_qty", self.config["default_min_order_qty"])),
                turnover_days=int(row.get("turnover_days", self.config["default_turnover_days"])),
                unit_cost=float(row.get("unit_cost", 0.0)),
                strategy_id=str(row.get("strategy_id", "")),
            )
            self.products[sku] = product

    def add_stores_from_dataframe(self, df: pd.DataFrame):
        for _, row in df.iterrows():
            store_id = str(row.get("store_id", "")).strip()
            if not store_id:
                continue
            store = Store(
                store_id=store_id,
                name=str(row.get("name", store_id)),
                region=str(row.get("region", "")),
                store_type=str(row.get("store_type", "")),
                group_name=str(row.get("group_name", "")),
                address=str(row.get("address", "")),
            )
            self.stores[store_id] = store

    def add_sales_from_dataframe(self, df: pd.DataFrame):
        for _, row in df.iterrows():
            record = SalesRecord(
                store_id=str(row.get("store_id", "")).strip(),
                sku=str(row.get("sku", "")).strip(),
                sale_date=str(row.get("sale_date", "")).strip(),
                quantity=int(row.get("quantity", 0)),
                amount=float(row.get("amount", 0.0)),
            )
            if record.store_id and record.sku and record.sale_date:
                self.sales.append(record)

    def add_stocks_from_dataframe(self, df: pd.DataFrame):
        for _, row in df.iterrows():
            record = StockRecord(
                store_id=str(row.get("store_id", "")).strip(),
                sku=str(row.get("sku", "")).strip(),
                current_stock=int(row.get("current_stock", 0)),
                in_transit=int(row.get("in_transit", 0)),
                safety_stock=int(row.get("safety_stock", 0)),
            )
            if record.store_id and record.sku:
                self.stocks.append(record)

    def add_promotions_from_dataframe(self, df: pd.DataFrame):
        for _, row in df.iterrows():
            store_id_val = str(row.get("store_id", "")).strip()
            if not store_id_val or store_id_val.lower() == "nan":
                store_id_val = ""
            is_all = not store_id_val
            record = PromotionRecord(
                sku=str(row.get("sku", "")).strip(),
                promo_type=str(row.get("promo_type", "")).strip(),
                start_date=str(row.get("start_date", "")).strip(),
                end_date=str(row.get("end_date", "")).strip(),
                uplift_factor=float(row.get("uplift_factor", 1.5)),
                store_id=store_id_val,
                is_all_stores=is_all,
            )
            if record.sku and record.start_date and record.end_date:
                self.promotions.append(record)


_store_instance: Optional[DataStore] = None


def get_store() -> DataStore:
    global _store_instance
    if _store_instance is None:
        _store_instance = DataStore()
        _store_instance.load()
    return _store_instance
