import os
from pathlib import Path
from urllib.parse import quote_plus, unquote_plus

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError

try:
    import certifi
except ImportError:
    certifi = None


BASE_DIR = Path(__file__).resolve().parents[3]
load_dotenv(BASE_DIR / ".env")

MONGODB_URI = os.getenv("MONGODB_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME", "Smartinventory")

if not MONGODB_URI:
    raise RuntimeError(
        "MONGODB_URI is not configured. Add it to your .env file."
    )


def normalize_mongodb_uri(uri):
    if "://" not in uri or "@" not in uri:
        return uri

    scheme, remainder = uri.split("://", 1)
    credentials, host_and_options = remainder.rsplit("@", 1)

    if ":" not in credentials:
        return uri

    username, password = credentials.split(":", 1)

    safe_username = quote_plus(
        unquote_plus(username)
    )

    safe_password = quote_plus(
        unquote_plus(password)
    )

    return (
        f"{scheme}://"
        f"{safe_username}:{safe_password}"
        f"@{host_and_options}"
    )


def mongo_client_options(uri):
    options = {
        "serverSelectionTimeoutMS": 5000,
        "connectTimeoutMS": 5000,
        "socketTimeoutMS": 30000,
    }

    is_atlas_uri = uri.startswith("mongodb+srv://")
    explicitly_uses_tls = "tls=true" in uri.lower() or "ssl=true" in uri.lower()

    if certifi and (is_atlas_uri or explicitly_uses_tls):
        options["tlsCAFile"] = certifi.where()

    return options


normalized_mongodb_uri = normalize_mongodb_uri(MONGODB_URI)

client = MongoClient(
    normalized_mongodb_uri,
    **mongo_client_options(normalized_mongodb_uri)
)

database = client[DATABASE_NAME]

users_collection = database["users"]
products_collection = database["products"]
categories_collection = database["categories"]
suppliers_collection = database["suppliers"]
inventory_history_collection = database["inventory_history"]
purchases_collection = database["purchases"]
sales_collection = database["sales"]
locations_collection = database["locations"]
restock_queue_collection = database["restock_queue"]
system_settings_collection = database["system_settings"]
warehouses_collection = database["warehouses"]
customers_collection = database["customers"]
stock_movements_collection = database["stock_movements"]
sales_items_collection = database["sales_items"]
purchase_items_collection = database["purchase_items"]
low_stock_alerts_collection = database["low_stock_alerts"]
notifications_collection = database["notifications"]
activity_logs_collection = database["activity_logs"]
returns_collection = database["returns"]
damaged_stock_collection = database["damaged_stock"]
warehouse_inventory_collection = database["warehouse_inventory"]


def ping_database():
    client.admin.command("ping")
    return True


