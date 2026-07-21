from pathlib import Path
import sys
from datetime import datetime, timezone

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError, OperationFailure, PyMongoError

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.auth import hash_password
from app.db.database import initialize_database, users_collection


def normalize_text(value):
    return str(value or "").strip().casefold()


def normalized_update(user):
    update = {}
    username = normalize_text(user.get("username"))
    email = normalize_text(user.get("email"))
    if username and user.get("username") != username:
        update["username"] = username
    if email and user.get("email") != email:
        update["email"] = email
    if not user.get("status"):
        update["status"] = "Active"
    update["updated_at"] = datetime.now(timezone.utc)
    return update


def normalize_existing_hash(value):
    value = str(value or "")
    if not value:
        return ""
    if value.startswith("hashed_"):
        return value
    return hash_password(value)


def main():
    initialize_database()
    for field in ("username", "email"):
        try:
            users_collection.create_index([(field, ASCENDING)], unique=True, sparse=True)
        except OperationFailure as exc:
            if "IndexKeySpecsConflict" not in str(exc):
                raise

    matched = 0
    migrated = 0
    skipped_without_password = 0

    projection = {
        "username": 1,
        "email": 1,
        "role": 1,
        "status": 1,
        "hashed_password": 1,
        "password": 1,
        "password_hash": 1,
    }
    for user in users_collection.find({}, projection):
        matched += 1
        password_hash = user.get("hashed_password")
        legacy_password = user.get("password") or user.get("password_hash")
        update = normalized_update(user)
        unset = {}

        if not password_hash and legacy_password:
            update["hashed_password"] = normalize_existing_hash(legacy_password)
            unset["password"] = ""
            unset["password_hash"] = ""
        elif password_hash:
            unset["password"] = ""
            unset["password_hash"] = ""
        else:
            skipped_without_password += 1

        update_doc = {"$set": update}
        if unset:
            update_doc["$unset"] = unset

        try:
            result = users_collection.update_one({"_id": user["_id"]}, update_doc)
            migrated += int(bool(result.modified_count))
        except DuplicateKeyError:
            print(
                "Skipped a user because normalized username/email would duplicate an existing record.",
                file=sys.stderr,
            )

    print(f"Users checked: {matched}")
    print(f"Users updated: {migrated}")
    print(f"Users skipped without any password field: {skipped_without_password}")


if __name__ == "__main__":
    try:
        main()
    except PyMongoError as exc:
        print(f"MongoDB migration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
