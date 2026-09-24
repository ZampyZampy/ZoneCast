import bcrypt

# bcrypt only uses the first 72 bytes of the input; longer passwords are
# truncated rather than raising, which is fine for a login password.
_MAX_BYTES = 72


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")[:_MAX_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    raw = password.encode("utf-8")[:_MAX_BYTES]
    try:
        return bcrypt.checkpw(raw, password_hash.encode("utf-8"))
    except ValueError:
        return False
