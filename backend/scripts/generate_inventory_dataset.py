import csv
import re
import urllib.request
from pathlib import Path


DATASETS_DIR = Path(__file__).resolve().parents[1] / "datasets"
RAW_DIR = DATASETS_DIR / "online_raw"
API_DIR = DATASETS_DIR / "api"
RAW_FILE = RAW_DIR / "grocery_inventory_sales.csv"
SOURCE_URL = (
    "https://raw.githubusercontent.com/debrupa03/"
    "Grocrey_Inventory_and_Sales_Dashboard/main/"
    "Grocery_Inventory_and_Sales_Dataset.csv"
)

LOCATIONS = [
    {"location_id": "LOC001", "location_name": "Hyderabad Warehouse", "location_type": "Warehouse", "city": "Hyderabad"},
    {"location_id": "LOC002", "location_name": "Vijayawada Store", "location_type": "Store", "city": "Vijayawada"},
    {"location_id": "LOC003", "location_name": "Bengaluru Warehouse", "location_type": "Warehouse", "city": "Bengaluru"},
    {"location_id": "LOC004", "location_name": "Mumbai Store", "location_type": "Store", "city": "Mumbai"},
    {"location_id": "LOC005", "location_name": "Chennai Warehouse", "location_type": "Warehouse", "city": "Chennai"},
]

USER_LOCATIONS = {
    "aunshika_admin": "ALL",
    "rahul_sharma": "LOC001",
    "priya_mehta": "LOC002",
    "arjun_patel": "LOC003",
    "neha_verma": "LOC004",
    "vikram_singh": "LOC005",
    "rohan_kumar": "LOC001",
    "sneha_iyer": "LOC002",
    "aman_gupta": "LOC003",
    "kavya_reddy": "LOC004",
    "dev_malhotra": "LOC005",
}


def download_source_dataset():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if RAW_FILE.exists():
        return

    print(f"Downloading source dataset from {SOURCE_URL}")
    urllib.request.urlretrieve(SOURCE_URL, RAW_FILE)


def clean_text(value):
    return (value or "").strip()


def parse_int(value, default=0):
    value = clean_text(value)
    if not value:
        return default
    return int(float(value.replace(",", "")))


def parse_price_to_cents(value):
    cleaned = re.sub(r"[^0-9.]", "", clean_text(value))
    if not cleaned:
        return 1
    return max(1, int(round(float(cleaned) * 100)))


def make_email(name, fallback_id):
    slug = re.sub(r"[^a-z0-9]+", ".", name.lower()).strip(".")
    suffix = re.sub(r"[^a-z0-9]+", "", fallback_id.lower())
    if not slug:
        slug = suffix or "supplier"
    return f"{slug}.{suffix}@supplier.example"


def make_phone(index):
    return f"98765{index:05d}"


def derive_category(source_category, product_name):
    name = product_name.lower()

    keyword_categories = [
        (("rice",), "Rice & Grains"),
        (("flour",), "Flour & Baking Staples"),
        (("sugar",), "Sugar & Sweeteners"),
        (("coffee",), "Coffee"),
        (("tea",), "Tea"),
        (("bread",), "Bread"),
        (("biscuit", "cookie"), "Biscuits & Cookies"),
        (("yogurt", "cream", "butter"), "Dairy Essentials"),
        (("cheese",), "Cheese"),
        (("egg",), "Eggs"),
        (("oil",), "Cooking Oils"),
        (("salmon", "trout", "haddock", "sardine", "tuna", "fish"), "Fish & Seafood"),
        (("apple", "banana", "orange", "mango", "plum", "pear", "kiwi", "pineapple", "lemon", "strawberr"), "Fruits"),
        (("spinach", "beans", "cabbage", "mushroom", "cucumber", "onion", "zucchini", "carrot", "broccoli", "eggplant", "pepper", "potato"), "Vegetables"),
    ]

    for keywords, category in keyword_categories:
        if any(keyword in name for keyword in keywords):
            return category

    source_category = clean_text(source_category)
    return source_category if source_category else "Other Grocery Items"


