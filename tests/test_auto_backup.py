"""Scheduled configuration backups: local, FTP/FTPS (a real local server)
and SMB (a fake smbclient) — never a real remote host."""
import asyncio
import hashlib
import io
import sys
import tarfile
import threading
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.database import SessionLocal
from app.models import BackupPolicy
from app.services import auto_backup, backup_remote, backup_worker, bundle, crypto
from app.services.instance import instance_id

PASSWORD = "a-long-backup-password"


def _policy_body(**overrides):
    body = {"enabled": True, "frequency": "daily", "weekday": 0, "time_of_day": "02:30:00", "include_media": True,
            "keep_local": 3, "keep_remote": 14, "bundle_password": PASSWORD, "bundle_password_confirmed": True,
            "destination": "local"}
    body.update(overrides)
    return body


@pytest.fixture(autouse=True)
def _fresh_policy(client):
    """Each test starts with no policy and no backup files."""
    def wipe():
        db = SessionLocal()
        try:
            db.query(BackupPolicy).delete()
            db.commit()
        finally:
            db.close()
        for f in auto_backup.backup_dir().iterdir():
            f.unlink()
    wipe()
    yield
    wipe()


@pytest.fixture()
def in_process_build(monkeypatch):
    """The real child-process build is covered once; elsewhere it runs
    in-process to keep the suite fast."""
    async def build(out, password, include_media, manifest):
        from app.config import settings
        backup_worker.build(str(out), password=password, db_path=str(settings.db_path),
                            secret_key_path=str(settings.secret_key_path), media_dir=str(settings.media_dir),
                            backups_dir=str(settings.backups_dir), include_media=include_media, manifest=manifest)

    monkeypatch.setattr(auto_backup, "_build", build)


def _clock(monkeypatch):
    """Distinct timestamps for back-to-back runs."""
    start = datetime(2026, 10, 1, 2, 30)
    ticks = iter(range(10_000))
    monkeypatch.setattr(auto_backup, "utcnow", lambda: start + timedelta(minutes=next(ticks)))


def _run():
    asyncio.run(auto_backup.run_backup("manual"))


def _members(path: Path) -> dict:
    data = path.read_bytes()
    rest = data[len(bundle.MAGIC):]
    salt, token = rest[:bundle.SALT_LEN], rest[bundle.SALT_LEN:]
    from cryptography.fernet import Fernet
    tar_bytes = Fernet(bundle._derive_key(PASSWORD, salt)).decrypt(token)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        return {m.name: tar.extractfile(m).read() if m.isfile() else None for m in tar.getmembers()}


def test_a_local_backup_is_built_in_a_child_process_and_restorable(admin_client):
    from app.config import settings
    (settings.media_dir / "bell-for-backup.wav").write_bytes(b"RIFF-media")
    try:
        assert admin_client.put("/api/system/auto-backup", json=_policy_body()).status_code == 200
        _run()
        status = admin_client.get("/api/system/auto-backup").json()
        assert status["last_status"] == "ok", status
        files = admin_client.get("/api/system/auto-backup/files").json()
        assert [f["name"] for f in files] == [status["last_file"]] and files[0]["own"]
        assert status["last_file"].startswith(f"zonecast-{instance_id()[:8]}-")
        members = _members(auto_backup.backup_dir() / status["last_file"])
        assert "data/zonecast.db" in members and members["media/bell-for-backup.wav"] == b"RIFF-media"
        assert b'"include_media": true' in members["manifest.json"]
        res = admin_client.get(f"/api/system/auto-backup/files/{status['last_file']}")
        assert res.status_code == 200 and res.content.startswith(bundle.MAGIC)
    finally:
        (settings.media_dir / "bell-for-backup.wav").unlink(missing_ok=True)


def test_audio_files_can_be_left_out(admin_client, in_process_build):
    from app.config import settings
    (settings.media_dir / "left-out.wav").write_bytes(b"RIFF")
    try:
        admin_client.put("/api/system/auto-backup", json=_policy_body(include_media=False))
        _run()
        name = admin_client.get("/api/system/auto-backup").json()["last_file"]
        members = _members(auto_backup.backup_dir() / name)
        assert not any(m.startswith("media/") for m in members)
    finally:
        (settings.media_dir / "left-out.wav").unlink(missing_ok=True)


