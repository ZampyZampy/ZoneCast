"""
Low-level HTTP client for Fanvil devices — the details specific to this
one brand. `fanvil.py` in this same package wraps these functions
behind the brand-agnostic `SpeakerDriver` interface (base.py); nothing
outside this package should import from here directly.

Confirmed against a real Fanvil A233 (firmware 2.12.58.24, web server
"Rapid Logic"):

- **The `ConfigManApp.com?key=...&value=...` CGI is a no-op for the
  multicast paging fields.** It replies `200 OK, result = [success]`
  for ANY key/value pair — including keys that don't exist — and
  nothing is ever actually written: verified directly by reading back
  `/default_user_config.txt` after calling it with both guessed keys
  (`paging.multicast_addr.1`) and the real internal ones (`MCAST1
  Host`), neither persisted. Do not trust this endpoint's response
  body as confirmation of anything. It's kept below only as a
  best-effort escape hatch for other CGI actions you may verify
  yourself; multicast paging no longer uses it.

- **The real mechanism** for multicast paging is the same HTML form
  the device's own web UI submits: `POST /mcast.htm`, fields named
  `MCAST_NameShow(N)` / `MCAST_UrlShow(N)` / `MCAST_PrioChannel_R(N)` /
  `MCAST_Volume_R(N)` for N=1..20, plus a handful of page-level
  settings (`MCAST_PrioTab_R`, tone, etc.) — verified end-to-end: typed
  a value into the real web UI, watched the POST, then confirmed it in
  `/default_user_config.txt` under `<MCAST CFG MODULE>`.
  **Must also include `DefaultSubmit=Apply`** (the real "Apply"
  button's name/value) — this page has three separate `<form>`s and
  the device's handler silently no-ops (200 OK, nothing written) if it
  can't tell which one submitted. This bit longer than the rest to
  find: a POST missing it looks completely successful.

- **That page requires a session cookie, not Basic Auth.** Fetching it
  with only Basic Auth (which works fine for the CGI and for
  `export_config` below) returns a "session expired, please log in
  again" stub instead of the real form. The session is established
  with a nonce challenge: `GET /key==nonce` returns a token, the
  client computes `username + ":" + md5(username + ":" + password +
  ":" + nonce)`, and POSTs that as `encoded` to `/` together with a
  `Cookie: auth=<nonce>` header — reverse-engineered from the login
  page's own inline JS (`comm.js`'s `encode()`/`md5()`). httpx's
  automatic cookie jar would not retain this cookie reliably in
  testing (possibly because the "domain" here is a bare IP); the
  `Cookie` header is set explicitly on every request instead of
  relying on the jar.

- **Per-slot paging volume is the same form's `MCAST_Volume_R(N)`
  field** — confirmed via its `<select>` options: `"default"` or
  `"1"`..`"9"`. Set alongside the address/name for our own managed
  slots when `speaker.paging_volume` is configured (see
  `push_mcast_listening` below); left untouched (whatever
  `_mcast_form_from_html` read back) when it's `None`, so an
  unconfigured speaker doesn't silently get its existing volume reset.
  SIP call volume was NOT found anywhere in `phone.htm` (94KB, no
  volume-related field of any kind) — the A233 being a paging horn
  rather than a handset, it may simply not expose one via the web UI
  (hardware-only, or shared with paging volume). Not implemented here;
  revisit if a real need for it comes up and can be verified live.

- **This device cannot handle concurrent OR rapid-fire requests.**
  Sending several calls at once made most of them fail with
  `httpx.ReadTimeout` even with correct credentials, and hammering it
  with repeated test requests in a short window during development
  once made it stop returning nonces at all (empty body, HTTP 200)
  until it was left alone for a while. Every caller here sends
  requests to a given speaker strictly sequentially, and
  `push_mcast_listening` deliberately does the minimum number of
  requests: one login, one read, one write, one read-back to verify.
"""
import asyncio
import hashlib
import logging
import re
import time

import httpx

from ...models import Speaker
from .base import ConfigExportError, PagingEntry, PushResult

logger = logging.getLogger("zonecast.drivers.fanvil")

_RETRY_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 0.5
_MCAST_SLOTS = 20


async def check_reachable(speaker: Speaker, timeout: float = 2.0) -> bool:
    """Best-effort liveness check: HTTP(S) reachability of the device's
    admin web UI on http_port. Does not require valid credentials."""
    url = f"http://{speaker.ip_address}:{speaker.http_port}/"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            return resp.status_code < 500
    except httpx.HTTPError:
        return False


async def trigger_action_url(speaker: Speaker, action: str, params: dict, timeout: float = 5.0) -> bool:
    """Generic best-effort CGI trigger (HTTP Basic auth). Confirmed to
    be a no-op for MCAST_* keys (see module docstring) — kept for other
    CGI actions you may verify independently for your firmware."""
    url = f"http://{speaker.ip_address}:{speaker.http_port}/cgi-bin/{action}"
    auth = httpx.BasicAuth(speaker.http_username, speaker.http_password) if speaker.http_password else None

    last_error = ""
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, auth=auth) as client:
                resp = await client.get(url, params=params)
            if resp.status_code < 400 and "success" in resp.text.lower():
                return True
            last_error = f"HTTP {resp.status_code} — {resp.text[:200]}"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt < _RETRY_ATTEMPTS:
            await asyncio.sleep(_RETRY_DELAY_SECONDS)

    logger.warning(
        "CGI call to %s (%s, key=%s) failed after %d attempt(s): %s",
        speaker.ip_address, action, params.get("key", "?"), _RETRY_ATTEMPTS, last_error,
    )
    return False