def write_csv(filename, fieldnames, rows):
    API_DIR.mkdir(parents=True, exist_ok=True)
    path = API_DIR / filename
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {path}")


def load_source_rows():
    download_source_dataset()
    with RAW_FILE.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def build_api_datasets(source_rows):
    category_map = {}
    supplier_map = {}
    products = []
    purchases = []
    sales = []
    stock_in = []
    stock_out = []

    for index, row in enumerate(source_rows, start=1):
        location_id = LOCATIONS[(index - 1) % len(LOCATIONS)]["location_id"]
        product_name = clean_text(row.get("Product_Name")) or f"Product {index}"
        source_category = derive_category(row.get("Catagory"), product_name)
        source_supplier_id = clean_text(row.get("Supplier_ID")) or f"SRC-SUP-{index:04d}"
        supplier_name = clean_text(row.get("Supplier_Name")) or "Unknown Supplier"

        if source_category not in category_map:
            category_map[source_category] = f"CAT{len(category_map) + 1:03d}"

        if source_supplier_id not in supplier_map:
            supplier_map[source_supplier_id] = {
                "supplier_id": f"SUP{len(supplier_map) + 1:03d}",
                "supplier_name": supplier_name,
                "email": make_email(supplier_name, source_supplier_id),
                "phone": make_phone(len(supplier_map) + 1),
                "address": (
                    clean_text(row.get("Warehouse_Location"))
                    or "Address not available"
                ),
            }

        product_id = f"PRD{index:04d}"
        category_id = category_map[source_category]
        supplier_id = supplier_map[source_supplier_id]["supplier_id"]
        price = parse_price_to_cents(row.get("Unit_Price"))
        unit_cost = max(1, int(price * 0.72))
        quantity = parse_int(row.get("Stock_Quantity"))
        reorder_level = parse_int(row.get("Reorder_Level"), 35)
        reorder_quantity = parse_int(row.get("Reorder_Quantity"), 1)
        sales_volume = parse_int(row.get("Sales_Volume"), 1)

        products.append({
            "product_id": product_id,
            "product_name": product_name,
            "quantity": quantity,
            "price": price,
            "unit_cost": unit_cost,
            "reorder_level": reorder_level,
            "category_id": category_id,
            "supplier_id": supplier_id,
            "location_id": location_id,
        })

        purchases.append({
            "purchase_id": f"PUR{index:05d}",
            "product_id": product_id,
            "quantity": max(1, reorder_quantity),
            "unit_cost": unit_cost,
            "supplier_id": supplier_id,
            "transaction_id": f"PTRX{index:05d}",
            "note": (
                "Online grocery dataset purchase row; "
                f"last_order_date={clean_text(row.get('Last_Order_Date'))}"
            ),
            "location_id": location_id,
        })

        sales.append({
            "sale_id": f"SAL{index:06d}",
            "transaction_id": f"STRX{index:06d}",
            "product_id": product_id,
            "quantity": max(1, sales_volume),
            "unit_price": price,
            "unit_cost": unit_cost,
            "discount_percent": 0,
            "reorder_level": reorder_level,
            "region": "",
            "customer_type": "",
            "customer_name": "",
            "payment_method": "",
            "note": (
                "Online grocery dataset sales row; "
                f"status={clean_text(row.get('Status'))}; "
                f"turnover={clean_text(row.get('Inventory_Turnover_Rate'))}"
            ),
            "location_id": location_id,
        })

        stock_in.append({
            "product_id": product_id,
            "quantity": max(1, reorder_quantity),
            "note": (
                "Source reorder quantity from online grocery inventory dataset"
            ),
            "location_id": location_id,
        })
        stock_out.append({
            "product_id": product_id,
            "quantity": max(1, min(sales_volume, max(quantity, 1))),
            "note": "Source sales volume from online grocery inventory dataset",
            "location_id": location_id,
        })

    categories = [
        {
            "category_id": category_id,
            "category_name": category_name,
            "description": f"{category_name} grocery inventory category",
        }
        for category_name, category_id in sorted(
            category_map.items(),
            key=lambda item: item[1]
        )
    ]

    suppliers = [
        supplier
        for _, supplier in sorted(
            supplier_map.items(),
            key=lambda item: item[1]["supplier_id"]
        )
    ]

    users = [
        {"username": "aunshika_admin", "email": "aunshika.admin@inventory.example", "password": "Admin@123", "confirm_password": "Admin@123", "role": "Admin"},
        {"username": "rahul_sharma", "email": "rahul.sharma@inventory.example", "password": "Manager@123", "confirm_password": "Manager@123", "role": "Manager"},
        {"username": "priya_mehta", "email": "priya.mehta@inventory.example", "password": "Manager@123", "confirm_password": "Manager@123", "role": "Manager"},
        {"username": "arjun_patel", "email": "arjun.patel@inventory.example", "password": "Manager@123", "confirm_password": "Manager@123", "role": "Manager"},
        {"username": "neha_verma", "email": "neha.verma@inventory.example", "password": "Manager@123", "confirm_password": "Manager@123", "role": "Manager"},
        {"username": "vikram_singh", "email": "vikram.singh@inventory.example", "password": "Manager@123", "confirm_password": "Manager@123", "role": "Manager"},
        {"username": "rohan_kumar", "email": "rohan.kumar@inventory.example", "password": "Staff@123", "confirm_password": "Staff@123", "role": "Staff"},
        {"username": "sneha_iyer", "email": "sneha.iyer@inventory.example", "password": "Staff@123", "confirm_password": "Staff@123", "role": "Staff"},
        {"username": "aman_gupta", "email": "aman.gupta@inventory.example", "password": "Staff@123", "confirm_password": "Staff@123", "role": "Staff"},
        {"username": "kavya_reddy", "email": "kavya.reddy@inventory.example", "password": "Staff@123", "confirm_password": "Staff@123", "role": "Staff"},
        {"username": "dev_malhotra", "email": "dev.malhotra@inventory.example", "password": "Staff@123", "confirm_password": "Staff@123", "role": "Staff"},
    ]
    for user in users:
        user["location_id"] = USER_LOCATIONS[user["username"]]

    return {
        "locations": LOCATIONS,
        "categories": categories,
        "suppliers": suppliers,
        "products": products,
        "purchases": purchases,
        "sales": sales,
        "stock_in": stock_in,
        "stock_out": stock_out,
        "users": users,
    }


