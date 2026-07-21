from datetime import datetime, timezone
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from pymongo import UpdateOne

from app.core.auth import hash_password
from app.db.database import (
    initialize_database,
    users_collection,
    products_collection,
    warehouses_collection,
    locations_collection,
    warehouse_inventory_collection,
)

NOW = datetime.now(timezone.utc)
DEFAULT_PASSWORD = os.getenv("SEED_USER_INITIAL_PASSWORD", "Smart@1234")

WAREHOUSES = [
    ("WH001", "Tirupati Central Warehouse", "Tirupati", "Andhra Pradesh", "Renigunta Road, Tirupati, Andhra Pradesh", "+91 87010 01001"),
    ("WH002", "Vijayawada Warehouse", "Vijayawada", "Andhra Pradesh", "Auto Nagar, Vijayawada, Andhra Pradesh", "+91 87010 01002"),
    ("WH003", "Hyderabad Warehouse", "Hyderabad", "Telangana", "Kompally Logistics Park, Hyderabad, Telangana", "+91 87010 01003"),
    ("WH004", "Chennai Warehouse", "Chennai", "Tamil Nadu", "Ambattur Industrial Estate, Chennai, Tamil Nadu", "+91 87010 01004"),
    ("WH005", "Bengaluru Warehouse", "Bengaluru", "Karnataka", "Peenya Industrial Area, Bengaluru, Karnataka", "+91 87010 01005"),
    ("WH006", "Visakhapatnam Warehouse", "Visakhapatnam", "Andhra Pradesh", "Gajuwaka Port Road, Visakhapatnam, Andhra Pradesh", "+91 87010 01006"),
    ("WH007", "Nellore Warehouse", "Nellore", "Andhra Pradesh", "Mini Bypass Road, Nellore, Andhra Pradesh", "+91 87010 01007"),
    ("WH008", "Guntur Warehouse", "Guntur", "Andhra Pradesh", "Autonagar, Guntur, Andhra Pradesh", "+91 87010 01008"),
    ("WH009", "Kurnool Warehouse", "Kurnool", "Andhra Pradesh", "Industrial Estate, Kurnool, Andhra Pradesh", "+91 87010 01009"),
    ("WH010", "Coimbatore Warehouse", "Coimbatore", "Tamil Nadu", "SIDCO Industrial Estate, Coimbatore, Tamil Nadu", "+91 87010 01010"),
]

USERS = [
    ("USR006", "Mahesh Varma", "mahesh.varma", "mahesh.varma@smartinventory.local", "Manager", "WH002"),
    ("USR007", "Divya Reddy", "divya.reddy", "divya.reddy@smartinventory.local", "Staff", "WH002"),
    ("USR008", "Naveen Kumar", "naveen.kumar", "naveen.kumar@smartinventory.local", "Manager", "WH003"),
    ("USR009", "Farah Khan", "farah.khan", "farah.khan@smartinventory.local", "Staff", "WH003"),
    ("USR010", "Arjun Iyer", "arjun.iyer", "arjun.iyer@smartinventory.local", "Manager", "WH004"),
    ("USR011", "Meera Nair", "meera.nair", "meera.nair@smartinventory.local", "Staff", "WH004"),
    ("USR012", "Kiran Rao", "kiran.rao", "kiran.rao@smartinventory.local", "Manager", "WH005"),
    ("USR013", "Pooja Shetty", "pooja.shetty", "pooja.shetty@smartinventory.local", "Staff", "WH005"),
    ("USR014", "Suresh Naidu", "suresh.naidu", "suresh.naidu@smartinventory.local", "Manager", "WH006"),
    ("USR015", "Anjali Das", "anjali.das", "anjali.das@smartinventory.local", "Staff", "WH006"),
    ("USR016", "Prakash Reddy", "prakash.reddy", "prakash.reddy@smartinventory.local", "Manager", "WH007"),
    ("USR017", "Lakshmi Priya", "lakshmi.priya", "lakshmi.priya@smartinventory.local", "Staff", "WH007"),
    ("USR018", "Rohit Sharma", "rohit.sharma", "rohit.sharma@smartinventory.local", "Manager", "WH008"),
    ("USR019", "Sneha Gupta", "sneha.gupta", "sneha.gupta@smartinventory.local", "Staff", "WH008"),
    ("USR020", "Vikram Singh", "vikram.singh", "vikram.singh@smartinventory.local", "Manager", "WH009"),
    ("USR021", "Priyanka Joshi", "priyanka.joshi", "priyanka.joshi@smartinventory.local", "Staff", "WH009"),
    ("USR022", "Manoj Krishnan", "manoj.krishnan", "manoj.krishnan@smartinventory.local", "Manager", "WH010"),
    ("USR023", "Revathi Menon", "revathi.menon", "revathi.menon@smartinventory.local", "Staff", "WH010"),
    ("USR024", "Rakesh Babu", "rakesh.babu", "rakesh.babu@smartinventory.local", "Manager", "WH001"),
    ("USR025", "Sravani Devi", "sravani.devi", "sravani.devi@smartinventory.local", "Staff", "WH001"),
]


def warehouse_docs():
    manager_by_warehouse = {warehouse_id: user_id for user_id, _, _, _, role, warehouse_id in USERS if role == "Manager"}
    for warehouse_id, name, city, state, address, phone in WAREHOUSES:
        yield {
            "warehouse_id": warehouse_id,
            "location_id": warehouse_id,
            "warehouse_name": name,
            "location_name": name,
            "city": city,
            "location": city,
            "state": state,
            "address": address,
            "phone": phone,
            "manager_id": manager_by_warehouse.get(warehouse_id),
            "status": "Active",
            "updated_at": NOW,
        }


