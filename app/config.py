from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ZoneCast"
    secret_key: str = "change-me-in-production"
    session_max_age_seconds: int = 60 * 60 * 12  # 12h

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


settings = Settings()
settings.media_dir.mkdir(parents=True, exist_ok=True)
settings.backups_dir.mkdir(parents=True, exist_ok=True)
settings.data_dir.mkdir(parents=True, exist_ok=True)
