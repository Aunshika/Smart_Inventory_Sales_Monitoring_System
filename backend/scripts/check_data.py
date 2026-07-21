from app.db.database import users_collection
from app.models.documents import serialize_document


if __name__ == "__main__":
    users = users_collection.find({}, {"password": 0})

    for user in users:
        print(serialize_document(user))
