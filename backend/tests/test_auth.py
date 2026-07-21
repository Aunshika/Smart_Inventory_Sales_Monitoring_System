from app.core.auth import hash_password, verify_password

hashed = hash_password("12345")

print(hashed)
print(verify_password("12345", hashed))
