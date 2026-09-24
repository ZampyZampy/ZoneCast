from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import settings
from .services.pending_import import apply_pending_import

# Must run before create_engine below: a staged import (see
# routers/system.py's /import endpoint) is applied by swapping files on
# disk, which has to happen before anything in this process opens the
# database file — see services/pending_import.py.
apply_pending_import()

is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=connect_args)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _record):
        # WAL lets readers (the dashboard's polling GETs) proceed while a
        # writer (an upload, a schedule firing, the log handler) is mid-
        # transaction, instead of blocking on SQLite's default single
        # writer/reader lock — meaningfully reduces "database is locked"
        # errors under concurrent use. NORMAL synchronous is the
        # documented safe pairing for WAL (still durable across an app
        # crash; only a full OS/power failure could lose the last commit).
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
