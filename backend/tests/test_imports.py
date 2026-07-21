from app.core.auth import hash_password, verify_password
from app.core.jwt_handler import create_access_token

print("Imports working")
print(hash_password("12345"))
print(create_access_token({"sub": "aunshika"}))