def initialize_database():
    try:
        ping_database()

        users_collection.create_index("username", unique=True)
        users_collection.create_index("email", unique=True)
        users_collection.create_index("google_id", unique=True, sparse=True)
        users_collection.create_index("password_reset_token_hash", sparse=True)
        users_collection.create_index("location_id")
        users_collection.create_index("warehouse_id")
        users_collection.create_index("user_id", unique=True, sparse=True)
        users_collection.create_index("role")
        users_collection.create_index("warehouse_name")

        locations_collection.create_index("location_id", unique=True)
        locations_collection.create_index("location_name", unique=True)
        locations_collection.create_index("warehouse_id", unique=True, sparse=True)
        locations_collection.create_index("warehouse_key", unique=True, sparse=True)

        products_collection.create_index("location_id")
        products_collection.create_index("warehouse_id")
        products_collection.create_index("product_name")
        products_collection.create_index("product_id", unique=True)
        products_collection.create_index("category_id")
        products_collection.create_index("supplier_id")
        products_collection.create_index("barcode_value", sparse=True)
        products_collection.create_index("qr_code_value", sparse=True)
        products_collection.create_index("quantity")
        products_collection.create_index("status")
        products_collection.create_index([("location_id", 1), ("product_id", 1)])
        products_collection.create_index([("location_id", 1), ("quantity", 1)])
        products_collection.create_index([("location_id", 1), ("category_id", 1)])

        categories_collection.create_index("category_name", unique=True)

        suppliers_collection.create_index("email", unique=True)
        suppliers_collection.create_index("supplier_id", unique=True, sparse=True)
        suppliers_collection.create_index("supplier_name")
        suppliers_collection.create_index("location_id")
        suppliers_collection.create_index("warehouse_id")

        inventory_history_collection.create_index("product_id")
        inventory_history_collection.create_index("warehouse_id")
        inventory_history_collection.create_index("created_at")
        inventory_history_collection.create_index("movement_type")
        inventory_history_collection.create_index("performed_by")
        inventory_history_collection.create_index("movement_id", unique=True, sparse=True)
        inventory_history_collection.create_index([("location_id", 1), ("created_at", -1)])

        purchases_collection.create_index("location_id")
        purchases_collection.create_index("warehouse_id")
        purchases_collection.create_index("purchase_id", unique=True, sparse=True)
        purchases_collection.create_index("product_id")
        purchases_collection.create_index("supplier_id")
        purchases_collection.create_index("created_at")
        purchases_collection.create_index("status")
        purchases_collection.create_index([("location_id", 1), ("status", 1)])
        purchases_collection.create_index([("location_id", 1), ("status", 1), ("product_id", 1)])
        purchases_collection.create_index([("location_id", 1), ("created_at", -1)])

        sales_collection.create_index("location_id")
        sales_collection.create_index("warehouse_id")
        sales_collection.create_index("sale_id", unique=True, sparse=True)
        sales_collection.create_index("product_id")
        sales_collection.create_index("sold_by")
        sales_collection.create_index("created_at")
        sales_collection.create_index([("location_id", 1), ("created_at", -1)])
        sales_collection.create_index([("location_id", 1), ("category_id", 1)])

        restock_queue_collection.create_index("location_id")
        restock_queue_collection.create_index("warehouse_id")
        restock_queue_collection.create_index(
            [("product_id", 1), ("location_id", 1)],
            unique=True
        )
        restock_queue_collection.create_index([("location_id", 1), ("updated_at", -1)])
        restock_queue_collection.create_index([("warehouse_id", 1), ("updated_at", -1)])
        restock_queue_collection.create_index([("product_id", 1), ("warehouse_id", 1)])
        system_settings_collection.create_index("key", unique=True)

        warehouses_collection.create_index("warehouse_id", unique=True)
        warehouses_collection.create_index("warehouse_name", unique=True)
        warehouses_collection.create_index("location_id", sparse=True)

        warehouse_inventory_collection.create_index("inventory_id", unique=True)
        warehouse_inventory_collection.create_index("product_id")
        warehouse_inventory_collection.create_index("warehouse_id")
        warehouse_inventory_collection.create_index("quantity")
        warehouse_inventory_collection.create_index("reorder_level")
        warehouse_inventory_collection.create_index([("warehouse_id", 1), ("product_id", 1)], unique=True)
        warehouse_inventory_collection.create_index([("warehouse_id", 1), ("quantity", 1)])
        warehouse_inventory_collection.create_index([("warehouse_id", 1), ("reorder_level", 1)])
        warehouse_inventory_collection.create_index("last_updated")

        customers_collection.create_index("customer_id", unique=True)
        customers_collection.create_index("email", unique=True, sparse=True)
        customers_collection.create_index("phone", sparse=True)
        customers_collection.create_index("location_id")

        stock_movements_collection.create_index("movement_id", unique=True)
        stock_movements_collection.create_index("product_id")
        stock_movements_collection.create_index("warehouse_id")
        stock_movements_collection.create_index("movement_type")
        stock_movements_collection.create_index("created_at")
        stock_movements_collection.create_index([("location_id", 1), ("created_at", -1)])

        sales_items_collection.create_index("sales_item_id", unique=True)
        sales_items_collection.create_index("sale_id")
        sales_items_collection.create_index("product_id")
        sales_items_collection.create_index("category_id")
        sales_items_collection.create_index("warehouse_id")
        sales_items_collection.create_index("created_at")
        sales_items_collection.create_index([("location_id", 1), ("created_at", -1)])

        purchase_items_collection.create_index("purchase_item_id", unique=True)
        purchase_items_collection.create_index("purchase_id")
        purchase_items_collection.create_index("product_id")
        purchase_items_collection.create_index("supplier_id")
        purchase_items_collection.create_index("warehouse_id")
        purchase_items_collection.create_index("created_at")
        purchase_items_collection.create_index([("location_id", 1), ("created_at", -1)])

        low_stock_alerts_collection.create_index("alert_id", unique=True)
        low_stock_alerts_collection.create_index("product_id")
        low_stock_alerts_collection.create_index("warehouse_id")
        low_stock_alerts_collection.create_index("status")
        low_stock_alerts_collection.create_index("created_at")
        low_stock_alerts_collection.create_index([("location_id", 1), ("status", 1)])

        notifications_collection.create_index("notification_id", unique=True)
        notifications_collection.create_index("user_id")
        notifications_collection.create_index("role")
        notifications_collection.create_index("type")
        notifications_collection.create_index("is_read")
        notifications_collection.create_index("created_at")
        notifications_collection.create_index([("location_id", 1), ("created_at", -1)])

        activity_logs_collection.create_index("activity_id", unique=True)
        activity_logs_collection.create_index("user_id")
        activity_logs_collection.create_index("action")
        activity_logs_collection.create_index("module")
        activity_logs_collection.create_index("created_at")
        activity_logs_collection.create_index([("location_id", 1), ("created_at", -1)])

        returns_collection.create_index("return_id", unique=True)
        returns_collection.create_index("sale_id")
        returns_collection.create_index("product_id")
        returns_collection.create_index("customer_id")
        returns_collection.create_index("status")
        returns_collection.create_index("created_at")
        returns_collection.create_index([("location_id", 1), ("created_at", -1)])

        damaged_stock_collection.create_index("damage_id", unique=True)
        damaged_stock_collection.create_index("product_id")
        damaged_stock_collection.create_index("warehouse_id")
        damaged_stock_collection.create_index("status")
        damaged_stock_collection.create_index("reported_at")
        damaged_stock_collection.create_index([("location_id", 1), ("reported_at", -1)])

    except PyMongoError as exc:
        raise RuntimeError(
            f"MongoDB initialization failed: {exc}"
        ) from exc

def get_collections():
    return database.list_collection_names()






