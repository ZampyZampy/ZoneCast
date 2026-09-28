import pytest

from app.database import SessionLocal
from app.models import Schedule, TargetType


_MEDIA = {}


@pytest.fixture(autouse=True)
def _media(media_id):
    _MEDIA["id"] = media_id


def _payload(**overrides):
    body = {
        "name": "Test schedule",
        "media_id": _MEDIA["id"],
        "target_type": "all",
        "time_of_day": "08:30:00",
        "days_of_week": "mon,tue,wed,thu,fri",
    }
    body.update(overrides)
    return body


def _delete(client, schedule_id):
    assert client.delete(f"/api/schedules/{schedule_id}").status_code == 200


def test_days_of_week_are_normalized(admin_client):
    res = admin_client.post("/api/schedules", json=_payload(days_of_week=" Mon, tue,MON "))
    assert res.status_code == 200, res.text
    assert res.json()["days_of_week"] == "mon,tue"
    _delete(admin_client, res.json()["id"])


def test_invalid_days_of_week_rejected(admin_client):
    before = len(admin_client.get("/api/schedules").json())
    for bad in ("lun,mar", "", " , ", "monday"):
        res = admin_client.post("/api/schedules", json=_payload(days_of_week=bad))
        assert res.status_code == 422, (bad, res.text)
    assert len(admin_client.get("/api/schedules").json()) == before


def test_zone_target_requires_target_id(admin_client):
    res = admin_client.post("/api/schedules", json=_payload(target_type="zone"))
    assert res.status_code == 422


def test_start_date_after_end_date_rejected(admin_client):
    res = admin_client.post("/api/schedules", json=_payload(start_date="2026-12-31", end_date="2026-01-01"))
    assert res.status_code == 422


def test_update_rejects_invalid_days_and_keeps_job(admin_client):
    from app.services import scheduler as scheduler_service

    created = admin_client.post("/api/schedules", json=_payload()).json()
    job_id = f"schedule-{created['id']}"
    assert scheduler_service.get_scheduler().get_job(job_id) is not None

    res = admin_client.put(f"/api/schedules/{created['id']}", json={"days_of_week": "lun"})
    assert res.status_code == 422
    res = admin_client.put(f"/api/schedules/{created['id']}", json={"target_type": "speaker"})
    assert res.status_code == 422

    listed = {s["id"]: s for s in admin_client.get("/api/schedules").json()}
    assert listed[created["id"]]["days_of_week"] == "mon,tue,wed,thu,fri"
    assert listed[created["id"]]["target_type"] == "all"
    assert scheduler_service.get_scheduler().get_job(job_id) is not None
    _delete(admin_client, created["id"])


def test_startup_survives_an_unloadable_schedule(admin_client):
    """A row saved before validation existed must not stop the others
    (or the app) from loading."""
    from datetime import time
    from app.services import scheduler as scheduler_service

    good = admin_client.post("/api/schedules", json=_payload(name="good")).json()
    db = SessionLocal()
    try:
        bad = Schedule(name="legacy", media_id=_MEDIA["id"], target_type=TargetType.all, time_of_day=time(9, 0), days_of_week="lun,mar")
        db.add(bad)
        db.commit()
        bad_id = bad.id
    finally:
        db.close()
    try:
        scheduler_service.load_all_schedules()  # must not raise
        engine = scheduler_service.get_scheduler()
        assert engine.get_job(f"schedule-{good['id']}") is not None
        assert engine.get_job(f"schedule-{bad_id}") is None
        # still listable, so it can be fixed from the dashboard
        assert any(s["id"] == bad_id for s in admin_client.get("/api/schedules").json())
    finally:
        _delete(admin_client, good["id"])
        _delete(admin_client, bad_id)


def test_update_rejects_explicit_nulls_and_keeps_job(admin_client):
    from app.services import scheduler as scheduler_service

    created = admin_client.post("/api/schedules", json=_payload()).json()
    job_id = f"schedule-{created['id']}"
    before = str(scheduler_service.get_scheduler().get_job(job_id).trigger)
    for field in ("time_of_day", "enabled", "days_of_week", "name"):
        res = admin_client.put(f"/api/schedules/{created['id']}", json={field: None})
        assert res.status_code == 422, (field, res.text)
    assert str(scheduler_service.get_scheduler().get_job(job_id).trigger) == before
    _delete(admin_client, created["id"])


def test_editing_a_legacy_row_returns_422_not_500(admin_client):
    from datetime import time

    db = SessionLocal()
    try:
        bad = Schedule(name="legacy", media_id=_MEDIA["id"], target_type=TargetType.all, time_of_day=time(9, 0), days_of_week="lun,mar")
        db.add(bad)
        db.commit()
        bad_id = bad.id
    finally:
        db.close()
    try:
        assert admin_client.put(f"/api/schedules/{bad_id}", json={"name": "renamed"}).status_code == 422
        res = admin_client.put(f"/api/schedules/{bad_id}", json={"days_of_week": "mon"})
        assert res.status_code == 200 and res.json()["days_of_week"] == "mon"
    finally:
        _delete(admin_client, bad_id)
