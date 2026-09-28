"""Speakers in several zones, managed from the zone side (1.6.0)."""
import asyncio
import sqlite3

import pytest

from app.database import SessionLocal
from app.config import settings
from app.models import Speaker, zone_members
from app.services import multicast_provisioning
from app.services.drivers import PushResult
from app.services.multicast_provisioning import request_push as real_request_push

_serial = iter(range(1, 10_000))


def _speaker(client, brand="Fanvil"):
    n = next(_serial)
    res = client.post("/api/speakers", json={
        "name": f"member-{n}", "ip_address": f"198.18.50.{n}", "own_multicast_address": f"239.254.50.{n}", "brand": brand})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _zone(client, speaker_ids=None, name=None):
    n = next(_serial)
    body = {"name": name or f"zone-{n}", "multicast_address": f"239.253.50.{n}"}
    if speaker_ids is not None:
        body["speaker_ids"] = speaker_ids
    res = client.post("/api/zones", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def _speakers(client):
    return {s["id"]: s for s in client.get("/api/speakers").json()}


@pytest.fixture()
def pushes(monkeypatch):
    pushed = []

    async def fake_pushes(speaker_ids):
        pushed.append(sorted(speaker_ids))

    monkeypatch.setattr(multicast_provisioning, "request_pushes", fake_pushes)
    return pushed


def test_a_speaker_can_be_in_several_zones(admin_client, pushes):
    sid = _speaker(admin_client)
    a = _zone(admin_client, [sid])
    b = _zone(admin_client, [sid])
    assert a["speaker_ids"] == [sid] and b["speaker_ids"] == [sid]
    assert _speakers(admin_client)[sid]["zone_ids"] == sorted([a["id"], b["id"]])
    preview = admin_client.get(f"/api/speakers/{sid}/multicast-preview").json()
    own = _speakers(admin_client)[sid]["own_multicast_address"]
    assert [e["address"] for e in preview] == [own, a["multicast_address"], b["multicast_address"],
                                               settings.global_all_call_address]
    assert [e["index"] for e in preview] == [1, 2, 3, 4]
    assert preview[1]["label"] == f"Zona: {a['name']}" and preview[2]["label"] == f"Zona: {b['name']}"
    assert pushes == [[sid], [sid]]


def test_members_are_replaced_only_when_a_list_is_sent(admin_client, pushes):
    s1, s2, s3 = _speaker(admin_client), _speaker(admin_client), _speaker(admin_client)
    zone = _zone(admin_client, [s1, s2])
    body = {"name": zone["name"], "multicast_address": zone["multicast_address"]}
    pushes.clear()
    assert admin_client.put(f"/api/zones/{zone['id']}", json=body).json()["speaker_ids"] == sorted([s1, s2])
    assert pushes == []  # nothing changed for the devices
    res = admin_client.put(f"/api/zones/{zone['id']}", json={**body, "speaker_ids": [s2, s3, s3]})
    assert sorted(res.json()["speaker_ids"]) == sorted([s2, s3])
    assert pushes == [sorted([s1, s3])]  # only who joined or left
    pushes.clear()
    admin_client.put(f"/api/zones/{zone['id']}", json={**body, "name": zone["name"] + "-renamed"})
    assert pushes == [sorted([s2, s3])]  # the slot label on every member changes
    pushes.clear()
    assert admin_client.put(f"/api/zones/{zone['id']}", json={**body, "name": zone["name"], "speaker_ids": []}).json()["speaker_ids"] == []
    assert pushes == [sorted([s2, s3])]


def test_unknown_members_are_refused(admin_client, pushes):
    res = admin_client.post("/api/zones", json={"name": "ghosts", "multicast_address": "239.253.51.1", "speaker_ids": [987654]})
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "zones.speaker_missing" and res.json()["detail"]["params"]["ids"] == "987654"


def test_a_device_cannot_join_more_zones_than_it_has_slots(admin_client, pushes):
    fanvil, generic = _speaker(admin_client), _speaker(admin_client, brand="other")
    for _ in range(18):  # 20 Fanvil slots: own group + all-call + 18 zones
        _zone(admin_client, [fanvil, generic])
    res = admin_client.post("/api/zones", json={"name": "one-too-many", "multicast_address": "239.253.52.1",
                                                "speaker_ids": [generic, fanvil]})
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["code"] == "zones.speaker_zone_limit" and detail["params"]["max"] == 18
    assert _zone(admin_client, [generic])["speaker_ids"] == [generic]  # no known limit without a driver
    # and a brand change that would overflow the device is refused too
    other = _speaker(admin_client, brand="other")
    for _ in range(19):
        _zone(admin_client, [other])
    res = admin_client.put(f"/api/speakers/{other}", json={"brand": "Fanvil"})
    assert res.status_code == 422 and res.json()["detail"]["code"] == "zones.speaker_zone_limit"


def test_speaker_form_no_longer_sets_a_zone(admin_client, pushes):
    sid = _speaker(admin_client)
    zone = _zone(admin_client, [sid])
    assert admin_client.put(f"/api/speakers/{sid}", json={"location": "Hall", "zone_id": None}).status_code == 200
    assert _speakers(admin_client)[sid]["zone_ids"] == [zone["id"]]  # untouched
    res = admin_client.put(f"/api/speakers/{sid}", json={"zone_id": zone["id"]})
    assert res.status_code == 422 and res.json()["detail"]["code"] == "speakers.zone_moved"
    res = admin_client.post("/api/speakers", json={"name": "x", "ip_address": "198.18.51.1",
                                                   "own_multicast_address": "239.254.51.1", "zone_id": zone["id"]})
    assert res.status_code == 422 and res.json()["detail"]["code"] == "speakers.zone_moved"


def test_deleting_a_speaker_leaves_no_membership_behind(admin_client, pushes):
    sid = _speaker(admin_client)
    zone = _zone(admin_client, [sid])
    assert admin_client.delete(f"/api/speakers/{sid}").status_code == 200
    zones = {z["id"]: z for z in admin_client.get("/api/zones").json()}
    assert zones[zone["id"]]["speaker_ids"] == []
    db = SessionLocal()
    try:
        assert db.execute(zone_members.select().where(zone_members.c.speaker_id == sid)).fetchall() == []
    finally:
        db.close()


# --- push queue ---------------------------------------------------------------

def test_pushes_to_one_device_never_overlap_and_the_last_one_wins(admin_client, monkeypatch):
    sid = _speaker(admin_client)
    running, seen = [], []

    async def slow_push(speaker):
        running.append(speaker.id)
        assert len(running) == 1, "two pushes to the same device at once"
        seen.append(speaker.location)
        await asyncio.sleep(0.05)
        running.pop()
        return PushResult(success=False, failed_keys=["login: timeout"])

    monkeypatch.setattr(multicast_provisioning, "push_to_speaker", slow_push)

    async def scenario():
        first = asyncio.create_task(real_request_push(sid))
        await asyncio.sleep(0.01)
        db = SessionLocal()
        try:
            db.query(Speaker).filter(Speaker.id == sid).update({Speaker.location: "after"})
            db.commit()
        finally:
            db.close()
        await asyncio.gather(real_request_push(sid), real_request_push(sid))  # coalesced into one more round
        await first

    asyncio.run(scenario())
    assert len(seen) == 2 and seen[-1] == "after"
    speaker = _speakers(admin_client)[sid]
    assert speaker["paging_sync_ok"] is False and "timeout" in speaker["paging_sync_error"]
    alerts = admin_client.get("/api/system/alerts").json()
    assert any(a["code"] == "speakers_out_of_sync" for a in alerts)


# --- migration and bundles ----------------------------------------------------

def _point_settings_at(monkeypatch, db_path):
    from app.config import settings
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path.as_posix()}")