def test_local_retention_keeps_the_newest_and_never_other_installations(admin_client, in_process_build, monkeypatch):
    _clock(monkeypatch)
    foreign = auto_backup.backup_dir() / "zonecast-00000000-20200101T000000Z.zcbundle"
    foreign.write_bytes(b"not ours")
    admin_client.put("/api/system/auto-backup", json=_policy_body(keep_local=2))
    for _ in range(4):
        _run()
    names = [f["name"] for f in admin_client.get("/api/system/auto-backup/files").json()]
    ours = [n for n in names if n != foreign.name]
    assert len(ours) == 2 and foreign.name in names
    assert ours[0] == admin_client.get("/api/system/auto-backup").json()["last_file"]  # the newest two are kept


def test_settings_imported_from_another_machine_stay_paused(admin_client, in_process_build):
    admin_client.put("/api/system/auto-backup", json=_policy_body())
    db = SessionLocal()
    try:
        db.get(BackupPolicy, 1).instance_id = "f" * 32
        db.commit()
    finally:
        db.close()
    assert admin_client.post("/api/system/auto-backup/run").status_code == 409
    _run()
    status = admin_client.get("/api/system/auto-backup").json()
    assert status["last_status"] == "paused_foreign" and status["foreign"]
    assert admin_client.get("/api/system/auto-backup/files").json() == []
    assert any(a["code"] == "backup_paused_foreign" for a in admin_client.get("/api/system/alerts").json())
    admin_client.put("/api/system/auto-backup", json=_policy_body(bundle_password=None))  # re-saved here
    assert admin_client.get("/api/system/auto-backup").json()["foreign"] is False


def test_an_unreadable_saved_password_fails_loudly(admin_client, in_process_build):
    admin_client.put("/api/system/auto-backup", json=_policy_body())
    db = SessionLocal()
    try:
        db.get(BackupPolicy, 1).bundle_password_enc = "gAAAAA-not-decryptable-with-this-key"
        db.commit()
    finally:
        db.close()
    _run()
    status = admin_client.get("/api/system/auto-backup").json()
    assert status["last_status"] == "failed" and status["last_error_code"] == "backup.secret_changed"
    assert status["has_bundle_password"] is False
    assert admin_client.get("/api/system/auto-backup/files").json() == []
    assert any(a["code"] == "backup_failed" for a in admin_client.get("/api/system/alerts").json())
    with pytest.raises(crypto.DecryptError):
        crypto.decrypt_strict("garbage")


def test_a_scheduled_run_waits_while_audio_plays(admin_client, in_process_build, monkeypatch):
    admin_client.put("/api/system/auto-backup", json=_policy_body())
    busy = iter([True, True, False])
    monkeypatch.setattr(auto_backup, "_busy", lambda: next(busy, False))
    monkeypatch.setattr(auto_backup, "POSTPONE_STEP_SECONDS", 0.01)
    asyncio.run(auto_backup.run_backup("scheduled"))
    assert next(busy, "done") == "done"  # asked until free
    assert admin_client.get("/api/system/auto-backup").json()["last_status"] == "ok"


def test_policy_validation(admin_client):
    def put(**kw):
        return admin_client.put("/api/system/auto-backup", json=_policy_body(**kw))

    def code(res):
        assert res.status_code == 422, res.text
        return res.json()["detail"]["code"]

    assert code(put(bundle_password=None)) == "backup.bundle_password_required"
    assert code(put(bundle_password="short")) == "backup.bundle_password_short"
    assert code(put(bundle_password_confirmed=False)) == "backup.bundle_password_not_confirmed"
    assert code(put(destination="ftp", host="nas.local")) == "backup.ftp_insecure_not_confirmed"
    assert code(put(destination="ftps", host="bad/host")) == "backup.invalid_target"
    assert code(put(destination="smb", host="nas", share="bad\\share")) == "backup.invalid_target"
    assert code(put(destination="ftps", host="nas", remote_dir="backups/../../etc")) == "backup.invalid_target"
    ok = put(destination="ftps", host="nas.local", username="zc", password="remote-secret")
    assert ok.status_code == 200 and ok.json()["has_password"] and "password" not in ok.json()
    # the stored password is only used for the server it was typed for
    assert code(put(destination="ftps", host="evil.example", username="zc")) == "backup.password_required_after_change"
    assert put(destination="ftps", host="nas.local", username="zc").status_code == 200  # unchanged: kept
    assert admin_client.get("/api/system/auto-backup").json()["has_password"]


