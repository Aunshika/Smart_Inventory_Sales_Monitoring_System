from app.db.database import initialize_database


if __name__ == "__main__":
    initialize_database()
    print("MongoDB collections and indexes are ready")
