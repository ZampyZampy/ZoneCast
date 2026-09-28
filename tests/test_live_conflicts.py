"""Live announcements vs whatever else is playing on the same speakers."""
import asyncio
from datetime import time as dtime

import pytest

from app.database import SessionLocal
from app.models import (Media, PlaybackLog, PlaybackSource, PlaybackStatus, Schedule, Speaker, TargetType, Zone,
                        zone_members)
from app.services import player, scheduler as scheduler_service
from app.services.footprint import Footprint, footprint


@pytest.fixture()
def site(client):
    """Speaker S in zones A and B, speaker T only in C, one media file."""
    db = SessionLocal()
    try:
        s = Speaker(name="S-shared", ip_address="198.18.70.1", own_multicast_address="239.254.70.1")
        t = Speaker(name="T-alone", ip_address="198.18.70.2", own_multicast_address="239.254.70.2")
        zones = [Zone(name=f"conflict-{n}", multicast_address=f"239.253.70.{i}", multicast_port=5004)
                 for i, n in enumerate("ABCE", start=1)]
        media = Media(original_filename="announce.wav", stored_filename="conflict.wav",
                      pcm_filename="conflict.pcm8k.wav", duration_seconds=30)
        db.add_all([s, t, *zones, media])
        db.flush()
        zones[0].speakers = [s]
        zones[1].speakers = [s]
        zones[2].speakers = [t]
        db.commit()
        ids = {"S": s.id, "T": t.id, "A": zones[0].id, "B": zones[1].id, "C": zones[2].id, "E": zones[3].id,
               "media": media.id}
    finally:
        db.close()
    yield ids
    db = SessionLocal()
    try:
        db.execute(zone_members.delete().where(zone_members.c.speaker_id.in_([ids["S"], ids["T"]])))
        db.query(Schedule).filter(Schedule.media_id == ids["media"]).delete()
        db.query(PlaybackLog).filter(PlaybackLog.media_id == ids["media"]).delete()
        db.query(Zone).filter(Zone.id.in_([ids[k] for k in "ABCE"])).delete()
        db.query(Speaker).filter(Speaker.id.in_([ids["S"], ids["T"]])).delete()
        db.query(Media).filter(Media.id == ids["media"]).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def fake_stream(monkeypatch):
    """Streams last `seconds` (default: until stopped, at most 5 s)."""
    lengths = {}

    async def stream(pcm_path, addr, port, stop_event):
        await asyncio.to_thread(stop_event.wait, lengths.get(addr, 5))

    monkeypatch.setattr(player, "stream_pcm_over_rtp", stream)
    return lengths


def _status(log_id):
    db = SessionLocal()
    try:
        return db.get(PlaybackLog, log_id).status
    finally:
        db.close()


def test_footprints(site):
    db = SessionLocal()
    try:
        a, b, c = (footprint(db, TargetType.zone, site[k]) for k in "ABC")
        empty = footprint(db, TargetType.zone, site["E"])
        everyone = footprint(db, TargetType.all, None)
        s_alone = footprint(db, TargetType.speaker, site["S"])
        assert a.intersects(b)  # S is in both zones
        assert not a.intersects(c)
        assert s_alone.intersects(a) and everyone.intersects(c)
        assert empty.intersects(footprint(db, TargetType.zone, site["E"]))  # same group, even with no one in it
        assert not empty.intersects(Footprint(frozenset(), ("all",)))
        assert footprint(db, TargetType.speaker, 999999).speakers == frozenset()  # deleted target: reaches no one
    finally:
        db.close()


def test_a_live_announcement_asks_before_cutting_in(site, fake_stream):
    async def scenario():
        db = SessionLocal()
        try:
            first = await player.play(db, site["media"], TargetType.zone, site["A"])
            other = await player.play(db, site["media"], TargetType.zone, site["C"], on_conflict="ask")  # no overlap
            with pytest.raises(player.SpeakersBusyError) as busy:
                await player.play(db, site["media"], TargetType.zone, site["B"], on_conflict="ask")
            conflict = busy.value.params["conflicts"][0]
            assert busy.value.params["count"] == 1 and conflict["log_id"] == first.id
            assert conflict["shared_speakers"] == ["S-shared"] and conflict["label"] == "conflict-A"
            assert 0 < conflict["remaining_seconds"] <= 30
            alongside = await player.play(db, site["media"], TargetType.zone, site["B"], on_conflict="overlap")
            assert _status(first.id) == PlaybackStatus.running  # both play; the device picks
            replaced = await player.play(db, site["media"], TargetType.all, None, on_conflict="stop")
            await asyncio.sleep(0.05)
            statuses = [_status(i) for i in (first.id, other.id, alongside.id, replaced.id)]
            await player.stop_all()
            return statuses
        finally:
            db.close()

    first, other, alongside, replaced = asyncio.run(scenario())
    assert (first, other, alongside) == (PlaybackStatus.stopped,) * 3  # "all" reaches every one of them
    assert replaced == PlaybackStatus.running


