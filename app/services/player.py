"""
Orchestrates on-demand and scheduled playback: resolves a target
(single speaker / zone / all) to the multicast group that carries the
audio to it, runs the RTP stream as a background task, and records a
PlaybackLog entry so the dashboard can show history/status.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import CodedError
from ..database import SessionLocal
from ..models import Media, Schedule, Speaker, User, Zone, TargetType, PlaybackLog, PlaybackSource, PlaybackStatus
from .footprint import Footprint, footprint
from .rtp_multicast import stream_pcm_over_rtp, StreamHandle
from ..timeutil import utcnow

logger = logging.getLogger("zonecast.player")


@dataclass
class _Stream:
    """What the conflict checks need to know about a running stream."""
    footprint: Footprint
    group: tuple[str, int]
    source: PlaybackSource
    target_type: TargetType
    label: str
    media_name: str
    by: str
    started: float  # time.monotonic()
    started_at: datetime
    duration: float


# Active streams keyed by PlaybackLog.id, so they can be stopped from the API.
_active_streams: dict[int, StreamHandle] = {}
_streams: dict[int, _Stream] = {}
# Serializes check -> stop -> register, so two requests racing for the
# same speakers can't both start. Keyed by event loop: an asyncio.Lock
# can't be shared across loops (tests run several).
_play_locks: dict[int, asyncio.Lock] = {}

ON_CONFLICT = ("ask", "stop", "overlap")
MAX_NAMES = 5

INTERRUPTED_MESSAGE = "Interrotta: il servizio è stato riavviato durante la riproduzione"


class TargetResolutionError(CodedError):
    pass


class GroupBusyError(CodedError):
    """A schedule fired while someone is making a live announcement to
    some of the same speakers — the announcement wins: the schedule waits
    for it (see scheduler._run_schedule) instead of cutting it off."""


class SpeakersBusyError(CodedError):
    """A live announcement asked first (on_conflict="ask") and some of
    its speakers are already playing: params["conflicts"] says what."""


def resolve_target(db: Session, target_type: TargetType, target_id: Optional[int]) -> tuple[str, int, str]:
    """Returns (multicast_address, multicast_port, human_label) for a target."""
    if target_type == TargetType.all:
        return settings.global_all_call_address, settings.global_all_call_port, "Tutti gli altoparlanti"

    if target_type == TargetType.speaker:
        speaker = db.query(Speaker).filter(Speaker.id == target_id).first()
        if not speaker:
            raise TargetResolutionError("playback.target_not_found", f"Speaker {target_id} not found", target_id=target_id)
        return speaker.own_multicast_address, speaker.own_multicast_port, speaker.name

    if target_type == TargetType.zone:
        zone = db.query(Zone).filter(Zone.id == target_id).first()
        if not zone:
            raise TargetResolutionError("playback.target_not_found", f"Zone {target_id} not found", target_id=target_id)
        return zone.multicast_address, zone.multicast_port, zone.name

    raise TargetResolutionError("playback.target_not_found", "Invalid destination")


async def _run_stream(log_id: int, pcm_path: Path, mcast_addr: str, mcast_port: int):
    handle = _active_streams[log_id]
    db = SessionLocal()
    try:
        await stream_pcm_over_rtp(pcm_path, mcast_addr, mcast_port, handle.stop_event)
        log = db.query(PlaybackLog).filter(PlaybackLog.id == log_id).first()
        if log:
            log.finished_at = utcnow()
            log.status = PlaybackStatus.stopped if handle.stop_event.is_set() else PlaybackStatus.completed
            db.commit()
    except asyncio.CancelledError:
        log = db.query(PlaybackLog).filter(PlaybackLog.id == log_id).first()
        if log:
            log.finished_at = utcnow()
            log.status = PlaybackStatus.stopped
            db.commit()
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Playback failed for log %s", log_id)
        log = db.query(PlaybackLog).filter(PlaybackLog.id == log_id).first()
        if log:
            log.finished_at = utcnow()
            log.status = PlaybackStatus.failed
            log.error_message = str(exc)[:500]
            db.commit()
    finally:
        db.close()
        _active_streams.pop(log_id, None)
        _streams.pop(log_id, None)


async def _stop_and_wait(log_id: int, timeout: float = 2.0) -> None:
    handle = _active_streams.get(log_id)
    if not handle:
        return
    handle.stop()
    if handle.task:
        await asyncio.wait({handle.task}, timeout=timeout)


def _conflict(db: Session, log_id: int, stream: _Stream, fp: Footprint) -> dict:
    """JSON-ready description of a running stream a new one would clash
    with, for the dashboard's "already playing" dialog."""
    shared = stream.footprint.speakers & fp.speakers
    names = db.scalars(select(Speaker.name).where(Speaker.id.in_(shared)).order_by(Speaker.name).limit(MAX_NAMES)).all()
    return {
        "log_id": log_id,
        "source": stream.source.value,
        "target_type": stream.target_type.value,
        "label": stream.label,
        "media": stream.media_name,
        "by": stream.by,
        "started_at": stream.started_at.isoformat(),
        "remaining_seconds": max(0, round(stream.duration - (time.monotonic() - stream.started))),
        "shared_count": len(shared),
        "shared_speakers": list(names),
    }


