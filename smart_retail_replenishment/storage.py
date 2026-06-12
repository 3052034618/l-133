import os
import json
from datetime import datetime, date
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict, field
import pandas as pd


DATA_DIR = os.path.join(os.getcwd(), ".retail_data")


def ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)


def _data_path(filename: str) -> str:
    ensure_data_dir()
    return os.path.join(DATA_DIR, filename)


@dataclass
class Product:
    sku: str
    name: str
    category: str = ""
    unit: str = "件"
    min_order_qty: int = 1
    turnover_days: int = 7
    unit_cost: float = 0.0


@dataclass
class Store:
    store_id: str
    name: str
    region: str = ""
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


@dataclass
class ForecastRecord:
    store_id: str
    sku: str
    forecast_date: str
    forecast_qty: float


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


class DataStore:
    def __init__(self):
        self.products: Dict[str, Product] = {}
        self.stores: Dict[str, Store] = {}
        self.sales: List[SalesRecord] = []
        self.stocks: List[StockRecord] = []
        self.promotions: List[PromotionRecord] = []
        self.forecasts: List[ForecastRecord] = []
        self.suggestions: List[SuggestionRecord] = []
        self.config: Dict[str, Any] = {
            "holiday_factor": 1.3,
            "default_min_order_qty": 1,
            "default_turnover_days": 7,
            "forecast_days": 7,
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
            "config": self.config,
        }
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
        self.config.update(data.get("config", {}))

    def clear_sales(self):
        self.sales = []

    def clear_stock_data(self):
        self.stocks = []
        self.promotions = []

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
            record = PromotionRecord(
                sku=str(row.get("sku", "")).strip(),
                promo_type=str(row.get("promo_type", "")).strip(),
                start_date=str(row.get("start_date", "")).strip(),
                end_date=str(row.get("end_date", "")).strip(),
                uplift_factor=float(row.get("uplift_factor", 1.5)),
                store_id=str(row.get("store_id", "")).strip(),
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
