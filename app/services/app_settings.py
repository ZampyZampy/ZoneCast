from sqlalchemy.orm import Session

from ..models import AppSettings

DEFAULT_THEME_COLOR = "#464c54"  # grigio/nero, in linea con lo stile richiesto


def get_settings(db: Session) -> AppSettings:
    settings_row = db.query(AppSettings).filter(AppSettings.id == 1).first()
    if not settings_row:
        settings_row = AppSettings(id=1, theme_color=DEFAULT_THEME_COLOR)
        db.add(settings_row)
        db.commit()
        db.refresh(settings_row)
    return settings_row


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def _mix(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[float, float, float]:
    return tuple(a + (b - a) * t for a, b in zip(c1, c2))


def derive_theme_shades(hex_color: str) -> dict:
    """From a single picked accent color, derives the hover shade (mixed
    toward black) and the light shade (mixed toward white, used for the
    active-nav indicator against the dark sidebar)."""
    try:
        rgb = _hex_to_rgb(hex_color)
    except (ValueError, IndexError):
        rgb = _hex_to_rgb(DEFAULT_THEME_COLOR)

    hover = _rgb_to_hex(_mix(rgb, (0, 0, 0), 0.18))
    light = _rgb_to_hex(_mix(rgb, (255, 255, 255), 0.35))

    return {
        "accent": _rgb_to_hex(rgb),
        "accent_hover": hover,
        "accent_light": light,
        "accent_rgb": f"{rgb[0]}, {rgb[1]}, {rgb[2]}",
    }
