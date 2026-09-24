from sqlalchemy.types import String, TypeDecorator

from .services import crypto


class EncryptedString(TypeDecorator):
    """A VARCHAR column that's encrypted at rest and transparently
    decrypted on read — application code (routers, drivers, schemas)
    keeps reading/writing plain strings as if this were a normal
    String column."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return crypto.encrypt_str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return crypto.decrypt_str(value)
