import asyncio
import logging
import time

from app.config import settings
from app.database import SessionLocal
from app.models import EventLog, Speaker
from app.services import event_log, multicast_provisioning, scheduler as scheduler_service, speaker_status


def test_log_records_reach_the_db_off_thread_and_handler_is_not_duplicated(client):
    zonecast = logging.getLogger("zonecast")
    handlers_before = list(zonecast.handlers)
    event_log.install()  # second call in the same run: must be a no-op
    assert zonecast.handlers == handlers_before

    logging.getLogger("zonecast.test").warning("reliability-probe-%s", "42")
    deadline = time.monotonic() + 3
    rows = []
    while time.monotonic() < deadline and not rows:
        db = SessionLocal()
        try:
            rows = db.query(EventLog).filter(EventLog.message == "reliability-probe-42").all()
        finally:
            db.close()
        time.sleep(0.05)
    assert len(rows) == 1


def test_sweep_survives_a_speaker_deleted_mid_check(client, monkeypatch):
    db = SessionLocal()
    try:
        a = Speaker(name="sweep-a", ip_address="192.0.2.81", own_multicast_address="239.255.40.1")
        b = Speaker(name="sweep-b", ip_address="192.0.2.82", own_multicast_address="239.255.40.2")
        db.add_all([a, b])
        db.commit()
        a_id, b_id = a.id, b.id
    finally:
        db.close()
    checked = []

    async def fake_check(session, speaker):
        if speaker.id == a_id:
            other = SessionLocal()  # deleted by someone else while "on the wire"
            try:
                other.query(Speaker).filter(Speaker.id == a_id).delete()
                other.commit()
            finally:
                other.close()
            raise RuntimeError("simulated failure after delete")
        checked.append(speaker.id)

    monkeypatch.setattr(speaker_status, "check_and_update", fake_check)
    try:
        asyncio.run(scheduler_service._check_all_speakers_job())
        assert b_id in checked  # the other speaker was still checked
    finally:
        db = SessionLocal()
        try:
            db.query(Speaker).filter(Speaker.id.in_([a_id, b_id])).delete()
            db.commit()
        finally:
            db.close()


def test_oversized_upload_is_rejected_without_leaving_files(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    before = set(settings.media_dir.iterdir())
    res = admin_client.post("/api/media/upload", files={"file": ("big.wav", b"x" * 2048, "audio/wav")})
    assert res.status_code == 400
    assert set(settings.media_dir.iterdir()) == before


def test_editing_only_non_paging_fields_does_not_rewrite_the_device(admin_client, monkeypatch):
    pushed = []

    async def fake_push(speaker_id):
        pushed.append(speaker_id)

    monkeypatch.setattr(multicast_provisioning, "request_push", fake_push)
    db = SessionLocal()
    try:
        sp = Speaker(name="diff-test", ip_address="192.0.2.90", own_multicast_address="239.255.41.1", own_multicast_port=5004)
        db.add(sp)
        db.commit()
        sp_id = sp.id
    finally:
        db.close()
    try:
        # the UI sends every field back, paging ones unchanged
        body = {"location": "new place", "own_multicast_address": "239.255.41.1", "own_multicast_port": 5004, "zone_id": None}
        assert admin_client.put(f"/api/speakers/{sp_id}", json=body).status_code == 200
        assert pushed == []
        body["own_multicast_address"] = "239.255.41.2"
        assert admin_client.put(f"/api/speakers/{sp_id}", json=body).status_code == 200
        assert pushed == [sp_id]
    finally:
        db = SessionLocal()
        try:
            db.query(Speaker).filter(Speaker.id == sp_id).delete()
            db.commit()
        finally:
            db.close()
