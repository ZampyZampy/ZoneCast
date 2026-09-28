"""End-to-end coverage of the main dashboard flows after the move to ES
modules: every dialog, the delegated table actions, 2FA, sorting, the
mobile drawer and the admin tabs."""
import uuid

import pyotp
import pytest

from .conftest import add_media, login, open_tab

expect = pytest.importorskip("playwright.sync_api").expect


def _uid(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def _row(page, body, text):
    return page.locator(f"#{body} tr", has_text=text)


def test_speaker_create_edit_delete(page, server):
    zone = server.api.post("/api/zones", json={"name": _uid("spk-zone"), "multicast_address": "239.255.70.1"}).json()
    login(page, server)
    open_tab(page, "speakers")
    name = _uid("speaker")
    page.click("#new-speaker-btn")
    assert page.locator("#speaker-zone").count() == 0  # zones are assigned from the Zones tab
    page.fill("#speaker-name", name)
    page.fill("#speaker-ip", "192.0.2.150")
    page.fill("#speaker-mcast-addr", "239.255.71.1")
    page.select_option("#speaker-brand", "fanvil")
    assert page.is_visible("#speaker-volume-wrap")  # Fanvil supports paging volume
    page.click("#speaker-modal button[type=submit]")
    row = _row(page, "speakers-body", name)
    row.wait_for()

    open_tab(page, "zones")
    _row(page, "zones-body", zone["name"]).locator("[data-action=edit]").click()
    page.wait_for_selector("#zone-modal.show")
    page.fill("#zone-members-filter", name)
    page.locator("#zone-members-list label", has_text=name).locator("input").check()
    assert page.inner_text("#zone-members-count") == "1"
    page.click("#zone-modal button[type=submit]")
    page.wait_for_selector(f"#zones-body tr:has-text('{zone['name']}'):has-text('{name}')")
    open_tab(page, "speakers")
    assert zone["name"] in _row(page, "speakers-body", name).inner_text()

    row = _row(page, "speakers-body", name)
    row.locator("[data-action=edit]").click()
    page.wait_for_selector("#speaker-modal.show")
    assert page.input_value("#speaker-name") == name
    assert zone["name"] in page.inner_text("#speaker-zones")
    page.fill("#speaker-location", "Hall")
    page.select_option("#speaker-brand", "other")
    page.fill("#speaker-brand-other", "Acme")
    page.click("#speaker-modal button[type=submit]")
    page.wait_for_selector(f"#speakers-body tr:has-text('{name}'):has-text('Acme')")

    _row(page, "speakers-body", name).locator("[data-action=delete]").click()
    _row(page, "speakers-body", name).wait_for(state="detached")
    assert page.errors == []


def test_schedule_create_edit_delete(page, server):
    zone = server.api.post("/api/zones", json={"name": _uid("sch-zone"), "multicast_address": "239.255.72.1"}).json()
    media_name = _uid("bell") + ".wav"
    add_media(server, name=media_name)
    login(page, server)
    open_tab(page, "schedules")
    name = _uid("schedule")
    page.click("#new-schedule-btn")
    page.fill("#schedule-name", name)
    page.select_option("#schedule-media", label=media_name)
    page.select_option("#schedule-target-type", "zone")
    page.select_option("#schedule-target-id", label=zone["name"])
    page.fill("#schedule-time", "07:15")
    page.click("label[for=day-sat]")
    page.select_option("#schedule-holiday-mode", "exclude")
    page.select_option("#schedule-holiday-country", "US")
    page.click("#schedule-modal button[type=submit]")
    row = _row(page, "schedules-body", name)
    row.wait_for()
    text = row.inner_text()
    assert "07:15" in text and zone["name"] in text and "(US)" in text and "Sat" in text

    row.locator("[data-action=edit]").click()
    page.wait_for_selector("#schedule-modal.show")
    assert page.input_value("#schedule-time") == "07:15"
    assert page.is_checked("#day-sat") and page.is_checked("#day-mon")
    page.uncheck("#schedule-enabled")
    page.click("#schedule-modal button[type=submit]")
    page.wait_for_selector(f"#schedules-body tr:has-text('{name}') .visually-hidden:has-text('Paused')")

    _row(page, "schedules-body", name).locator("[data-action=delete]").click()
    _row(page, "schedules-body", name).wait_for(state="detached")
    assert page.errors == []


def test_deleting_media_in_use_explains_why(page, server):
    media_name = _uid("used") + ".wav"
    media_id = add_media(server, name=media_name)
    sched = server.api.post("/api/schedules", json={
        "name": _uid("uses-it"), "media_id": media_id, "target_type": "all",
        "time_of_day": "06:00:00", "days_of_week": "mon",
    }).json()
    login(page, server)
    open_tab(page, "media")
    _row(page, "media-body", media_name).locator("[data-action=delete]").click()
    toast = page.wait_for_selector("#toast-container .alert-danger")
    assert sched["name"] in toast.inner_text()
    assert _row(page, "media-body", media_name).count() == 1
    server.api.delete(f"/api/schedules/{sched['id']}")


def test_user_create_edit_delete(page, server):
    login(page, server)
    open_tab(page, "users")
    username = _uid("user")
    page.click("[data-bs-target='#user-modal']")
    page.fill("#user-username", username)
    page.fill("#user-fullname", "Test Person")
    page.fill("#user-password", "a-long-enough-password")
    page.click("#user-modal button[type=submit]")
    row = _row(page, "users-body", username)
    row.wait_for()
    assert "Operator" in row.inner_text()
    row.locator("[data-action=edit]").click()
    page.wait_for_selector("#user-edit-modal.show")
    page.select_option("#user-edit-role", "admin")
    page.click("#user-edit-modal button[type=submit]")
    page.wait_for_selector(f"#users-body tr:has-text('{username}'):has-text('Administrator')")
    _row(page, "users-body", username).locator("[data-action=delete]").click()
    _row(page, "users-body", username).wait_for(state="detached")
    assert page.errors == []


def test_two_factor_enable_then_disable_with_password_dialog(page, server):
    username, password = _uid("tfa"), "tfa-user-password-123"
    server.api.post("/api/auth/users", json={"username": username, "password": password, "role": "operator"}).raise_for_status()
    login(page, server, username, password)
    page.click("#security-open-btn")
    page.wait_for_selector("#security-modal.show")
    page.click("#tfa-enable-btn")
    page.wait_for_selector("#tfa-setup-view:not(.d-none)")
    assert page.locator("#tfa-qr-container svg").count() == 1
    secret = page.input_value("#tfa-secret")
    page.fill("#tfa-confirm-code", pyotp.TOTP(secret).now())
    page.click("#tfa-confirm-btn")
    page.wait_for_selector("#tfa-codes-view:not(.d-none)")
    assert len(page.inner_text("#tfa-codes-list").split()) >= 5
    page.click("#tfa-codes-done-btn")
    page.wait_for_selector("#tfa-disable-btn:not(.d-none)")

    page.click("#tfa-disable-btn")
    page.wait_for_selector("#password-prompt-modal.show")
    assert page.get_attribute("#password-prompt-input", "type") == "password"
    page.fill("#password-prompt-input", password)
    page.click("#password-prompt-form button[type=submit]")
    page.wait_for_selector("#security-modal.show")
    page.wait_for_selector("#tfa-enable-btn:not(.d-none)")
    assert page.errors == []


def test_password_change_and_wrong_current_password(page, server):
    username, password = _uid("pw"), "first-password-123"
    server.api.post("/api/auth/users", json={"username": username, "password": password, "role": "operator"}).raise_for_status()
    login(page, server, username, password)
    page.click("[data-bs-target='#password-modal']")
    page.fill("#pw-current", "not-my-password")
    page.fill("#pw-new", "second-password-456")
    page.fill("#pw-confirm", "second-password-456")
    page.click("#password-modal button[type=submit]")
    page.wait_for_selector("#pw-error:not(.d-none)")
    assert "incorrect" in page.inner_text("#pw-error").lower()
    page.fill("#pw-current", password)
    page.click("#password-modal button[type=submit]")
    page.wait_for_selector("#password-modal", state="hidden")
    page.click("#logout-btn")
    page.wait_for_url(f"{server.url}/login")
    login(page, server, username, "second-password-456")


def test_sorting_by_keyboard_survives_a_data_refresh(page, server):
    tag = uuid.uuid4().hex[:6]
    for i, prefix in enumerate(("zz", "aa", "mm"), start=1):
        server.api.post("/api/zones", json={"name": f"{prefix}-sort-{tag}", "multicast_address": f"239.255.73.{i}"})

    def ours():
        names = page.locator("#zones-body tr td:first-child").all_inner_texts()
        return [n.split("-")[0] for n in names if n.endswith(f"-sort-{tag}")]

    login(page, server)
    open_tab(page, "zones")
    header = page.locator("#tab-zones th[data-sort=name]")
    header.focus()
    page.keyboard.press("Enter")
    assert header.get_attribute("aria-sort") == "ascending"
    assert ours() == ["aa", "mm", "zz"]
    page.keyboard.press("Enter")
    assert header.get_attribute("aria-sort") == "descending"
    assert ours() == ["zz", "mm", "aa"]
    # a save elsewhere refreshes the data: the order must stay descending
    page.click("#new-zone-btn")
    page.fill("#zone-name", f"bb-sort-{tag}")
    page.fill("#zone-mcast-addr", "239.255.74.1")
    page.click("#zone-modal button[type=submit]")
    page.wait_for_selector(f"#zones-body tr:has-text('bb-sort-{tag}')")
    assert ours() == ["zz", "mm", "bb", "aa"]


def test_mobile_drawer_holds_language_and_account_controls(browser, server):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="en-US")
    ctx.add_init_script("try { localStorage.setItem('zc_lang', 'en') } catch (e) {}")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        login(page, server)
        assert page.locator("#sidebar-prefs-slot #language-select").count() == 1
        toggle = page.locator("#sidebar-toggle-btn")
        toggle.click()
        assert toggle.get_attribute("aria-expanded") == "true"
        page.click('#main-tabs .nav-item[data-tab="zones"]')
        assert toggle.get_attribute("aria-expanded") == "false"
        assert page.is_visible("#tab-zones")
        assert errors == []
    finally:
        ctx.close()


