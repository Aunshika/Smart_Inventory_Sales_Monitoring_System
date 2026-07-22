from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
LEGACY_PREFIX = "hashed_"


def hash_password(password):
    if password is None:
        raise ValueError("Password cannot be empty")
    return pwd_context.hash(str(password))


def verify_password(plain_password, hashed_password):
    if not plain_password or not hashed_password:
        return False

    stored = str(hashed_password)

    # Backward compatibility for old development data. Successful legacy logins
    # should be upgraded to bcrypt by the login flow.
    if stored.startswith(LEGACY_PREFIX):
        return stored == LEGACY_PREFIX + str(plain_password)

    try:
        return pwd_context.verify(str(plain_password), stored)
    except Exception:
        return False


def password_needs_rehash(hashed_password):
    if not hashed_password:
        return False
    stored = str(hashed_password)
    if stored.startswith(LEGACY_PREFIX):
        return True
    try:
        return pwd_context.needs_update(stored)
    except Exception:
        return False