from pymongo.errors import PyMongoError

from app.db.database import DATABASE_NAME, get_collections, ping_database


if __name__ == "__main__":
    try:
        ping_database()
        print("MongoDB connection successful")
        print(f"Database: {DATABASE_NAME}")
        print(f"Collections: {get_collections()}")
    except PyMongoError as exc:
        print(f"MongoDB connection failed: {exc}")