def _upgrade(db_path, revision):
    from alembic import command
    from app import migrate

    engine = migrate._migration_engine()
    with engine.begin() as connection:
        command.upgrade(migrate._config(connection), revision)
    engine.dispose()


def test_migration_keeps_existing_memberships_and_drops_dangling_ones(tmp_path, monkeypatch):
    from app import migrate

    db_path = tmp_path / "v158.db"
    _point_settings_at(monkeypatch, db_path)
    _upgrade(db_path, "0002")
    con = sqlite3.connect(db_path)
    con.execute("insert into zones (id, name, description, multicast_address, multicast_port) values (1, 'A', '', '239.1.1.1', 5004)")
    for sid, zone_id in ((1, 1), (2, 7), (3, None)):  # zone 7 was deleted long ago
        con.execute("insert into speakers (id, name, ip_address, http_port, http_username, http_password, brand, model, "
                    "location, status, own_multicast_address, own_multicast_port, zone_id, notes) values "
                    "(?, ?, ?, 80, 'admin', '', 'Fanvil', 'A233', '', 'unknown', ?, 5004, ?, '')",
                    (sid, f"s{sid}", f"10.0.0.{sid}", f"239.2.0.{sid}", zone_id))
    con.commit()
    con.close()
    migrate.run_migrations()
    con = sqlite3.connect(db_path)
    try:
        assert con.execute("select zone_id, speaker_id from zone_members").fetchall() == [(1, 1)]
        assert "zone_id" not in [r[1] for r in con.execute("pragma table_info(speakers)")]
    finally:
        con.close()
    snapshots = list(tmp_path.glob("v158.db.pre-upgrade-0002-*"))
    assert len(snapshots) == 1  # the database as it was, before the upgrade
    migrate.run_migrations()
    assert len(list(tmp_path.glob("v158.db.pre-upgrade-*"))) == 1  # nothing to upgrade, no new copy


