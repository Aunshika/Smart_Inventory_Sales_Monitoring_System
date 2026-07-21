from app.db.database import users_collection


if __name__ == "__main__":
    users_collection.create_index("email", unique=True)
    print("Unique email index is ready")