async def export_config(speaker: Speaker, fmt: str = "txt", timeout: float = 10.0) -> str:
    """Fetches the device's own configuration export. Plain static
    files behind Basic-auth, no session cookie needed:
      - /default_user_config.txt — full config, human-readable
      - /default_user_config.xml — full config, XML
      - /ncConfig.txt            — "nc" config subset
    Contains the device's own credentials/SIP settings — callers must
    treat the result as sensitive (admin-only, not logged)."""
    path = {
        "txt": "/default_user_config.txt",
        "xml": "/default_user_config.xml",
        "nc": "/ncConfig.txt",
    }.get(fmt)
    if not path:
        raise ConfigExportError(f"Formato di export non supportato: {fmt}")

    url = f"http://{speaker.ip_address}:{speaker.http_port}{path}"
    auth = httpx.BasicAuth(speaker.http_username, speaker.http_password) if speaker.http_password else None
    try:
        async with httpx.AsyncClient(timeout=timeout, auth=auth) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        raise ConfigExportError(f"Impossibile raggiungere il device: {exc}") from exc

    if resp.status_code != 200:
        raise ConfigExportError(f"Il device ha risposto HTTP {resp.status_code}")
    return resp.text


async def set_config_param(speaker: Speaker, key: str, value: str, timeout: float = 5.0) -> bool:
    """Best-effort single CGI parameter write. NOT used for multicast
    paging (confirmed no-op there, see module docstring) — kept for
    other keys you may verify independently."""
    return await trigger_action_url(speaker, action="ConfigManApp.com", params={"key": key, "value": value}, timeout=timeout)


# ---------- Session login (nonce + MD5 challenge) ----------
class _LoginError(RuntimeError):
    pass


async def _login(client: httpx.AsyncClient, ip: str, port: int, username: str, password: str) -> dict:
    """Returns the `Cookie` header to pass on every subsequent
    authenticated request. See module docstring for the algorithm."""
    base = f"http://{ip}:{port}"
    nonce_resp = await client.get(f"{base}/key==nonce", params={"now": int(time.time() * 1000)})
    nonce = nonce_resp.text.strip()
    if not nonce:
        raise _LoginError(
            "Il device non ha restituito un nonce di login (server sovraccarico o in rate-limit temporaneo — riprovare tra qualche minuto)"
        )
    cookie_header = {"Cookie": f"auth={nonce}"}
    encoded = f"{username}:" + hashlib.md5(f"{username}:{password}:{nonce}".encode()).hexdigest()
    login_resp = await client.post(
        f"{base}/",
        data={"encoded": encoded, "CurLanguage": "it", "ReturnPage": "/"},
        headers=cookie_header,
    )
    if login_resp.status_code != 200:
        raise _LoginError(f"Login al web UI del device fallito: HTTP {login_resp.status_code}")
    return cookie_header


# ---------- mcast.htm form parsing/writing ----------
def _parse_selected(html: str, name: str) -> str | None:
    m = re.search(rf'name="{re.escape(name)}"[^>]*>(.*?)</select>', html, re.S)
    if not m:
        return None
    sel = re.search(r'<option[^>]*value="([^"]*)"[^>]*SELECTED', m.group(1))
    return sel.group(1) if sel else None


def _parse_text_value(html: str, name: str) -> str:
    m = re.search(rf'name="{re.escape(name)}"[^>]*value="([^"]*)"', html)
    return m.group(1) if m else ""


def _parse_checked(html: str, name: str) -> bool:
    m = re.search(rf'name="{re.escape(name)}"[^>]*?(CHECKED)?>', html)
    return bool(m and m.group(1))


def _mcast_form_from_html(html: str) -> dict:
    """Parses the CURRENT state of every field on the MCAST Listening
    form, so a subsequent POST can preserve everything except the
    specific slots being changed."""
    form = {
        "MCAST_PrioTab_R": _parse_selected(html, "MCAST_PrioTab_R") or "1",
        "MCAST_IntercomPrioTab_R": _parse_selected(html, "MCAST_IntercomPrioTab_R") or "0",
        "MCAST_RENEW_TIME_RW": _parse_text_value(html, "MCAST_RENEW_TIME_RW"),
        "MCAST_MULTICAST_TONE_RW": _parse_selected(html, "MCAST_MULTICAST_TONE_RW") or "0",
        "ReturnPage": "/mcast.htm",
        # The device's own handler apparently keys off which submit
        # button fired (this page has 3 separate <form>s) — a POST
        # without this returns 200 and looks like it worked but writes
        # nothing. Its name/value exactly as the real "Apply" button.
        "DefaultSubmit": "Apply",
    }
    for checkbox in ("MCAST_ENABLE_PRIOTRITY_RW", "MCAST_ENABLE_PRIOCHAN_RW", "MCAST_ENABLE_EMERCHAN_RW"):
        if _parse_checked(html, checkbox):
            form[checkbox] = "ON"
    for n in range(1, _MCAST_SLOTS + 1):
        form[f"MCAST_NameShow({n})"] = _parse_text_value(html, f"MCAST_NameShow({n})")
        form[f"MCAST_UrlShow({n})"] = _parse_text_value(html, f"MCAST_UrlShow({n})")
        form[f"MCAST_PrioChannel_R({n})"] = _parse_selected(html, f"MCAST_PrioChannel_R({n})") or "0"
        form[f"MCAST_Volume_R({n})"] = _parse_selected(html, f"MCAST_Volume_R({n})") or "0"
    return form