def test_logs_tab_search_and_retention(page, server):
    server.api.post("/api/auth/login", json={"username": "log-probe-user", "password": "x"})
    login(page, server)
    open_tab(page, "logs")
    page.fill("#logs-search", "log-probe-user")
    page.wait_for_timeout(900)
    assert "log-probe-user" in page.inner_text("#logs-body")
    assert "q=log-probe-user" in page.get_attribute("#logs-export-csv", "href")
    page.uncheck("#logs-retention-never")
    page.fill("#logs-retention-days", "")
    page.click("#logs-retention-save")
    assert "Never" in page.wait_for_selector("#toast-container .alert-danger").inner_text()
    page.fill("#logs-retention-days", "30")
    page.click("#logs-retention-save")
    page.wait_for_selector("#toast-container .alert-success")
    assert server.api.get("/api/logs/settings").json()["retention_days"] == 30
    assert page.errors == []


def test_every_view_redraws_in_every_language(page, server):
    login(page, server)
    for lang in ("it", "fr", "de", "es", "ja", "zh", "en"):
        page.select_option("#language-select", lang)
        for tab in ("play", "media", "speakers", "zones", "schedules", "users", "system", "logs", "backuparchive"):
            open_tab(page, tab)
        assert page.evaluate("document.documentElement.lang") == lang
    assert page.errors == []


