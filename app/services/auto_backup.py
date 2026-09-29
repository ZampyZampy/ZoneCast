"""
Scheduled configuration backups: the same encrypted bundle as Sistema >
Export (services/bundle.py), made daily or weekly, kept in
data/auto_backups/ and optionally copied to an FTP/FTPS server or an SMB
share (services/backup_remote.py).

Safety rules, since this runs unattended inside a PA controller:
  - the bundle is built in a low-priority child process, never in the
    process that paces the RTP audio, and not while something is playing
    or a schedule is about to fire (postponed up to 30 minutes);
  - it's refused if it would be too big or fill the disk;
  - the passwords are decrypted strictly: with a lost or replaced
    secret.key the run fails loudly instead of encrypting the bundle
    with garbage as its password;
  - file names carry this installation's id and a UTC timestamp, and
    retention only ever deletes this installation's own files, so two
    machines can share a destination; a policy that came in with a
    bundle imported from another machine stays paused until re-saved;
  - retention never deletes the file just written, runs only after the
    upload succeeded, and a retention problem is a warning, not a failure.
"""
import asyncio
import functools
import logging
import multiprocessing
import re
import shutil
import threading
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..config import settings
from ..database import SessionLocal
from ..models import BackupPolicy
from ..timeutil import utcnow
from . import backup_remote, crypto
from .backup_remote import BackupError, Target
from .instance import instance_id

logger = logging.getLogger("zonecast.backup")

JOB_ID = "auto-backup"
CATCH_UP_JOB_ID = "auto-backup-catch-up"
POSTPONE_STEP_SECONDS = 60
POSTPONE_MAX_SECONDS = 30 * 60
DUE_SOON_SECONDS = 120
BUILD_TIMEOUT_SECONDS = 30 * 60
ANY_NAME = re.compile(r"^zonecast-[0-9a-f]{8}-(\d{8}T\d{6})Z\.zcbundle$")

_run_lock = threading.Lock()


def backup_dir() -> Path:
    path = settings.data_dir / "auto_backups"
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def own_name(pattern_name: str) -> bool:
    return bool(re.fullmatch(rf"zonecast-{instance_id()[:8]}-\d{{8}}T\d{{6}}Z\.zcbundle", pattern_name))


def get_policy(db) -> BackupPolicy:
    policy = db.get(BackupPolicy, 1)
    if policy is None:
        policy = BackupPolicy(id=1, time_of_day=time(2, 30), instance_id=instance_id())
        db.add(policy)
        db.flush()
    return policy


def secret(value_enc: Optional[str]) -> Optional[str]:
    """The stored password, or None if none was saved. Raises BackupError
    when it can't be decrypted (secret.key lost or replaced)."""
    if not value_enc:
        return None
    try:
        return crypto.decrypt_strict(value_enc)
    except crypto.DecryptError as exc:
        raise BackupError("backup.secret_changed",
                          "The saved backup passwords can't be read with this installation's key: enter them again.") from exc


def readable(value_enc: Optional[str]) -> bool:
    try:
        return secret(value_enc) is not None
    except BackupError:
        return False


def target_of(policy, password: str) -> Target:
    return Target(destination=policy.destination, host=policy.host, port=policy.port, share=policy.share,
                  remote_dir=policy.remote_dir, username=policy.username, password=password,
                  smb_encrypt=policy.smb_encrypt, tls_fingerprint=policy.tls_fingerprint)


def period(policy) -> timedelta:
    return timedelta(days=7 if policy.frequency == "weekly" else 1)


# ---------- files ----------
def list_files() -> list[dict]:
    out = []
    for path in backup_dir().iterdir():
        m = ANY_NAME.match(path.name)
        if m and path.is_file() and not path.is_symlink():
            created = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
            out.append({"name": path.name, "size": path.stat().st_size, "created_at": created, "own": own_name(path.name)})
    return sorted(out, key=lambda f: f["name"], reverse=True)


def file_path(name: str) -> Path:
    """The backup file called `name` — refusing anything that isn't one of
    ours (the name comes from the URL)."""
    directory = backup_dir().resolve()
    path = directory / name
    if not ANY_NAME.match(name) or path.resolve().parent != directory or path.is_symlink() or not path.is_file():
        raise BackupError("backup.file_not_found", "Backup file not found.")
    return path


