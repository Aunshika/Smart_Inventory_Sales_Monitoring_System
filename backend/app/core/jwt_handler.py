from jose import JWTError, jwt

SECRET_KEY = "mysecretkey"
ALGORITHM = "HS256"

def create_access_token(data):

    to_encode = data.copy()

    encoded_jwt = jwt.encode(
        to_encode,
        SECRET_KEY,
        algorithm=ALGORITHM
    )

    return encoded_jwt


def decode_access_token(token):
    try:
        return jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )
    except JWTError:
        return None
