"""
Full application data export/import — lets an admin move ZoneCast to a
new machine (e.g. a fresh Ubuntu Server install) without reconfiguring
speakers, zones, schedules, users, uploaded media or config backups
from scratch.

The bundle is a single encrypted file: a tar.gz of
  - data/zonecast.db      (consistent copy via sqlite3's backup API)
  - data/secret.key       (the at-rest encryption key — without it,
                            every encrypted speaker password in the DB
                            copy is unreadable garbage)
  - media/**               uploaded audio files
  - backups/**              speaker config backup snapshots
encrypted with a key derived (PBKDF2-HMAC-SHA256) from an
admin-supplied password, so the file is safe to store/transfer even
though its contents (device credentials, password hashes, TOTP
secrets) are highly sensitive. There is no way to recover a bundle
without that password — it is not stored anywhere.

Import is deliberately NOT exposed as a "hot" API endpoint: swapping
the SQLite file out from under a running app with open connections
risks corruption. Instead it's a standalone script
(app/tools/import_bundle.py) meant to be run once against a fresh
install, before the app's first start.
"""
import base64
import io
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"ZCBUNDLE1"
SALT_LEN = 16
PBKDF2_ITERATIONS = 390_000


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def _snapshot_sqlite(db_path: Path) -> bytes:
    """Copies the live DB via sqlite3's own backup API rather than
    reading the file directly, so a concurrent writer can't leave the
    snapshot in a torn/inconsistent state."""
    with tempfile.TemporaryDirectory() as tmp:
        dest_path = Path(tmp) / "zonecast.db"
        src = sqlite3.connect(str(db_path))
        dest = sqlite3.connect(str(dest_path))
        try:
            src.backup(dest)
        finally:
            dest.close()
            src.close()
        return dest_path.read_bytes()


def build_export(*, password: str, db_path: Path, secret_key_path: Path, media_dir: Path, backups_dir: Path) -> bytes:
    # Password is optional (an empty string still derives a valid key,
    # just a weak/guessable one) — the caller decides whether to warn
    # about that; this function doesn't force a minimum.
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tar:
        db_bytes = _snapshot_sqlite(db_path)
        info = tarfile.TarInfo("data/zonecast.db")
        info.size = len(db_bytes)
        tar.addfile(info, io.BytesIO(db_bytes))

        if secret_key_path.exists():
            key_bytes = secret_key_path.read_bytes()
            info = tarfile.TarInfo("data/secret.key")
            info.size = len(key_bytes)
            tar.addfile(info, io.BytesIO(key_bytes))

        for src_dir, arc_prefix in ((media_dir, "media"), (backups_dir, "backups")):
            if not src_dir.exists():
                continue
            for path in sorted(src_dir.rglob("*")):
                if path.is_file():
                    tar.add(str(path), arcname=f"{arc_prefix}/{path.relative_to(src_dir).as_posix()}")

    salt = Fernet.generate_key()[:SALT_LEN]  # cheap source of random bytes
    fernet = Fernet(_derive_key(password, salt))
    token = fernet.encrypt(tar_buf.getvalue())
    return MAGIC + salt + token


def extract_bundle(*, data: bytes, password: str, target_root: Path) -> list[str]:
    """Decrypts and unpacks a bundle under target_root (which should
    contain/become data/, media/, backups/). Returns the list of
    extracted member paths. Raises ValueError on wrong password or a
    file that isn't a ZoneCast bundle."""
    if not data.startswith(MAGIC):
        raise ValueError("File non riconosciuto: non è un bundle ZoneCast valido")
    rest = data[len(MAGIC):]
    salt, token = rest[:SALT_LEN], rest[SALT_LEN:]
    fernet = Fernet(_derive_key(password, salt))
    try:
        tar_bytes = fernet.decrypt(token)
    except InvalidToken as exc:
        raise ValueError("Password errata o file corrotto") from exc

    target_root.mkdir(parents=True, exist_ok=True)
    extracted = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        for member in tar.getmembers():
            member_path = (target_root / member.name).resolve()
            if not str(member_path).startswith(str(target_root.resolve())):
                raise ValueError(f"Percorso non sicuro nel bundle: {member.name}")
        tar.extractall(path=target_root)
        extracted = [m.name for m in tar.getmembers()]
    return extracted
