import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.db.database import (  # noqa: E402
    categories_collection,
    initialize_database,
    inventory_history_collection,
    products_collection,
    purchases_collection,
    sales_collection,
    suppliers_collection,
)
from scripts.seed_data import build_documents  # noqa: E402


def remove_records_not_in_dataset():
    initialize_database()
    data = build_documents()
    targets = [
        ("categories", categories_collection, "category_id"),
        ("suppliers", suppliers_collection, "supplier_id"),
        ("products", products_collection, "product_id"),
        ("purchases", purchases_collection, "purchase_id"),
        ("sales", sales_collection, "sale_id"),
        ("inventory_history", inventory_history_collection, "movement_id"),
    ]
    return {
        name: collection.delete_many({
            key: {"$nin": [record[key] for record in data[name]]}
        }).deleted_count
        for name, collection, key in targets
    }


if __name__ == "__main__":
    counts = remove_records_not_in_dataset()
    print("Removed records outside the API datasets:")
    for name, count in counts.items():
        print(f"- {name}: {count}")
    print("- existing users: preserved")
