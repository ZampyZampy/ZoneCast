"""Custom dates, and the rule that two schedules never play on the same
speaker at the same time (1.6.0)."""
import threading
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.database import SessionLocal
from app.models import Media, Schedule, TargetType
from app.services import scheduler as scheduler_service
from app.services.calendars import DateEntry, Rule, entry_contains, rules_allow

_serial = iter(range(1, 10_000))


# --- pure date logic -----------------------------------------------------------

def test_single_days_and_ranges():
    entry = DateEntry(date(2026, 8, 10), date(2026, 8, 21), False)
    assert entry_contains(entry, date(2026, 8, 10)) and entry_contains(entry, date(2026, 8, 21))
    assert not entry_contains(entry, date(2026, 8, 22)) and not entry_contains(entry, date(2027, 8, 15))
    assert entry_contains(DateEntry(date(2026, 3, 1), None, False), date(2026, 3, 1))


def test_yearly_ranges_across_the_new_year():
    xmas = DateEntry(date(2026, 12, 24), date(2027, 1, 6), True)
    assert entry_contains(xmas, date(2031, 12, 31)) and entry_contains(xmas, date(2032, 1, 1))
    assert entry_contains(xmas, date(2030, 1, 6)) and not entry_contains(xmas, date(2030, 1, 7))
    assert not entry_contains(xmas, date(2030, 12, 23))


def test_yearly_february_29():
    leap_day = DateEntry(date(2028, 2, 29), None, True)
    assert entry_contains(leap_day, date(2027, 2, 28))  # no Feb 29 that year
    assert entry_contains(leap_day, date(2032, 2, 29)) and not entry_contains(leap_day, date(2032, 2, 28))


def test_exclude_and_only_rules():
    closures = Rule("exclude", (DateEntry(date(2026, 8, 1), date(2026, 8, 31), False),))
    exams = Rule("only", (DateEntry(date(2026, 6, 10), date(2026, 6, 20), False),))
    extra = Rule("only", (DateEntry(date(2026, 8, 15), None, False),))
    assert rules_allow([], date(2026, 8, 5)) is None
    assert rules_allow([closures], date(2026, 8, 5)) == "excluded"
    assert rules_allow([exams], date(2026, 6, 12)) is None and rules_allow([exams], date(2026, 6, 21)) == "not_included"
    assert rules_allow([exams, extra], date(2026, 8, 15)) is None  # any "only" list will do
    assert rules_allow([closures, extra], date(2026, 8, 15)) == "excluded"  # exclusion wins


def test_a_late_run_after_midnight_belongs_to_the_evening_before():
    assert scheduler_service.fire_date(time(23, 58), datetime(2026, 10, 5, 0, 2)) == date(2026, 10, 4)
    assert scheduler_service.fire_date(time(8, 0), datetime(2026, 10, 5, 8, 3)) == date(2026, 10, 5)


# --- overlaps through the API --------------------------------------------------

def _today():
    return datetime.now(ZoneInfo(scheduler_service.scheduler_timezone())).date()


def _next(weekday: int, after_days: int = 7) -> date:
    d = _today() + timedelta(days=after_days)
    return d + timedelta(days=(weekday - d.weekday()) % 7)


