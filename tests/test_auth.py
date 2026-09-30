from conftest import ADMIN_PASSWORD, ADMIN_USERNAME


def test_login_success(client):
    res = client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert res.status_code == 200
    body = res.json()
    assert body["requires_2fa"] is False
    assert body["user"]["username"] == ADMIN_USERNAME


def test_login_wrong_password(client):
    res = client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": "wrong"})
    assert res.status_code == 401


def test_me_requires_authentication(client):
    res = client.get("/api/auth/me")
    assert res.status_code == 401


def test_me_after_login(admin_client):
    res = admin_client.get("/api/auth/me")
    assert res.status_code == 200
    assert res.json()["username"] == ADMIN_USERNAME


def test_login_rate_limited_after_five_failures(client):
    for _ in range(5):
        res = client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": "wrong"})
        assert res.status_code == 401
    res = client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": "wrong"})
    assert res.status_code == 429

    # Correct password doesn't bypass the lockout window either.
    res = client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert res.status_code == 429


def test_change_password_requires_current_password(admin_client):
    res = admin_client.post("/api/auth/me/password", json={"current_password": "wrong", "new_password": "newpassword123"})
    assert res.status_code == 400


def test_change_password_rejects_short_password(admin_client):
    res = admin_client.post("/api/auth/me/password", json={"current_password": ADMIN_PASSWORD, "new_password": "short"})
    assert res.status_code == 400


def test_lockout_countdown_is_the_real_remaining_wait(monkeypatch):
    """The lock lifts when the oldest of the last 5 failures leaves the
    5-minute window; the countdown must say so, not 5 minutes from the
    newest failure."""
    from types import SimpleNamespace
    from app.services import rate_limit
    clock = [1000.0]
    monkeypatch.setattr(rate_limit, "time", SimpleNamespace(time=lambda: clock[0]))
    for offset in (0, 60, 120, 180, 240):
        clock[0] = 1000.0 + offset
        rate_limit.record_failure("mario", "10.0.0.1")
    clock[0] = 1250.0
    assert rate_limit.is_locked_out("mario", "10.0.0.1") == 50
    assert rate_limit.is_locked_out("mario", "10.0.0.2") == 0
    clock[0] = 1299.5
    assert rate_limit.is_locked_out("mario", "10.0.0.1") == 1  # rounded up: still locked
    clock[0] = 1300.0
    assert rate_limit.is_locked_out("mario", "10.0.0.1") == 0


def test_login_lockout_reports_its_wait(client):
    for _ in range(5):
        client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": "wrong"})
    res = client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": "wrong"})
    assert res.status_code == 429
    detail = res.json()["detail"]
    assert detail["code"] == "auth.too_many_attempts"
    assert 290 <= detail["params"]["seconds"] <= 300


def _user_id(admin_client, username):
    return next(u["id"] for u in admin_client.get("/api/auth/users").json() if u["username"] == username)


def test_default_admin_cannot_be_demoted(admin_client):
    admin_id = _user_id(admin_client, ADMIN_USERNAME)
    res = admin_client.put(f"/api/auth/users/{admin_id}", json={"role": "operator"})
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "users.cannot_demote_default"
    # Everything else stays editable, including a form that resends the same role.
    res = admin_client.put(f"/api/auth/users/{admin_id}", json={"role": "admin", "full_name": "Amministratore"})
    assert res.status_code == 200
    assert res.json()["role"] == "admin" and res.json()["full_name"] == "Amministratore"


def test_other_admins_can_still_be_demoted(admin_client):
    res = admin_client.post("/api/auth/users", json={"username": "secondo", "password": "password-lunga-1", "role": "admin"})
    assert res.status_code == 200
    res = admin_client.put(f"/api/auth/users/{res.json()['id']}", json={"role": "operator"})
    assert res.status_code == 200
    assert res.json()["role"] == "operator"
    admin_client.delete(f"/api/auth/users/{res.json()['id']}")


# --- session expiry: counted from the last real use, not from sign-in ---------

def _frozen_clock(monkeypatch, start):
    """Drives both the session cookie's signature age (itsdangerous) and
    SlidingSession's renewal clock."""
    from types import SimpleNamespace
    import itsdangerous.timed
    import app.main as main_module
    clock = [start]
    monkeypatch.setattr(itsdangerous.timed.TimestampSigner, "get_timestamp", lambda self: int(clock[0]))
    monkeypatch.setattr(main_module, "time", SimpleNamespace(time=lambda: clock[0]))
    return clock


def test_session_expires_after_inactivity_not_after_sign_in(client, monkeypatch):
    from app.config import settings
    hours = 3600
    assert settings.session_max_age_seconds == 12 * hours
    clock = _frozen_clock(monkeypatch, 1_000_000)
    assert client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}).status_code == 200
    signed_at_login = client.cookies.get("session")

    clock[0] += 11 * hours  # in use again before it expires: renewed
    res = client.get("/api/auth/me")
    assert res.status_code == 200 and "session=" in res.headers.get("set-cookie", "")

    clock[0] += 2 * hours  # 13 h after sign-in, 2 h after the last use
    assert client.get("/api/auth/me").status_code == 200

    client.cookies.set("session", signed_at_login)  # the cookie from sign-in has run out
    assert client.get("/api/auth/me").status_code == 401


def test_dashboard_polling_does_not_keep_a_session_alive(client, monkeypatch):
    hours = 3600
    clock = _frozen_clock(monkeypatch, 2_000_000)
    assert client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}).status_code == 200
    background = {"X-ZoneCast-Background": "1"}
    for _ in range(11):
        clock[0] += hours
        res = client.get("/api/playback/history?limit=30", headers=background)
        assert res.status_code == 200 and "set-cookie" not in res.headers
    clock[0] += 2 * hours  # 13 h of nothing but polling since sign-in
    assert client.get("/api/playback/history?limit=30", headers=background).status_code == 401


def test_session_renewal_is_throttled(client, monkeypatch):
    clock = _frozen_clock(monkeypatch, 3_000_000)
    client.post("/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert "set-cookie" in client.get("/api/auth/me").headers  # first use after sign-in
    clock[0] += 30
    assert "set-cookie" not in client.get("/api/auth/me").headers  # not on every request
    clock[0] += 31
    assert "set-cookie" in client.get("/api/auth/me").headers
