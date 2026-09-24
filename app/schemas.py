import re
from datetime import datetime, date, time
from typing import Optional
from pydantic import BaseModel, ConfigDict, field_validator

from .models import UserRole, SpeakerStatus, TargetType, PlaybackSource, PlaybackStatus


# ---------- Auth ----------
class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResult(BaseModel):
    """Returned by POST /login. When the account has 2FA enabled, the
    session is NOT established yet — `requires_2fa` is true and the
    client must call POST /login/2fa with a TOTP or recovery code
    before `user` is populated."""
    requires_2fa: bool = False
    user: Optional["UserOut"] = None


class TwoFactorLoginRequest(BaseModel):
    code: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    full_name: str
    role: UserRole
    is_active: bool
    is_protected: bool = False  # default admin account — cannot be deleted
    totp_enabled: bool = False


LoginResult.model_rebuild()


class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str = ""
    role: UserRole = UserRole.operator


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    new_password: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ---------- Two-factor auth (self-service) ----------
class TwoFactorSetupOut(BaseModel):
    secret: str
    otpauth_uri: str
    qr_svg: str


class TwoFactorConfirmRequest(BaseModel):
    code: str


class TwoFactorConfirmOut(BaseModel):
    recovery_codes: list[str]


class TwoFactorDisableRequest(BaseModel):
    password: str


class TwoFactorStatusOut(BaseModel):
    enabled: bool
    remaining_recovery_codes: int = 0


# ---------- System time ----------
class TimeStatusOut(BaseModel):
    local_time: str
    timezone: str
    ntp_synchronized: bool
    ntp_servers: list[str]
    ntp_enabled: Optional[bool] = None
    controllable: bool = False


class SetManualTimeRequest(BaseModel):
    datetime_local: datetime


class SetNtpEnabledRequest(BaseModel):
    enabled: bool


class SetTimezoneRequest(BaseModel):
    timezone: str


class SetNtpServersRequest(BaseModel):
    servers: list[str]


class AlertOut(BaseModel):
    severity: str
    message: str


class HostResourcesOut(BaseModel):
    disk_total_bytes: int
    disk_used_bytes: int
    disk_percent: float
    mem_total_bytes: int
    mem_used_bytes: int
    mem_percent: float
    cpu_percent: float
    cpu_count: int


# ---------- Network configuration (native install only) ----------
class NetworkStatusOut(BaseModel):
    interface: str
    address_cidr: Optional[str] = None
    gateway: Optional[str] = None
    dns_servers: list[str] = []
    available_interfaces: list[str] = []


class NetworkApplyRequest(BaseModel):
    interface: str
    address_cidr: str
    gateway: str
    dns_servers: list[str]


class NetworkApplyOut(BaseModel):
    watchdog_seconds: int


# ---------- System / export ----------
class ExportRequest(BaseModel):
    password: str = ""


class ImportStagedOut(BaseModel):
    ok: bool
    message: str


# ---------- Event log ----------
class EventLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    level: str
    logger_name: str
    message: str


class LogSettingsOut(BaseModel):
    retention_days: Optional[int] = None  # None = "mai"


class LogSettingsUpdate(BaseModel):
    retention_days: Optional[int] = None

    @field_validator("retention_days")
    @classmethod
    def check_retention(cls, v):
        if v is not None and v < 1:
            raise ValueError("I giorni di conservazione devono essere almeno 1 (o vuoto per 'mai')")
        return v


# ---------- App settings ----------
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ChangelogEntryOut(BaseModel):
    version: str
    date: str
    changes: list[str]


class VersionOut(BaseModel):
    version: str
    changelog: list[ChangelogEntryOut]


class ThemeOut(BaseModel):
    theme_color: str


class ThemeUpdate(BaseModel):
    theme_color: str

    @field_validator("theme_color")
    @classmethod
    def check_theme(cls, v):
        if not _HEX_COLOR_RE.match(v):
            raise ValueError("Colore non valido: atteso formato esadecimale #rrggbb")
        return v


# ---------- Zones ----------
class ZoneBase(BaseModel):
    name: str
    description: str = ""
    multicast_address: str
    multicast_port: int = 5004


class ZoneCreate(ZoneBase):
    pass


