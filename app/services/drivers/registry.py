"""
Maps a Speaker's `brand` string to its driver instance. To add a new
brand: implement `SpeakerDriver` in a new module in this package (see
base.py), import it here, and add one line to `_DRIVERS`.
"""
from .base import SpeakerDriver
from .fanvil import FanvilDriver

_DRIVERS: dict[str, SpeakerDriver] = {
    "fanvil": FanvilDriver(),
}


def get_driver(brand: str | None) -> SpeakerDriver | None:
    if not brand:
        return None
    return _DRIVERS.get(brand.strip().lower())
