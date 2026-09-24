"""
Lets the dashboard show the host's clock/NTP status, and — on a native
(systemd) install — control it: toggle NTP on/off and change the
timezone, via the privileged wrapper (see services/privileged.py and
deploy/zonecast-netctl.sh). On Docker this degrades to the old
read-only behavior rather than failing, since the wrapper/sudoers rule
generally isn't set up there and D-Bus is blocked by the default
AppArmor profile anyway:

- Status is read via `timedatectl show` when available (native —
  reflects NTP on/off, sync state, timezone, all in one call). If that
  fails (no D-Bus access, e.g. Docker), falls back to the older
  file-based approach: `date` for the clock, the synced marker file
  systemd-timesyncd writes, and timesyncd.conf for configured servers.
- Manual time set always uses `date -s` via CAP_SYS_TIME (a plain
  kernel syscall, not D-Bus) — works the same in both deployments. If
  NTP sync is currently on and controllable, it's switched off first:
  otherwise systemd-timesyncd notices the drift and reverts the
  manual value at its next poll, often within seconds, making the
  change look like it silently didn't take.
- NTP on/off, NTP server list and timezone changes go through the
  sudo'd wrapper script, which fails fast with a clear "richiede
  installazione nativa" message if it isn't installed (see
  PrivilegedActionError).
- Ubuntu 26.04+ ships chrony instead of systemd-timesyncd (timesyncd
  isn't even installed); `timedatectl` abstracts over both for on/off
  and sync status, but the configured-servers list doesn't come from
  one place — see `_read_ntp_servers` below.
"""
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import privileged

TIMESYNCD_CONF = Path("/etc/systemd/timesyncd.conf")
SYNCHRONIZED_MARKER = Path("/run/systemd/timesync/synchronized")
# Must match CHRONY_ZONECAST_CONF in deploy/zonecast-netctl.sh.
CHRONY_ZONECAST_CONF = Path("/etc/chrony/conf.d/99-zonecast.conf")


class SystemTimeError(RuntimeError):
    pass


@dataclass
class TimeStatus:
    local_time: str
    timezone: str
    ntp_synchronized: bool
    ntp_servers: list[str]
    ntp_enabled: bool | None       # None when unknown (Docker fallback path)
    controllable: bool             # whether ntp-on/off and timezone changes are available here