def main():
    datasets = build_api_datasets(load_source_rows())

    write_csv(
        "locations.csv",
        ["location_id", "location_name", "location_type", "city"],
        datasets["locations"],
    )
    write_csv(
        "categories.csv",
        ["category_id", "category_name", "description"],
        datasets["categories"],
    )
    write_csv(
        "suppliers.csv",
        ["supplier_id", "supplier_name", "email", "phone", "address"],
        datasets["suppliers"],
    )
    write_csv(
        "products.csv",
        [
            "product_id", "product_name", "quantity", "price",
            "unit_cost", "reorder_level", "category_id", "supplier_id", "location_id"
        ],
        datasets["products"],
    )
    write_csv(
        "purchases.csv",
        [
            "purchase_id", "product_id", "quantity", "unit_cost",
            "supplier_id", "transaction_id", "note", "location_id"
        ],
        datasets["purchases"],
    )
    write_csv(
        "sales.csv",
        [
            "sale_id", "transaction_id", "product_id", "quantity",
            "unit_price", "unit_cost", "discount_percent",
            "reorder_level", "region", "customer_type",
            "customer_name", "payment_method", "note", "location_id"
        ],
        datasets["sales"],
    )
    write_csv(
        "stock_in.csv",
        ["product_id", "quantity", "note", "location_id"],
        datasets["stock_in"],
    )
    write_csv(
        "stock_out.csv",
        ["product_id", "quantity", "note", "location_id"],
        datasets["stock_out"],
    )
    write_csv(
        "users.csv",
        ["username", "email", "password", "confirm_password", "role", "location_id"],
        datasets["users"],
    )


if __name__ == "__main__":
    main()
