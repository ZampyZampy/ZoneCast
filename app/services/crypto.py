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
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings

_fernet: Fernet | None = None


def _load_or_create_key() -> bytes:
    key_path = settings.secret_key_path
    if key_path.exists():
        return key_path.read_bytes().strip()
    key = Fernet.generate_key()
    # Written whole or not at all: a key truncated by a crash would make
    # every stored password unreadable.
    tmp = key_path.with_name(key_path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, key_path)
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


class DecryptError(ValueError):
    """The stored value can't be decrypted with this installation's key
    (secret.key lost or replaced)."""


def decrypt_strict(value: str) -> str:
    """For secrets that must never silently turn into something else — a
    backup encrypted with the ciphertext as its password can't be
    restored by anyone."""
    if value == "":
        return ""
    try:
        return _get_fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise DecryptError("stored secret can't be decrypted") from exc


def decrypt_str(value: str) -> str:
    if value == "":
        return ""
    try:
        return _get_fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Value predates encryption (plaintext, from before this feature
        # or an unmigrated row) — return as-is rather than losing it.
        if value.startswith("gAAAAA"):  # but this one is a Fernet token: wrong or damaged secret.key
            logging.getLogger("zonecast.crypto").error("Un valore cifrato non si decifra con data/secret.key")
        return value