def _who(db: Session, source: PlaybackSource, triggered_by_id: Optional[int], schedule_id: Optional[int]) -> str:
    if source == PlaybackSource.schedule:
        return db.scalar(select(Schedule.name).where(Schedule.id == schedule_id)) or ""
    user = db.get(User, triggered_by_id) if triggered_by_id else None
    return (user.full_name or user.username) if user else ""


async def play(
    db: Session,
    media_id: int,
    target_type: TargetType,
    target_id: Optional[int],
    source: PlaybackSource = PlaybackSource.manual,
    schedule_id: Optional[int] = None,
    triggered_by_id: Optional[int] = None,
    on_conflict: Optional[str] = None,
) -> PlaybackLog:
    """Starts a stream. Whatever else is playing on the same multicast
    group is always stopped first (two flows on one group would mix into
    noise). Streams on OTHER groups that reach some of the same speakers:
      - a schedule never cuts into a live announcement: GroupBusyError;
      - a live announcement, per `on_conflict`: "ask" raises
        SpeakersBusyError describing them, "stop" stops them first,
        "overlap" (or None, the pre-1.6 API behaviour) plays alongside,
        and each device then plays whichever of its groups it prefers."""
    media = db.query(Media).filter(Media.id == media_id).first()
    if not media:
        raise TargetResolutionError("playback.media_not_found", f"Audio file {media_id} not found", media_id=media_id)
    if not media.pcm_filename:
        raise TargetResolutionError("playback.not_converted", "The file hasn't been converted for streaming yet")

    mcast_addr, mcast_port, label = resolve_target(db, target_type, target_id)
    pcm_path = settings.media_dir / media.pcm_filename
    fp = footprint(db, target_type, target_id)
    group = (mcast_addr, mcast_port)

    lock = _play_locks.setdefault(id(asyncio.get_running_loop()), asyncio.Lock())
    async with lock:
        live = {lid: s for lid, s in _streams.items()
                if lid in _active_streams and not _active_streams[lid].stop_event.is_set()}
        clashing = {lid: s for lid, s in live.items() if s.group == group or s.footprint.intersects(fp)}
        same_group = [lid for lid, s in live.items() if s.group == group]
        if source == PlaybackSource.schedule:
            blocking = [lid for lid, s in clashing.items() if s.source == PlaybackSource.manual]
            if blocking:
                raise GroupBusyError("playback.group_busy", "Destination busy with a live announcement", log_ids=blocking)
            to_stop = same_group
        elif on_conflict == "ask" and clashing:
            conflicts = [_conflict(db, lid, s, fp) for lid, s in clashing.items()]
            raise SpeakersBusyError(
                "playback.speakers_busy", f"{len(conflicts)} playback(s) already running on some of these speakers.",
                count=len(conflicts), names=", ".join(c["label"] for c in conflicts[:MAX_NAMES]), conflicts=conflicts)
        elif on_conflict == "stop":
            to_stop = list(dict.fromkeys([*clashing, *same_group]))
        else:
            to_stop = same_group
        if source == PlaybackSource.manual and clashing and on_conflict in ("stop", "overlap"):
            logger.warning("Riproduzione con conflitto (%s) da %s su %d flussi già in corso: %s",
                           on_conflict, _who(db, source, triggered_by_id, schedule_id) or "?", len(clashing),
                           ", ".join(s.label for s in clashing.values()))
        for lid in to_stop:
            await _stop_and_wait(lid)
        log = _start_stream(db, media_id, target_type, target_id, label, source, schedule_id,
                            triggered_by_id, pcm_path, mcast_addr, mcast_port)
        _streams[log.id] = _Stream(
            footprint=fp, group=group, source=source, target_type=TargetType(target_type), label=label,
            media_name=media.original_filename, by=_who(db, source, triggered_by_id, schedule_id),
            started=time.monotonic(), started_at=log.started_at, duration=media.duration_seconds or 0.0,
        )
        return log


async def wait_for(log_ids: list[int], timeout: float) -> None:
    """Waits (at most `timeout` s) for these streams to end."""
    tasks = {h.task for lid in log_ids if (h := _active_streams.get(lid)) and h.task}
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)


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
    handle.task = asyncio.create_task(_run_stream(log.id, pcm_path, mcast_addr, mcast_port))
    return log


def record_failed_run(db: Session, schedule, reason: str) -> None:
    """History row for a scheduled run that never started (media or
    target deleted, group busy, run missed) — so it shows in the history
    and in the login alert instead of only in the server log."""
    now = utcnow()
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
                PlaybackLog.finished_at: utcnow(),
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
