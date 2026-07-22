from datetime import datetime, timedelta, timezone

from jose import ExpiredSignatureError, JWTError, jwt

from app.core.config import clean_env, env_int

SECRET_KEY = clean_env("JWT_SECRET_KEY") or clean_env("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY is missing. Add it to .env before starting the backend.")
if SECRET_KEY in {"mysecretkey", "secret", "change_me", "change_this_to_a_long_random_secret"}:
    raise RuntimeError("JWT_SECRET_KEY must be changed to a strong secret before starting the backend.")

ALGORITHM = clean_env("JWT_ALGORITHM", "HS256") or "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = env_int("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", 60)


def create_access_token(data, expires_delta=None):
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"iat": now, "exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except ExpiredSignatureError:
        return None
    except JWTError:
        return None