@pytest.fixture()
def media(client):
    made = []

    def make(seconds):
        db = SessionLocal()
        try:
            m = Media(original_filename=f"{seconds}s.wav", stored_filename=f"overlap-{next(_serial)}.wav",
                      duration_seconds=seconds)
            db.add(m)
            db.commit()
            made.append(m.id)
            return m.id
        finally:
            db.close()

    yield make
    db = SessionLocal()
    try:
        db.query(Schedule).filter(Schedule.media_id.in_(made)).delete(synchronize_session=False)
        db.query(Media).filter(Media.id.in_(made)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _speaker(client):
    n = next(_serial)
    res = client.post("/api/speakers", json={"name": f"ov-{n}", "ip_address": f"198.18.60.{n}",
                                              "own_multicast_address": f"239.254.60.{n}", "brand": "other"})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _zone(client, members):
    n = next(_serial)
    res = client.post("/api/zones", json={"name": f"ov-zone-{n}", "multicast_address": f"239.253.60.{n}",
                                           "speaker_ids": members})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _schedule(client, media_id, at, days="mon,tue,wed,thu,fri,sat,sun", target=("all", None), **extra):
    body = {"name": f"ov-{next(_serial)}", "media_id": media_id, "target_type": target[0], "target_id": target[1],
            "time_of_day": at, "days_of_week": days, **extra}
    return client.post("/api/schedules", json=body)


def _ok(res):
    assert res.status_code == 200, res.text
    return res.json()


def _refused(res):
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "schedules.overlap"
    return detail["params"]


def test_same_time_on_a_shared_speaker_is_refused_and_names_the_blocker(admin_client, media):
    s = _speaker(admin_client)
    first = _ok(_schedule(admin_client, media(5), "08:00:00", target=("speaker", s)))
    params = _refused(_schedule(admin_client, media(5), "08:00:03", target=("zone", _zone(admin_client, [s]))))
    assert params["count"] == 1 and params["first_name"] == first["name"]
    conflict = params["conflicts"][0]
    assert conflict["schedule_id"] == first["id"] and conflict["time"] == "08:00"
    assert conflict["shared_count"] == 1 and len(conflict["days"]) == 7


def test_touching_runs_and_other_speakers_are_fine(admin_client, media):
    s, other = _speaker(admin_client), _speaker(admin_client)
    _ok(_schedule(admin_client, media(60), "08:00:00", target=("speaker", s)))
    _ok(_schedule(admin_client, media(5), "08:01:00", target=("speaker", s)))  # starts as the other ends
    _ok(_schedule(admin_client, media(60), "08:00:00", target=("speaker", other)))


def test_a_speaker_in_two_zones_links_their_schedules(admin_client, media):
    s = _speaker(admin_client)
    _ok(_schedule(admin_client, media(10), "09:00:00", target=("zone", _zone(admin_client, [s]))))
    _refused(_schedule(admin_client, media(10), "09:00:05", target=("zone", _zone(admin_client, [s]))))


def test_a_run_spilling_past_midnight_meets_the_next_morning(admin_client, media):
    s = _speaker(admin_client)
    _ok(_schedule(admin_client, media(120), "23:59:00", days="sun", target=("speaker", s)))
    params = _refused(_schedule(admin_client, media(10), "00:00:30", days="mon", target=("speaker", s)))
    assert params["first_clash"].endswith("T00:00") and date.fromisoformat(params["first_clash"][:10]).weekday() == 0


def test_the_spill_counts_even_on_the_last_day_of_a_date_range(admin_client, media):
    s = _speaker(admin_client)
    sunday = _next(6)
    _ok(_schedule(admin_client, media(180), "23:59:00", days="sun", target=("speaker", s), end_date=sunday.isoformat()))
    _refused(_schedule(admin_client, media(10), "00:00:00", days="mon", target=("speaker", s),
                       start_date=(sunday + timedelta(days=1)).isoformat()))


def test_a_date_range_without_its_weekday_never_runs(admin_client, media):
    s = _speaker(admin_client)
    tuesday = _next(1)
    _ok(_schedule(admin_client, media(10), "10:00:00", days="mon", target=("speaker", s),
                  start_date=tuesday.isoformat(), end_date=tuesday.isoformat()))
    _ok(_schedule(admin_client, media(10), "10:00:00", target=("speaker", s)))


def test_holiday_rules(admin_client, media):
    s = _speaker(admin_client)
    _ok(_schedule(admin_client, media(10), "12:00:00", target=("speaker", s), holidays_only=True, holiday_country="IT"))
    # never on the same day as the one above
    _ok(_schedule(admin_client, media(10), "12:00:00", target=("speaker", s), exclude_holidays=True, holiday_country="IT"))
    # ...but a holiday's late run spills into the working day after it
    _ok(_schedule(admin_client, media(120), "23:59:00", target=("speaker", s), holidays_only=True, holiday_country="IT"))
    _refused(_schedule(admin_client, media(10), "00:00:30", target=("speaker", s), exclude_holidays=True,
                       holiday_country="IT"))
    res = _schedule(admin_client, media(10), "15:00:00", exclude_holidays=True, holidays_only=True)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "schedules.holiday_mode_invalid"


def test_legacy_overlaps_block_only_new_conflicts(admin_client, media):
    s = _speaker(admin_client)
    short, long_ = media(5), media(300)
    db = SessionLocal()
    try:  # saved before the check existed
        rows = [Schedule(name=f"legacy-{i}", media_id=short, target_type=TargetType.speaker, target_id=s,
                         time_of_day=time(7, 0), days_of_week="mon,tue,wed,thu,fri") for i in range(2)]
        db.add_all(rows)
        db.commit()
        a_id, b_id = rows[0].id, rows[1].id
    finally:
        db.close()
    pairs = admin_client.get("/api/schedules/overlaps").json()
    assert {(p["a_id"], p["b_id"]) for p in pairs} >= {(a_id, b_id)}
    assert any(a["code"] == "schedule_overlaps" for a in admin_client.get("/api/system/alerts").json())
    assert admin_client.put(f"/api/schedules/{a_id}", json={"name": "legacy renamed"}).status_code == 200
    third = _ok(_schedule(admin_client, short, "07:02:00", days="mon,tue,wed,thu,fri", target=("speaker", s)))
    params = _refused(admin_client.put(f"/api/schedules/{a_id}", json={"media_id": long_}))  # now reaches 07:02
    assert [c["schedule_id"] for c in params["conflicts"]] == [third["id"]]
    assert admin_client.put(f"/api/schedules/{a_id}", json={"enabled": False}).status_code == 200
    _refused(admin_client.put(f"/api/schedules/{a_id}", json={"enabled": True}))  # re-enabling checks everything


def test_disabled_schedules_are_not_checked(admin_client, media):
    s = _speaker(admin_client)
    _ok(_schedule(admin_client, media(10), "11:00:00", target=("speaker", s)))
    _ok(_schedule(admin_client, media(10), "11:00:00", target=("speaker", s), enabled=False))


def test_concurrent_saves_cannot_both_pass(admin_client, media):
    s, m = _speaker(admin_client), media(10)
    codes = []

    def post():
        codes.append(_schedule(admin_client, m, "13:30:00", target=("speaker", s)).status_code)

    threads = [threading.Thread(target=post) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200, 409]


# --- custom dates ---------------------------------------------------------------

def _calendar(client, name=None, dates=()):
    res = client.post("/api/calendars", json={"name": name or f"cal-{next(_serial)}", "dates": list(dates)})
    assert res.status_code == 200, res.text
    return res.json()


def test_calendar_crud_and_references(admin_client, media):
    cal = _calendar(admin_client, dates=[{"start_date": "2026-12-24", "end_date": "2027-01-06", "yearly": True,
                                          "label": "Christmas"}])
    assert cal["dates"][0]["label"] == "Christmas" and cal["schedule_count"] == 0
    res = admin_client.post("/api/calendars", json={"name": cal["name"]})
    assert res.status_code == 400 and res.json()["detail"]["code"] == "calendars.name_taken"
    res = admin_client.post("/api/calendars", json={"name": "bad", "dates": [{"start_date": "2026-05-02", "end_date": "2026-05-01"}]})
    assert res.status_code == 422 and res.json()["detail"]["code"] == "calendars.range_invalid"

    sched = _ok(_schedule(admin_client, media(5), "06:30:00", calendars=[{"calendar_id": cal["id"], "mode": "exclude"}]))
    assert sched["calendars"] == [{"calendar_id": cal["id"], "mode": "exclude"}]
    listed = {c["id"]: c for c in admin_client.get("/api/calendars").json()}
    assert listed[cal["id"]]["schedule_count"] == 1
    res = admin_client.delete(f"/api/calendars/{cal['id']}")
    assert res.status_code == 409 and res.json()["detail"]["code"] == "calendars.in_use"
    assert admin_client.put(f"/api/schedules/{sched['id']}", json={"calendars": []}).json()["calendars"] == []
    assert admin_client.delete(f"/api/calendars/{cal['id']}").status_code == 200

    res = _schedule(admin_client, media(5), "06:40:00", calendars=[{"calendar_id": 987654, "mode": "only"}])
    assert res.status_code == 422 and res.json()["detail"]["code"] == "schedules.calendar_missing"
    other = _calendar(admin_client)
    res = _schedule(admin_client, media(5), "06:50:00", calendars=[{"calendar_id": other["id"], "mode": "only"},
                                                                    {"calendar_id": other["id"], "mode": "exclude"}])
    assert res.status_code == 422 and res.json()["detail"]["code"] == "schedules.calendar_duplicate"


def test_custom_dates_can_keep_two_schedules_apart(admin_client, media):
    s = _speaker(admin_client)
    exams = _calendar(admin_client, dates=[{"start_date": "2026-06-10", "end_date": "2026-06-20", "yearly": True}])
    _ok(_schedule(admin_client, media(10), "10:00:00", target=("speaker", s),
                  calendars=[{"calendar_id": exams["id"], "mode": "only"}]))
    _ok(_schedule(admin_client, media(10), "10:00:00", target=("speaker", s),
                  calendars=[{"calendar_id": exams["id"], "mode": "exclude"}]))
    _refused(_schedule(admin_client, media(10), "10:00:00", target=("speaker", s)))


def test_editing_custom_dates_reports_the_overlaps_it_creates(admin_client, media):
    s = _speaker(admin_client)
    jan = _calendar(admin_client, dates=[{"start_date": "2026-01-15", "yearly": True}])
    feb = _calendar(admin_client, dates=[{"start_date": "2026-02-15", "yearly": True}])
    a = _ok(_schedule(admin_client, media(10), "14:00:00", target=("speaker", s),
                      calendars=[{"calendar_id": jan["id"], "mode": "only"}]))
    b = _ok(_schedule(admin_client, media(10), "14:00:00", target=("speaker", s),
                      calendars=[{"calendar_id": feb["id"], "mode": "only"}]))
    res = admin_client.put(f"/api/calendars/{feb['id']}", json={"name": feb["name"],
                                                                "dates": [{"start_date": "2026-01-15", "yearly": True}]})
    assert res.status_code == 200
    assert [(w["a_id"], w["b_id"]) for w in res.json()["warnings"]] == [(a["id"], b["id"])]


def test_new_zone_members_report_the_overlaps_they_create(admin_client, media):
    s = _speaker(admin_client)
    zone = _zone(admin_client, [])
    on_zone = _ok(_schedule(admin_client, media(10), "16:00:00", target=("zone", zone)))
    on_speaker = _ok(_schedule(admin_client, media(10), "16:00:00", target=("speaker", s)))
    body = {"name": f"ov-zone-renamed-{zone}", "multicast_address": f"239.253.61.{zone % 250}", "speaker_ids": [s]}
    res = admin_client.put(f"/api/zones/{zone}", json=body)
    assert res.status_code == 200  # saved anyway: blocking could stop the very fix
    assert [(w["a_id"], w["b_id"]) for w in res.json()["warnings"]] == [(on_zone["id"], on_speaker["id"])]
