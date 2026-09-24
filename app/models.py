import enum
from datetime import datetime, date, time

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Date, Time, Float,
    ForeignKey, Enum as SAEnum, Text
)
from sqlalchemy.orm import relationship

from .database import Base
from .db_types import EncryptedString


class UserRole(str, enum.Enum):
    admin = "admin"
    operator = "operator"


class SpeakerStatus(str, enum.Enum):
    unknown = "unknown"
    online = "online"
    offline = "offline"


class TargetType(str, enum.Enum):
    speaker = "speaker"
    zone = "zone"
    all = "all"


class PlaybackSource(str, enum.Enum):
    manual = "manual"
    schedule = "schedule"


class PlaybackStatus(str, enum.Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    stopped = "stopped"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(128), default="")
    role = Column(SAEnum(UserRole), default=UserRole.operator, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Optional per-user TOTP two-factor auth — self-service (see
    # routers/auth.py's /me/2fa/* endpoints). totp_secret is only
    # meaningful once totp_enabled is True; while a setup is pending
    # confirmation the candidate secret lives here with enabled=False.
    totp_secret = Column(EncryptedString(128), nullable=True)
    totp_enabled = Column(Boolean, default=False, nullable=False)
    # JSON list of bcrypt-hashed one-time recovery codes, consumed one
    # at a time as a fallback if the authenticator device is lost.
    totp_recovery_codes = Column(Text, nullable=True)

    @property
    def is_protected(self) -> bool:
        """The bootstrap admin account — kept undeletable so there's
        always a way back in."""
        from .config import settings
        return self.username == settings.default_admin_username


class Zone(Base):
    __tablename__ = "zones"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), unique=True, nullable=False)
    description = Column(String(255), default="")
    multicast_address = Column(String(64), nullable=False)
    multicast_port = Column(Integer, nullable=False, default=5004)
    created_at = Column(DateTime, default=datetime.utcnow)

    speakers = relationship("Speaker", back_populates="zone")


class Speaker(Base):
    __tablename__ = "speakers"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    ip_address = Column(String(64), unique=True, nullable=False)
    http_port = Column(Integer, default=80)
    http_username = Column(String(64), default="admin")
    # Encrypted at rest (Fernet, key in data/secret.key) — see
    # db_types.EncryptedString / services/crypto.py. Transparent to
    # every driver/router: they read/write speaker.http_password as a
    # normal plaintext string, encryption happens at the DB boundary.
    http_password = Column(EncryptedString(255), default="")
    brand = Column(String(64), default="Fanvil")
    model = Column(String(64), default="A233")
    location = Column(String(128), default="")
    status = Column(SAEnum(SpeakerStatus), default=SpeakerStatus.unknown, nullable=False)
    last_seen = Column(DateTime, nullable=True)

    # Dedicated multicast group this speaker listens to (provisioned on the
    # device itself) — used to target this single speaker without affecting
    # others in the same zone.
    own_multicast_address = Column(String(64), nullable=False)
    own_multicast_port = Column(Integer, nullable=False, default=5004)

    # Playback volume for paging/multicast announcements on this device
    # — None means "leave the device's own current setting alone"
    # (never overwritten), otherwise "default" or "1".."9", matching
    # the Fanvil MCAST_Volume_R field's own value set exactly (see
    # drivers/fanvil_http.py). Brand-specific by nature — only applied
    # when supports_paging_volume is True for this speaker's driver.
    paging_volume = Column(String(10), nullable=True)

    zone_id = Column(Integer, ForeignKey("zones.id"), nullable=True)
    zone = relationship("Zone", back_populates="speakers")

    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def supports_auto_config(self) -> bool:
        """Whether a driver is registered for this speaker's brand and
        supports pushing the multicast paging list automatically —
        drives whether the dashboard shows "Applica ora" or "Config.
        manuale". Lazy import to avoid a services->models->services
        circular import at module load time."""
        from .services.drivers import get_driver
        driver = get_driver(self.brand)
        return bool(driver and driver.supports_multicast_push)

    @property
    def supports_paging_volume(self) -> bool:
        from .services.drivers import get_driver
        driver = get_driver(self.brand)
        return bool(driver and driver.supports_paging_volume)

    @property
    def supports_config_backup(self) -> bool:
        from .services.drivers import get_driver
        driver = get_driver(self.brand)
        return bool(driver and driver.supports_config_backup)


