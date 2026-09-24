def test_log_settings_default_is_never(admin_client):
    res = admin_client.get("/api/logs/settings")
    assert res.status_code == 200
    assert res.json()["retention_days"] is None


def test_log_settings_update_and_read_back(admin_client):
    res = admin_client.put("/api/logs/settings", json={"retention_days": 30})
    assert res.status_code == 200
    assert res.json()["retention_days"] == 30

    res = admin_client.get("/api/logs/settings")
    assert res.json()["retention_days"] == 30

    # Setting it back to null ("mai") must be accepted too.
    res = admin_client.put("/api/logs/settings", json={"retention_days": None})
    assert res.status_code == 200
    assert res.json()["retention_days"] is None


def test_log_settings_rejects_zero_days(admin_client):
    res = admin_client.put("/api/logs/settings", json={"retention_days": 0})
    assert res.status_code == 422


def test_clear_logs(admin_client):
    res = admin_client.delete("/api/logs")
    assert res.status_code == 200
    assert "deleted" in res.json()

    res = admin_client.get("/api/logs")
    assert res.status_code == 200
    assert res.json() == []
