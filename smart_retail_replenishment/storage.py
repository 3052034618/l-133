import os
import json
from datetime import datetime, date
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict
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
    original_qty: int = 0
    budget_gap: int = 0
    budget_reason: str = ""
    allocated_cost: float = 0.0
    estimated_cost: float = 0.0
    budget_pool: str = ""
    pool_used: float = 0.0
    pool_limit: float = 0.0
    deferred: bool = False


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


@dataclass
class InboundRecord:
    """入库流水记录"""
    inbound_id: str
    order_id: str
    sku: str
    store_id: str
    qty: int
    inbound_date: str
    unit_cost: float = 0.0
    supplier: str = ""
    remark: str = ""


@dataclass
class TransferOrder:
    """调拨单执行台账"""
    transfer_id: str
    from_store_id: str
    to_store_id: str
    sku: str
    qty: int
    status: str = "已创建"
    created_date: str = ""
    in_transit_date: str = ""
    received_date: str = ""
    received_qty: int = 0
    unit_cost: float = 0.0
    estimated_cost: float = 0.0
    actual_cost: float = 0.0
    remark: str = ""
    source_suggestion_idx: int = -1


@dataclass
class ReviewSnapshot:
    """复盘快照：保存 suggest 结果，用于后续对比分析"""
    snapshot_id: str
    snapshot_date: str
    snapshot_time: str
    strategy_id: str
    strategy_name: str
    records: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


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
        self.inbound_records: List[InboundRecord] = []
        self.review_snapshots: List[ReviewSnapshot] = []
        self.transfer_orders: List[TransferOrder] = []
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
            "inbound_records": [asdict(i) for i in self.inbound_records],
            "review_snapshots": [asdict(r) for r in self.review_snapshots],
            "transfer_orders": [asdict(t) for t in self.transfer_orders],
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
        self.inbound_records = [InboundRecord(**i) for i in data.get("inbound_records", [])]
        raw_snaps = data.get("review_snapshots", [])
        for r in raw_snaps:
            if "snapshot_time" not in r:
                r["snapshot_time"] = r.get("snapshot_date", "")
        self.review_snapshots = [ReviewSnapshot(**r) for r in raw_snaps]
        self.transfer_orders = [TransferOrder(**t) for t in data.get("transfer_orders", [])]
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
        self.upsert_purchase_order(order)

    def upsert_purchase_order(self, order: PurchaseOrder) -> str:
        """同订单号存在则更新原单，不存在则新增。返回 'updated' 或 'inserted'。"""
        for idx, existing in enumerate(self.purchase_orders):
            if existing.order_id == order.order_id:
                old_arrived = existing.arrived_qty
                existing.sku = order.sku or existing.sku
                existing.store_id = order.store_id or existing.store_id
                existing.supplier = order.supplier or existing.supplier
                existing.unit_cost = order.unit_cost if order.unit_cost > 0 else existing.unit_cost
                existing.ordered_date = order.ordered_date or existing.ordered_date
                existing.expected_arrival_date = order.expected_arrival_date or existing.expected_arrival_date
                existing.remark = order.remark or existing.remark
                if order.qty > 0:
                    existing.qty = order.qty
                if order.status and order.status != existing.status:
                    existing.status = order.status
                    if order.status == "已到货":
                        existing.arrived_qty = existing.qty
                    elif order.status == "已取消":
                        existing.arrived_qty = min(existing.arrived_qty, existing.qty)
                        existing.qty = existing.arrived_qty
                if order.arrived_qty is not None and order.arrived_qty > 0 and order.arrived_qty != old_arrived:
                    existing.arrived_qty = min(order.arrived_qty, existing.qty)
                    if existing.arrived_qty >= existing.qty and existing.status != "已取消":
                        existing.status = "已到货"
                    elif existing.arrived_qty > 0 and existing.status == "已下单":
                        existing.status = "部分到货"
                return "updated"
        self.purchase_orders.append(order)
        return "inserted"

    def update_purchase_order_status(self, order_id: str, status: str, arrived_qty: Optional[int] = None) -> Tuple[Optional[PurchaseOrder], int]:
        """更新PO状态并返回 (订单, 新增到货量)"""
        for order in self.purchase_orders:
            if order.order_id == order_id:
                old_arrived = order.arrived_qty
                order.status = status
                if status == "已到货":
                    order.arrived_qty = order.qty
                elif status == "已取消":
                    order.arrived_qty = min(order.arrived_qty, order.qty)
                    order.qty = order.arrived_qty
                if arrived_qty is not None:
                    order.arrived_qty = min(arrived_qty, order.qty)
                    if order.arrived_qty >= order.qty and order.status != "已取消":
                        order.status = "已到货"
                    elif order.arrived_qty > 0 and order.status == "已下单":
                        order.status = "部分到货"
                new_arrived = order.arrived_qty - old_arrived
                return order, new_arrived
        return None, 0

    def find_purchase_order(self, order_id: str) -> Optional[PurchaseOrder]:
        for o in self.purchase_orders:
            if o.order_id == order_id:
                return o
        return None

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

    def add_inbound_record(self, inbound: InboundRecord):
        self.inbound_records.append(inbound)

    def get_inbound_records(self, sku: str = "", store_id: str = "") -> List[InboundRecord]:
        result = []
        for rec in self.inbound_records:
            if sku and rec.sku != sku:
                continue
            if store_id and rec.store_id != store_id:
                continue
            result.append(rec)
        result.sort(key=lambda x: x.inbound_date, reverse=True)
        return result

    def add_stock_qty(self, store_id: str, sku: str, qty: int):
        for stock in self.stocks:
            if stock.store_id == store_id and stock.sku == sku:
                stock.current_stock += qty
                return
        self.stocks.append(StockRecord(store_id=store_id, sku=sku, current_stock=qty))

    def add_review_snapshot(self, snapshot: ReviewSnapshot):
        self.review_snapshots.append(snapshot)

    def get_latest_snapshot(self) -> Optional[ReviewSnapshot]:
        if not self.review_snapshots:
            return None
        sorted_snaps = sorted(self.review_snapshots, key=lambda x: x.snapshot_date, reverse=True)
        return sorted_snaps[0]

    def get_sales_by_sku_store(self, start_date: str, end_date: str) -> Dict[Tuple[str, str], int]:
        result = defaultdict(int)
        for sale in self.sales:
            if sale.sale_date < start_date or sale.sale_date > end_date:
                continue
            result[(sale.store_id, sale.sku)] += sale.quantity
        return dict(result)

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

    def get_stock_by_key(self, store_id: str, sku: str) -> Optional[StockRecord]:
        for s in self.stocks:
            if s.store_id == store_id and s.sku == sku:
                return s
        return None

    def add_stock_in_transit(self, store_id: str, sku: str, qty: int):
        stock = self.get_stock_by_key(store_id, sku)
        if stock:
            stock.in_transit += qty
            if stock.in_transit < 0:
                stock.in_transit = 0
        else:
            self.stocks.append(StockRecord(
                store_id=store_id, sku=sku, current_stock=0, in_transit=max(0, qty), safety_stock=0
            ))

    def add_transfer_order(self, order: TransferOrder):
        self.transfer_orders.append(order)

    def find_transfer_order(self, transfer_id: str) -> Optional[TransferOrder]:
        for t in self.transfer_orders:
            if t.transfer_id == transfer_id:
                return t
        return None

    def get_transfer_orders(self, from_store: str = "", to_store: str = "", sku: str = "", status: str = "") -> List[TransferOrder]:
        result = []
        for t in self.transfer_orders:
            if from_store and t.from_store_id != from_store:
                continue
            if to_store and t.to_store_id != to_store:
                continue
            if sku and t.sku != sku:
                continue
            if status and t.status != status:
                continue
            result.append(t)
        result.sort(key=lambda x: x.created_date, reverse=True)
        return result

    def get_total_transfer_in_transit(self, sku: str, store_id: str = "") -> int:
        total = 0
        for t in self.transfer_orders:
            if t.status not in ("已创建", "在途"):
                continue
            if t.sku != sku:
                continue
            pending = t.qty - t.received_qty
            if pending <= 0:
                continue
            if store_id and t.to_store_id != store_id:
                continue
            total += pending
        return total

    def get_total_transfer_pending_out(self, sku: str, store_id: str = "") -> int:
        total = 0
        for t in self.transfer_orders:
            if t.status not in ("已创建", "在途"):
                continue
            if t.sku != sku:
                continue
            pending = t.qty - t.received_qty
            if pending <= 0:
                continue
            if store_id and t.from_store_id != store_id:
                continue
            total += pending
        return total


_store_instance: Optional[DataStore] = None


def get_store() -> DataStore:
    global _store_instance
    if _store_instance is None:
        _store_instance = DataStore()
        _store_instance.load()
    return _store_instance
