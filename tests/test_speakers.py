def _speaker_payload(**overrides):
    payload = {
        "name": "Test Speaker",
        "ip_address": "10.0.0.50",
        "own_multicast_address": "239.5.5.1",
        "own_multicast_port": 5004,
    }
    payload.update(overrides)
    return payload


def test_create_speaker_requires_authentication(client):
    res = client.post("/api/speakers", json=_speaker_payload())
    assert res.status_code == 401


def test_create_and_list_speaker(admin_client):
    res = admin_client.post("/api/speakers", json=_speaker_payload())
    assert res.status_code == 200, res.text
    created = res.json()
    assert created["name"] == "Test Speaker"
    assert created["status"] == "unknown"

    res = admin_client.get("/api/speakers")
    assert res.status_code == 200
    assert any(s["id"] == created["id"] for s in res.json())


def test_duplicate_ip_rejected(admin_client):
    res = admin_client.post("/api/speakers", json=_speaker_payload(ip_address="10.0.0.55", own_multicast_address="239.5.5.2"))
    assert res.status_code == 200

    res = admin_client.post("/api/speakers", json=_speaker_payload(name="Other", ip_address="10.0.0.55", own_multicast_address="239.5.5.3"))
    assert res.status_code == 400
    assert "IP" in res.json()["detail"]


def test_multicast_address_conflict_rejected(admin_client):
    res = admin_client.post("/api/speakers", json=_speaker_payload(ip_address="10.0.0.60", own_multicast_address="239.5.5.10"))
    assert res.status_code == 200

    res = admin_client.post("/api/speakers", json=_speaker_payload(
        name="Other", ip_address="10.0.0.61", own_multicast_address="239.5.5.10",
    ))
    assert res.status_code == 400


def test_delete_speaker_detaches_backups_instead_of_failing(admin_client):
    res = admin_client.post("/api/speakers", json=_speaker_payload(ip_address="10.0.0.70", own_multicast_address="239.5.5.20"))
    speaker_id = res.json()["id"]

    res = admin_client.delete(f"/api/speakers/{speaker_id}")
    assert res.status_code == 200

    res = admin_client.get("/api/speakers")
    assert all(s["id"] != speaker_id for s in res.json())