def test_backup_files_cannot_be_reached_outside_their_folder(admin_client):
    for name in ("..%2F..%2Fdata%2Fzonecast.db", "secret.key", "zonecast-12345678-20260101T000000Z.zcbundle"):
        assert admin_client.get(f"/api/system/auto-backup/files/{name}").status_code == 404
        assert admin_client.delete(f"/api/system/auto-backup/files/{name}").status_code == 404


def test_only_admins(client, admin_client):
    admin_client.post("/api/auth/users", json={"username": "op-backup", "password": "op-backup-password", "role": "operator"})
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as op:
        op.post("/api/auth/login", json={"username": "op-backup", "password": "op-backup-password"})
        assert op.get("/api/system/auto-backup").status_code == 403
        assert op.post("/api/system/auto-backup/run").status_code == 403


def test_the_public_theme_endpoint_exposes_nothing_else():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as anonymous:
        assert set(anonymous.get("/api/system/theme").json()) == {"theme_color"}


def test_the_job_follows_the_policy_and_catches_up_a_missed_run(admin_client):
    from app.services import scheduler
    admin_client.put("/api/system/auto-backup", json=_policy_body(frequency="weekly", weekday=2, time_of_day="03:15:00"))
    job = scheduler.get_scheduler().get_job(auto_backup.JOB_ID)
    assert job is not None and "day_of_week='2'" in str(job.trigger) and "hour='3'" in str(job.trigger)
    db = SessionLocal()
    try:
        db.get(BackupPolicy, 1).last_run_at = datetime(2020, 1, 1)
        db.commit()
    finally:
        db.close()
    auto_backup.register_job(startup=True)
    assert scheduler.get_scheduler().get_job(auto_backup.CATCH_UP_JOB_ID) is not None
    admin_client.put("/api/system/auto-backup", json=_policy_body(enabled=False, bundle_password=None))
    assert scheduler.get_scheduler().get_job(auto_backup.JOB_ID) is None


# --- FTP / FTPS against a local server ------------------------------------------

def _self_signed(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=2)).sign(key, hashes.SHA256()))
    pem = tmp_path / "server.pem"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    fingerprint = backup_remote.normalize_fingerprint(hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest())
    return pem, fingerprint


@pytest.fixture()
def ftp_server(tmp_path):
    pytest.importorskip("pyftpdlib")
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler, TLS_FTPHandler
    from pyftpdlib.servers import FTPServer

    servers = []

    def start(tls=False):
        root = tmp_path / ("ftps-root" if tls else "ftp-root")
        root.mkdir()
        authorizer = DummyAuthorizer()
        authorizer.add_user("zc", "remote-secret", str(root), perm="elradfmwMT")
        fingerprint = None
        if tls:
            pem, fingerprint = _self_signed(tmp_path)
            handler = type("Handler", (TLS_FTPHandler,), {"certfile": str(pem), "tls_control_required": True,
                                                          "tls_data_required": True})
        else:
            handler = type("Handler", (FTPHandler,), {})
        handler.authorizer = authorizer
        server = FTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, kwargs={"timeout": 0.05}, daemon=True).start()
        servers.append(server)
        return types.SimpleNamespace(root=root, port=server.socket.getsockname()[1], fingerprint=fingerprint)

    yield start
    for server in servers:
        server.close_all()


def test_plain_ftp_upload_with_remote_retention(admin_client, in_process_build, ftp_server, monkeypatch):
    _clock(monkeypatch)
    srv = ftp_server()
    body = _policy_body(destination="ftp", allow_insecure_ftp=True, host="127.0.0.1", port=srv.port,
                        remote_dir="zonecast/backups", username="zc", password="remote-secret",
                        keep_local=0, keep_remote=1)
    assert admin_client.put("/api/system/auto-backup", json=body).status_code == 200
    assert admin_client.post("/api/system/auto-backup/test", json=body).json()["ok"] is True
    (srv.root / "zonecast" / "backups").mkdir(parents=True, exist_ok=True)
    (srv.root / "zonecast" / "backups" / "someone-elses.zcbundle").write_bytes(b"x")
    _run()
    _run()
    status = admin_client.get("/api/system/auto-backup").json()
    assert status["last_status"] == "ok", status
    remote = sorted(p.name for p in (srv.root / "zonecast" / "backups").iterdir())
    assert remote == sorted([status["last_file"], "someone-elses.zcbundle"])  # retention touched only ours
    assert admin_client.get("/api/system/auto-backup/files").json() == []  # keep_local 0: not kept here