class Media(Base):
    __tablename__ = "media"

    id = Column(Integer, primary_key=True)
    original_filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), nullable=False, unique=True)
    # Cached 8kHz mono 16-bit PCM WAV, ready for RTP/G.711 streaming
    pcm_filename = Column(String(255), nullable=True)
    duration_seconds = Column(Float, default=0.0)
    size_bytes = Column(Integer, default=0)
    content_type = Column(String(64), default="")
    uploaded_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    # Populated by services/audio_analysis.py right after upload (and
    # refreshed after /normalize) — see its module docstring for why
    # these particular bands/thresholds were chosen for PA horns.
    peak_db = Column(Float, nullable=True)
    mean_db = Column(Float, nullable=True)
    band_low_pct = Column(Float, nullable=True)
    band_mid_pct = Column(Float, nullable=True)
    band_high_pct = Column(Float, nullable=True)
    suggested_gain_db = Column(Float, nullable=True)
    normalized = Column(Boolean, default=False, nullable=False)

    @property
    def low_freq_warning(self) -> bool:
        return self.band_low_pct is not None and self.band_low_pct >= 50

    @property
    def frequency_note(self) -> str | None:
        if self.band_low_pct is None:
            return None
        if self.band_low_pct >= 50:
            return f"Bassi dominanti ({self.band_low_pct:.0f}%) — su una tromba PA il suono può risultare debole o poco chiaro."
        if self.band_low_pct >= 30:
            return f"Presenza significativa di bassi ({self.band_low_pct:.0f}%) — le trombe li riproducono con qualche difficoltà."
        return f"Buon bilanciamento per trombe PA (bassi {self.band_low_pct:.0f}%, medi {self.band_mid_pct:.0f}%, alti {self.band_high_pct:.0f}%)."

    @property
    def headroom_note(self) -> str | None:
        if self.peak_db is None:
            return None
        if self.suggested_gain_db:
            return f"Picco a {self.peak_db:.1f} dBFS — puoi amplificare di +{self.suggested_gain_db:.1f} dB senza distorsione."
        return f"Picco già vicino al massimo ({self.peak_db:.1f} dBFS) — nessuna amplificazione necessaria."


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    media_id = Column(Integer, ForeignKey("media.id"), nullable=False)
    media = relationship("Media")

    target_type = Column(SAEnum(TargetType), nullable=False)
    target_id = Column(Integer, nullable=True)  # speaker.id or zone.id, null when target_type=all

    time_of_day = Column(Time, nullable=False)
    # comma-separated 3-letter days: mon,tue,wed,thu,fri,sat,sun
    days_of_week = Column(String(64), nullable=False, default="mon,tue,wed,thu,fri")

    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)

    exclude_holidays = Column(Boolean, default=True)   # skip run on public holidays
    holidays_only = Column(Boolean, default=False)      # run ONLY on public holidays
    # ISO 3166-1 alpha-2 country code for the holiday calendar above —
    # see services/scheduler.py's HOLIDAY_COUNTRIES for the supported set.
    holiday_country = Column(String(8), nullable=False, default="IT")

    enabled = Column(Boolean, default=True, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AppSettings(Base):
    """Single-row table (id is always 1) for global, app-wide UI settings
    — shared across every user's dashboard, not a per-browser preference."""
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, default=1)
    theme_color = Column(String(16), default="#464c54", nullable=False)
    # Days of history to keep in event_logs; NULL = "mai" (never
    # auto-delete by age — the DBLogHandler's row-count safety net in
    # services/event_log.py still applies regardless, to protect
    # against unbounded disk growth).
    log_retention_days = Column(Integer, nullable=True)


class SpeakerConfigBackup(Base):
    """A snapshot of a Fanvil speaker's own configuration export
    (fetched from the device, e.g. /default_user_config.txt), kept so
    an admin can inspect or restore it manually. Contains the device's
    own credentials/SIP settings — admin-only, never exposed in the
    regular speaker API.

    Deliberately outlives the speaker it was taken from: speaker_id is
    nullable and set to NULL (not cascade-deleted) when the speaker
    record is removed, and speaker_name/speaker_ip are snapshotted at
    creation time so the backup stays identifiable and downloadable
    even after the speaker it came from no longer exists."""
    __tablename__ = "speaker_config_backups"

    id = Column(Integer, primary_key=True)
    speaker_id = Column(Integer, ForeignKey("speakers.id"), nullable=True)
    speaker = relationship("Speaker")
    speaker_name = Column(String(128), nullable=False, default="")
    speaker_ip = Column(String(64), nullable=False, default="")

    format = Column(String(8), nullable=False)  # "txt" | "xml"
    stored_filename = Column(String(255), nullable=False, unique=True)
    size_bytes = Column(Integer, default=0)

    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_by = relationship("User")
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def created_by_name(self) -> str | None:
        if not self.created_by:
            return None
        return self.created_by.full_name or self.created_by.username

    @property
    def speaker_exists(self) -> bool:
        return self.speaker_id is not None


class EventLog(Base):
    """Persistent application event/debug log — admin-only, survives
    container restarts. Populated automatically from every
    `logging.getLogger("zonecast.*")` call (see
    services/event_log.py's DBLogHandler) rather than requiring each
    call site to log twice."""
    __tablename__ = "event_logs"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    level = Column(String(16), nullable=False)
    logger_name = Column(String(128), nullable=False)
    message = Column(Text, nullable=False)


class PlaybackLog(Base):
    __tablename__ = "playback_logs"

    id = Column(Integer, primary_key=True)
    media_id = Column(Integer, ForeignKey("media.id"), nullable=False)
    media = relationship("Media")

    target_type = Column(SAEnum(TargetType), nullable=False)
    target_id = Column(Integer, nullable=True)
    target_label = Column(String(128), default="")

    source = Column(SAEnum(PlaybackSource), default=PlaybackSource.manual, nullable=False)
    schedule_id = Column(Integer, ForeignKey("schedules.id"), nullable=True)
    triggered_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    triggered_by = relationship("User")

    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(SAEnum(PlaybackStatus), default=PlaybackStatus.running, nullable=False)
    error_message = Column(String(500), default="")

    @property
    def triggered_by_name(self) -> str | None:
        if not self.triggered_by:
            return None
        return self.triggered_by.full_name or self.triggered_by.username
