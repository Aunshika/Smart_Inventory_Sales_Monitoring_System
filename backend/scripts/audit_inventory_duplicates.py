import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.database import products_collection, warehouse_inventory_collection  # noqa: E402


def main():
    duplicate_products = list(products_collection.aggregate([
        {"$group": {"_id": "$product_id", "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"_id": 1}},
    ]))
    duplicate_inventory = list(warehouse_inventory_collection.aggregate([
        {
            "$group": {
                "_id": {
                    "product_id": "$product_id",
                    "warehouse_id": "$warehouse_id",
                },
                "count": {"$sum": 1},
                "quantity": {"$sum": "$quantity"},
            }
        },
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"_id.product_id": 1, "_id.warehouse_id": 1}},
    ]))

    print("Total unique product documents:", products_collection.count_documents({}))
    print("Total warehouse inventory records:", warehouse_inventory_collection.count_documents({}))
    print("Duplicate product_id groups:", len(duplicate_products))
    print("Duplicate product_id + warehouse_id groups:", len(duplicate_inventory))

    if duplicate_products:
        print("Sample duplicate products:")
        for row in duplicate_products[:10]:
            print(row)

    if duplicate_inventory:
        print("Sample duplicate warehouse inventory:")
        for row in duplicate_inventory[:10]:
            print(row)

    print("Sample PRD0001 warehouse inventory:")
    for row in warehouse_inventory_collection.find(
        {"product_id": "PRD0001"},
        {"_id": 0, "product_id": 1, "warehouse_id": 1, "quantity": 1, "reorder_level": 1},
    ).sort("warehouse_id", 1):
        print(row)


if __name__ == "__main__":
    main()