def test_ftps_needs_a_trusted_or_pinned_certificate(admin_client, in_process_build, ftp_server):
    srv = ftp_server(tls=True)
    body = _policy_body(destination="ftps", host="127.0.0.1", port=srv.port, username="zc", password="remote-secret")
    first = admin_client.post("/api/system/auto-backup/test", json=body).json()
    assert first["ok"] is False and first["code"] == "backup.tls_failed"
    assert first["fingerprint"] == srv.fingerprint  # shown so the admin can choose to trust it
    wrong = admin_client.post("/api/system/auto-backup/test", json={**body, "tls_fingerprint": "00" * 32}).json()
    assert wrong["code"] == "backup.tls_fingerprint_mismatch"
    pinned = {**body, "tls_fingerprint": srv.fingerprint}
    assert admin_client.post("/api/system/auto-backup/test", json=pinned).json()["ok"] is True
    admin_client.put("/api/system/auto-backup", json=pinned)
    _run()
    status = admin_client.get("/api/system/auto-backup").json()
    assert status["last_status"] == "ok", status
    assert (srv.root / status["last_file"]).stat().st_size > 0


def test_wrong_ftp_password_is_reported(admin_client, ftp_server):
    srv = ftp_server()
    body = _policy_body(destination="ftp", allow_insecure_ftp=True, host="127.0.0.1", port=srv.port,
                        username="zc", password="wrong")
    assert admin_client.post("/api/system/auto-backup/test", json=body).json()["code"] == "backup.auth_failed"


# --- SMB through a fake smbclient ------------------------------------------------

@pytest.fixture()
def fake_smb(tmp_path, monkeypatch):
    root = tmp_path / "smb"
    root.mkdir()
    sessions, ports, fail = [], [], {}

    class LogonFailure(Exception):
        pass

    def local(path):
        parts = [p for p in path.split("\\") if p]
        assert parts[0] == "nas" and parts[1] == "backups"
        return root.joinpath(*parts[2:])

    def op(fn):
        """Every file operation must reuse the registered host:port session."""
        def call(*args, **kw):
            ports.append(kw.get("port"))
            if fn.__name__ in fail:
                raise fail.pop(fn.__name__)
            return fn(*args, **{k: v for k, v in kw.items() if k == "mode"})
        return call

    def makedirs(path):
        local(path).mkdir(parents=True, exist_ok=True)

    def open_file(path, mode="rb"):
        return open(local(path), mode)

    def rename(src, dst):
        local(src).rename(local(dst))

    def stat(path):
        return local(path).stat()

    def listdir(path):
        return [p.name for p in local(path).iterdir()]

    def remove(path):
        local(path).unlink()

    fake = types.SimpleNamespace(
        ClientConfig=lambda **kw: None,
        register_session=lambda host, username=None, password=None, **kw: (
            sessions.append((host, username, kw.get("encrypt"), kw.get("port"))) if password == "smb-secret"
            else (_ for _ in ()).throw(LogonFailure("bad credentials"))),
        delete_session=lambda host, port=445: None,
        makedirs=op(makedirs), open_file=op(open_file), rename=op(rename), stat=op(stat),
        listdir=op(listdir), remove=op(remove),
    )
    monkeypatch.setitem(sys.modules, "smbclient", fake)
    return types.SimpleNamespace(root=root, sessions=sessions, ports=ports, fail=fail)


def test_smb_upload_with_encryption(admin_client, in_process_build, fake_smb):
    body = _policy_body(destination="smb", host="nas", share="backups", remote_dir="zonecast",
                        username="DOMAIN\\zc", password="smb-secret")
    assert admin_client.put("/api/system/auto-backup", json=body).status_code == 200
    _run()
    status = admin_client.get("/api/system/auto-backup").json()
    assert status["last_status"] == "ok", status
    assert (fake_smb.root / "zonecast" / status["last_file"]).exists()
    assert fake_smb.sessions[-1] == ("nas", "DOMAIN\\zc", True, 445)
    bad = admin_client.post("/api/system/auto-backup/test", json={**body, "password": "nope"}).json()
    assert bad["ok"] is False and bad["code"] == "backup.auth_failed"


