"""Smoke test of a real Docker install, run by the CI "docker" job
against the container started with docker compose, as the deploy guide
says. It drives the app over HTTP like a user would. Standard library
only: it runs on the CI host's own python3, not in the test venv.

    python3 tests/docker_smoke.py setup URL PASSWORD HOST_NTP_SYNCED
    (docker compose restart)
    python3 tests/docker_smoke.py after-restart URL PASSWORD

`setup` creates a zone, a speaker, an audio file (converted by ffmpeg
in the image) and an automatic backup (built in a child process);
`after-restart` checks they all survived the restart.
Not a pytest module (no test_ prefix): pytest never collects it.
"""
import http.cookiejar
import io
import json
import math
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave

ZONE = "Docker smoke zone"
SPEAKER = "Docker smoke speaker"
MEDIA = "docker-smoke-bell.wav"
BACKUP_PASSWORD = "docker-smoke-backup-password"


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None, raw=None, content_type=None, expect=200):
        data, headers = None, {}
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        elif raw is not None:
            data, headers["Content-Type"] = raw, content_type
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=60) as res:
                status, payload, ctype = res.status, res.read(), res.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            status, payload, ctype = exc.code, exc.read(), exc.headers.get("Content-Type", "")
        if status != expect:
            sys.exit(f"FAIL {method} {path}: HTTP {status} (expected {expect}): {payload[:500]!r}")
        return json.loads(payload) if "json" in ctype else payload

    def login(self, password):
        self.call("POST", "/api/auth/login", {"username": "admin", "password": password})


def check(condition, message):
    if not condition:
        sys.exit(f"FAIL {message}")
    print(f"ok   {message}")


def _wav_bytes(seconds=2.0, rate=44100):
    """A stereo 44.1 kHz chime: the image has to convert it (ffmpeg) to
    the 8 kHz mono it streams."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for n in range(int(seconds * rate)):
            v = int(12000 * math.sin(2 * math.pi * 880 * n / rate))
            frames += struct.pack("<hh", v, v)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def _multipart(field, filename, content, ctype="audio/wav"):
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def setup(c, password, host_ntp_synced):
    c.login(password)
    check(b"/static/js/main.js" in c.call("GET", "/"), "the dashboard page is served")
    version = c.call("GET", "/api/system/version")["version"]
    print(f"     version {version}")

    zone = c.call("POST", "/api/zones", {"name": ZONE, "multicast_address": "239.255.60.1"})
    # No brand: nothing to push to, so no request ever leaves for 192.0.2.x.
    speaker = c.call("POST", "/api/speakers", {"name": SPEAKER, "ip_address": "192.0.2.60",
                                               "own_multicast_address": "239.255.61.1"})
    c.call("PUT", f"/api/zones/{zone['id']}", {"name": ZONE, "multicast_address": "239.255.60.1",
                                               "speaker_ids": [speaker["id"]]})
    check(any(s["id"] == speaker["id"] and s["zone_ids"] == [zone["id"]] for s in c.call("GET", "/api/speakers")),
          "a zone with a member speaker is saved")

    body, ctype = _multipart("file", MEDIA, _wav_bytes())
    media = c.call("POST", "/api/media/upload", raw=body, content_type=ctype)
    check(abs((media["duration_seconds"] or 0) - 2.0) < 0.2, f"an audio file is uploaded and converted by ffmpeg ({media['duration_seconds']} s)")

    time_status = c.call("GET", "/api/system/time")
    check(time_status["controllable"] is False, "NTP settings are read-only in Docker")
    check(time_status["ntp_synchronized"] is host_ntp_synced,
          f"the NTP state matches the host's timedatectl ({host_ntp_synced}), read from the kernel")

    c.call("PUT", "/api/system/auto-backup", {
        "enabled": False, "frequency": "daily", "weekday": 0, "time_of_day": "03:00:00", "include_media": True,
        "keep_local": 3, "keep_remote": 14, "bundle_password": BACKUP_PASSWORD, "bundle_password_confirmed": True,
        "destination": "local"})
    c.call("POST", "/api/system/auto-backup/run")
    for _ in range(120):
        policy = c.call("GET", "/api/system/auto-backup")
        if not policy["running"] and policy["last_status"]:
            break
        time.sleep(1)
    check(policy["last_status"] == "ok", f"an automatic backup is built in a child process ({policy['last_status']}, {policy['last_error']})")
    files = c.call("GET", "/api/system/auto-backup/files")
    check([f["name"] for f in files] == [policy["last_file"]], f"the backup file is listed ({policy['last_file']})")


def after_restart(c, password):
    c.login(password)
    speakers = c.call("GET", "/api/speakers")
    zones = c.call("GET", "/api/zones")
    check(any(z["name"] == ZONE and len(z["speaker_ids"]) == 1 for z in zones), "the zone and its member survived the restart")
    check(any(s["name"] == SPEAKER for s in speakers), "the speaker survived the restart")
    media = [m for m in c.call("GET", "/api/media") if m["original_filename"] == MEDIA]
    check(len(media) == 1, "the audio file survived the restart")
    audio = c.call("GET", f"/api/media/{media[0]['id']}/download")
    check(audio[:4] == b"RIFF", "and it can still be downloaded")
    files = c.call("GET", "/api/system/auto-backup/files")
    check(len(files) == 1, "the automatic backup survived the restart")
    policy = c.call("GET", "/api/system/auto-backup")
    check(policy["has_bundle_password"] and not policy["foreign"], "the backup settings are still this installation's")


if __name__ == "__main__":
    phase, url, password = sys.argv[1:4]
    client = Client(url)
    if phase == "setup":
        setup(client, password, sys.argv[4].strip() == "yes")
    elif phase == "after-restart":
        after_restart(client, password)
    else:
        sys.exit(f"unknown phase {phase}")
    print(f"PASS {phase}")
