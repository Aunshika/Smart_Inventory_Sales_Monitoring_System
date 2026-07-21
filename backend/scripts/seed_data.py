import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymongo import UpdateOne


BACKEND_DIR = Path(__file__).resolve().parents[1]
DATASET_DIR = BACKEND_DIR / "datasets" / "api"
sys.path.insert(0, str(BACKEND_DIR))

from app.core.auth import hash_password  # noqa: E402
from app.db.database import (  # noqa: E402
    categories_collection,
    initialize_database,
    inventory_history_collection,
    products_collection,
    purchases_collection,
    sales_collection,
    suppliers_collection,
    database,
    users_collection,
)


SCHEMAS = {
    "locations": ["location_id", "location_name", "location_type", "city"],
    "categories": ["category_id", "category_name", "description"],
    "suppliers": [
        "supplier_id", "supplier_name", "email", "phone", "address"
    ],
    "products": [
        "product_id", "product_name", "quantity", "price", "unit_cost",
        "reorder_level", "category_id", "supplier_id", "location_id"
    ],
    "purchases": [
        "purchase_id", "product_id", "quantity", "unit_cost",
        "supplier_id", "transaction_id", "note", "location_id"
    ],
    "sales": [
        "sale_id", "transaction_id", "product_id", "quantity",
        "unit_price", "unit_cost", "discount_percent",
        "reorder_level", "region", "customer_type",
        "customer_name", "payment_method", "note", "location_id"
    ],
    "stock_in": ["product_id", "quantity", "note", "location_id"],
    "stock_out": ["product_id", "quantity", "note", "location_id"],
    "users": [
        "username", "email", "password", "confirm_password", "role", "location_id"
    ],
}


def load_csv(name):
    path = DATASET_DIR / f"{name}.csv"
    try:
        file = path.open(newline="", encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"Dataset not found: {path}") from exc

    with file:
        reader = csv.DictReader(file)
        if reader.fieldnames != SCHEMAS[name]:
            raise RuntimeError(
                f"{path.name} columns must exactly match: "
                f"{', '.join(SCHEMAS[name])}"
            )
        return list(reader)


def parse_positive_int(value, field, dataset, row_number):
    try:
        number = int(value)
    except ValueError as exc:
        raise RuntimeError(
            f"{dataset}.csv row {row_number}: {field} must be an integer"
        ) from exc
    if number <= 0:
        raise RuntimeError(
            f"{dataset}.csv row {row_number}: {field} must be positive"
        )
    return number


def dataset_time(index):
    return datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(
        hours=index
    )


