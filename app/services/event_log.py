"""
Persists every `logging.getLogger("zonecast.*")` record (INFO and
above) to the event_logs table, so an admin can look back at what
happened after the fact — the debugging session that found today's
"Applica ora" bug (wrong auth scheme, then a concurrency bug that made
the device time out) would have been much faster with this available
instead of re-running things live.

Deliberately scoped to the "zonecast" logger hierarchy only — NOT the
root logger — so per-request noise from uvicorn/httpx access logs
never reaches the database; only the curated events our own code
already logs (CGI failures, provisioning results, scheduler runs,
login attempts, etc.) do.
"""
import logging
from datetime import datetime, timedelta

MAX_ROWS = 5000
_PRUNE_TO = 4000


def prune_by_age(db, retention_days: int) -> int:
    """Deletes event_logs rows older than `retention_days`. Called by a
    daily scheduler job (see services/scheduler.py) — a no-op when the
    admin has set retention to "mai" (see AppSettings.log_retention_days,
    None). Returns the number of rows deleted."""
    from ..models import EventLog

    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    deleted = db.query(EventLog).filter(EventLog.created_at < cutoff).delete()
    db.commit()
    return deleted


class DBLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        # Local import: this module is imported by main.py at startup,
        # before the app (and its DB engine) is necessarily ready, and
        # importing models/database at module load time here would risk
        # a circular import (models -> services.drivers -> ... ).
        from ..database import SessionLocal
        from ..models import EventLog

        db = SessionLocal()
        try:
            db.add(EventLog(
                level=record.levelname,
                logger_name=record.name,
                message=self.format(record),
            ))
            # Cheap unbounded-growth guard: only bother counting/pruning
            # occasionally, not on every single insert.
            if record.created % 50 < 1:
                count = db.query(EventLog).count()
                if count > MAX_ROWS:
                    cutoff_id = (
                        db.query(EventLog.id)
                        .order_by(EventLog.id.desc())
                        .offset(_PRUNE_TO)
                        .limit(1)
                        .scalar()
                    )
                    if cutoff_id:
                        db.query(EventLog).filter(EventLog.id < cutoff_id).delete()
            db.commit()
        except Exception:  # noqa: BLE001 — a logging handler must never raise
            db.rollback()
        finally:
            db.close()


def install() -> None:
    zonecast_logger = logging.getLogger("zonecast")
    handler = DBLogHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    zonecast_logger.addHandler(handler)
