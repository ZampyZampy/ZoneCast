"""
Brings the database schema up to date at every startup, before anything
else touches the tables — so a release that changes the schema just
works after the code is updated and the service restarted, on the
native install, on Docker and after importing a bundle from an older
version (see services/pending_import.py). Maintenance tools that open
the database (seed_sample_media, reset_admin_password) call it too.

Databases created before Alembic was wired in (every install up to
v1.5.7) have all the tables but no alembic_version row: they already
match the 0001 baseline, so they're stamped there first and then
upgraded like any other.
"""
import logging

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.pool import NullPool

from .config import BASE_DIR, settings

logger = logging.getLogger("zonecast.migrate")
BASELINE_REVISION = "0001"
# Tables every pre-Alembic install has (create_all ran at each start).
BASELINE_TABLES = {
    "users", "zones", "speakers", "media", "schedules", "playback_logs",
    "app_settings", "event_logs", "speaker_config_backups",
}


def _migration_engine():
    """A private engine whose single transaction covers the whole
    stamp + upgrade. pysqlite would otherwise commit each DDL statement
    on its own, so a crash or kill mid-way left a half-migrated schema
    that failed every later start. BEGIN IMMEDIATE also takes SQLite's
    write lock up front: the service and a maintenance tool starting at
    the same moment migrate one after the other, not on top of each
    other."""
    engine = create_engine(settings.database_url, poolclass=NullPool)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _connect(dbapi_connection, _record):
            dbapi_connection.isolation_level = None  # we issue BEGIN ourselves
            dbapi_connection.execute("PRAGMA journal_mode=WAL")
            dbapi_connection.execute("PRAGMA busy_timeout=30000")

        @event.listens_for(engine, "begin")
        def _begin(connection):
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    return engine


def _config(connection) -> Config:
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    # ConfigParser interpolation: a literal % in the path must be doubled.
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations").replace("%", "%%"))
    cfg.attributes["connection"] = connection
    cfg.attributes["skip_logging_config"] = True
    return cfg


def _is_pre_alembic(connection, tables: set[str]) -> bool:
    if not tables:
        return False  # empty DB: the 0001 migration creates everything
    if "alembic_version" not in tables:
        return True
    # An alembic_version table left empty by an interrupted stamp.
    return not connection.exec_driver_sql("SELECT version_num FROM alembic_version").fetchall()


def run_migrations() -> None:
    engine = _migration_engine()
    try:
        with engine.begin() as connection:
            tables = set(inspect(connection).get_table_names())
            cfg = _config(connection)
            if _is_pre_alembic(connection, tables):
                missing = BASELINE_TABLES - tables
                if missing:
                    raise RuntimeError(
                        "Database pre-Alembic incompleto (mancano le tabelle: "
                        f"{', '.join(sorted(missing))}): ripristinare un backup o un export completo"
                    )
                logger.info("Database pre-Alembic: registro la baseline %s", BASELINE_REVISION)
                command.stamp(cfg, BASELINE_REVISION)
            command.upgrade(cfg, "head")
    finally:
        engine.dispose()
