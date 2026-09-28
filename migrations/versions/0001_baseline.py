"""baseline — full schema as of v1.5.8

Creates every table from scratch on an empty database. Databases that
existed before Alembic was wired into startup (every install up to
v1.5.7, whose tables were created by Base.metadata.create_all()) already
match this schema: app/migrate.py stamps them at this revision instead
of running it, then applies the later migrations normally.

EncryptedString columns are plain VARCHARs at the SQL level (the
encryption happens in app/db_types.py), so they're declared as
sa.String here to keep migrations independent of app code.

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
    op.create_table('app_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('theme_color', sa.String(length=16), nullable=False),
    sa.Column('log_retention_days', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('event_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('level', sa.String(length=16), nullable=False),
    sa.Column('logger_name', sa.String(length=128), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('event_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_event_logs_created_at'), ['created_at'], unique=False)

    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('full_name', sa.String(length=128), nullable=True),
    sa.Column('role', sa.Enum('admin', 'operator', name='userrole'), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('totp_secret', sa.String(length=128), nullable=True),
    sa.Column('totp_enabled', sa.Boolean(), nullable=False),
    sa.Column('totp_recovery_codes', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_username'), ['username'], unique=True)

    op.create_table('zones',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=True),
    sa.Column('multicast_address', sa.String(length=64), nullable=False),
    sa.Column('multicast_port', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('media',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('stored_filename', sa.String(length=255), nullable=False),
    sa.Column('pcm_filename', sa.String(length=255), nullable=True),
    sa.Column('duration_seconds', sa.Float(), nullable=True),
    sa.Column('size_bytes', sa.Integer(), nullable=True),
    sa.Column('content_type', sa.String(length=64), nullable=True),
    sa.Column('uploaded_by_id', sa.Integer(), nullable=True),
    sa.Column('uploaded_at', sa.DateTime(), nullable=True),
    sa.Column('peak_db', sa.Float(), nullable=True),
    sa.Column('mean_db', sa.Float(), nullable=True),
    sa.Column('band_low_pct', sa.Float(), nullable=True),
    sa.Column('band_mid_pct', sa.Float(), nullable=True),
    sa.Column('band_high_pct', sa.Float(), nullable=True),
    sa.Column('suggested_gain_db', sa.Float(), nullable=True),
    sa.Column('normalized', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['uploaded_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stored_filename')
    )
    op.create_table('speakers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('ip_address', sa.String(length=64), nullable=False),
    sa.Column('http_port', sa.Integer(), nullable=True),
    sa.Column('http_username', sa.String(length=64), nullable=True),
    sa.Column('http_password', sa.String(length=255), nullable=True),
    sa.Column('brand', sa.String(length=64), nullable=True),
    sa.Column('model', sa.String(length=64), nullable=True),
    sa.Column('location', sa.String(length=128), nullable=True),
    sa.Column('status', sa.Enum('unknown', 'online', 'offline', name='speakerstatus'), nullable=False),
    sa.Column('last_seen', sa.DateTime(), nullable=True),
    sa.Column('own_multicast_address', sa.String(length=64), nullable=False),
    sa.Column('own_multicast_port', sa.Integer(), nullable=False),
    sa.Column('paging_volume', sa.String(length=10), nullable=True),
    sa.Column('zone_id', sa.Integer(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['zone_id'], ['zones.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ip_address')
    )
    op.create_table('schedules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('media_id', sa.Integer(), nullable=False),
    sa.Column('target_type', sa.Enum('speaker', 'zone', 'all', name='targettype'), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=True),
    sa.Column('time_of_day', sa.Time(), nullable=False),
    sa.Column('days_of_week', sa.String(length=64), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('exclude_holidays', sa.Boolean(), nullable=True),
    sa.Column('holidays_only', sa.Boolean(), nullable=True),
    sa.Column('holiday_country', sa.String(length=8), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['media_id'], ['media.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('speaker_config_backups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('speaker_id', sa.Integer(), nullable=True),
    sa.Column('speaker_name', sa.String(length=128), nullable=False),
    sa.Column('speaker_ip', sa.String(length=64), nullable=False),
    sa.Column('format', sa.String(length=8), nullable=False),
    sa.Column('stored_filename', sa.String(length=255), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['speaker_id'], ['speakers.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stored_filename')
    )
    op.create_table('playback_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('media_id', sa.Integer(), nullable=False),
    sa.Column('target_type', sa.Enum('speaker', 'zone', 'all', name='targettype'), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=True),
    sa.Column('target_label', sa.String(length=128), nullable=True),
    sa.Column('source', sa.Enum('manual', 'schedule', name='playbacksource'), nullable=False),
    sa.Column('schedule_id', sa.Integer(), nullable=True),
    sa.Column('triggered_by_id', sa.Integer(), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.Enum('running', 'completed', 'failed', 'stopped', name='playbackstatus'), nullable=False),
    sa.Column('error_message', sa.String(length=500), nullable=True),
    sa.ForeignKeyConstraint(['media_id'], ['media.id'], ),
    sa.ForeignKeyConstraint(['schedule_id'], ['schedules.id'], ),
    sa.ForeignKeyConstraint(['triggered_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('playback_logs')
    op.drop_table('speaker_config_backups')
    op.drop_table('schedules')
    op.drop_table('speakers')
    op.drop_table('media')
    op.drop_table('zones')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_username'))

    op.drop_table('users')
    with op.batch_alter_table('event_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_event_logs_created_at'))

    op.drop_table('event_logs')
    op.drop_table('app_settings')
