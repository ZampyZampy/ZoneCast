"""baseline — schema as of the security/2FA/audio-analysis feature set

This migration is intentionally empty. It marks the point where Alembic
starts tracking schema changes; everything before it was created by
Base.metadata.create_all() (fresh installs) or by hand-written one-off
migration scripts (this project's existing production DB, evolved
across several sessions before Alembic was introduced). Both are
brought here with `alembic stamp head` — a bookkeeping-only operation,
it runs no SQL — rather than a real migration, since the tables
already match this baseline in both cases.

Revision ID: 0001
Revises:
Create Date: 2026-09-22

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
