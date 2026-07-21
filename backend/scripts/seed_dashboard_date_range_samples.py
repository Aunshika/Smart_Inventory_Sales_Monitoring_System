"""Seed small, idempotent dashboard date-range transactions for local testing.

This script creates a handful of sales and purchases across current IST periods so
Dashboard filters visibly change in Docker/local development. It upserts by stable
IDs and does not delete or modify existing business data.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import (  # noqa: E402
    products_collection,
    purchases_collection,
    sales_collection,
    suppliers_collection,
)

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - Windows fallback without tzdata
    IST = timezone(timedelta(hours=5, minutes=30))


def utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def as_float(value, fallback=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def get_seed_product():
    product = products_collection.find_one(
        {"status": {"$ne": "Inactive"}},
        sort=[("product_id", 1)],
    )
    if not product:
        raise RuntimeError("No products found. Seed products before dashboard date samples.")
    return product


def get_seed_supplier(product):
    supplier_id = product.get("supplier_id")
    supplier = suppliers_collection.find_one({"supplier_id": supplier_id}) if supplier_id else None
    if supplier:
        return supplier
    return suppliers_collection.find_one({}, sort=[("supplier_id", 1)]) or {}


def document_metadata(product, supplier):
    return {
        "warehouse_id": product.get("warehouse_id") or "WH001",
        "warehouse_name": product.get("warehouse_name") or "Tirupati Central Warehouse",
        "location": product.get("location") or product.get("warehouse_name") or "Tirupati",
        "category_id": product.get("category_id"),
        "category": product.get("category") or product.get("category_name"),
        "supplier_id": supplier.get("supplier_id") or product.get("supplier_id"),
        "supplier": supplier.get("supplier_name") or product.get("supplier") or "Seed Supplier",
    }


def upsert_sale(product, metadata, sale_id, created_ist, quantity, unit_price):
    total_amount = round(quantity * unit_price, 2)
    created_at = utc_naive(created_ist)
    sales_collection.update_one(
        {"sale_id": sale_id},
        {
            "$set": {
                "sale_id": sale_id,
                "transaction_id": sale_id,
                "product_id": product["product_id"],
                "product_name": product.get("product_name"),
                "quantity": quantity,
                "unit_price": unit_price,
                "unit_cost": as_float(product.get("unit_cost"), 0),
                "total_amount": total_amount,
                "payment_method": "UPI",
                "customer_name": "Dashboard Filter Customer",
                "customer_phone": "9999999999",
                "sold_by": "date_filter_seed",
                "created_by": "date_filter_seed",
                "role": "Admin",
                "status": "Completed",
                "date": created_ist.date().isoformat(),
                "sale_date": created_ist.date().isoformat(),
                "created_at": created_at,
                "updated_at": created_at,
                **metadata,
            }
        },
        upsert=True,
    )
    return total_amount


def upsert_purchase(product, metadata, purchase_id, created_ist, quantity, unit_cost):
    total_cost = round(quantity * unit_cost, 2)
    created_at = utc_naive(created_ist)
    purchases_collection.update_one(
        {"purchase_id": purchase_id},
        {
            "$set": {
                "purchase_id": purchase_id,
                "transaction_id": purchase_id,
                "product_id": product["product_id"],
                "product_name": product.get("product_name"),
                "quantity": quantity,
                "unit_cost": unit_cost,
                "total_cost": total_cost,
                "previous_stock": product.get("quantity", 0),
                "current_stock": product.get("quantity", 0),
                "purchased_by": "date_filter_seed",
                "created_by": "date_filter_seed",
                "role": "Admin",
                "status": "Completed",
                "date": created_ist.date().isoformat(),
                "purchase_date": created_ist.date().isoformat(),
                "created_at": created_at,
                "updated_at": created_at,
                **metadata,
            }
        },
        upsert=True,
    )
    return total_cost


def main():
    product = get_seed_product()
    supplier = get_seed_supplier(product)
    metadata = document_metadata(product, supplier)
    now = datetime.now(IST)
    today = now.replace(hour=11, minute=15, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=10, minute=30, second=0, microsecond=0)

    samples = [
        ("TODAY", today, 2, 510.0, 3, 210.0),
        ("LAST7", today - timedelta(days=3), 3, 620.0, 4, 240.0),
        ("MONTH", month_start + timedelta(days=1), 4, 730.0, 5, 260.0),
        ("LAST30", today - timedelta(days=24), 5, 840.0, 6, 280.0),
        ("OLDER", today - timedelta(days=45), 6, 950.0, 7, 300.0),
    ]

    sales_total = 0.0
    purchases_total = 0.0
    for label, created_ist, sale_qty, sale_price, purchase_qty, purchase_cost in samples:
        sales_total += upsert_sale(
            product,
            metadata,
            f"DF-SALE-{label}",
            created_ist,
            sale_qty,
            sale_price,
        )
        purchases_total += upsert_purchase(
            product,
            metadata,
            f"DF-PURCHASE-{label}",
            created_ist,
            purchase_qty,
            purchase_cost,
        )

    print("Dashboard date-range seed complete")
    print("Seed product:", product.get("product_id"), product.get("product_name"))
    print("Sales upserted:", len(samples), "total", round(sales_total, 2))
    print("Purchases upserted:", len(samples), "total", round(purchases_total, 2))


if __name__ == "__main__":
    main()