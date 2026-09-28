"""custom calendars — named lists of dates schedules can skip or keep to

A link table (schedule_calendars) rather than columns on schedules: a
schedule can combine several lists ("skip Closures and Exam days"), and
the schedules table isn't rebuilt.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28

"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_calendars",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "custom_calendar_dates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("calendar_id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("label", sa.String(length=128), nullable=True),
        sa.Column("yearly", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["calendar_id"], ["custom_calendars.id"], name="fk_custom_calendar_dates_calendar_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_custom_calendar_dates_calendar_id", "custom_calendar_dates", ["calendar_id"])
    op.create_table(
        "schedule_calendars",
        sa.Column("schedule_id", sa.Integer(), nullable=False),
        sa.Column("calendar_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.ForeignKeyConstraint(["schedule_id"], ["schedules.id"], name="fk_schedule_calendars_schedule_id"),
        sa.ForeignKeyConstraint(["calendar_id"], ["custom_calendars.id"], name="fk_schedule_calendars_calendar_id"),
        sa.PrimaryKeyConstraint("schedule_id", "calendar_id"),
    )
    op.create_index("ix_schedule_calendars_calendar_id", "schedule_calendars", ["calendar_id"])


def downgrade() -> None:
    op.drop_index("ix_schedule_calendars_calendar_id", table_name="schedule_calendars")
    op.drop_table("schedule_calendars")
    op.drop_index("ix_custom_calendar_dates_calendar_id", table_name="custom_calendar_dates")
    op.drop_table("custom_calendar_dates")
    op.drop_table("custom_calendars")