def test_only_the_newest_pre_upgrade_copies_are_kept(tmp_path, monkeypatch):
    import os
    from app import migrate

    db_path = tmp_path / "z.db"
    _point_settings_at(monkeypatch, db_path)
    for i in range(5):
        old = tmp_path / f"z.db.pre-upgrade-0001-2020010{i}T000000Z"
        old.write_bytes(b"x")
        os.utime(old, (1_600_000_000 + i, 1_600_000_000 + i))
    _upgrade(db_path, "0002")
    migrate.run_migrations()
    kept = sorted(p.name for p in tmp_path.glob("z.db.pre-upgrade-*"))
    assert len(kept) == migrate.KEEP_PRE_UPGRADE_SNAPSHOTS and any("-0002-" in k for k in kept)


def test_a_bundle_from_a_newer_version_is_refused(tmp_path, monkeypatch):
    from app.services import bundle, pending_import

    db_path = tmp_path / "newer.db"
    con = sqlite3.connect(db_path)
    con.execute("create table alembic_version (version_num varchar(32) not null)")
    con.execute("insert into alembic_version values ('9999')")
    con.commit()
    con.close()
    (tmp_path / "media").mkdir()
    (tmp_path / "backups").mkdir()
    data = bundle.build_export(password="pw", db_path=db_path, secret_key_path=tmp_path / "none.key",
                               media_dir=tmp_path / "media", backups_dir=tmp_path / "backups")
    monkeypatch.setattr(pending_import, "STAGING_DIR", tmp_path / "staging")
    with pytest.raises(bundle.BundleError) as exc:
        pending_import.stage(data, "pw")
    assert exc.value.code == "bundle.newer_schema" and exc.value.params["revision"] == "9999"
    assert not (tmp_path / "staging").exists()


def test_a_bundle_without_audio_files_keeps_the_current_ones(tmp_path, monkeypatch):
    from app.config import settings
    from app.services import pending_import

    staging = tmp_path / "staging"
    (staging / "data").mkdir(parents=True)
    media = tmp_path / "media"
    media.mkdir()
    (media / "bell.pcm8k.wav").write_bytes(b"RIFF")
    monkeypatch.setattr(pending_import, "STAGING_DIR", staging)
    monkeypatch.setattr(settings, "media_dir", media)
    monkeypatch.setattr(settings, "backups_dir", tmp_path / "backups")
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{(tmp_path / 'none.db').as_posix()}")
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    pending_import.apply_pending_import()
    assert (media / "bell.pcm8k.wav").exists()
