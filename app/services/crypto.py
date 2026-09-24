"""
At-rest encryption for sensitive DB columns (currently: each speaker's
device HTTP password, app/services/drivers use it in plaintext so the
encryption has to be transparent to them — see db_types.EncryptedString).

The key lives in a file next to the database (data/secret.key),
generated once on first startup and never rotated automatically —
losing it makes every encrypted value unrecoverable, so it must be
included whenever the app's data is backed up/exported (see
services/bundle.py, which bundles it alongside the DB for exactly this
reason).
"""
from cryptography.fernet import Fernet, InvalidToken

from ..config import settings

_fernet: Fernet | None = None


def _load_or_create_key() -> bytes:
    key_path = settings.secret_key_path
    if key_path.exists():
        return key_path.read_bytes().strip()
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    try:
        key_path.chmod(0o600)
    except OSError:
        pass  # not supported on this filesystem (e.g. some Windows setups) — non-fatal
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt_str(value: str) -> str:
    if value == "":
        return ""
    return _get_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_str(value: str) -> str:
    if value == "":
        return ""
    try:
        return _get_fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Value predates encryption (plaintext, from before this feature
        # or an unmigrated row) — return as-is rather than losing it.
        return value
