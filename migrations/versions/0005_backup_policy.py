"""backup_policy — scheduled configuration backups

A table of its own rather than columns on app_settings: that row is also
read by the unauthenticated theme endpoint, and this one holds encrypted
credentials.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28

"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_policy",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("frequency", sa.String(length=8), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("time_of_day", sa.Time(), nullable=False),
        sa.Column("include_media", sa.Boolean(), nullable=False),
        sa.Column("keep_local", sa.Integer(), nullable=False),
        sa.Column("keep_remote", sa.Integer(), nullable=False),
        sa.Column("bundle_password_enc", sa.String(length=1024), nullable=True),
        sa.Column("destination", sa.String(length=8), nullable=False),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("share", sa.String(length=80), nullable=False),
        sa.Column("remote_dir", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password_enc", sa.String(length=1024), nullable=True),
        sa.Column("smb_encrypt", sa.Boolean(), nullable=False),
        sa.Column("allow_insecure_ftp", sa.Boolean(), nullable=False),
        sa.Column("tls_fingerprint", sa.String(length=95), nullable=False),
        sa.Column("instance_id", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(length=16), nullable=True),
        sa.Column("last_stage", sa.String(length=16), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("last_file", sa.String(length=128), nullable=True),
        sa.Column("last_warning", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("backup_policy")