def build_documents():
    raw = {name: load_csv(name) for name in SCHEMAS}
    manager_names = [
        row["username"]
        for row in raw["users"]
        if row["role"] == "Manager"
    ]
    staff_names = [
        row["username"]
        for row in raw["users"]
        if row["role"] == "Staff"
    ]
    if not manager_names or not staff_names:
        raise RuntimeError(
            "users.csv must contain at least one Manager and one Staff user"
        )

    manager_by_location = {
        row["location_id"]: row["username"]
        for row in raw["users"]
        if row["role"] == "Manager"
    }
    staff_by_location = {
        row["location_id"]: row["username"]
        for row in raw["users"]
        if row["role"] == "Staff"
    }

    locations = raw["locations"]
    categories = [
        {**row, "role": "dataset"}
        for row in raw["categories"]
    ]
    suppliers = [
        {**row, "role": "dataset"}
        for row in raw["suppliers"]
    ]
    products = []
    product_map = {}

    for index, row in enumerate(raw["products"], start=2):
        product = {
            **row,
            "quantity": parse_positive_int(
                row["quantity"], "quantity", "products", index
            ),
            "price": parse_positive_int(
                row["price"], "price", "products", index
            ),
            "unit_cost": parse_positive_int(
                row["unit_cost"], "unit_cost", "products", index
            ),
            "reorder_level": parse_positive_int(
                row["reorder_level"],
                "reorder_level",
                "products",
                index
            ),
        }
        products.append(product)
        product_map[product["product_id"]] = product

    category_ids = {row["category_id"] for row in categories}
    supplier_ids = {row["supplier_id"] for row in suppliers}
    location_ids = {row["location_id"] for row in locations}
    for product in products:
        if product["category_id"] not in category_ids:
            raise RuntimeError(
                f"{product['product_id']} references an unknown category"
            )
        if product["supplier_id"] not in supplier_ids:
            raise RuntimeError(
                f"{product['product_id']} references an unknown supplier"
            )
        if product["location_id"] not in location_ids:
            raise RuntimeError(
                f"{product['product_id']} references an unknown location"
            )

    purchases = []
    sales = []
    history = []

    for index, row in enumerate(raw["purchases"], start=1):
        manager_name = manager_by_location.get(
            row["location_id"], manager_names[(index - 1) % len(manager_names)]
        )
        product = product_map.get(row["product_id"])
        if not product:
            raise RuntimeError(
                f"{row['purchase_id']} references an unknown product"
            )
        quantity = parse_positive_int(
            row["quantity"], "quantity", "purchases", index + 1
        )
        unit_cost = parse_positive_int(
            row["unit_cost"], "unit_cost", "purchases", index + 1
        )
        created_at = dataset_time(index)
        previous_stock = product["quantity"]
        current_stock = previous_stock + quantity
        purchases.append({
            **row,
            "quantity": quantity,
            "unit_cost": unit_cost,
            "product_name": product["product_name"],
            "total_cost": quantity * unit_cost,
            "previous_stock": previous_stock,
            "current_stock": current_stock,
            "purchased_by": manager_name,
            "role": "Manager",
            "created_at": created_at,
            "location_id": row["location_id"],
        })
        history.append({
            "movement_id": f"MOV-PUR-{row['purchase_id']}",
            "product_id": product["product_id"],
            "product_name": product["product_name"],
            "movement_type": "Purchase",
            "quantity": quantity,
            "previous_stock": previous_stock,
            "current_stock": current_stock,
            "performed_by": manager_name,
            "role": "Manager",
            "created_at": created_at,
            "note": row["note"] or None,
            "location_id": row["location_id"],
        })

    sale_offset = len(purchases)
    for index, row in enumerate(raw["sales"], start=1):
        staff_name = staff_by_location.get(
            row["location_id"], staff_names[(index - 1) % len(staff_names)]
        )
        product = product_map.get(row["product_id"])
        if not product:
            raise RuntimeError(
                f"{row['sale_id']} references an unknown product"
            )
        quantity = parse_positive_int(
            row["quantity"], "quantity", "sales", index + 1
        )
        unit_price = parse_positive_int(
            row["unit_price"], "unit_price", "sales", index + 1
        )
        unit_cost = parse_positive_int(
            row["unit_cost"], "unit_cost", "sales", index + 1
        )
        discount_percent = int(row["discount_percent"])
        reorder_level = parse_positive_int(
            row["reorder_level"], "reorder_level", "sales", index + 1
        )
        created_at = dataset_time(sale_offset + index)
        previous_stock = product["quantity"] + quantity
        current_stock = product["quantity"]
        total_amount = round(
            quantity * unit_price * (1 - discount_percent / 100)
        )
        sales.append({
            **row,
            "quantity": quantity,
            "unit_price": unit_price,
            "unit_cost": unit_cost,
            "discount_percent": discount_percent,
            "reorder_level": reorder_level,
            "product_name": product["product_name"],
            "total_amount": total_amount,
            "cost_amount": quantity * unit_cost,
            "profit": total_amount - (quantity * unit_cost),
            "previous_stock": previous_stock,
            "current_stock": current_stock,
            "sold_by": staff_name,
            "role": "Staff",
            "created_at": created_at,
            "location_id": row["location_id"],
            "customer_name": row["customer_name"] or None,
            "note": row["note"] or None,
            "location_id": row["location_id"],
        })
        history.append({
            "movement_id": f"MOV-SALE-{row['sale_id']}",
            "product_id": product["product_id"],
            "product_name": product["product_name"],
            "movement_type": "Sale",
            "quantity": quantity,
            "previous_stock": previous_stock,
            "current_stock": current_stock,
            "performed_by": staff_name,
            "role": "Staff",
            "created_at": created_at,
            "note": row["note"] or None,
            "location_id": row["location_id"],
        })

    movement_offset = sale_offset + len(sales)
    for dataset_name, movement_type, role in [
        ("stock_in", "Stock In", "Manager"),
        ("stock_out", "Stock Out", "Staff"),
    ]:
        for index, row in enumerate(raw[dataset_name], start=1):
            operator_map = (
                manager_by_location
                if dataset_name == "stock_in"
                else staff_by_location
            )
            fallback_names = manager_names if dataset_name == "stock_in" else staff_names
            operator_name = operator_map.get(
                row["location_id"], fallback_names[(index - 1) % len(fallback_names)]
            )
            product = product_map.get(row["product_id"])
            if not product:
                raise RuntimeError(
                    f"{dataset_name}.csv references unknown product "
                    f"{row['product_id']}"
                )
            quantity = parse_positive_int(
                row["quantity"], "quantity", dataset_name, index + 1
            )
            previous_stock = product["quantity"]
            current_stock = (
                previous_stock + quantity
                if dataset_name == "stock_in"
                else max(0, previous_stock - quantity)
            )
            history.append({
                "movement_id": f"MOV-{dataset_name.upper()}-{index:05d}",
                "product_id": product["product_id"],
                "product_name": product["product_name"],
                "movement_type": movement_type,
                "quantity": quantity,
                "previous_stock": previous_stock,
                "current_stock": current_stock,
                "performed_by": operator_name,
                "role": role,
                "created_at": dataset_time(movement_offset + index),
                "note": row["note"] or None,
                "location_id": row["location_id"],
            })
        movement_offset += len(raw[dataset_name])

    users = []
    for row in raw["users"]:
        if row["password"] != row["confirm_password"]:
            raise RuntimeError(
                f"Passwords do not match for user {row['username']}"
            )
        users.append({
            "username": row["username"],
            "email": row["email"],
            "hashed_password": hash_password(row["password"]),
            "role": row["role"],
            "location_id": row["location_id"],
        })

    return {
        "locations": locations,
        "categories": categories,
        "suppliers": suppliers,
        "products": products,
        "purchases": purchases,
        "sales": sales,
        "inventory_history": history,
        "users": users,
    }