def test_the_api_answers_409_with_what_is_playing(admin_client, site, fake_stream):
    body = {"media_id": site["media"], "target_type": "zone", "target_id": site["A"]}
    first = admin_client.post("/api/playback/play", json=body).json()
    try:
        res = admin_client.post("/api/playback/play", json={**body, "target_id": site["B"], "on_conflict": "ask"})
        assert res.status_code == 409
        detail = res.json()["detail"]
        assert detail["code"] == "playback.speakers_busy"
        conflict = detail["params"]["conflicts"][0]
        me = admin_client.get("/api/auth/me").json()
        assert conflict["by"] == (me["full_name"] or me["username"]) and conflict["source"] == "manual" and conflict["media"] == "announce.wav"
        assert detail["params"]["upcoming"] == []
        # an API client that doesn't ask keeps the old behaviour
        assert admin_client.post("/api/playback/play", json={**body, "target_id": site["B"]}).status_code == 200
    finally:
        for log_id in admin_client.get("/api/playback/active").json()["active_log_ids"]:
            admin_client.post(f"/api/playback/stop/{log_id}")
    assert first["status"] == "running"


def test_a_bell_waits_for_a_live_announcement_on_shared_speakers(site, fake_stream, monkeypatch):
    monkeypatch.setattr(scheduler_service, "LIVE_WAIT_SECONDS", 3)
    fake_stream["239.253.70.1"] = 0.3  # the announcement on zone A lasts 0.3 s
    db = SessionLocal()
    try:
        bell = Schedule(name="bell-on-B", media_id=site["media"], target_type=TargetType.zone, target_id=site["B"],
                        time_of_day=dtime(8, 0), days_of_week="mon,tue,wed,thu,fri,sat,sun")
        db.add(bell)
        db.commit()
        bell_id = bell.id
    finally:
        db.close()

    async def scenario():
        db = SessionLocal()
        try:
            live = await player.play(db, site["media"], TargetType.zone, site["A"])
        finally:
            db.close()
        await scheduler_service._run_schedule(bell_id)
        live_status = _status(live.id)
        await player.stop_all()
        return live_status

    assert asyncio.run(scenario()) == PlaybackStatus.completed  # not cut off
    db = SessionLocal()
    try:
        runs = db.query(PlaybackLog).filter(PlaybackLog.schedule_id == bell_id).all()
        assert [r.status for r in runs] != [PlaybackStatus.failed] and len(runs) == 1
        assert runs[0].source == PlaybackSource.schedule
    finally:
        db.close()


def test_a_bell_gives_up_if_the_announcement_goes_on_too_long(site, fake_stream, monkeypatch):
    monkeypatch.setattr(scheduler_service, "LIVE_WAIT_SECONDS", 0.2)
    db = SessionLocal()
    try:
        bell = Schedule(name="bell-too-late", media_id=site["media"], target_type=TargetType.speaker,
                        target_id=site["S"], time_of_day=dtime(8, 0), days_of_week="mon,tue,wed,thu,fri,sat,sun")
        db.add(bell)
        db.commit()
        bell_id = bell.id
    finally:
        db.close()

    async def scenario():
        db = SessionLocal()
        try:
            await player.play(db, site["media"], TargetType.zone, site["A"])  # 5 s, S is in it
        finally:
            db.close()
        await scheduler_service._run_schedule(bell_id)
        await player.stop_all()

    asyncio.run(scenario())
    db = SessionLocal()
    try:
        runs = db.query(PlaybackLog).filter(PlaybackLog.schedule_id == bell_id).all()
        assert [r.status for r in runs] == [PlaybackStatus.failed] and "live announcement" in runs[0].error_message
    finally:
        db.close()
