"""
A random id for this installation, kept in data/instance.id. It is not
part of exported bundles (those carry only the database and the
encryption key), so a restored or copied database can tell it's running
on another machine — see services/auto_backup.py.
"""
import uuid

from ..config import settings

_cached: str | None = None


def instance_id() -> str:
    global _cached
    if _cached is None:
        path = settings.data_dir / "instance.id"
        try:
            value = path.read_text(encoding="ascii").strip()
        except OSError:
            value = ""
        if len(value) != 32:
            value = uuid.uuid4().hex
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="ascii")
            try:
                path.chmod(0o600)
            except OSError:
                pass
        _cached = value
    return _cached
