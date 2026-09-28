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
import queue
from datetime import timedelta
from logging.handlers import QueueHandler, QueueListener
from ..timeutil import utcnow

MAX_ROWS = 5000
_PRUNE_TO = 4000
_PRUNE_CHECK_EVERY = 200  # records between row-count checks

_listener: QueueListener | None = None
_queue_handler: QueueHandler | None = None


def prune_by_age(db, retention_days: int) -> int:
    """Deletes event_logs rows older than `retention_days`. Called by a
    daily scheduler job (see services/scheduler.py) — a no-op when the
    admin has set retention to "mai" (see AppSettings.log_retention_days,
    None). Returns the number of rows deleted."""
    from ..models import EventLog

    cutoff = utcnow() - timedelta(days=retention_days)
    deleted = db.query(EventLog).filter(EventLog.created_at < cutoff).delete()
    db.commit()
    return deleted


class DBLogHandler(logging.Handler):
    """Runs on the QueueListener's thread (see install), never on the
    thread that logged — an INSERT waiting on SQLite's write lock would
    otherwise stall the event loop, audio included."""

    def __init__(self) -> None:
        super().__init__()
        self._since_prune_check = 0

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
            self._since_prune_check += 1
            if self._since_prune_check >= _PRUNE_CHECK_EVERY:
                self._since_prune_check = 0
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
            self.handleError(record)  # at least on stderr/journal, not silently lost
        finally:
            db.close()


def install() -> None:
    """Idempotent: safe to call on every app start (tests start it many
    times in one process)."""
    global _listener, _queue_handler
    if _listener is not None:
        return
    db_handler = DBLogHandler()
    db_handler.setLevel(logging.INFO)
    db_handler.setFormatter(logging.Formatter("%(message)s"))
    log_queue: queue.SimpleQueue = queue.SimpleQueue()
    _queue_handler = QueueHandler(log_queue)
    _queue_handler.setLevel(logging.INFO)
    logging.getLogger("zonecast").addHandler(_queue_handler)
    _listener = QueueListener(log_queue, db_handler, respect_handler_level=True)
    _listener.start()


def shutdown() -> None:
    """Writes out whatever is still queued, then detaches."""
    global _listener, _queue_handler
    if _listener is None:
        return
    logging.getLogger("zonecast").removeHandler(_queue_handler)
    _listener.stop()
    _listener = None
    _queue_handler = None
