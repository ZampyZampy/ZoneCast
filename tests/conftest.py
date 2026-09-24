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