def test_zone_members_checklist_filters_and_keeps_the_selection(page, server):
    tag = uuid.uuid4().hex[:6]
    ids = []
    for i in range(3):
        res = server.api.post("/api/speakers", json={
            "name": f"hall-{tag}-{i}" if i < 2 else f"dock-{tag}", "ip_address": f"198.18.90.{i + 1}",
            "own_multicast_address": f"239.254.90.{i + 1}", "brand": "other", "location": "north" if i == 0 else ""})
        ids.append(res.json()["id"])
    login(page, server)
    open_tab(page, "zones")
    page.click("#new-zone-btn")
    page.fill("#zone-name", f"members-{tag}")
    page.fill("#zone-mcast-addr", "239.255.91.1")
    page.fill("#zone-members-filter", f"hall-{tag}")
    assert page.locator("#zone-members-list input[type=checkbox]").count() == 2
    page.click("#zone-members-select-shown")
    page.fill("#zone-members-filter", "north")  # location matches too
    assert page.locator("#zone-members-list input[type=checkbox]").count() == 1
    page.fill("#zone-members-filter", "")
    page.check("#zone-members-selected-only")
    assert page.locator("#zone-members-list input:checked").count() == 2
    page.click("#zone-modal button[type=submit]")
    page.wait_for_selector(f"#zones-body tr:has-text('members-{tag}')")
    zone = next(z for z in server.api.get("/api/zones").json() if z["name"] == f"members-{tag}")
    assert sorted(zone["speaker_ids"]) == sorted(ids[:2])
    assert page.errors == []


