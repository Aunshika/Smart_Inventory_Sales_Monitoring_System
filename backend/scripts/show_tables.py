from app.db.database import get_collections


if __name__ == "__main__":
    collections = get_collections()

    if not collections:
        print("No collections found")
    else:
        print("MongoDB collections:")
        for collection in collections:
            print(f"- {collection}")
