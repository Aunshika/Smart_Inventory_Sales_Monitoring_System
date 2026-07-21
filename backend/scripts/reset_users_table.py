from app.db.database import users_collection


if __name__ == "__main__":
    users_collection.delete_many({})
    print("Users collection cleared")
