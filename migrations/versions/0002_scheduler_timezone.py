"""app_settings.scheduler_timezone — one timezone for schedules and host

Until now schedules always fired in the TIMEZONE from .env, even after
the host timezone was changed from Sistema. The value chosen there is
now stored here and used by the scheduler; NULL means "not chosen yet,
use TIMEZONE from .env".

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("scheduler_timezone", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.drop_column("scheduler_timezone")
