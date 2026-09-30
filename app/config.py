import logging
import os
import secrets
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ZoneCast"
    secret_key: str = "change-me-in-production"
    session_max_age_seconds: int = 60 * 60 * 12  # 12h without activity (main.SlidingSession)

    data_dir: Path = BASE_DIR / "data"
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'zonecast.db'}"
    media_dir: Path = BASE_DIR / "media"
    backups_dir: Path = BASE_DIR / "backups"

    # Default admin bootstrap (only used if no users exist yet)
    default_admin_username: str = "admin"
    default_admin_password: str = "admin"

    # RTP / multicast paging
    rtp_payload_type: int = 0  # 0 = PCMU (G.711 u-law), 8 = PCMA (G.711 a-law)
    rtp_packet_ms: int = 20
    rtp_multicast_ttl: int = 8
    global_all_call_address: str = "239.1.1.99"
    global_all_call_port: int = 5004

    # Timezone used by the scheduler / holiday calculation
    timezone: str = "Europe/Rome"

    max_upload_mb: int = 50
    max_duration_seconds: int = 600
    # Scheduled backups (Sistema > Backup automatico): refuse to build a
    # bundle bigger than this (it's built in memory, in a child process).
    backup_max_bundle_mb: int = 500

    # Listening port for uvicorn — read by the Dockerfile CMD and by
    # zonecast.service's ExecStart (both expand ${APP_PORT} from this
    # same .env at process start, not by FastAPI itself). Ports below
    # 1024 (e.g. 80) need CAP_NET_BIND_SERVICE, already granted to the
    # app user in both the Dockerfile and zonecast.service.
    app_port: int = 8000

    @property
    def db_path(self) -> Path:
        # database_url is "sqlite:///<path>" — the path may be relative
        # (e.g. the deployed .env uses "./data/zonecast.db", resolved
        # against the process's cwd, which Docker/systemd always set to
        # BASE_DIR) or absolute (the in-code default above).
        raw = self.database_url.removeprefix("sqlite:///")
        path = Path(raw)
        return path if path.is_absolute() else (BASE_DIR / path).resolve()

    @property
    def secret_key_path(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def session_key_path(self) -> Path:
        return self.data_dir / "session.key"


settings = Settings()
settings.media_dir.mkdir(parents=True, exist_ok=True)
settings.backups_dir.mkdir(parents=True, exist_ok=True)
settings.data_dir.mkdir(parents=True, exist_ok=True)

# Values shipped in the code / .env.example — anyone can read them, so a
# session cookie signed with one of them can be forged at will.
_PLACEHOLDER_SECRET_KEYS = {"", "change-me-in-production", "replace-with-a-long-random-string"}


def session_secret() -> str:
    """The key SessionMiddleware signs cookies with: SECRET_KEY when
    it's been set to something real, otherwise a random one generated
    once and kept in data/session.key (so sessions survive restarts)."""
    if settings.secret_key.strip() not in _PLACEHOLDER_SECRET_KEYS:
        return settings.secret_key
    path = settings.session_key_path
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    # An empty/truncated file (crash mid-write, or `> session.key` to log
    # everyone out) must never be trusted: an empty key signs forgeable
    # cookies. Regenerate instead.
    if len(existing) >= 32:
        return existing
    key = secrets.token_hex(32)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    logging.getLogger("zonecast").warning(
        "SECRET_KEY non impostata (valore di esempio): generata una chiave casuale in %s", path
    )
    return key
