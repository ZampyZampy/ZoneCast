"""Browser tests: a real ZoneCast process on a random local port (see
_server.py) driven by Playwright. Skipped when Playwright or a browser
isn't available (`pip install playwright && playwright install chromium`)."""
import os
import socket
import subprocess
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
ADMIN_PASSWORD = "e2e-admin-password"
OPERATOR = ("operator1", "e2e-operator-password")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _write_pcm(path: Path, seconds: float = 3.0) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * int(8000 * seconds))


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    work = tmp_path_factory.mktemp("e2e")
    for sub in ("data", "media", "backups"):
        (work / sub).mkdir()
    port = _free_port()
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "E2E_PORT": str(port),
        "SECRET_KEY": "e2e-only-secret-key",
        "DATA_DIR": str(work / "data"),
        "MEDIA_DIR": str(work / "media"),
        "BACKUPS_DIR": str(work / "backups"),
        "DATABASE_URL": f"sqlite:///{(work / 'data' / 'zonecast.db').as_posix()}",
        "DEFAULT_ADMIN_USERNAME": "admin",
        "DEFAULT_ADMIN_PASSWORD": ADMIN_PASSWORD,
        "RTP_MULTICAST_TTL": "0",
        "GLOBAL_ALL_CALL_ADDRESS": "239.255.99.99",
    }
    proc = subprocess.Popen([sys.executable, "-m", "tests.e2e._server"], cwd=work, env=env)
    url = f"http://127.0.0.1:{port}"
    api = httpx.Client(base_url=url, timeout=10)
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            pytest.fail("e2e server did not start")
        api.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}).raise_for_status()
        api.post("/api/auth/users", json={"username": OPERATOR[0], "password": OPERATOR[1], "role": "operator"}).raise_for_status()
        yield SimpleNamespace(url=url, work=work, api=api, db=work / "data" / "zonecast.db")
    finally:
        api.close()
        proc.terminate()
        proc.wait(timeout=15)


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        for launch in (lambda: p.chromium.launch(channel="chrome"), lambda: p.chromium.launch()):
            try:
                b = launch()
                break
            except Exception:  # noqa: BLE001 — try the next browser
                b = None
        if b is None:
            pytest.skip("no Chromium/Chrome available for Playwright")
        yield b
        b.close()


@pytest.fixture()
def page(browser, server):
    ctx = browser.new_context(locale="en-US", viewport={"width": 1400, "height": 900})
    ctx.add_init_script("try { localStorage.setItem('zc_lang', 'en') } catch (e) {}")
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda exc: pg.errors.append(str(exc)))
    pg.on("dialog", lambda d: d.accept())
    yield pg
    ctx.close()


def login(page, server, username="admin", password=ADMIN_PASSWORD):
    page.goto(f"{server.url}/login")
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("#login-submit")
    page.wait_for_url(f"{server.url}/")
    page.wait_for_selector("#history-body tr")


def open_tab(page, tab):
    page.click(f'#main-tabs .nav-item[data-tab="{tab}"]')
    page.wait_for_timeout(300)


def add_media(server, name="bell.wav", seconds=3.0):
    """A playable media row without ffmpeg: the PCM copy is written
    directly and the row inserted straight into the server's DB."""
    import sqlite3
    import uuid

    token = uuid.uuid4().hex
    _write_pcm(server.work / "media" / f"{token}.pcm8k.wav", seconds)
    (server.work / "media" / f"{token}.wav").write_bytes(b"RIFF")
    con = sqlite3.connect(server.db)
    try:
        cur = con.execute(
            "insert into media (original_filename, stored_filename, pcm_filename, duration_seconds, size_bytes, "
            "content_type, uploaded_at, normalized) values (?, ?, ?, ?, 10, 'audio/wav', datetime('now'), 0)",
            (name, f"{token}.wav", f"{token}.pcm8k.wav", seconds),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()
