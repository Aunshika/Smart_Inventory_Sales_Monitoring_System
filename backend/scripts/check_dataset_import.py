import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.db.database import (  # noqa: E402
    categories_collection,
    inventory_history_collection,
    products_collection,
    purchases_collection,
    sales_collection,
    suppliers_collection
)


COLLECTIONS = {
    "categories": categories_collection,
    "suppliers": suppliers_collection,
    "products": products_collection,
    "purchases": purchases_collection,
    "sales": sales_collection,
    "inventory_history": inventory_history_collection
}


if __name__ == "__main__":
    for name, collection in COLLECTIONS.items():
        print(f"{name}: {collection.count_documents({})}")

    product = products_collection.find_one(
        {"product_id": "PRD001"},
        {"_id": 0, "product_id": 1, "product_name": 1, "quantity": 1}
    )
    sale = sales_collection.find_one(
        {"sale_id": "SAL000001"},
        {
            "_id": 0,
            "sale_id": 1,
            "product_name": 1,
            "region": 1,
            "payment_method": 1,
            "total_amount": 1,
            "profit": 1,
            "stock_status": 1
        }
    )
    print(f"sample_product: {product}")
    print(f"sample_sale: {sale}")
