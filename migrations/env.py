"""
Alembic environment — introduced after several sessions of hand-rolled
"RENAME table, recreate via metadata, copy rows, DROP old" SQLite
migrations, which are exactly the kind of risky, easy-to-get-wrong
operation Alembic exists to avoid. `render_as_batch=True` below is the
important bit for this project: SQLite can't ALTER COLUMN directly,
and batch mode is Alembic's automatic handling of the
rebuild-and-copy dance that used to be done by hand here.

Usage (see README for the full walkthrough):
    alembic revision --autogenerate -m "add whatever column"
    alembic upgrade head

New installs (fresh DB created by Base.metadata.create_all() at app
startup) and this project's already-existing production DB are both
brought to a known baseline with `alembic stamp head` — see the first
migration in migrations/versions/.
"""
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402
from app import models  # noqa: E402,F401 — populates Base.metadata with every table

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"}, render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
