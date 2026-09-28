import pytest

from app.database import SessionLocal
from app.services import scheduler as scheduler_service
from app.services import system_time
from app.services.app_settings import get_settings


@pytest.fixture()
def restore_timezone():
    yield
    db = SessionLocal()
    try:
        get_settings(db).scheduler_timezone = None
        db.commit()
    finally:
        db.close()
    scheduler_service._timezone = None


def test_changing_system_timezone_moves_schedules_too(admin_client, monkeypatch, restore_timezone, media_id):
    monkeypatch.setattr(system_time, "set_timezone", lambda tz: None)  # no host access in tests
    created = admin_client.post("/api/schedules", json={
        "name": "tz", "media_id": media_id, "target_type": "all", "time_of_day": "08:00:00", "days_of_week": "mon",
    }).json()

    res = admin_client.post("/api/system/time/timezone", json={"timezone": "Europe/London"})
    assert res.status_code == 200, res.text

    assert scheduler_service.scheduler_timezone() == "Europe/London"
    job = scheduler_service.get_scheduler().get_job(f"schedule-{created['id']}")
    assert str(job.trigger.timezone) == "Europe/London"
    db = SessionLocal()
    try:
        assert get_settings(db).scheduler_timezone == "Europe/London"  # survives a restart
    finally:
        db.close()
    admin_client.delete(f"/api/schedules/{created['id']}")


def test_unknown_timezone_rejected_before_touching_the_host(admin_client, monkeypatch, restore_timezone):
    called = []
    monkeypatch.setattr(system_time, "set_timezone", lambda tz: called.append(tz))
    res = admin_client.post("/api/system/time/timezone", json={"timezone": "Mars/Olympus_Mons"})
    assert res.status_code == 400
    assert called == []