def _run(cmd: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise SystemTimeError(f"comando non disponibile: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SystemTimeError(f"timeout eseguendo: {' '.join(cmd)}") from exc


def _chrony_installed() -> bool:
    """Whether chrony is the box's NTP backend — checked by unit-file
    presence, not by `is-active`: NTP sync being toggled off (this
    tab's own switch, or the auto-disable manual clock-set does)
    legitimately stops chrony without uninstalling it, and treating
    "not active" as "not chrony" here misrouted reads/writes to
    timesyncd.conf, which isn't even present on a chrony-only box."""
    try:
        result = subprocess.run(
            ["systemctl", "list-unit-files", "chrony.service", "--no-legend"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return "chrony" in result.stdout


def _read_ntp_servers() -> list[str]:
    if _chrony_installed():
        # Prefer ZoneCast's own conf.d file when present — it holds
        # exactly what the admin typed (e.g. "time.google.com"), read
        # back verbatim. `chronyc sources` reports the *resolved* IP of
        # whichever peer chrony currently has selected instead, which is
        # correct for the distro-default pool (a pool has no single name
        # to show — each peer really is just an IP) but confusingly
        # replaces a plain hostname with an IP address for our own
        # explicit `server` lines, which do have one.
        if CHRONY_ZONECAST_CONF.exists():
            servers = []
            for line in CHRONY_ZONECAST_CONF.read_text().splitlines():
                m = re.match(r"^\s*server\s+(\S+)", line)
                if m:
                    servers.append(m.group(1))
            return servers

        # No override — report what the distro-default pool is actually
        # using right now, via `chronyc -c sources` (CSV, untruncated
        # names unlike the plain table).
        try:
            result = subprocess.run(
                ["chronyc", "-c", "sources"], capture_output=True, text=True, timeout=5
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        servers: list[str] = []
        for line in result.stdout.splitlines():
            fields = line.split(",")
            if len(fields) > 2 and fields[2] and fields[2] not in servers:
                servers.append(fields[2])
        return servers

    try:
        with open(TIMESYNCD_CONF) as f:
            in_time_section = False
            for line in f:
                stripped = line.strip()
                if stripped.startswith("["):
                    in_time_section = stripped.lower() == "[time]"
                    continue
                if in_time_section:
                    m = re.match(r"^\s*NTP\s*=\s*(.+)$", line)
                    if m:
                        return m.group(1).split()
    except FileNotFoundError:
        pass
    return []


def _timedatectl_show() -> dict[str, str] | None:
    try:
        result = subprocess.run(["timedatectl", "show", "--no-pager"], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None  # not available (e.g. Docker) — caller falls back to file-based reading
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def get_status() -> TimeStatus:
    local_time_result = _run(["date", "+%Y-%m-%d %H:%M:%S %Z"])
    if local_time_result.returncode != 0:
        raise SystemTimeError(f"comando date fallito: {local_time_result.stderr.strip()}")
    local_time = local_time_result.stdout.strip()

    td = _timedatectl_show()
    if td is not None:
        return TimeStatus(
            local_time=local_time,
            timezone=td.get("Timezone", "?"),
            ntp_synchronized=td.get("NTPSynchronized") == "yes",
            ntp_servers=_read_ntp_servers(),
            ntp_enabled=td.get("NTP") == "yes",
            controllable=True,
        )

    tz_result = _run(["date", "+%Z"])
    return TimeStatus(
        local_time=local_time,
        timezone=tz_result.stdout.strip() if tz_result.returncode == 0 else "?",
        ntp_synchronized=SYNCHRONIZED_MARKER.exists(),
        ntp_servers=_read_ntp_servers(),
        ntp_enabled=None,
        controllable=False,
    )


def set_manual_time(dt: datetime) -> bool:
    """Sets the clock to `dt`. Returns whether NTP sync was switched
    off as a side effect (see module docstring) — the caller can use
    this to tell the admin why, rather than leaving it silent."""
    ntp_disabled = False
    status = get_status()
    if status.controllable and status.ntp_enabled:
        set_ntp_enabled(False)
        ntp_disabled = True

    formatted = dt.strftime("%Y-%m-%d %H:%M:%S")
    result = _run(["date", "-s", formatted])
    if result.returncode != 0:
        raise SystemTimeError(
            f"Impossibile impostare l'orario (verificare la capability CAP_SYS_TIME): {result.stderr.strip()}"
        )
    return ntp_disabled


def list_timezones() -> list[str]:
    result = _run(["timedatectl", "list-timezones"])
    if result.returncode != 0:
        raise SystemTimeError("Impossibile elencare i fusi orari — richiede installazione nativa")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def set_ntp_enabled(enabled: bool) -> None:
    try:
        privileged.run_netctl(["ntp-on" if enabled else "ntp-off"])
    except privileged.PrivilegedActionError as exc:
        raise SystemTimeError(str(exc)) from exc


def set_timezone(tz: str) -> None:
    try:
        privileged.run_netctl(["timezone", tz])
    except privileged.PrivilegedActionError as exc:
        raise SystemTimeError(str(exc)) from exc


_NTP_SERVER_RE = re.compile(r"^[A-Za-z0-9.\-:]+$")


def set_ntp_servers(servers: list[str]) -> None:
    """Replaces the configured NTP server list. An empty list clears
    it, falling back to systemd-timesyncd's own compiled-in default
    servers."""
    cleaned = [s.strip() for s in servers if s.strip()]
    for s in cleaned:
        if not _NTP_SERVER_RE.match(s):
            raise SystemTimeError(f"Server NTP non valido: '{s}'")
    try:
        privileged.run_netctl(["ntp-servers"], stdin_text=" ".join(cleaned))
    except privileged.PrivilegedActionError as exc:
        raise SystemTimeError(str(exc)) from exc
