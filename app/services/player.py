"""
Orchestrates on-demand and scheduled playback: resolves a target
(single speaker / zone / all) to the multicast group that carries the
audio to it, runs the RTP stream as a background task, and records a
PlaybackLog entry so the dashboard can show history/status.
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import Media, Speaker, Zone, TargetType, PlaybackLog, PlaybackSource, PlaybackStatus
from .rtp_multicast import stream_pcm_over_rtp, StreamHandle, RtpStreamError

logger = logging.getLogger("zonecast.player")

# Active streams keyed by PlaybackLog.id, so they can be stopped from the API.
_active_streams: dict[int, StreamHandle] = {}


class TargetResolutionError(RuntimeError):
    pass


def resolve_target(db: Session, target_type: TargetType, target_id: Optional[int]) -> tuple[str, int, str]:
    """Returns (multicast_address, multicast_port, human_label) for a target."""
    if target_type == TargetType.all:
        return settings.global_all_call_address, settings.global_all_call_port, "Tutti gli altoparlanti"

    if target_type == TargetType.speaker:
        speaker = db.query(Speaker).filter(Speaker.id == target_id).first()
        if not speaker:
            raise TargetResolutionError(f"Speaker {target_id} non trovato")
        return speaker.own_multicast_address, speaker.own_multicast_port, speaker.name

    if target_type == TargetType.zone:
        zone = db.query(Zone).filter(Zone.id == target_id).first()
        if not zone:
            raise TargetResolutionError(f"Zona {target_id} non trovata")
        return zone.multicast_address, zone.multicast_port, zone.name

    raise TargetResolutionError("Target non valido")


async def _run_stream(log_id: int, pcm_path: Path, mcast_addr: str, mcast_port: int):
    handle = _active_streams[log_id]
    db = SessionLocal()
    try:
        await stream_pcm_over_rtp(pcm_path, mcast_addr, mcast_port, handle.stop_event)
        log = db.query(PlaybackLog).filter(PlaybackLog.id == log_id).first()
        if log:
            from datetime import datetime
            log.finished_at = datetime.utcnow()
            log.status = PlaybackStatus.stopped if handle.stop_event.is_set() else PlaybackStatus.completed
            db.commit()
    except asyncio.CancelledError:
        log = db.query(PlaybackLog).filter(PlaybackLog.id == log_id).first()
        if log:
            from datetime import datetime
            log.finished_at = datetime.utcnow()
            log.status = PlaybackStatus.stopped
            db.commit()
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Playback failed for log %s", log_id)
        log = db.query(PlaybackLog).filter(PlaybackLog.id == log_id).first()
        if log:
            from datetime import datetime
            log.finished_at = datetime.utcnow()
            log.status = PlaybackStatus.failed
            log.error_message = str(exc)[:500]
            db.commit()
    finally:
        db.close()
        _active_streams.pop(log_id, None)


async def play(
    db: Session,
    media_id: int,
    target_type: TargetType,
    target_id: Optional[int],
    source: PlaybackSource = PlaybackSource.manual,
    schedule_id: Optional[int] = None,
    triggered_by_id: Optional[int] = None,
) -> PlaybackLog:
    media = db.query(Media).filter(Media.id == media_id).first()
    if not media:
        raise TargetResolutionError(f"Media {media_id} non trovato")
    if not media.pcm_filename:
        raise TargetResolutionError("Il file non è stato ancora convertito per lo streaming")

    mcast_addr, mcast_port, label = resolve_target(db, target_type, target_id)
    pcm_path = settings.media_dir / media.pcm_filename

    log = PlaybackLog(
        media_id=media_id,
        target_type=target_type,
        target_id=target_id,
        target_label=label,
        source=source,
        schedule_id=schedule_id,
        triggered_by_id=triggered_by_id,
        status=PlaybackStatus.running,
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    handle = StreamHandle()
    _active_streams[log.id] = handle
    handle.task = asyncio.create_task(_run_stream(log.id, pcm_path, mcast_addr, mcast_port))

    return log


def stop_playback(log_id: int) -> bool:
    handle = _active_streams.get(log_id)
    if not handle:
        return False
    handle.stop()
    return True


def list_active() -> list[int]:
    return list(_active_streams.keys())