def upsert_records(collection, records, key_field):
    if not records:
        return 0
    collection.bulk_write([
        UpdateOne(
            {key_field: record[key_field]},
            {"$set": record},
            upsert=True,
        )
        for record in records
    ])
    return len(records)


def seed_dataset():
    data = build_documents()
    initialize_database()

    for category in data["categories"]:
        categories_collection.delete_many({
            "category_name": category["category_name"],
            "category_id": {"$ne": category["category_id"]},
        })
    for supplier in data["suppliers"]:
        suppliers_collection.delete_many({
            "supplier_name": supplier["supplier_name"],
            "supplier_id": {"$ne": supplier["supplier_id"]},
        })
    users_collection.delete_many({
        "username": {"$in": ["dataset_manager", "dataset_staff"]}
    })

    targets = [
        ("locations", database["locations"], "location_id"),
        ("categories", categories_collection, "category_id"),
        ("suppliers", suppliers_collection, "supplier_id"),
        ("products", products_collection, "product_id"),
        ("purchases", purchases_collection, "purchase_id"),
        ("sales", sales_collection, "sale_id"),
        ("inventory_history", inventory_history_collection, "movement_id"),
        ("users", users_collection, "username"),
    ]
    return {
        name: upsert_records(collection, data[name], key)
        for name, collection, key in targets
    }


if __name__ == "__main__":
    counts = seed_dataset()
    print("API dataset seed complete:")
    for name, count in counts.items():
        print(f"- {name}: {count}")

