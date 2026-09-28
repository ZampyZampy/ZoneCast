"""End-to-end checks of the dashboard in a real browser."""
import itertools
import re
import uuid


from .conftest import OPERATOR, add_media, login, open_tab

ADMIN_TABS = ["play", "media", "speakers", "zones", "schedules", "users", "system", "logs", "backuparchive"]
XSS = '<img src=x onerror="window.__xss=(window.__xss||0)+1">'


def _uid(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


# Unique addresses per test session: random ones collided now and then
# (the API rightly refuses a duplicate IP or multicast group).
_serial = itertools.count(1)


def _zone(server, name=None, addr=None):
    n = next(_serial)
    res = server.api.post("/api/zones", json={
        "name": name or _uid("zone"),
        "multicast_address": addr or f"239.253.{n // 250}.{n % 250 + 1}",
    })
    res.raise_for_status()
    return res.json()


def _speaker(server, name=None):
    n = next(_serial)
    res = server.api.post("/api/speakers", json={
        "name": name or _uid("spk"), "ip_address": f"198.18.{n // 250}.{n % 250 + 1}",
        "own_multicast_address": f"239.254.{n // 250}.{n % 250 + 1}", "brand": "other",
    })
    res.raise_for_status()
    return res.json()


def test_every_tab_renders_without_script_errors(page, server):
    login(page, server)
    for tab in ADMIN_TABS:
        open_tab(page, tab)
    assert page.errors == []


def test_free_text_is_never_executed(page, server):
    server.api.post("/api/auth/login", json={"username": XSS, "password": "x"})  # lands in the Log tab
    _zone(server, name=_uid("z") + XSS)
    _speaker(server, name=_uid("s") + XSS)
    add_media(server, name=f"{_uid('m')}{XSS}.wav")
    login(page, server)
    for tab in ADMIN_TABS:
        open_tab(page, tab)
    assert page.evaluate("window.__xss") is None
    assert page.locator('img[src="x"]').count() == 0


def test_language_switch_translates_rendered_tables(page, server):
    _zone(server)
    login(page, server)
    open_tab(page, "zones")
    page.select_option("#language-select", "it")
    page.wait_for_timeout(300)
    assert "Modifica" in page.inner_text("#zones-body")
    assert page.evaluate("document.documentElement.lang") == "it"
    page.select_option("#language-select", "en")


def test_api_errors_are_shown_translated(page, server):
    zone = _zone(server)
    login(page, server)
    page.select_option("#language-select", "it")
    open_tab(page, "zones")
    page.click("#new-zone-btn")
    page.fill("#zone-name", zone["name"])
    page.fill("#zone-mcast-addr", "239.255.250.250")
    page.click("#zone-modal button[type=submit]")
    toast = page.wait_for_selector("#toast-container .alert")
    assert "Esiste già una zona con questo nome" in toast.inner_text()
    page.select_option("#language-select", "en")


def test_zone_crud_through_the_ui(page, server):
    login(page, server)
    open_tab(page, "zones")
    name = _uid("ui-zone")
    page.click("#new-zone-btn")
    page.fill("#zone-name", name)
    page.fill("#zone-mcast-addr", "239.255.251.1")
    page.click("#zone-modal button[type=submit]")
    page.wait_for_selector(f"#zones-body tr:has-text('{name}')")
    page.locator(f"#zones-body tr:has-text('{name}') button.btn-outline-danger").click()
    page.wait_for_selector(f"#zones-body tr:has-text('{name}')", state="detached")
    assert page.errors == []


def test_play_selection_survives_other_actions(page, server):
    zone = _zone(server)
    add_media(server, name=_uid("first") + ".wav")
    second = _uid("second") + ".wav"
    add_media(server, name=second)
    _speaker(server)
    login(page, server)
    page.select_option("#play-media", label=second)
    page.select_option("#play-target-type", "zone")
    page.select_option("#play-target-id", label=zone["name"])
    open_tab(page, "speakers")
    page.locator("#speakers-body tr").first.locator("button").first.click()  # Ping
    page.wait_for_timeout(800)
    open_tab(page, "play")
    assert page.eval_on_selector("#play-media", "e => e.options[e.selectedIndex].text") == second
    assert page.eval_on_selector("#play-target-id", "e => e.options[e.selectedIndex].text") == zone["name"]


def test_double_click_on_play_starts_one_announcement(page, server):
    zone = _zone(server)
    name = _uid("dbl") + ".wav"
    add_media(server, name=name)
    login(page, server)
    page.select_option("#play-media", label=name)
    page.select_option("#play-target-type", "zone")
    page.select_option("#play-target-id", label=zone["name"])
    posts = []
    page.on("request", lambda r: posts.append(r) if r.method == "POST" and r.url.endswith("/api/playback/play") else None)
    page.dblclick("#play-btn")
    page.wait_for_timeout(1000)
    assert len(posts) == 1


def test_failed_playbacks_show_their_reason(page, server):
    import sqlite3
    reason = "Interrotta: prova e2e"
    con = sqlite3.connect(server.db)
    con.execute("insert into playback_logs (media_id, target_type, target_label, source, started_at, finished_at, status, error_message) "
                "values (1, 'all', '', 'schedule', datetime('now'), datetime('now'), 'failed', ?)", (reason,))
    con.commit()
    con.close()
    login(page, server)
    page.wait_for_selector("#history-body [title*='prova e2e']", state="attached")


def test_operator_sees_no_admin_ui_and_does_not_poll_admin_endpoints(page, server):
    forbidden = []
    page.on("response", lambda r: forbidden.append(r.url) if r.status == 403 else None)
    page.clock.install()
    login(page, server, *OPERATOR)
    for tab in ("users", "system", "logs", "backuparchive"):
        assert page.locator(f'#main-tabs .nav-item[data-tab="{tab}"]').count() == 0
        assert page.locator(f"#tab-{tab}").count() == 0  # not even rendered
    page.clock.run_for(20_000)  # past any 15 s polling interval
    page.wait_for_timeout(500)
    assert forbidden == []
    assert page.errors == []


def test_dashboard_needs_no_internet(page, server):
    hosts = set()
    page.on("request", lambda r: hosts.add(re.sub(r"^https?://([^/:]+).*$", r"\1", r.url)))
    login(page, server)
    assert hosts <= {"127.0.0.1"}, hosts


def test_content_security_policy_is_enforced_without_violations(page, server):
    violations = []
    page.on("console", lambda m: violations.append(m.text) if "Content Security Policy" in m.text else None)
    res = page.goto(f"{server.url}/login")
    assert "script-src 'self'" in (res.headers.get("content-security-policy") or "")
    login(page, server)
    for tab in ADMIN_TABS:
        open_tab(page, tab)
    assert violations == []


def test_basic_accessibility(page, server):
    _speaker(server)
    login(page, server)
    unlabeled = page.evaluate("""() => [...document.querySelectorAll('label.form-label')]
        .filter(l => !l.htmlFor && !l.querySelector('input,select,textarea')).length""")
    assert unlabeled == 0
    open_tab(page, "speakers")
    nameless = page.evaluate("""() => [...document.querySelectorAll('#speakers-body button')]
        .filter(b => !(b.getAttribute('aria-label') || b.textContent.trim())).length""")
    assert nameless == 0
    assert page.get_attribute("#toast-container", "aria-live") in ("polite", "assertive")
