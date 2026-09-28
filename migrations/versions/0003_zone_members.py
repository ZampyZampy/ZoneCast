"""zone_members — a speaker can belong to several zones

speakers.zone_id (one zone per speaker) becomes the zone_members
association table, managed from the Zones tab. Memberships are copied
over only for zones that still exist: SQLite doesn't enforce the old
foreign key, and a dangling zone_id would otherwise attach the speaker
to the next zone created with that reused id.

Also adds the outcome of the last paging-list push to each speaker, so
the dashboard can show a device that didn't take its new zones.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28

"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "zone_members",
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column("speaker_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], name="fk_zone_members_zone_id"),
        sa.ForeignKeyConstraint(["speaker_id"], ["speakers.id"], name="fk_zone_members_speaker_id"),
        sa.PrimaryKeyConstraint("zone_id", "speaker_id"),
    )
    op.create_index("ix_zone_members_speaker_id", "zone_members", ["speaker_id"])
    op.execute(
        "INSERT INTO zone_members (zone_id, speaker_id) "
        "SELECT s.zone_id, s.id FROM speakers s JOIN zones z ON z.id = s.zone_id"
    )
    with op.batch_alter_table("speakers", schema=None) as batch_op:
        batch_op.drop_column("zone_id")
        batch_op.add_column(sa.Column("paging_sync_ok", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("paging_sync_error", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("paging_synced_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    # Lossy: a speaker in several zones keeps only the oldest one.
    with op.batch_alter_table("speakers", schema=None) as batch_op:
        batch_op.drop_column("paging_synced_at")
        batch_op.drop_column("paging_sync_error")
        batch_op.drop_column("paging_sync_ok")
        batch_op.add_column(sa.Column("zone_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_speakers_zone_id", "zones", ["zone_id"], ["id"])
    op.execute(
        "UPDATE speakers SET zone_id = "
        "(SELECT MIN(zone_id) FROM zone_members m WHERE m.speaker_id = speakers.id)"
    )
    op.drop_index("ix_zone_members_speaker_id", table_name="zone_members")
    op.drop_table("zone_members")
