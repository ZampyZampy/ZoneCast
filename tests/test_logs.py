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


def _add_log_row(created_at):
    from app.database import SessionLocal
    from app.models import EventLog
    db = SessionLocal()
    try:
        db.add(EventLog(created_at=created_at, level="INFO", logger_name="test", message="export-probe"))
        db.commit()
    finally:
        db.close()


def test_log_export_marks_timestamps_as_utc(admin_client):
    from datetime import datetime
    _add_log_row(datetime(2026, 9, 25, 15, 0, 0))

    res = admin_client.get("/api/logs/export", params={"format": "json", "q": "export-probe"})
    assert res.status_code == 200
    assert [r["created_at"] for r in res.json()] == ["2026-09-25T15:00:00+00:00"]

    res = admin_client.get("/api/logs/export", params={"format": "csv", "q": "export-probe"})
    assert res.status_code == 200
    assert "2026-09-25T15:00:00+00:00" in res.text


def test_log_export_rejects_unknown_format(admin_client):
    res = admin_client.get("/api/logs/export", params={"format": "xml"})
    assert res.status_code == 400