def test_live_announcement_on_busy_speakers_asks_first(page, server):
    tag = uuid.uuid4().hex[:6]
    spk = server.api.post("/api/speakers", json={"name": f"shared-{tag}", "ip_address": "198.18.95.1",
                                                 "own_multicast_address": "239.254.95.1", "brand": "other"}).json()
    busy_zone = server.api.post("/api/zones", json={
        "name": f'busy-{tag}<img src=x onerror="window.__xss=1">', "multicast_address": "239.255.95.1",
        "speaker_ids": [spk["id"]]}).json()
    new_zone = server.api.post("/api/zones", json={"name": f"new-{tag}", "multicast_address": "239.255.95.2",
                                                   "speaker_ids": [spk["id"]]}).json()
    media_name = _uid("long") + ".wav"
    media_id = add_media(server, name=media_name, seconds=30)
    running = server.api.post("/api/playback/play", json={"media_id": media_id, "target_type": "zone",
                                                          "target_id": busy_zone["id"]}).json()
    try:
        login(page, server)
        page.select_option("#play-media", label=media_name)
        page.select_option("#play-target-type", "zone")
        page.select_option("#play-target-id", label=new_zone["name"])
        page.click("#play-btn")
        page.wait_for_selector("#play-conflict-modal.show")
        listed = page.inner_text("#play-conflict-list")
        assert f"busy-{tag}" in listed and f"shared-{tag}" in listed and media_name in listed
        expect(page.locator("#play-conflict-cancel")).to_be_focused()
        page.click("#play-conflict-cancel")
        page.wait_for_selector("#play-conflict-modal", state="hidden")
        assert server.api.get("/api/playback/active").json()["active_log_ids"] == [running["id"]]

        page.click("#play-btn")
        page.wait_for_selector("#play-conflict-modal.show")
        page.click("#play-conflict-stop")
        page.wait_for_selector("#toast-container .alert-success")
        active = server.api.get("/api/playback/active").json()["active_log_ids"]
        assert running["id"] not in active and len(active) == 1
        assert page.evaluate("window.__xss") is None
        assert page.errors == []
    finally:
        for log_id in server.api.get("/api/playback/active").json()["active_log_ids"]:
            server.api.post(f"/api/playback/stop/{log_id}")


def test_custom_dates_list_and_schedule_rule(page, server):
    tag = uuid.uuid4().hex[:6]
    media_name = _uid("cal") + ".wav"
    add_media(server, name=media_name)
    login(page, server)
    open_tab(page, "schedules")
    page.click("#new-calendar-btn")
    page.wait_for_selector("#calendar-modal.show")
    page.fill("#calendar-name", f"closures-{tag}")
    page.locator("#calendar-entries [data-field=start]").first.fill("2026-08-10")
    page.locator("#calendar-entries [data-field=end]").first.fill("2026-08-21")
    page.click("#calendar-modal summary")
    page.fill("#calendar-paste", "24/12/2026 06/01/2027 Christmas\nnot a date")
    page.click("#calendar-paste-add")
    assert page.inner_text("#calendar-entry-count") == "2"
    assert page.input_value("#calendar-paste") == "not a date"  # what couldn't be read stays there
    page.locator("#calendar-entries [data-field=yearly]").nth(1).check()
    page.click("#calendar-modal button[type=submit]")
    page.wait_for_selector(f"#calendars-body tr:has-text('closures-{tag}'):has-text('Christmas')")
    cal = next(c for c in server.api.get("/api/calendars").json() if c["name"] == f"closures-{tag}")
    assert [(d["start_date"], d["end_date"], d["yearly"]) for d in cal["dates"]] == [
        ("2026-08-10", "2026-08-21", False), ("2026-12-24", "2027-01-06", True)]

    page.click("#new-schedule-btn")
    page.fill("#schedule-name", f"with-dates-{tag}")
    page.select_option("#schedule-media", label=media_name)
    page.fill("#schedule-time", "05:05")
    page.select_option(f"#schedule-calendar-{cal['id']}", "exclude")
    page.click("#schedule-modal button[type=submit]")
    page.wait_for_selector(f"#schedules-body tr:has-text('with-dates-{tag}'):has-text('closures-{tag}')")
    assert page.errors == []


def test_an_overlapping_schedule_is_refused_inside_the_dialog(page, server):
    tag = uuid.uuid4().hex[:6]
    media_name = _uid("ov") + ".wav"
    media_id = add_media(server, name=media_name, seconds=20)
    blocker = server.api.post("/api/schedules", json={
        "name": f'blocker-{tag}<img src=x onerror="window.__xss=1">', "media_id": media_id, "target_type": "all",
        "time_of_day": "10:10:00", "days_of_week": "mon,tue,wed,thu,fri,sat,sun"}).json()
    try:
        login(page, server)
        open_tab(page, "schedules")
        page.click("#new-schedule-btn")
        page.fill("#schedule-name", f"late-{tag}")
        page.select_option("#schedule-media", label=media_name)
        page.fill("#schedule-time", "10:10")
        page.click("#schedule-modal button[type=submit]")
        box = page.wait_for_selector("#schedule-conflicts:not(.d-none)")
        assert f"blocker-{tag}" in box.inner_text()
        assert page.is_visible("#schedule-modal")  # still open, to fix it
        page.fill("#schedule-time", "10:20")
        page.click("#schedule-modal button[type=submit]")
        page.wait_for_selector("#schedule-modal", state="hidden")
        page.wait_for_selector(f"#schedules-body tr:has-text('late-{tag}')")
        assert page.evaluate("window.__xss") is None
        assert page.errors == []
    finally:
        server.api.delete(f"/api/schedules/{blocker['id']}")
        for s in server.api.get("/api/schedules").json():
            if s["name"] == f"late-{tag}":
                server.api.delete(f"/api/schedules/{s['id']}")
