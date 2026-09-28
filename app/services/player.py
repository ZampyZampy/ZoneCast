"""
Orchestrates on-demand and scheduled playback: resolves a target
(single speaker / zone / all) to the multicast group that carries the
audio to it, runs the RTP stream as a background task, and records a
PlaybackLog entry so the dashboard can show history/status.
"""
import asyncio
import logging
from datetime import datetime
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
# Which stream (PlaybackLog.id) is currently sending to each multicast
# group, and whether it was started by a person or by a schedule — two
# RTP flows on one group would interleave into garbled audio.
_group_owner: dict[tuple[str, int], int] = {}
_stream_source: dict[int, PlaybackSource] = {}
# Serializes check -> stop -> register per group, so two requests racing
# for the same group can't both start a stream. Keyed by event loop too:
# an asyncio.Lock can't be shared across loops (tests run several).
_group_locks: dict[tuple[int, str, int], asyncio.Lock] = {}

INTERRUPTED_MESSAGE = "Interrotta: il servizio è stato riavviato durante la riproduzione"


class TargetResolutionError(RuntimeError):
    pass


class GroupBusyError(RuntimeError):
    """A schedule fired while someone is making a manual announcement to
    the same group — the announcement wins, the schedule is recorded as
    failed rather than cutting it off."""


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
            log.finished_at = datetime.utcnow()
            log.status = PlaybackStatus.stopped if handle.stop_event.is_set() else PlaybackStatus.completed
            db.commit()
    except asyncio.CancelledError:
        log = db.query(PlaybackLog).filter(PlaybackLog.id == log_id).first()
        if log:
            log.finished_at = datetime.utcnow()
            log.status = PlaybackStatus.stopped
            db.commit()
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Playback failed for log %s", log_id)
        log = db.query(PlaybackLog).filter(PlaybackLog.id == log_id).first()
        if log:
            log.finished_at = datetime.utcnow()
            log.status = PlaybackStatus.failed
            log.error_message = str(exc)[:500]
            db.commit()
    finally:
        db.close()
        _active_streams.pop(log_id, None)
        _stream_source.pop(log_id, None)
        if _group_owner.get((mcast_addr, mcast_port)) == log_id:
            del _group_owner[(mcast_addr, mcast_port)]


async def _stop_and_wait(log_id: int, timeout: float = 2.0) -> None:
    handle = _active_streams.get(log_id)
    if not handle:
        return
    handle.stop()
    if handle.task:
        await asyncio.wait({handle.task}, timeout=timeout)


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

    lock = _group_locks.setdefault((id(asyncio.get_running_loop()), mcast_addr, mcast_port), asyncio.Lock())
    async with lock:
        busy_id = _group_owner.get((mcast_addr, mcast_port))
        if busy_id is not None:
            if source == PlaybackSource.schedule and _stream_source.get(busy_id) == PlaybackSource.manual:
                raise GroupBusyError("Destinazione occupata da un annuncio manuale in corso")
            # Otherwise the newest request wins: finish the old stream
            # first so the two never overlap on the group.
            await _stop_and_wait(busy_id)
        return _start_stream(db, media_id, target_type, target_id, label, source, schedule_id,
                             triggered_by_id, pcm_path, mcast_addr, mcast_port)


def _start_stream(db, media_id, target_type, target_id, label, source, schedule_id,
                  triggered_by_id, pcm_path, mcast_addr, mcast_port) -> PlaybackLog:
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
    _stream_source[log.id] = source
    _group_owner[(mcast_addr, mcast_port)] = log.id
    handle.task = asyncio.create_task(_run_stream(log.id, pcm_path, mcast_addr, mcast_port))
    return log


def record_failed_run(db: Session, schedule, reason: str) -> None:
    """History row for a scheduled run that never started (media or
    target deleted, group busy, run missed) — so it shows in the history
    and in the login alert instead of only in the server log."""
    now = datetime.utcnow()
    db.add(PlaybackLog(
        media_id=schedule.media_id,
        target_type=schedule.target_type,
        target_id=schedule.target_id,
        target_label="",
        source=PlaybackSource.schedule,
        schedule_id=schedule.id,
        started_at=now,
        finished_at=now,
        status=PlaybackStatus.failed,
        error_message=reason[:500],
    ))
    db.commit()


def reconcile_interrupted() -> int:
    """At startup nothing can be playing yet: rows still marked running
    were cut off by a crash, a power loss or a restart (e.g. after an
    import), and would otherwise show a Stop button that can't work."""
    db = SessionLocal()
    try:
        count = (
            db.query(PlaybackLog)
            .filter(PlaybackLog.status == PlaybackStatus.running)
            .update({
                PlaybackLog.status: PlaybackStatus.failed,
                PlaybackLog.finished_at: datetime.utcnow(),
                PlaybackLog.error_message: INTERRUPTED_MESSAGE,
            }, synchronize_session=False)
        )
        db.commit()
        if count:
            logger.warning("%d riproduzioni risultavano in corso all'avvio: segnate come interrotte", count)
        return count
    finally:
        db.close()


async def stop_all(timeout: float = 2.0) -> None:
    """Graceful shutdown: let every stream finish its bookkeeping."""
    handles = list(_active_streams.values())
    for handle in handles:
        handle.stop()
    tasks = {h.task for h in handles if h.task}
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)


def stop_playback(log_id: int) -> bool:
    handle = _active_streams.get(log_id)
    if not handle:
        return False
    handle.stop()
    return True


def list_active() -> list[int]:
    return list(_active_streams.keys())
