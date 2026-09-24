"""
Common interface every brand-specific speaker integration implements.

To add support for a new brand/model:
  1. Create `app/services/drivers/<brand>.py` with a class subclassing
     `SpeakerDriver`, implementing whichever of `push_multicast_config`
     / `export_config` it actually supports (leave the other raising
     NotImplementedError — the default — and set the matching
     `supports_*` flag to False).
  2. Register an instance of it in `registry.py`.
Nothing outside `app/services/drivers/` needs to change: the rest of
the app (multicast_provisioning.py, the speakers router, the
dashboard's Speaker model) only ever asks the registry for a driver by
brand name and checks its `supports_*` flags — see
`multicast_provisioning.push_to_speaker` and
`routers/speakers.py::create_backup` for the two call sites.
"""
from abc import ABC
from dataclasses import dataclass, field

from ...models import Speaker


@dataclass
class PagingEntry:
    """One multicast paging-list entry to apply on a speaker — brand
    agnostic, computed by multicast_provisioning.compute_entries()."""
    index: int
    address: str
    port: int
    label: str
    priority: int


@dataclass
class PushResult:
    success: bool
    applied_keys: list[str] = field(default_factory=list)
    failed_keys: list[str] = field(default_factory=list)
    unsupported_brand: bool = False


class ConfigExportError(RuntimeError):
    pass


async def generic_check_reachable(speaker: Speaker, timeout: float = 2.0) -> bool:
    """Best-effort liveness check usable for ANY speaker regardless of
    brand/driver: plain HTTP GET of the admin web UI root, no
    credentials needed. Used both as SpeakerDriver's default
    `check_reachable` and directly for speakers with no registered
    driver at all (see routers/speakers.py::ping_speaker)."""
    import httpx

    url = f"http://{speaker.ip_address}:{speaker.http_port}/"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            return resp.status_code < 500
    except httpx.HTTPError:
        return False


class SpeakerDriver(ABC):
    brand: str = "generic"
    supports_multicast_push: bool = False
    supports_config_backup: bool = False
    # Per-slot paging/announcement volume, applied alongside the
    # multicast address on the same push (see Speaker.paging_volume
    # and drivers/fanvil_http.py's MCAST_Volume_R handling) — a
    # separate flag from supports_multicast_push since not every brand
    # that can receive a paging list can also set its playback volume
    # through the same mechanism.
    supports_paging_volume: bool = False

    async def push_multicast_config(self, speaker: Speaker, entries: list[PagingEntry]) -> PushResult:
        """Write the given multicast paging list to the device. Only
        called when `supports_multicast_push` is True."""
        raise NotImplementedError(f"{self.brand}: applicazione automatica della configurazione multicast non supportata")

    async def export_config(self, speaker: Speaker, fmt: str = "txt") -> str:
        """Fetch the device's own configuration export. Only called
        when `supports_config_backup` is True."""
        raise NotImplementedError(f"{self.brand}: backup della configurazione non supportato")

    async def check_reachable(self, speaker: Speaker, timeout: float = 2.0) -> bool:
        """Best-effort liveness check of the device's admin web UI.
        Generic HTTP GET by default — override only if a brand needs
        something else."""
        return await generic_check_reachable(speaker, timeout=timeout)
