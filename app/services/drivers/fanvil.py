"""
Fanvil adapter — the only brand implemented so far. See base.py for
what a driver needs to implement to support a new brand.
"""
from ...models import Speaker
from . import fanvil_http
from .base import ConfigExportError, PagingEntry, PushResult, SpeakerDriver


class FanvilDriver(SpeakerDriver):
    brand = "Fanvil"
    supports_multicast_push = True
    supports_config_backup = True
    supports_paging_volume = True

    async def push_multicast_config(self, speaker: Speaker, entries: list[PagingEntry]) -> PushResult:
        """Writes the paging list via the device's real MCAST Listening
        form (session login + full /mcast.htm POST) and verifies by
        reading the page back — see fanvil_http.push_mcast_listening
        for the full story of why this replaced an earlier CGI-based
        approach that looked like it worked but silently wrote
        nothing."""
        return await fanvil_http.push_mcast_listening(speaker, entries)

    async def export_config(self, speaker: Speaker, fmt: str = "txt") -> str:
        return await fanvil_http.export_config(speaker, fmt=fmt)

    async def check_reachable(self, speaker: Speaker, timeout: float = 2.0) -> bool:
        return await fanvil_http.check_reachable(speaker, timeout=timeout)


__all__ = ["FanvilDriver", "ConfigExportError"]
