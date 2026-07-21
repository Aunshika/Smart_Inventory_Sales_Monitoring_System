from app.db.database import suppliers_collection


if __name__ == "__main__":
    result = suppliers_collection.update_many(
        {"role": {"$exists": False}},
        {"$set": {"role": "Manager"}}
    )

    print(f"Matched: {result.matched_count}")
    print(f"Modified: {result.modified_count}")
