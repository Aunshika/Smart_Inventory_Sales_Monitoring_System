import csv
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
REPORTS_DIR = PROJECT_ROOT / "reports"

sys.path.insert(0, str(BACKEND_ROOT))

from app.db.database import products_collection  # noqa: E402


EXPECTED_FIRST_ID = 1
EXPECTED_LAST_ID = 991


def product_id_number(product_id: str) -> int:
    match = re.search(r"(\d+)$", str(product_id or ""))
    return int(match.group(1)) if match else 10**9


def main() -> None:
    REPORTS_DIR.mkdir(exist_ok=True)

    products = list(
        products_collection.find(
            {},
            {
                "_id": 0,
                "product_id": 1,
                "product_name": 1,
            },
        )
    )
    products.sort(key=lambda item: (product_id_number(item.get("product_id")), item.get("product_id", "")))

    txt_path = REPORTS_DIR / "all_products_mongodb.txt"
    csv_path = REPORTS_DIR / "all_products_mongodb.csv"
    missing_path = REPORTS_DIR / "missing_product_ids.txt"

    lines = [
        f"{str(product.get('product_id', '')).strip()} | {str(product.get('product_name', '')).strip()}"
        for product in products
    ]
    txt_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["Product ID", "Product Name"])
        writer.writerows(
            [
                str(product.get("product_id", "")).strip(),
                str(product.get("product_name", "")).strip(),
            ]
            for product in products
        )

    existing_ids = {str(product.get("product_id", "")).strip() for product in products}
    expected_ids = [f"PRD{number:04d}" for number in range(EXPECTED_FIRST_ID, EXPECTED_LAST_ID + 1)]
    missing_ids = [product_id for product_id in expected_ids if product_id not in existing_ids]

    missing_path.write_text(
        "\n".join(missing_ids) + ("\n" if missing_ids else "No missing product IDs.\n"),
        encoding="utf-8",
    )

    print(f"Total Products: {len(products)}")
    print(f"Missing Product IDs: {len(missing_ids)}")
    if len(products) != EXPECTED_LAST_ID:
        print(f"Expected 991 products, but MongoDB returned {len(products)} products.")
    if lines:
        print(f"First Product: {lines[0]}")
        print(f"Last Product: {lines[-1]}")
    print(f"TXT: {txt_path}")
    print(f"CSV: {csv_path}")
    print(f"Missing IDs: {missing_path}")


if __name__ == "__main__":
    main()
