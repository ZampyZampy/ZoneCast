"""
Applies a staged data-bundle import at process startup — see
routers/system.py's /import endpoint for how a staging directory gets
created, and services/bundle.py for the bundle format itself.

The two-phase design (stage now, apply on the NEXT process start)
exists because swapping the SQLite file out from under a running
SQLAlchemy engine with open connections risks corrupting it. Staging
does the risky part (decrypting, validating, extracting) inside the
request handler where a wrong password fails loudly and nothing has
touched live data yet; applying is just a few file moves, done here
before database.py creates its engine — i.e. before anything in this
process has touched the database file at all.

The endpoint that stages an import also forces the process to exit
right after responding, relying on Docker's `restart: unless-stopped`
or systemd's `Restart=always` to bring it back up — at which point
`apply_pending_import()` runs and completes the swap.
"""
import logging
import shutil
from datetime import datetime
from pathlib import Path

from ..config import settings

STAGING_DIR = settings.data_dir / "_pending_import"
logger = logging.getLogger("zonecast.import")


def stage(data: bytes, password: str) -> None:
    """Decrypts and extracts the bundle into STAGING_DIR. Raises
    ValueError (wrong password / corrupt file) before anything is
    staged — see services/bundle.extract_bundle."""
    from . import bundle

    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    bundle.extract_bundle(data=data, password=password, target_root=STAGING_DIR)


def has_pending() -> bool:
    return STAGING_DIR.exists()


def _backup_current() -> None:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    if settings.db_path.exists():
        shutil.copy2(settings.db_path, settings.db_path.with_name(f"{settings.db_path.name}.pre-import-{ts}"))
    if settings.secret_key_path.exists():
        shutil.copy2(settings.secret_key_path, settings.secret_key_path.with_name(f"secret.key.pre-import-{ts}"))
    for d, label in ((settings.media_dir, "media"), (settings.backups_dir, "backups")):
        if d.exists() and any(d.iterdir()):
            shutil.move(str(d), str(d.with_name(f"{label}.pre-import-{ts}")))


def apply_pending_import() -> None:
    """Must run before database.py's engine is created. A no-op (fast
    return) when no import is staged — the normal case on every
    startup."""
    if not STAGING_DIR.exists():
        return

    logger.warning("Import di configurazione in sospeso rilevato — applico prima di avviare l'app")
    _backup_current()

    staged_db = STAGING_DIR / "data" / "zonecast.db"
    if staged_db.exists():
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged_db), str(settings.db_path))

    staged_key = STAGING_DIR / "data" / "secret.key"
    if staged_key.exists():
        shutil.move(str(staged_key), str(settings.secret_key_path))

    staged_media = STAGING_DIR / "media"
    if staged_media.exists():
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        for item in staged_media.iterdir():
            shutil.move(str(item), str(settings.media_dir / item.name))

    staged_backups = STAGING_DIR / "backups"
    if staged_backups.exists():
        settings.backups_dir.mkdir(parents=True, exist_ok=True)
        for item in staged_backups.iterdir():
            shutil.move(str(item), str(settings.backups_dir / item.name))

    shutil.rmtree(STAGING_DIR, ignore_errors=True)
    logger.warning("Import di configurazione applicato con successo")
