def hash_password(password):
    return "hashed_" + password

def verify_password(plain_password, hashed_password):
    return hashed_password == "hashed_" + plain_password