"""
Sets up an isolated environment (temp SQLite DB, temp media/backups
dirs, throwaway secret key) BEFORE app.config is ever imported —
pydantic-settings reads these env vars at import time, so this has to
happen ahead of any `from app...` import in this file or in tests.
"""
import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="zonecast_test_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["MEDIA_DIR"] = str(_TMP / "media")
os.environ["BACKUPS_DIR"] = str(_TMP / "backups")
os.environ["SECRET_KEY"] = "test-only-secret-key-not-for-production"
os.environ["DEFAULT_ADMIN_USERNAME"] = "admin"
os.environ["DEFAULT_ADMIN_PASSWORD"] = "testpassword123"
os.environ["RTP_MULTICAST_TTL"] = "0"  # multicast audio never leaves this machine

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "testpassword123"


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """rate_limit.py keeps its counters in a module-level dict shared
    across the whole test session — without this, an early test's
    failed logins could trip the 429 lockout for a later, unrelated
    test using the same username."""
    from app.services import rate_limit
    rate_limit._failed_attempts.clear()
    yield
    rate_limit._failed_attempts.clear()


@pytest.fixture(autouse=True)
def _no_device_network(monkeypatch):
    """Tests must never reach real hosts: a developer's (or a CI runner's)
    network can have actual PA speakers on it. Device I/O is a no-op by
    default — tests exercising it monkeypatch their own fakes — and any
    HTTP request to a non-local host fails the test outright."""
    import httpx
    from app.services import multicast_provisioning, rtp_multicast, speaker_status

    async def _no_io(*_args, **_kwargs):
        return None

    async def _device_untouched(*_args, **_kwargs):
        from app.services.drivers import PushResult
        return PushResult(success=False, unsupported_brand=True)

    def _no_rtp(_ttl):
        raise RuntimeError("test tried to open an RTP multicast socket — fake rtp_multicast._make_socket instead")

    monkeypatch.setattr(rtp_multicast, "_make_socket", _no_rtp)

    monkeypatch.setattr(speaker_status, "check_and_update", _no_io)
    monkeypatch.setattr(multicast_provisioning, "request_push", _no_io)
    monkeypatch.setattr(multicast_provisioning, "request_pushes", _no_io)
    monkeypatch.setattr(multicast_provisioning, "clear_device", _device_untouched)

    local = {"testserver", "127.0.0.1", "localhost"}
    real_async_send, real_send = httpx.AsyncClient.send, httpx.Client.send

    def _check(request):
        if request.url.host not in local:
            raise RuntimeError(f"test tried to contact {request.url.host} — stub the device I/O instead")

    async def guarded_async_send(self, request, *args, **kwargs):
        _check(request)
        return await real_async_send(self, request, *args, **kwargs)

    def guarded_send(self, request, *args, **kwargs):
        _check(request)
        return real_send(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", guarded_async_send)
    monkeypatch.setattr(httpx.Client, "send", guarded_send)


@pytest.fixture(autouse=True)
def _no_leftover_schedules():
    """Schedules (and custom-dates lists) a test creates are removed after
    it: an enabled one left behind would make a later test's schedule at
    the same time "overlap" and be refused."""
    from sqlalchemy import func
    from sqlalchemy.exc import OperationalError

    from app.database import SessionLocal
    from app.models import CustomCalendar, PlaybackLog, Schedule, ScheduleCalendar

    db = SessionLocal()
    try:
        last_schedule = db.query(func.max(Schedule.id)).scalar() or 0
        last_calendar = db.query(func.max(CustomCalendar.id)).scalar() or 0
    except OperationalError:  # the very first test: tables not created yet
        last_schedule = last_calendar = 0
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        ids = [sid for (sid,) in db.query(Schedule.id).filter(Schedule.id > last_schedule).all()]
        if ids:
            db.query(PlaybackLog).filter(PlaybackLog.schedule_id.in_(ids)).delete(synchronize_session=False)
            db.query(ScheduleCalendar).filter(ScheduleCalendar.schedule_id.in_(ids)).delete(synchronize_session=False)
            db.query(Schedule).filter(Schedule.id.in_(ids)).delete(synchronize_session=False)
        for cal in db.query(CustomCalendar).filter(CustomCalendar.id > last_calendar).all():
            db.query(ScheduleCalendar).filter(ScheduleCalendar.calendar_id == cal.id).delete(synchronize_session=False)
            db.delete(cal)
        db.commit()
    except OperationalError:
        db.rollback()
    finally:
        db.close()


@pytest.fixture()
def client():
    # scheduler.py keeps its AsyncIOScheduler in a module-level
    # singleton tied to the event loop it was created on. Each test's
    # TestClient spins up its own event loop via the lifespan, so the
    # singleton from a previous test (now shut down, loop closed) has
    # to be cleared first or start()/shutdown() blow up on teardown.
    from app.services import scheduler as scheduler_module
    scheduler_module._scheduler = None
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_client(client):
    res = client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert res.status_code == 200, res.text
    return client


@pytest.fixture()
def media_id(client):
    """A Media row schedules can point at (they're now checked to exist)."""
    from app.database import SessionLocal
    from app.models import Media, PlaybackLog, Schedule, ScheduleCalendar

    db = SessionLocal()
    try:
        media = Media(original_filename="fixture.wav", stored_filename=f"fixture-{os.urandom(4).hex()}.wav")
        db.add(media)
        db.commit()
        mid = media.id
    finally:
        db.close()
    yield mid
    db = SessionLocal()
    try:
        db.query(PlaybackLog).filter(PlaybackLog.media_id == mid).delete()
        ids = [sid for (sid,) in db.query(Schedule.id).filter(Schedule.media_id == mid).all()]
        db.query(ScheduleCalendar).filter(ScheduleCalendar.schedule_id.in_(ids)).delete(synchronize_session=False)
        db.query(Schedule).filter(Schedule.media_id == mid).delete()
        db.query(Media).filter(Media.id == mid).delete()
        db.commit()
    finally:
        db.close()