def _dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


def _check_space(include_media: bool) -> None:
    estimate = sum(p.stat().st_size for p in (settings.db_path, settings.secret_key_path) if p.exists())
    estimate += _dir_size(settings.backups_dir) + (_dir_size(settings.media_dir) if include_media else 0)
    if estimate > settings.backup_max_bundle_mb * 1024 * 1024:
        raise BackupError("backup.too_large", "The backup would be too big: leave the audio files out, or raise BACKUP_MAX_BUNDLE_MB.",
                          size_mb=round(estimate / 1024 / 1024), max_mb=settings.backup_max_bundle_mb)
    free = shutil.disk_usage(backup_dir()).free
    if free < 2 * estimate + 512 * 1024 * 1024:
        raise BackupError("backup.insufficient_space", "Not enough free disk space for the backup.",
                          free_mb=round(free / 1024 / 1024))


# ---------- the run ----------
def _busy() -> bool:
    """Audio playing, or a schedule about to fire."""
    from . import player, scheduler

    if player.list_active():
        return True
    soon = datetime.now(timezone.utc) + timedelta(seconds=DUE_SOON_SECONDS)
    return any(j.id.startswith("schedule-") and j.next_run_time and j.next_run_time <= soon
               for j in scheduler.get_scheduler().get_jobs())


def _manifest(policy) -> dict:
    from .. import migrate
    from ..version import APP_VERSION

    media_count = sum(1 for p in settings.media_dir.iterdir() if p.is_file()) if settings.media_dir.exists() else 0
    return {
        "app_version": APP_VERSION,
        "alembic_revision": migrate._stored_revision(settings.db_path),
        "instance_id": instance_id(),
        "created_at": utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "include_media": policy.include_media,
        "media_count": media_count if policy.include_media else 0,
    }


async def _build(out: Path, password: str, include_media: bool, manifest: dict) -> None:
    """Runs backup_worker.build in a child process. Submitted as a partial
    of that function, so the child imports only backup_worker and bundle
    — never app.database, whose import applies a staged import. On a
    timeout or cancellation the child is killed rather than waited for:
    waiting would block the event loop, bells included."""
    from . import backup_worker

    loop = asyncio.get_running_loop()
    pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    try:
        job = functools.partial(
            backup_worker.build, str(out), password=password, db_path=str(settings.db_path),
            secret_key_path=str(settings.secret_key_path), media_dir=str(settings.media_dir),
            backups_dir=str(settings.backups_dir), include_media=include_media, manifest=manifest)
        await asyncio.wait_for(loop.run_in_executor(pool, job), timeout=BUILD_TIMEOUT_SECONDS)
    except BaseException:
        for proc in list((getattr(pool, "_processes", None) or {}).values()):
            try:
                proc.kill()
            except Exception:  # noqa: BLE001 — already gone
                pass
        for leftover in (out, out.with_name(f".{out.name}.tmp")):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _retention(files: list[str], keep: int, current: Optional[str]) -> list[str]:
    """Which of our own files to delete: all but the newest `keep`, and
    never the one just written (a clock set back could make it sort
    first)."""
    ours = sorted((n for n in files if own_name(n)), reverse=True)
    kept = set(ours[:keep]) | ({current} if current else set())
    return [n for n in ours if n not in kept]


def _prune_local(keep: int, current: Optional[str]) -> list[str]:
    """Local retention, plus half-written files a killed run left behind.
    Returns warnings instead of raising: the backup itself is what matters."""
    warnings = []
    try:
        for old in _retention([p.name for p in backup_dir().iterdir()], keep, current):
            (backup_dir() / old).unlink(missing_ok=True)
        stale = datetime.now().timestamp() - 24 * 3600
        for leftover in backup_dir().glob(f".zonecast-{instance_id()[:8]}-*.tmp"):
            if leftover.stat().st_mtime < stale:
                leftover.unlink(missing_ok=True)
    except OSError as exc:
        warnings.append(f"local retention: {exc}"[:200])
    return warnings


def is_foreign(policy) -> bool:
    """Saved on another installation (it came in with an imported
    bundle). A policy never saved anywhere isn't foreign."""
    return bool(policy.instance_id) and policy.instance_id != instance_id()


