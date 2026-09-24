"""Output formats and their layout geometry.

Safe areas for vertical video are the intersection of what TikTok, Instagram /
Facebook Reels and YouTube Shorts leave uncovered by their own UI: roughly
x 65-960 and y 380-1250 on a 1080x1920 frame. Reels is the tightest at the bottom
(about 35% covered by caption + buttons), Shorts at the top (about 380 px).
Headlines sit at the top of that band and captions at its bottom, so both survive
whichever app the video ends up in.

Font sizes are ASS Fontsize units: libass maps them to usWinAscent+usWinDescent,
so for Be Vietnam Pro the em is Fontsize * 1000/1526 (Fontsize 100 = 65.5 px em).
"""

from __future__ import annotations

from dataclasses import dataclass

FPS = 30
HEADLINE_BOX_PAD = 16      # ASS Outline of the boxed headline style = box padding in px


@dataclass(frozen=True)
class FormatSpec:
    name: str
    width: int
    height: int
    image_aspect: str          # value for create_image aspect_ratio
    safe_left: int
    safe_right: int            # x of the right edge of the safe area
    safe_top: int
    safe_bottom: int           # y of the bottom edge of the safe area
    headline_size: int         # ASS Fontsize
    caption_size: int          # ASS Fontsize
    outline: int               # caption outline width (px)
    headline_max_chars: int    # sanity cap; the real limit is pixel fit in 2 lines
    min_total: float           # seconds
    max_total: float
    target_total: tuple[float, float]
    max_scene_syllables: int   # keeps a visual change every ~3-7 s
    min_scenes: int
    max_scenes: int

    @property
    def safe_width(self) -> int:
        return self.safe_right - self.safe_left

    @property
    def text_width(self) -> int:
        """Usable caption line width once the outline is drawn on both sides."""
        return self.safe_width - 2 * self.outline - 8

    @property
    def headline_width(self) -> int:
        """Usable headline line width inside its padded box (HEADLINE_BOX_PAD each side)."""
        return self.safe_width - 2 * HEADLINE_BOX_PAD - 8

    @property
    def caption_margin_v(self) -> int:
        """ASS MarginV for bottom-aligned captions: distance from frame bottom."""
        return self.height - self.safe_bottom

    @property
    def headline_margin_v(self) -> int:
        return self.safe_top

    @property
    def size(self) -> str:
        return f"{self.width}x{self.height}"


FORMATS: dict[str, FormatSpec] = {
    "short": FormatSpec(
        name="short", width=1080, height=1920, image_aspect="9:16",
        safe_left=70, safe_right=950, safe_top=390, safe_bottom=1240,
        headline_size=100, caption_size=96, outline=7, headline_max_chars=60,
        min_total=15.0, max_total=90.0, target_total=(30.0, 60.0),
        max_scene_syllables=32, min_scenes=3, max_scenes=16,
    ),
    "long": FormatSpec(
        name="long", width=1920, height=1080, image_aspect="16:9",
        safe_left=120, safe_right=1800, safe_top=80, safe_bottom=1000,
        headline_size=84, caption_size=72, outline=6, headline_max_chars=80,
        min_total=45.0, max_total=900.0, target_total=(120.0, 480.0),
        max_scene_syllables=60, min_scenes=4, max_scenes=60,
    ),
    "square": FormatSpec(
        name="square", width=1080, height=1080, image_aspect="1:1",
        safe_left=70, safe_right=1010, safe_top=80, safe_bottom=1000,
        headline_size=88, caption_size=80, outline=6, headline_max_chars=60,
        min_total=15.0, max_total=90.0, target_total=(30.0, 60.0),
        max_scene_syllables=32, min_scenes=3, max_scenes=16,
    ),
}


def get_format(name: str) -> FormatSpec:
    try:
        return FORMATS[name]
    except KeyError as exc:
        raise ValueError(f"unknown format {name!r}; use one of {sorted(FORMATS)}") from exc
