"""Regression tests for the problems an independent review found in the
first version of the reliability changes."""
import asyncio
import sqlite3
import time

import pytest

from app.database import SessionLocal
from app.models import Media, PlaybackLog, Speaker, TargetType, Zone
from app.services import multicast_provisioning, player, scheduler as scheduler_service, speaker_status


# --- migrations: atomic, and the tools no longer create_all -------------------

def _point_settings_at(monkeypatch, db_path):
    from app.config import settings
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path.as_posix()}")


def test_failed_migration_leaves_no_half_applied_schema(tmp_path, monkeypatch):
    from alembic import command
    from app import migrate

    db_path = tmp_path / "fresh.db"
    _point_settings_at(monkeypatch, db_path)
    real_upgrade = command.upgrade

    def upgrade_then_crash(cfg, rev):
        real_upgrade(cfg, rev)
        raise RuntimeError("killed mid-migration")

    monkeypatch.setattr(command, "upgrade", upgrade_then_crash)
    with pytest.raises(RuntimeError):
        migrate.run_migrations()
    con = sqlite3.connect(db_path)
    try:
        assert con.execute("select name from sqlite_master where type='table'").fetchall() == []
    finally:
        con.close()

    monkeypatch.setattr(command, "upgrade", real_upgrade)
    migrate.run_migrations()  # a clean retry succeeds
    con = sqlite3.connect(db_path)
    try:
        assert con.execute("select version_num from alembic_version").fetchall() == [("0002",)]
    finally:
        con.close()


def test_maintenance_tools_use_migrations_not_create_all():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for tool in (root / "app/tools/seed_sample_media.py", root / "scripts/reset_admin_password.py"):
        source = tool.read_text(encoding="utf-8")
        assert "create_all" not in source and "run_migrations()" in source, tool


def test_interrupted_stamp_is_recovered(tmp_path, monkeypatch):
    """Tables present plus an EMPTY alembic_version (a stamp that died)."""
    from app import migrate

    db_path = tmp_path / "legacy.db"
    _point_settings_at(monkeypatch, db_path)
    migrate.run_migrations()
    con = sqlite3.connect(db_path)
    con.execute("delete from alembic_version")
    con.execute("alter table app_settings drop column scheduler_timezone")
    con.commit()
    con.close()
    migrate.run_migrations()
    con = sqlite3.connect(db_path)
    try:
        assert con.execute("select version_num from alembic_version").fetchall() == [("0002",)]
    finally:
        con.close()


# --- speaker sweep: no connection held across the network wait ----------------

def test_sweep_of_many_slow_speakers_does_not_exhaust_the_pool(client, monkeypatch):
    db = SessionLocal()
    try:
        speakers = [Speaker(name=f"pool-{i}", ip_address=f"192.0.2.{100 + i}", own_multicast_address=f"239.255.50.{i + 1}")
                    for i in range(40)]
        db.add_all(speakers)
        db.commit()
        ids = [s.id for s in speakers]
    finally:
        db.close()

    async def slow_check(session, speaker):
        await asyncio.sleep(0.3)  # a device answering slowly
        session.commit()

    monkeypatch.setattr(speaker_status, "check_and_update", slow_check)
    try:
        started = time.monotonic()
        asyncio.run(scheduler_service._check_all_speakers_job())
        assert time.monotonic() - started < 10  # used to block 30 s per pool timeout
    finally:
        db = SessionLocal()
        try:
            db.query(Speaker).filter(Speaker.id.in_(ids)).delete()
            db.commit()
        finally:
            db.close()


# --- player: concurrent requests for one group --------------------------------

@pytest.fixture()
def group(client):
    db = SessionLocal()
    try:
        zone = Zone(name="race-zone", multicast_address="239.255.60.1", multicast_port=5004)
        media = Media(original_filename="race.wav", stored_filename="race.wav", pcm_filename="race.pcm8k.wav")
        db.add_all([zone, media])
        db.commit()
        ids = zone.id, media.id
    finally:
        db.close()
    yield ids
    db = SessionLocal()
    try:
        db.query(PlaybackLog).filter(PlaybackLog.media_id == ids[1]).delete()
        db.query(Zone).filter(Zone.id == ids[0]).delete()
        db.query(Media).filter(Media.id == ids[1]).delete()
        db.commit()
    finally:
        db.close()


def test_concurrent_plays_on_one_group_never_overlap(group, monkeypatch):
    zone_id, media_id = group
    alive = set()
    max_alive = []

    async def fake_stream(pcm_path, addr, port, stop_event):
        alive.add(id(stop_event))
        max_alive.append(len(alive))
        try:
            await asyncio.to_thread(stop_event.wait, 5)
        finally:
            alive.discard(id(stop_event))

    monkeypatch.setattr(player, "stream_pcm_over_rtp", fake_stream)

    async def scenario():
        dbs = [SessionLocal() for _ in range(4)]
        try:
            await player.play(dbs[0], media_id, TargetType.zone, zone_id)
            await asyncio.sleep(0.05)
            await asyncio.gather(*(player.play(d, media_id, TargetType.zone, zone_id) for d in dbs[1:]))
            await asyncio.sleep(0.1)
            running = len(alive)
            await player.stop_all()
            return running
        finally:
            for d in dbs:
                d.close()

    assert asyncio.run(scenario()) == 1
    assert max(max_alive) == 1


# --- speaker edits: device access changes re-provision ------------------------

def test_changing_the_ip_re_provisions_the_device(admin_client, monkeypatch):
    pushed = []

    async def fake_push(speaker_id):
        pushed.append(speaker_id)

    monkeypatch.setattr(multicast_provisioning, "push_to_speaker_id", fake_push)
    db = SessionLocal()
    try:
        sp = Speaker(name="ip-change", ip_address="192.0.2.200", own_multicast_address="239.255.61.1")
        db.add(sp)
        db.commit()
        sp_id = sp.id
    finally:
        db.close()
    try:
        res = admin_client.put(f"/api/speakers/{sp_id}", json={"ip_address": "192.0.2.201", "own_multicast_address": "239.255.61.1"})
        assert res.status_code == 200
        assert pushed == [sp_id]
    finally:
        db = SessionLocal()
        try:
            db.query(Speaker).filter(Speaker.id == sp_id).delete()
            db.commit()
        finally:
            db.close()
