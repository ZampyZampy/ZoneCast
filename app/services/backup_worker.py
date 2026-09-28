"""
Runs in a child process (see auto_backup._build): building a bundle
holds it in memory several times over and keeps a CPU busy for a while,
which must not happen inside the process that paces the RTP audio.
Imports nothing that opens the database.
"""
import os
from pathlib import Path


def build(out_path: str, *, password: str, db_path: str, secret_key_path: str, media_dir: str, backups_dir: str,
          include_media: bool, manifest: dict) -> int:
    if hasattr(os, "nice"):
        try:
            os.nice(10)
        except OSError:
            pass
    from .bundle import build_export

    data = build_export(password=password, db_path=Path(db_path), secret_key_path=Path(secret_key_path),
                        media_dir=Path(media_dir), backups_dir=Path(backups_dir), include_media=include_media,
                        compresslevel=1, manifest=manifest)  # audio doesn't compress: don't burn CPU trying
    out = Path(out_path)
    tmp = out.with_name(f".{out.name}.tmp")
    tmp.write_bytes(data)
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, out)
    return len(data)
