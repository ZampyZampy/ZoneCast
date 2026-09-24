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
