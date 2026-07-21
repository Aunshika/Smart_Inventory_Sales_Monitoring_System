from pathlib import Path
import argparse
import getpass
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.auth import hash_password
from app.db.database import initialize_database, users_collection


def normalize_identifier(value):
    return str(value or "").strip().casefold()


def find_user(identifier):
    normalized = normalize_identifier(identifier)
    return users_collection.find_one({
        "$or": [
            {"username": normalized},
            {"email": normalized},
            {"username": {"$regex": f"^{normalized}$", "$options": "i"}},
            {"email": {"$regex": f"^{normalized}$", "$options": "i"}},
        ]
    })


def main():
    parser = argparse.ArgumentParser(description="Safely reset a Smart Inventory user's password.")
    parser.add_argument("--identifier", required=True, help="Username or email address")
    parser.add_argument("--password", help="Optional password value. If omitted, you will be prompted.")
    args = parser.parse_args()

    initialize_database()
    user = find_user(args.identifier)
    if not user:
        print("No matching user found.", file=sys.stderr)
        return 1

    new_password = args.password
    if not new_password:
        new_password = getpass.getpass("New password: ")
        confirm_password = getpass.getpass("Confirm password: ")
        if new_password != confirm_password:
            print("Passwords do not match.", file=sys.stderr)
            return 1

    users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "hashed_password": hash_password(new_password),
                "updated_at": datetime.now(timezone.utc),
            },
            "$unset": {
                "password": "",
                "password_hash": "",
            }
        }
    )
    print(f"Password reset successfully for {user.get('username') or user.get('email')}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
