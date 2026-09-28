"""
Where "sunrise/sunset" is, for the dashboard's Automatic theme: the
coordinates of the configured timezone's reference city, from the IANA
zone1970.tab table. The browser's own geolocation can't be used — it's
unavailable on plain-HTTP LAN deployments and disallowed by our
Permissions-Policy header — and the timezone is what the site is set up
for anyway (see services/scheduler.py's scheduler_timezone()).
"""
import re
from functools import lru_cache
from importlib import resources
from pathlib import Path

ROME = (41.9, 12.5)  # the project's default TIMEZONE, used if nothing matches
_COORD_RE = re.compile(r"^([+-])(\d{2})(\d{2})(\d{2})?([+-])(\d{3})(\d{2})(\d{2})?$")


def _zone_table() -> str:
    try:
        return resources.files("tzdata").joinpath("zoneinfo/zone1970.tab").read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError, OSError):
        pass
    for path in (Path("/usr/share/zoneinfo/zone1970.tab"), Path("/usr/share/zoneinfo/zone.tab")):
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return ""


def _parse(coords: str) -> tuple[float, float] | None:
    m = _COORD_RE.match(coords)
    if not m:
        return None
    lat_sign, lat_d, lat_m, lat_s, lon_sign, lon_d, lon_m, lon_s = m.groups()
    lat = int(lat_d) + int(lat_m) / 60 + int(lat_s or 0) / 3600
    lon = int(lon_d) + int(lon_m) / 60 + int(lon_s or 0) / 3600
    return (-lat if lat_sign == "-" else lat, -lon if lon_sign == "-" else lon)


@lru_cache(maxsize=32)
def coords_for_timezone(tz: str) -> tuple[float, float]:
    for line in _zone_table().splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 3 and fields[2] == tz:
            parsed = _parse(fields[1])
            if parsed:
                return round(parsed[0], 3), round(parsed[1], 3)
    return ROME