class ZoneOut(ZoneBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


# ---------- Speakers ----------
_PAGING_VOLUME_VALUES = {"default", "1", "2", "3", "4", "5", "6", "7", "8", "9"}


class SpeakerBase(BaseModel):
    name: str
    ip_address: str
    http_port: int = 80
    http_username: str = "admin"
    http_password: str = ""
    brand: str = "Fanvil"
    model: str = "A233"
    location: str = ""
    own_multicast_address: str
    own_multicast_port: int = 5004
    zone_id: Optional[int] = None
    notes: str = ""
    paging_volume: Optional[str] = None

    @field_validator("paging_volume")
    @classmethod
    def check_paging_volume(cls, v):
        if v is not None and v not in _PAGING_VOLUME_VALUES:
            raise ValueError("Volume non valido: usare 'default' o un valore da 1 a 9")
        return v


class SpeakerCreate(SpeakerBase):
    pass


class SpeakerUpdate(BaseModel):
    name: Optional[str] = None
    ip_address: Optional[str] = None
    http_port: Optional[int] = None
    http_username: Optional[str] = None
    http_password: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    location: Optional[str] = None
    own_multicast_address: Optional[str] = None
    own_multicast_port: Optional[int] = None
    zone_id: Optional[int] = None
    notes: Optional[str] = None
    paging_volume: Optional[str] = None

    @field_validator("paging_volume")
    @classmethod
    def check_paging_volume(cls, v):
        if v is not None and v not in _PAGING_VOLUME_VALUES:
            raise ValueError("Volume non valido: usare 'default' o un valore da 1 a 9")
        return v


class SpeakerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    ip_address: str
    http_port: int
    http_username: str
    brand: str
    model: str
    location: str
    status: SpeakerStatus
    last_seen: Optional[datetime]
    own_multicast_address: str
    own_multicast_port: int
    zone_id: Optional[int]
    notes: str
    paging_volume: Optional[str] = None
    supports_auto_config: bool = False
    supports_config_backup: bool = False
    supports_paging_volume: bool = False


class SpeakerBackupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    speaker_id: Optional[int]
    speaker_name: str
    speaker_ip: str
    speaker_exists: bool = True
    format: str
    size_bytes: int
    created_at: datetime
    created_by_name: Optional[str] = None


# ---------- Media ----------
class MediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    original_filename: str
    duration_seconds: float
    size_bytes: int
    content_type: str
    uploaded_at: datetime
    peak_db: Optional[float] = None
    band_low_pct: Optional[float] = None
    band_mid_pct: Optional[float] = None
    band_high_pct: Optional[float] = None
    suggested_gain_db: Optional[float] = None
    normalized: bool = False
    low_freq_warning: bool = False
    frequency_note: Optional[str] = None
    headroom_note: Optional[str] = None


# ---------- Playback ----------
class PlayRequest(BaseModel):
    media_id: int
    target_type: TargetType
    target_id: Optional[int] = None  # required for speaker/zone, ignored for all

    @field_validator("target_id")
    @classmethod
    def check_target(cls, v, info):
        return v


class PlaybackLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    media_id: int
    target_type: TargetType
    target_id: Optional[int]
    target_label: str
    source: PlaybackSource
    triggered_by_name: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime]
    status: PlaybackStatus
    error_message: str


# ---------- Schedules ----------
class ScheduleBase(BaseModel):
    name: str
    media_id: int
    target_type: TargetType
    target_id: Optional[int] = None
    time_of_day: time
    days_of_week: str = "mon,tue,wed,thu,fri"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    exclude_holidays: bool = False
    holidays_only: bool = False
    holiday_country: str = "IT"
    enabled: bool = True

    @field_validator("holiday_country")
    @classmethod
    def _validate_holiday_country(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Z]{2}", v):
            raise ValueError("holiday_country deve essere un codice ISO 3166-1 alpha-2 (es. IT, US, FR)")
        return v


class ScheduleCreate(ScheduleBase):
    pass


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    media_id: Optional[int] = None
    target_type: Optional[TargetType] = None
    target_id: Optional[int] = None
    time_of_day: Optional[time] = None
    days_of_week: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    exclude_holidays: Optional[bool] = None
    holidays_only: Optional[bool] = None
    holiday_country: Optional[str] = None
    enabled: Optional[bool] = None

    @field_validator("holiday_country")
    @classmethod
    def _validate_holiday_country(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.fullmatch(r"[A-Z]{2}", v):
            raise ValueError("holiday_country deve essere un codice ISO 3166-1 alpha-2 (es. IT, US, FR)")
        return v


class ScheduleOut(ScheduleBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime
