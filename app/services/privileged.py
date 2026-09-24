"""
Thin wrapper around the one root-privileged helper script ZoneCast is
allowed to invoke: deploy/zonecast-netctl.sh, granted via a narrow
NOPASSWD sudoers rule scoped to exactly that script path (see
deploy/install_ubuntu.sh) — the app process itself never runs as root.

Only meaningful on a native (systemd) install: on Docker, `sudo` either
isn't installed in the image or the wrapper/sudoers rule was never set
up, so this fails fast with a clear message rather than hanging or
silently doing nothing.
"""
import subprocess

WRAPPER_PATH = "/opt/zonecast/deploy/zonecast-netctl.sh"


class PrivilegedActionError(RuntimeError):
    pass


def run_netctl(args: list[str], stdin_text: str | None = None, timeout: float = 15.0) -> str:
    cmd = ["sudo", "-n", WRAPPER_PATH, *args]
    try:
        result = subprocess.run(cmd, input=stdin_text, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise PrivilegedActionError(
            "Funzione non disponibile: richiede l'installazione nativa (systemd), non Docker."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PrivilegedActionError("Timeout durante l'operazione di sistema") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PrivilegedActionError(detail or f"Comando fallito (exit {result.returncode})")
    return result.stdout.strip()