def _load_policy() -> "_Snapshot":
    db = SessionLocal()
    try:
        policy = get_policy(db)
        db.commit()
        return _Snapshot({c.name: getattr(policy, c.name) for c in BackupPolicy.__table__.columns})
    finally:
        db.close()


def _record(**fields) -> None:
    db = SessionLocal()
    try:
        policy = get_policy(db)
        for k, v in fields.items():
            setattr(policy, k, v)
        db.commit()
    finally:
        db.close()


async def run_backup(trigger: str = "scheduled") -> None:
    """One backup run. Raises BackupError("backup.already_running") if
    another run holds the lock; any other failure is recorded on the
    policy (and logged), not raised."""
    from .pending_import import has_pending

    if not _run_lock.acquire(blocking=False):
        raise BackupError("backup.already_running", "A backup is already running.")
    stage = "prepare"
    try:
        policy = _load_policy()
        if is_foreign(policy):
            logger.warning("Backup automatico in pausa: configurazione arrivata da un'altra installazione, va salvata di nuovo")
            _record(last_status="paused_foreign", last_run_at=utcnow(), last_error_code=None, last_error=None)
            return
        if trigger == "scheduled":
            waited = 0
            while _busy() and waited < POSTPONE_MAX_SECONDS:
                await asyncio.sleep(POSTPONE_STEP_SECONDS)
                waited += POSTPONE_STEP_SECONDS
            if waited:
                logger.info("Backup automatico rimandato di %d s (audio in riproduzione o campanella imminente)", waited)
                # settings saved during the wait (a new password, another
                # destination, backups turned off) apply to this run too
                policy = _load_policy()
                if is_foreign(policy) or not policy.enabled:
                    return
        if has_pending():
            logger.info("Backup automatico saltato: un import di configurazione è in corso")
            return

        bundle_password = secret(policy.bundle_password_enc)
        if not bundle_password:
            raise BackupError("backup.bundle_password_required", "Set the password that protects the backups.")
        remote_password = secret(policy.password_enc) or ""
        keep_local = max(policy.keep_local, 1 if policy.destination == "local" else 0)
        # leftovers of earlier runs whose upload failed, before judging the space
        warnings = _prune_local(max(keep_local, 1), None)
        _check_space(policy.include_media)

        stage = "build"
        name = f"zonecast-{instance_id()[:8]}-{utcnow():%Y%m%dT%H%M%S}Z.zcbundle"
        out = backup_dir() / name
        try:
            await _build(out, bundle_password, policy.include_media, _manifest(policy))
        except (BackupError, asyncio.CancelledError):
            raise
        except TimeoutError as exc:
            raise BackupError("backup.build_failed", "The backup file couldn't be created.",
                              detail=f"took longer than {BUILD_TIMEOUT_SECONDS // 60} minutes") from exc
        except Exception as exc:
            raise BackupError("backup.build_failed", "The backup file couldn't be created.",
                              detail=f"{type(exc).__name__}: {exc}"[:300]) from exc

        if policy.destination != "local":
            stage = "upload"
            target = target_of(policy, remote_password)
            own_part = re.compile(rf"\.zonecast-{instance_id()[:8]}-\d{{8}}T\d{{6}}Z\.zcbundle\.part")

            def upload_and_prune():
                with backup_remote.open_remote(target) as remote:
                    remote.upload(out, name)
                    try:
                        listing = remote.list()
                        for old in _retention(listing, policy.keep_remote, name):
                            remote.delete(old)
                        for part in listing:  # interrupted uploads of earlier runs
                            if own_part.fullmatch(part):
                                remote.delete(part)
                    except Exception as exc:  # noqa: BLE001 — the backup itself is safe
                        warnings.append(f"remote retention: {type(exc).__name__}: {exc}"[:200])

            try:
                await asyncio.to_thread(upload_and_prune)
            except Exception:
                # keep this bundle (it isn't anywhere else) but not a pile of them
                _prune_local(max(keep_local, 1), name)
                raise

        stage = "retention"
        warnings += _prune_local(keep_local, name)
        if keep_local == 0:
            try:
                out.unlink(missing_ok=True)  # copied to the remote and verified: no local copy wanted
            except OSError as exc:
                warnings.append(f"local retention: {exc}"[:200])

        now = utcnow()
        _record(last_status="ok", last_run_at=now, last_success_at=now, last_stage=None, last_error_code=None,
                last_error=None, last_file=name, last_warning="; ".join(warnings)[:500] or None)
        logger.info("Backup automatico completato: %s (%s)", name, policy.destination)
    except BackupError as exc:
        if exc.code == "backup.already_running":
            raise
        logger.error("Backup automatico non riuscito (%s): %s %s", stage, exc.code, exc.params.get("detail", ""))
        # only technical detail: the message itself is shown translated from the code
        detail = exc.params.get("detail")
        _record(last_status="failed", last_run_at=utcnow(), last_stage=stage, last_error_code=exc.code,
                last_error=str(detail)[:500] if detail else None)
    except Exception as exc:
        logger.exception("Backup automatico non riuscito (%s)", stage)
        _record(last_status="failed", last_run_at=utcnow(), last_stage=stage, last_error_code="backup.build_failed",
                last_error=f"{type(exc).__name__}: {exc}"[:500])
    finally:
        _run_lock.release()


