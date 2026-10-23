"""Visual themes shared by HTML cards and ASS captions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    bg1: str        # gradient start (hex RGB)
    bg2: str        # gradient end
    text: str
    muted: str
    accent: str     # caption highlight, card emphasis
    accent2: str
    dark: bool = True


THEMES: dict[str, Theme] = {
    "midnight": Theme("midnight", "#0B1026", "#1B2A4A", "#FFFFFF", "#AFC3E6", "#FFD23F", "#4CC9F0"),
    "ocean": Theme("ocean", "#03256C", "#1768AC", "#FFFFFF", "#CFE3FF", "#FFD166", "#06D6A0"),
    "sunset": Theme("sunset", "#2D0B3A", "#B23A48", "#FFFFFF", "#FAD4D8", "#FFE66D", "#FF9F1C"),
    "forest": Theme("forest", "#0B2B26", "#1F5F4F", "#FFFFFF", "#CDEBE0", "#F2E94E", "#7BE0AD"),
    "paper": Theme("paper", "#F7F3EA", "#EDE6D6", "#1B1B1B", "#5A5A5A", "#E63946", "#1D3557", dark=False),
}

DEFAULT_THEME = "midnight"


def get_theme(name: str | None) -> Theme:
    return THEMES.get(name or DEFAULT_THEME, THEMES[DEFAULT_THEME])


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR (ASS byte order, alpha 0 = opaque)."""
    value = hex_rgb.lstrip("#")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha:02X}{blue}{green}{red}".upper()