def sync_warehouses():
    created = 0
    for doc in warehouse_docs():
        result = warehouses_collection.update_one(
            {"warehouse_id": doc["warehouse_id"]},
            {"$set": doc, "$setOnInsert": {"created_at": NOW}},
            upsert=True,
        )
        locations_collection.update_one(
            {"$or": [
                {"location_id": doc["warehouse_id"]},
                {"location_name": doc["location_name"]},
                {"warehouse_id": doc["warehouse_id"]},
            ]},
            {"$set": doc, "$setOnInsert": {"created_at": NOW}},
            upsert=True,
        )
        created += int(bool(result.upserted_id))
    return created


def sync_existing_admins():
    users_collection.update_many(
        {"role": "Admin"},
        {"$set": {
            "warehouse_id": "",
            "warehouse_name": "All Warehouses",
            "location_id": "ALL",
            "location": "All Warehouses",
            "state": "",
            "status": "Active",
            "updated_at": NOW,
        }}
    )


def sync_users():
    created = 0
    warehouse_lookup = {doc["warehouse_id"]: doc for doc in warehouse_docs()}
    for user_id, name, username, email, role, warehouse_id in USERS:
        warehouse = warehouse_lookup[warehouse_id]
        doc = {
            "user_id": user_id,
            "name": name,
            "full_name": name,
            "username": username,
            "email": email.lower(),
            "role": role,
            "warehouse_id": warehouse_id,
            "warehouse_name": warehouse["warehouse_name"],
            "location_id": warehouse_id,
            "location": warehouse["city"],
            "state": warehouse["state"],
            "status": "Active",
            "updated_at": NOW,
        }
        result = users_collection.update_one(
            {"username": username},
            {"$set": doc, "$setOnInsert": {"hashed_password": hash_password(DEFAULT_PASSWORD), "created_at": NOW}},
            upsert=True,
        )
        created += int(bool(result.upserted_id))
    return created


def sync_inventory():
    warehouses = list(warehouse_docs())
    products = list(products_collection.find({}, {"_id": 1, "product_id": 1, "reorder_level": 1, "quantity": 1}).sort("product_id", 1))
    inventory_ops = []
    product_ops = []
    for index, product in enumerate(products):
        product_id = product.get("product_id")
        if not product_id:
            continue
        selected_indexes = [(index % len(warehouses)), ((index + 3) % len(warehouses))]
        if index % 3 == 0:
            selected_indexes.append((index + 6) % len(warehouses))
        selected_indexes = list(dict.fromkeys(selected_indexes))
        primary_warehouse = warehouses[selected_indexes[0]]
        primary_quantity = None
        for offset, warehouse_index in enumerate(selected_indexes):
            warehouse = warehouses[warehouse_index]
            reorder_level = int(product.get("reorder_level") or 35)
            quantity = max(0, ((index * 11) + (offset * 17)) % 140)
            if index % 17 == 0 and offset == 0:
                quantity = max(0, reorder_level - 5)
            if primary_quantity is None:
                primary_quantity = quantity
            inventory_id = f"INV-{warehouse['warehouse_id']}-{product_id}"
            inventory_ops.append(UpdateOne(
                {"inventory_id": inventory_id},
                {"$set": {
                    "inventory_id": inventory_id,
                    "product_id": product_id,
                    "warehouse_id": warehouse["warehouse_id"],
                    "warehouse_name": warehouse["warehouse_name"],
                    "quantity": quantity,
                    "reorder_level": reorder_level,
                    "last_updated": NOW,
                }, "$setOnInsert": {"created_at": NOW}},
                upsert=True,
            ))
        product_ops.append(UpdateOne(
            {"_id": product["_id"]},
            {"$set": {
                "location_id": primary_warehouse["warehouse_id"],
                "warehouse_id": primary_warehouse["warehouse_id"],
                "warehouse_name": primary_warehouse["warehouse_name"],
                "location": primary_warehouse["city"],
                "state": primary_warehouse["state"],
                "quantity": primary_quantity if primary_quantity is not None else int(product.get("quantity") or 0),
                "updated_at": NOW,
            }}
        ))
    inventory_result = warehouse_inventory_collection.bulk_write(inventory_ops, ordered=False) if inventory_ops else None
    product_result = products_collection.bulk_write(product_ops, ordered=False) if product_ops else None
    created = inventory_result.upserted_count if inventory_result else 0
    updated = inventory_result.modified_count if inventory_result else 0
    product_updates = product_result.modified_count if product_result else 0
    return len(products), created, updated, product_updates


def main():
    initialize_database()
    warehouse_created = sync_warehouses()
    sync_existing_admins()
    user_created = sync_users()
    product_count, inventory_created, inventory_updated, product_updates = sync_inventory()
    total_warehouses = warehouses_collection.count_documents({})
    total_users = users_collection.count_documents({})
    total_inventory = warehouse_inventory_collection.count_documents({})
    print(f"Warehouses created this run: {warehouse_created}")
    print(f"Users created this run: {user_created}")
    print(f"Products processed: {product_count}")
    print(f"Warehouse inventory records created this run: {inventory_created}")
    print(f"Warehouse inventory records updated this run: {inventory_updated}")
    print(f"Product primary warehouse records updated this run: {product_updates}")
    print(f"Total warehouses: {total_warehouses}")
    print(f"Total users: {total_users}")
    print(f"Total warehouse inventory records: {total_inventory}")


if __name__ == "__main__":
    main()