async def push_mcast_listening(speaker: Speaker, entries: list[PagingEntry], timeout: float = 12.0) -> PushResult:
    """Writes the multicast paging list via the device's real
    mechanism (session login + full /mcast.htm form POST, see module
    docstring), and VERIFIES by reading the page back afterward and
    comparing — not just trusting the device's response.

    Every push makes ZoneCast the sole source of truth for this
    device's paging slots: slots covered by `entries` are (over)written,
    and every OTHER slot (1.._MCAST_SLOTS) is explicitly cleared, so
    anything configured directly on the device's own web UI — or left
    over from a zone this speaker no longer belongs to — doesn't
    silently linger. Page-level settings not tied to a specific slot
    (priority tab, tone, etc.) are still preserved as read."""
    ip, port = speaker.ip_address, speaker.http_port
    base = f"http://{ip}:{port}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            headers = await _login(client, ip, port, speaker.http_username, speaker.http_password)
        except (_LoginError, httpx.HTTPError) as exc:
            logger.warning("Fanvil %s: login fallito: %s", ip, exc)
            return PushResult(success=False, failed_keys=[f"login: {exc}"])

        try:
            current_html = (await client.get(f"{base}/mcast.htm", headers=headers)).text
        except httpx.HTTPError as exc:
            return PushResult(success=False, failed_keys=[f"lettura mcast.htm: {exc}"])

        if "MCAST_NameShow" not in current_html:
            return PushResult(success=False, failed_keys=["sessione non autenticata (contenuto pagina inatteso)"])

        form = _mcast_form_from_html(current_html)

        skipped = [e for e in entries if e.index > _MCAST_SLOTS]
        applicable = [e for e in entries if e.index <= _MCAST_SLOTS]
        managed_indices = {e.index for e in applicable}
        for entry in applicable:
            form[f"MCAST_NameShow({entry.index})"] = entry.label[:40]
            form[f"MCAST_UrlShow({entry.index})"] = f"{entry.address}:{entry.port}"
            # None (not configured) leaves the device's own current
            # value alone — _mcast_form_from_html already preserved it
            # from the page read above.
            if speaker.paging_volume:
                form[f"MCAST_Volume_R({entry.index})"] = speaker.paging_volume
        # Any slot ZoneCast doesn't manage is cleared, not left as-is —
        # see the "sole source of truth" note in the docstring above.
        for n in range(1, _MCAST_SLOTS + 1):
            if n in managed_indices:
                continue
            form[f"MCAST_NameShow({n})"] = ""
            form[f"MCAST_UrlShow({n})"] = ""

        try:
            post_resp = await client.post(f"{base}/mcast.htm", data=form, headers=headers)
        except httpx.HTTPError as exc:
            return PushResult(success=False, failed_keys=[f"scrittura mcast.htm: {exc}"])
        if post_resp.status_code != 200:
            return PushResult(success=False, failed_keys=[f"scrittura mcast.htm: HTTP {post_resp.status_code}"])

        try:
            verify_html = (await client.get(f"{base}/mcast.htm", headers=headers)).text
        except httpx.HTTPError as exc:
            return PushResult(success=False, failed_keys=[f"verifica mcast.htm: {exc}"])

    applied_keys: list[str] = []
    failed_keys: list[str] = [f"index {e.index} (oltre i {_MCAST_SLOTS} slot supportati dal device)" for e in skipped]
    for entry in applicable:
        expected = f"{entry.address}:{entry.port}"
        actual = _parse_text_value(verify_html, f"MCAST_UrlShow({entry.index})")
        label = f"MCAST slot {entry.index} ({entry.label})"
        if actual == expected:
            applied_keys.append(label)
        else:
            failed_keys.append(f"{label}: atteso '{expected}', letto '{actual}'")

    for n in range(1, _MCAST_SLOTS + 1):
        if n in managed_indices:
            continue
        leftover = _parse_text_value(verify_html, f"MCAST_UrlShow({n})")
        if leftover:
            failed_keys.append(f"MCAST slot {n}: non svuotato (letto '{leftover}')")
        else:
            applied_keys.append(f"MCAST slot {n} (svuotato)")

    return PushResult(success=not failed_keys, applied_keys=applied_keys, failed_keys=failed_keys)
