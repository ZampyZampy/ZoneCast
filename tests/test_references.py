from app.database import SessionLocal
from app.models import Speaker, Zone
from app.services import multicast_provisioning


def _zone(db, name, addr):
    zone = Zone(name=name, multicast_address=addr, multicast_port=5004)
    db.add(zone)
    db.commit()
    return zone.id


def _schedule(client, media_id, **overrides):
    body = {"name": "ref-test", "media_id": media_id, "target_type": "all",
            "time_of_day": "07:00:00", "days_of_week": "mon"}
    body.update(overrides)
    res = client.post("/api/schedules", json=body)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def test_media_in_use_cannot_be_deleted(admin_client, media_id):
    sched_id = _schedule(admin_client, media_id, name="Morning bell")
    res = admin_client.delete(f"/api/media/{media_id}")
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "media.in_use" and "Morning bell" in detail["params"]["names"]
    admin_client.delete(f"/api/schedules/{sched_id}")


def test_zone_in_use_cannot_be_deleted(admin_client, media_id):
    db = SessionLocal()
    try:
        zone_id = _zone(db, "ref-zone-used", "239.255.30.1")
    finally:
        db.close()
    sched_id = _schedule(admin_client, media_id, name="Dock bell", target_type="zone", target_id=zone_id)
    res = admin_client.delete(f"/api/zones/{zone_id}")
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "zones.in_use" and "Dock bell" in res.json()["detail"]["params"]["names"]
    admin_client.delete(f"/api/schedules/{sched_id}")
    assert admin_client.delete(f"/api/zones/{zone_id}").status_code == 200


def test_deleting_a_zone_reprovisions_its_speakers(admin_client, monkeypatch):
    pushed = []

    async def fake_push(speaker_id):
        pushed.append(speaker_id)

    monkeypatch.setattr(multicast_provisioning, "push_to_speaker_id", fake_push)
    db = SessionLocal()
    try:
        zone_id = _zone(db, "ref-zone-members", "239.255.30.2")
        speaker = Speaker(name="ref-speaker", ip_address="192.0.2.77", own_multicast_address="239.255.31.1", zone_id=zone_id)
        db.add(speaker)
        db.commit()
        speaker_id = speaker.id
    finally:
        db.close()
    try:
        assert admin_client.delete(f"/api/zones/{zone_id}").status_code == 200
        assert pushed == [speaker_id]  # device told to drop the old group
        db = SessionLocal()
        try:
            assert db.query(Speaker).filter(Speaker.id == speaker_id).first().zone_id is None
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            db.query(Speaker).filter(Speaker.id == speaker_id).delete()
            db.commit()
        finally:
            db.close()


def test_schedule_must_reference_existing_media_and_target(admin_client, media_id):
    base = {"name": "x", "time_of_day": "07:00:00", "days_of_week": "mon"}
    assert admin_client.post("/api/schedules", json={**base, "media_id": 999999, "target_type": "all"}).status_code == 422
    assert admin_client.post("/api/schedules", json={**base, "media_id": media_id, "target_type": "zone", "target_id": 999999}).status_code == 422
    assert admin_client.post("/api/schedules", json={**base, "media_id": media_id, "target_type": "speaker", "target_id": 999999}).status_code == 422
    sched_id = _schedule(admin_client, media_id)
    assert admin_client.put(f"/api/schedules/{sched_id}", json={"media_id": 999999}).status_code == 422
    admin_client.delete(f"/api/schedules/{sched_id}")