def test_retention_rules():
    own = f"zonecast-{instance_id()[:8]}"
    names = [f"{own}-2026010{i}T000000Z.zcbundle" for i in range(1, 6)] + ["zonecast-00000000-20260109T000000Z.zcbundle"]
    current = names[0]  # e.g. the clock was set back: the new file sorts oldest
    doomed = auto_backup._retention(names, 2, current)
    assert current not in doomed and "zonecast-00000000-20260109T000000Z.zcbundle" not in doomed
    assert sorted(doomed) == sorted(names[1:3])


# --- second review round -------------------------------------------------------

def test_a_fresh_install_owns_its_backup_settings(admin_client):
    status = admin_client.get("/api/system/auto-backup").json()
    assert status["foreign"] is False
    res = admin_client.post("/api/system/auto-backup/run")  # no password yet: said so, translated
    assert res.status_code == 422 and res.json()["detail"]["code"] == "backup.bundle_password_required"


def test_smb_on_another_port_uses_that_session_everywhere(admin_client, in_process_build, fake_smb):
    body = _policy_body(destination="smb", host="nas", port=4445, share="backups", remote_dir="zonecast",
                        username="zc", password="smb-secret")
    admin_client.put("/api/system/auto-backup", json=body)
    _run()
    assert admin_client.get("/api/system/auto-backup").json()["last_status"] == "ok"
    assert fake_smb.sessions[-1][3] == 4445 and fake_smb.ports and set(fake_smb.ports) == {4445}


def test_smb_errors_after_login_are_told_apart(admin_client, fake_smb):
    body = _policy_body(destination="smb", host="nas", share="backups", username="zc", password="smb-secret")

    class SMBOSError(OSError):
        def __init__(self, status):
            super().__init__(f"status 0x{status:08x}")
            self.ntstatus = status

    fake_smb.fail["open_file"] = SMBOSError(0xC0000022)
    assert admin_client.post("/api/system/auto-backup/test", json=body).json()["code"] == "backup.access_denied"
    fake_smb.fail["makedirs"] = SMBOSError(0xC00000CC)
    assert admin_client.post("/api/system/auto-backup/test", json=body).json()["code"] == "backup.share_not_found"
    fake_smb.fail["open_file"] = SMBOSError(0xC000007F)  # disk full
    assert admin_client.post("/api/system/auto-backup/test", json=body).json()["code"] == "backup.upload_failed"


def test_an_interrupted_smb_upload_leaves_no_part_file(admin_client, in_process_build, fake_smb):
    body = _policy_body(destination="smb", host="nas", share="backups", remote_dir="zonecast",
                        username="zc", password="smb-secret")
    admin_client.put("/api/system/auto-backup", json=body)
    fake_smb.fail["rename"] = ConnectionResetError("link dropped")
    _run()
    assert admin_client.get("/api/system/auto-backup").json()["last_status"] == "failed"
    assert list((fake_smb.root / "zonecast").iterdir()) == []


def test_failed_uploads_do_not_pile_up_locally(admin_client, in_process_build, monkeypatch):
    _clock(monkeypatch)
    body = _policy_body(destination="ftp", allow_insecure_ftp=True, host="127.0.0.1", port=9,  # nothing listens
                        username="zc", password="x", keep_local=0)
    admin_client.put("/api/system/auto-backup", json=body)
    for _ in range(3):
        _run()
    status = admin_client.get("/api/system/auto-backup").json()
    assert status["last_status"] == "failed" and status["last_error_code"] == "backup.connect_failed"
    assert len(admin_client.get("/api/system/auto-backup/files").json()) == 1  # only the newest unsent one


def test_errors_without_technical_detail_are_not_repeated(admin_client, in_process_build):
    admin_client.put("/api/system/auto-backup", json=_policy_body())
    db = SessionLocal()
    try:
        db.get(BackupPolicy, 1).bundle_password_enc = None
        db.commit()
    finally:
        db.close()
    _run()
    status = admin_client.get("/api/system/auto-backup").json()
    assert status["last_error_code"] == "backup.bundle_password_required" and status["last_error"] is None


