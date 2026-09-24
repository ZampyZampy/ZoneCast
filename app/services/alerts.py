"""
Attention-grabbing alerts shown once per session on first dashboard
load (Sistema-adjacent, but relevant to every admin) — currently:
recent failed scheduled playbacks, and disk space running low.
Read-only and cheap enough to compute on every login.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import host_resources
from ..models import PlaybackLog, PlaybackSource, PlaybackStatus

FAILED_PLAYBACK_LOOKBACK = timedelta(hours=24)
DISK_WARNING_PERCENT = 75.0
DISK_DANGER_PERCENT = 90.0


@dataclass
class Alert:
    severity: str  # "warning" | "danger"
    message: str


def get_alerts(db: Session) -> list[Alert]:
    alerts: list[Alert] = []

    cutoff = datetime.utcnow() - FAILED_PLAYBACK_LOOKBACK
    failed_count = (
        db.query(PlaybackLog)
        .filter(
            PlaybackLog.source == PlaybackSource.schedule,
            PlaybackLog.status == PlaybackStatus.failed,
            PlaybackLog.started_at >= cutoff,
        )
        .count()
    )
    if failed_count:
        alerts.append(Alert(
            severity="danger",
            message=f"{failed_count} schedule playback(s) failed in the last 24 hours — check the Log tab.",
        ))

    try:
        disk_percent = host_resources.get_snapshot(cpu_sample_seconds=0.0).disk_percent
    except host_resources.HostResourcesError:
        disk_percent = None
    if disk_percent is not None:
        if disk_percent >= DISK_DANGER_PERCENT:
            alerts.append(Alert(severity="danger", message=f"Disk space is critically low ({disk_percent:.0f}% used)."))
        elif disk_percent >= DISK_WARNING_PERCENT:
            alerts.append(Alert(severity="warning", message=f"Disk space is running low ({disk_percent:.0f}% used)."))

    return alerts