class _Snapshot:
    """The policy's column values, detached from any session."""

    def __init__(self, values: dict):
        self.__dict__.update(values)


def running() -> bool:
    return _run_lock.locked()


# ---------- scheduling ----------
async def _scheduled_run() -> None:
    try:
        await run_backup("scheduled")
    except BackupError:
        logger.info("Backup automatico saltato: un altro backup è in corso")


def register_job(startup: bool = False) -> None:
    """(Re)creates the cron job from the stored policy — at startup, when
    the policy is saved and when the timezone changes. At startup a run
    missed while the service was down is caught up two minutes later."""
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.date import DateTrigger

    from .scheduler import get_scheduler, scheduler_timezone

    engine = get_scheduler()
    for job_id in (JOB_ID, CATCH_UP_JOB_ID):
        if engine.get_job(job_id):
            engine.remove_job(job_id)
    db = SessionLocal()
    try:
        policy = db.get(BackupPolicy, 1)
        if policy is None or not policy.enabled:
            return
        trigger = CronTrigger(day_of_week=policy.weekday if policy.frequency == "weekly" else "*",
                              hour=policy.time_of_day.hour, minute=policy.time_of_day.minute,
                              timezone=scheduler_timezone())
        engine.add_job(_scheduled_run, trigger=trigger, id=JOB_ID, replace_existing=True,
                       max_instances=1, coalesce=True, misfire_grace_time=3600)
        reference = policy.last_run_at or policy.updated_at
        if startup and not is_foreign(policy) and reference and utcnow() - reference > period(policy) + timedelta(hours=6):
            engine.add_job(_scheduled_run, trigger=DateTrigger(run_date=datetime.now(timezone.utc) + timedelta(minutes=2)),
                           id=CATCH_UP_JOB_ID, replace_existing=True)
            logger.info("Backup automatico: l'ultimo è stato saltato, ne eseguo uno tra 2 minuti")
    finally:
        db.close()


def next_run_at() -> Optional[datetime]:
    from .scheduler import get_scheduler

    job = get_scheduler().get_job(JOB_ID)
    return job.next_run_time if job else None


# ---------- alerts ----------
def alerts(db) -> list[tuple[str, str, str, dict]]:
    """(severity, code, English message, params) for the login alerts."""
    policy = db.get(BackupPolicy, 1)
    if policy is None or not policy.enabled:
        return []
    if is_foreign(policy):
        return [("warning", "backup_paused_foreign",
                 "Automatic backups are paused: their settings came from another installation. Save them again in System.", {})]
    out = []
    if policy.last_status == "failed":
        out.append(("danger", "backup_failed", "The last automatic backup failed: check System > Automatic backup.", {}))
    reference = policy.last_success_at or policy.updated_at
    if reference and utcnow() - reference > 2 * period(policy) + timedelta(hours=6) and policy.last_status != "failed":
        out.append(("warning", "backup_stale", "No automatic backup has completed recently: check System > Automatic backup.", {}))
    return out
