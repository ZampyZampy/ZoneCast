import asyncio
import threading
import time
import wave
from datetime import time as dtime

import pytest

from app.database import SessionLocal
from app.models import Media, PlaybackLog, PlaybackSource, PlaybackStatus, Schedule, TargetType, Zone
from app.services import player, rtp_multicast


def _write_pcm(path, seconds=1.0):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * int(8000 * seconds))


class _FakeSocket:
    """Records send times; stalls once, like a host hiccup would."""

    def __init__(self, stall_on=None, stall_s=0.3):
        self.sent_at = []
        self.stall_on, self.stall_s = stall_on, stall_s

    def sendto(self, data, dest):
        self.sent_at.append(time.perf_counter())
        if len(self.sent_at) == self.stall_on:
            time.sleep(self.stall_s)

    def close(self):
        pass


def test_sender_resyncs_after_a_stall_instead_of_bursting(tmp_path, monkeypatch):
    pcm = tmp_path / "a.pcm8k.wav"
    _write_pcm(pcm, seconds=1.0)  # 50 packets of 20 ms
    sock = _FakeSocket(stall_on=10)
    monkeypatch.setattr(rtp_multicast, "_make_socket", lambda ttl: sock)

    asyncio.run(rtp_multicast.stream_pcm_over_rtp(pcm, "239.255.0.1", 5004, threading.Event(),
                                                  payload_type=0, packet_ms=20, ttl=0))

    gaps = [b - a for a, b in zip(sock.sent_at, sock.sent_at[1:])]
    assert len(sock.sent_at) == 50
    assert max(gaps) >= 0.29  # the stall is heard as a gap...
    # ...but not followed by a burst of the ~15 packets that fell behind
    assert sum(1 for g in gaps if g < 0.005) <= 2, [round(g * 1000, 1) for g in gaps]


def test_sender_stops_within_a_packet(tmp_path, monkeypatch):
    pcm = tmp_path / "b.pcm8k.wav"
    _write_pcm(pcm, seconds=5.0)
    sock = _FakeSocket()
    monkeypatch.setattr(rtp_multicast, "_make_socket", lambda ttl: sock)
    stop = threading.Event()

    async def run():
        task = asyncio.create_task(rtp_multicast.stream_pcm_over_rtp(pcm, "239.255.0.1", 5004, stop, packet_ms=20, ttl=0))
        await asyncio.sleep(0.2)
        stop.set()
        started = time.perf_counter()
        await task
        return time.perf_counter() - started

    assert asyncio.run(run()) < 0.1
    assert len(sock.sent_at) < 50


@pytest.fixture()
def zone_and_media(client):
    db = SessionLocal()
    try:
        zone = Zone(name="player-test-zone", multicast_address="239.255.0.9", multicast_port=5004)
        media = Media(original_filename="bell.wav", stored_filename="player-test.wav", pcm_filename="player-test.pcm8k.wav")
        db.add_all([zone, media])
        db.commit()
        ids = zone.id, media.id
    finally:
        db.close()
    yield ids
    db = SessionLocal()
    try:
        db.query(PlaybackLog).filter(PlaybackLog.media_id == ids[1]).delete()
        db.query(Zone).filter(Zone.id == ids[0]).delete()
        db.query(Media).filter(Media.id == ids[1]).delete()
        db.commit()
    finally:
        db.close()


def _status(log_id):
    db = SessionLocal()
    try:
        return db.query(PlaybackLog).filter(PlaybackLog.id == log_id).first().status
    finally:
        db.close()


def test_group_policy_manual_preempts_and_schedule_yields(zone_and_media, monkeypatch):
    zone_id, media_id = zone_and_media

    async def fake_stream(pcm_path, addr, port, stop_event):
        await asyncio.to_thread(stop_event.wait, 5)

    monkeypatch.setattr(player, "stream_pcm_over_rtp", fake_stream)

    async def scenario():
        db = SessionLocal()
        try:
            first = await player.play(db, media_id, TargetType.zone, zone_id)
            await asyncio.sleep(0.05)
            second = await player.play(db, media_id, TargetType.zone, zone_id)  # manual again: newest wins
            await asyncio.sleep(0.05)
            with pytest.raises(player.GroupBusyError):
                await player.play(db, media_id, TargetType.zone, zone_id, source=PlaybackSource.schedule)
            statuses = (_status(first.id), _status(second.id))
            await player.stop_all()
            return first.id, second.id, statuses
        finally:
            db.close()

    first_id, second_id, (first_status, second_status) = asyncio.run(scenario())
    assert first_status == PlaybackStatus.stopped
    assert second_status == PlaybackStatus.running
    assert _status(second_id) == PlaybackStatus.stopped  # after stop_all
    assert player.list_active() == []


def test_interrupted_rows_are_closed_at_startup(zone_and_media):
    _, media_id = zone_and_media
    db = SessionLocal()
    try:
        row = PlaybackLog(media_id=media_id, target_type=TargetType.all, target_label="", status=PlaybackStatus.running)
        db.add(row)
        db.commit()
        row_id = row.id
    finally:
        db.close()

    assert player.reconcile_interrupted() >= 1
    db = SessionLocal()
    try:
        row = db.query(PlaybackLog).filter(PlaybackLog.id == row_id).first()
        assert row.status == PlaybackStatus.failed
        assert row.finished_at is not None and row.error_message == player.INTERRUPTED_MESSAGE
    finally:
        db.close()


def test_schedule_with_deleted_media_is_recorded_as_failed(zone_and_media):
    from app.services import scheduler as scheduler_service

    zone_id, _ = zone_and_media
    db = SessionLocal()
    try:
        sched = Schedule(name="orphan", media_id=999999, target_type=TargetType.zone, target_id=zone_id,
                         time_of_day=dtime(8, 0), days_of_week="mon,tue,wed,thu,fri,sat,sun")
        db.add(sched)
        db.commit()
        sched_id = sched.id
    finally:
        db.close()
    try:
        asyncio.run(scheduler_service._run_schedule(sched_id))
        db = SessionLocal()
        try:
            rows = db.query(PlaybackLog).filter(PlaybackLog.schedule_id == sched_id).all()
            assert len(rows) == 1
            assert rows[0].status == PlaybackStatus.failed and rows[0].source == PlaybackSource.schedule
            assert "999999" in rows[0].error_message
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            db.query(PlaybackLog).filter(PlaybackLog.schedule_id == sched_id).delete()
            db.query(Schedule).filter(Schedule.id == sched_id).delete()
            db.commit()
        finally:
            db.close()
