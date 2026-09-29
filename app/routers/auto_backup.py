"""Sistema > Automatic backup (admin only) — see services/auto_backup.py."""
import asyncio
import logging
import time
from collections import defaultdict, deque

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin
from ..errors import AppError
from ..models import User
from ..schemas import BackupFileOut, BackupPolicyIn, BackupPolicyOut, BackupTestOut
from ..services import auto_backup, backup_remote, crypto
from ..services.backup_remote import BackupError, Target
from ..services.instance import instance_id

router = APIRouter(prefix="/api/system/auto-backup", tags=["auto-backup"])
logger = logging.getLogger("zonecast.backup")

MIN_BUNDLE_PASSWORD = 12
# Where the stored remote password gets sent: changing any of these
# without typing the password again could hand it to another server.
_IDENTITY = ("destination", "host", "port", "share", "username")
TEST_LIMIT, TEST_WINDOW_SECONDS = 10, 300
_tests: dict[int, deque] = defaultdict(deque)


def _out(policy) -> dict:
    return {
        **{c.name: getattr(policy, c.name) for c in policy.__table__.columns
           if not c.name.endswith("_enc") and c.name not in ("id", "instance_id", "updated_at")},
        "has_bundle_password": auto_backup.readable(policy.bundle_password_enc),
        "has_password": auto_backup.readable(policy.password_enc),
        "foreign": auto_backup.is_foreign(policy),
        "running": auto_backup.running(),
        "next_run_at": auto_backup.next_run_at() if policy.enabled else None,
    }


def _identity_changed(policy, payload: BackupPolicyIn) -> bool:
    return any(getattr(policy, f) != getattr(payload, f) for f in _IDENTITY)


def _target(policy, payload: BackupPolicyIn) -> Target:
    """The destination the form describes, with the password typed in it
    or — only while the destination is unchanged — the stored one."""
    password = payload.password or None
    if password is None and payload.username:
        if _identity_changed(policy, payload):
            raise AppError(422, "backup.password_required_after_change",
                           "The server or username changed: type the password again.")
        try:
            password = auto_backup.secret(policy.password_enc)
        except BackupError as exc:
            raise exc.http(422) from exc
    target = Target(destination=payload.destination, host=payload.host.strip(), port=payload.port,
                    share=payload.share.strip(), remote_dir=payload.remote_dir.strip(), username=payload.username,
                    password=password or "", smb_encrypt=payload.smb_encrypt,
                    tls_fingerprint=backup_remote.normalize_fingerprint(payload.tls_fingerprint))
    try:
        backup_remote.validate(target)
    except BackupError as exc:
        raise exc.http(422) from exc
    if payload.destination == "ftp" and not payload.allow_insecure_ftp:
        raise AppError(422, "backup.ftp_insecure_not_confirmed",
                       "Plain FTP sends the password and the backup unencrypted: confirm it, or use FTPS.")
    return target


@router.get("", response_model=BackupPolicyOut)
def get_policy(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    policy = auto_backup.get_policy(db)
    db.commit()
    return _out(policy)


@router.put("", response_model=BackupPolicyOut)
def save_policy(payload: BackupPolicyIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    policy = auto_backup.get_policy(db)
    if payload.destination != "local":
        _target(policy, payload)
    if payload.bundle_password:
        if len(payload.bundle_password) < MIN_BUNDLE_PASSWORD:
            raise AppError(422, "backup.bundle_password_short",
                           f"The backup password must be at least {MIN_BUNDLE_PASSWORD} characters.", min=MIN_BUNDLE_PASSWORD)
        if not payload.bundle_password_confirmed:
            raise AppError(422, "backup.bundle_password_not_confirmed",
                           "Confirm you have stored the backup password somewhere safe: without it no backup can be restored.")
        policy.bundle_password_enc = crypto.encrypt_str(payload.bundle_password)
    elif payload.enabled and not auto_backup.readable(policy.bundle_password_enc):
        raise AppError(422, "backup.bundle_password_required", "Set the password that protects the backups.")

    identity_changed = _identity_changed(policy, payload)
    if payload.password:
        policy.password_enc = crypto.encrypt_str(payload.password)
    elif identity_changed:
        policy.password_enc = None  # never send an old password to a new server
    fingerprint = backup_remote.normalize_fingerprint(payload.tls_fingerprint)
    for field in ("enabled", "frequency", "weekday", "time_of_day", "include_media", "keep_local", "keep_remote",
                  "destination", "port", "username", "smb_encrypt", "allow_insecure_ftp"):
        setattr(policy, field, getattr(payload, field))
    policy.host, policy.share, policy.remote_dir = payload.host.strip(), payload.share.strip(), payload.remote_dir.strip()
    policy.tls_fingerprint = fingerprint
    policy.instance_id = instance_id()
    if policy.last_status == "paused_foreign":
        policy.last_status = None
    db.commit()
    auto_backup.register_job()
    logger.info("Backup automatico: impostazioni salvate da %s (%s, %s)", user.username,
                "attivo" if policy.enabled else "disattivato", policy.destination)
    return _out(policy)


@router.post("/test", response_model=BackupTestOut)
async def test_destination(payload: BackupPolicyIn, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Tries the destination in the form (saved or not): connect, log in,
    write and delete a small file."""
    now = time.monotonic()
    recent = _tests[user.id]
    while recent and now - recent[0] > TEST_WINDOW_SECONDS:
        recent.popleft()
    if len(recent) >= TEST_LIMIT:
        raise AppError(429, "backup.test_rate_limited", "Too many tests in a short time: wait a few minutes.")
    recent.append(now)
    if payload.destination == "local":
        return BackupTestOut(ok=True)
    target = _target(auto_backup.get_policy(db), payload)
    db.commit()
    return BackupTestOut(**await asyncio.to_thread(backup_remote.test, target))


@router.post("/run")
async def run_now(background_tasks: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    policy = auto_backup.get_policy(db)
    db.commit()
    if auto_backup.running():
        raise AppError(409, "backup.already_running", "A backup is already running.")
    if not auto_backup.readable(policy.bundle_password_enc):
        raise AppError(422, "backup.bundle_password_required", "Set the password that protects the backups.")
    if auto_backup.is_foreign(policy):
        raise AppError(409, "backup.paused_foreign", "These settings came from another installation: save them first.")
    logger.info("Backup automatico avviato a mano da %s", user.username)

    async def run():
        try:
            await auto_backup.run_backup("manual")
        except BackupError:
            pass  # another run started in between: it records its own outcome

    background_tasks.add_task(run)
    return {"started": True}


@router.get("/files", response_model=list[BackupFileOut])
def list_files(_: User = Depends(require_admin)):
    return auto_backup.list_files()


@router.get("/files/{name}")
def download_file(name: str, _: User = Depends(require_admin)):
    try:
        path = auto_backup.file_path(name)
    except BackupError as exc:
        raise exc.http(404) from exc
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@router.delete("/files/{name}")
def delete_file(name: str, user: User = Depends(require_admin)):
    try:
        path = auto_backup.file_path(name)
    except BackupError as exc:
        raise exc.http(404) from exc
    path.unlink()
    logger.info("Backup automatico %s eliminato da %s", name, user.username)
    return {"ok": True}