def test_settings_changed_while_a_run_waits_apply_to_it(admin_client, in_process_build, monkeypatch):
    admin_client.put("/api/system/auto-backup", json=_policy_body())
    calls = []

    def busy():
        if not calls:  # during the wait, someone turns backups off
            db = SessionLocal()
            try:
                db.get(BackupPolicy, 1).enabled = False
                db.commit()
            finally:
                db.close()
        calls.append(1)
        return len(calls) < 2

    monkeypatch.setattr(auto_backup, "_busy", busy)
    monkeypatch.setattr(auto_backup, "POSTPONE_STEP_SECONDS", 0.01)
    asyncio.run(auto_backup.run_backup("scheduled"))
    assert admin_client.get("/api/system/auto-backup/files").json() == []


def test_the_build_child_never_applies_a_staged_import(tmp_path, monkeypatch):
    from app.services import pending_import

    staging = pending_import.STAGING_DIR
    (staging / "media").mkdir(parents=True)
    (staging / "media" / "partial.wav").write_bytes(b"half")
    out = auto_backup.backup_dir() / f"zonecast-{instance_id()[:8]}-20260101T000000Z.zcbundle"
    try:
        asyncio.run(auto_backup._build(out, PASSWORD, False, {}))
        assert out.exists() and (staging / "media" / "partial.wav").exists()
    finally:
        import shutil
        shutil.rmtree(staging, ignore_errors=True)
        out.unlink(missing_ok=True)


def test_a_build_that_takes_too_long_is_killed_without_blocking(admin_client, monkeypatch):
    import time as _time
    admin_client.put("/api/system/auto-backup", json=_policy_body())
    monkeypatch.setattr(auto_backup, "BUILD_TIMEOUT_SECONDS", 0.05)
    started = _time.monotonic()
    _run()
    assert _time.monotonic() - started < 10
    status = admin_client.get("/api/system/auto-backup").json()
    assert status["last_status"] == "failed" and status["last_error_code"] == "backup.build_failed"
    assert admin_client.get("/api/system/auto-backup/files").json() == []


def test_automatic_backups_restore_from_the_command_line(tmp_path, monkeypatch):
    """The bundle's manifest.json must not be written into the (root-owned)
    install directory, and a bundle from a newer version is refused."""
    import sqlite3

    from app.config import settings
    from app.services import pending_import
    from app.tools import import_bundle

    src = tmp_path / "src.db"
    con = sqlite3.connect(src)
    con.execute("create table alembic_version (version_num varchar(32) not null)")
    con.execute("insert into alembic_version values ('0005')")
    con.commit()
    con.close()
    (tmp_path / "m").mkdir()
    (tmp_path / "b").mkdir()
    data = bundle.build_export(password=PASSWORD, db_path=src, secret_key_path=tmp_path / "none.key",
                               media_dir=tmp_path / "m", backups_dir=tmp_path / "b", manifest={"include_media": True})
    target = tmp_path / "install"
    extracted = bundle.extract_bundle(data=data, password=PASSWORD, target_root=target)
    assert "manifest.json" not in extracted and not (target / "manifest.json").exists()

    site = tmp_path / "site"
    (site / "data").mkdir(parents=True)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{(site / 'data' / 'zonecast.db').as_posix()}")
    monkeypatch.setattr(settings, "data_dir", site / "data")
    monkeypatch.setattr(settings, "media_dir", site / "media")
    monkeypatch.setattr(settings, "backups_dir", site / "backups")
    monkeypatch.setattr(pending_import, "STAGING_DIR", site / "data" / "_pending_import")
    bundle_file = tmp_path / "x.zcbundle"
    bundle_file.write_bytes(data)
    monkeypatch.setattr(sys, "argv", ["import_bundle", str(bundle_file), "--password", PASSWORD, "--yes"])
    assert import_bundle.main() == 0
    assert (site / "data" / "zonecast.db").exists()

    con = sqlite3.connect(src)
    con.execute("update alembic_version set version_num = '0999'")
    con.commit()
    con.close()
    bundle_file.write_bytes(bundle.build_export(password=PASSWORD, db_path=src, secret_key_path=tmp_path / "none.key",
                                                media_dir=tmp_path / "m", backups_dir=tmp_path / "b"))
    assert import_bundle.main() == 1  # newer schema: refused, nothing replaced
