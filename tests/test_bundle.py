import io
import sqlite3
import tarfile

import pytest
from cryptography.fernet import Fernet

from app.services import bundle


def _encrypt_tar(members, password="pw"):
    """Builds a bundle the same way build_export does, from raw tar
    members — lets a test craft what build_export itself never would."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for info, payload in members:
            tar.addfile(info, io.BytesIO(payload) if payload is not None else None)
    salt = b"0123456789abcdef"
    token = Fernet(bundle._derive_key(password, salt)).encrypt(buf.getvalue())
    return bundle.MAGIC + salt + token


def _file(name, payload=b"x"):
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    return info, payload


def test_export_import_round_trip(tmp_path):
    db = tmp_path / "src" / "zonecast.db"
    db.parent.mkdir()
    con = sqlite3.connect(db)
    con.execute("create table t(x)")
    con.execute("insert into t values (42)")
    con.commit()
    con.close()
    key = tmp_path / "src" / "secret.key"
    key.write_bytes(b"k" * 44)
    media = tmp_path / "src" / "media"
    (media / "sub").mkdir(parents=True)
    (media / "sub" / "a.wav").write_bytes(b"RIFF")

    data = bundle.build_export(password="pw", db_path=db, secret_key_path=key,
                               media_dir=media, backups_dir=tmp_path / "src" / "missing")
    out = tmp_path / "out"
    names = bundle.extract_bundle(data=data, password="pw", target_root=out)

    assert set(names) == {"data/zonecast.db", "data/secret.key", "media/sub/a.wav"}
    assert sqlite3.connect(out / "data" / "zonecast.db").execute("select x from t").fetchone() == (42,)
    assert (out / "media" / "sub" / "a.wav").read_bytes() == b"RIFF"


def test_wrong_password_rejected(tmp_path):
    data = _encrypt_tar([_file("data/zonecast.db")])
    with pytest.raises(ValueError):
        bundle.extract_bundle(data=data, password="nope", target_root=tmp_path / "out")


@pytest.mark.parametrize("name", ["../evil.txt", "/abs/evil.txt", "../out_sibling/evil.txt"])
def test_paths_outside_target_rejected(tmp_path, name):
    data = _encrypt_tar([_file(name)])
    with pytest.raises(ValueError):
        bundle.extract_bundle(data=data, password="pw", target_root=tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / "out_sibling").exists()


def test_symlink_members_rejected(tmp_path):
    link = tarfile.TarInfo("media")
    link.type = tarfile.SYMTYPE
    link.linkname = str(tmp_path / "elsewhere")
    data = _encrypt_tar([(link, None), _file("media/evil.txt")])
    with pytest.raises(ValueError):
        bundle.extract_bundle(data=data, password="pw", target_root=tmp_path / "out")
    assert not (tmp_path / "elsewhere").exists()


def test_apply_pending_import_drops_stale_wal(tmp_path, monkeypatch):
    from app.config import settings
    from app.services import pending_import

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    live_db = data_dir / "zonecast.db"
    con = sqlite3.connect(live_db)
    con.execute("create table old_db(x)")
    con.commit()
    con.close()
    (data_dir / "zonecast.db-wal").write_bytes(b"leftover from the old database")

    staging = tmp_path / "staging"
    (staging / "data").mkdir(parents=True)
    con = sqlite3.connect(staging / "data" / "zonecast.db")
    con.execute("create table imported_db(x)")
    con.commit()
    con.close()

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{live_db.as_posix()}")
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    monkeypatch.setattr(settings, "backups_dir", tmp_path / "backups")
    monkeypatch.setattr(pending_import, "STAGING_DIR", staging)

    pending_import.apply_pending_import()

    assert not (data_dir / "zonecast.db-wal").exists()
    con = sqlite3.connect(live_db)
    tables = {r[0] for r in con.execute("select name from sqlite_master")}
    con.close()
    assert "imported_db" in tables and "old_db" not in tables
    backups = list(data_dir.glob("zonecast.db.pre-import-*"))
    assert len(backups) == 1
    con = sqlite3.connect(backups[0])
    assert "old_db" in {r[0] for r in con.execute("select name from sqlite_master")}
    con.close()
    assert not staging.exists()


def test_export_with_linked_media_is_still_importable(tmp_path):
    import os

    db = tmp_path / "zonecast.db"
    sqlite3.connect(db).close()
    media = tmp_path / "media"
    media.mkdir()
    (media / "a.wav").write_bytes(b"RIFF")
    os.link(media / "a.wav", media / "b.wav")

    data = bundle.build_export(password="pw", db_path=db, secret_key_path=tmp_path / "none",
                               media_dir=media, backups_dir=tmp_path / "none")
    out = tmp_path / "out"
    names = bundle.extract_bundle(data=data, password="pw", target_root=out)
    assert {"media/a.wav", "media/b.wav"} <= set(names)
    assert (out / "media" / "b.wav").read_bytes() == b"RIFF"
