from pathlib import Path
import os
import sys
from datetime import datetime, timezone

from pymongo import ASCENDING
from pymongo.errors import OperationFailure

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.auth import hash_password
from app.db.database import initialize_database, users_collection


def clean(value):
    return str(value or "").strip().strip('"').strip("'")


def main():
    password = clean(os.getenv("ADMIN_INITIAL_PASSWORD"))
    if not password:
        print("ADMIN_INITIAL_PASSWORD is missing. Add it to .env before running this script.", file=sys.stderr)
        return 1

    username = clean(os.getenv("ADMIN_INITIAL_USERNAME", "aunshika")).casefold()
    email = clean(os.getenv("ADMIN_INITIAL_EMAIL", "gaunshika@gmail.com")).casefold()
    now = datetime.now(timezone.utc)

    initialize_database()
    for field in ("username", "email"):
        try:
            users_collection.create_index([(field, ASCENDING)], unique=True, sparse=True)
        except OperationFailure as exc:
            if "IndexKeySpecsConflict" not in str(exc):
                raise
    result = users_collection.update_one(
        {"$or": [{"username": username}, {"email": email}]},
        {
            "$set": {
                "user_id": "USR-ADMIN-LOCAL",
                "name": "Aunshika",
                "full_name": "Aunshika",
                "username": username,
                "email": email,
                "hashed_password": hash_password(password),
                "role": "Admin",
                "status": "Active",
                "warehouse_id": "",
                "warehouse_name": "All Warehouses",
                "location_id": "ALL",
                "location": "All Warehouses",
                "state": "",
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
            "$unset": {"password": "", "password_hash": ""}
        },
        upsert=True,
    )
    action = "created" if result.upserted_id else "updated"
    print(f"Development admin {action}: {username} / {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
