"""Measure text width from a TrueType font with the standard library only.

Pillow is not in the container and agents cannot pip install, but wrapping
headlines and captions by character count is visibly wrong: "Mỗi ngày" and
"WWWWWWWW" have the same length and very different widths. Reading advance
widths straight from the font's cmap + hmtx tables gives pixel-accurate line
breaks for libass, which draws with the same font.
"""

from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path

from .paths import FONTS_DIR
from .textutil import nfc, tokens

HEADLINE_FONT_FILE = "BeVietnamPro-Black.ttf"
CAPTION_FONT_FILE = "BeVietnamPro-ExtraBold.ttf"
HEADLINE_FONT_NAME = "Be Vietnam Pro Black"
CAPTION_FONT_NAME = "Be Vietnam Pro ExtraBold"


class TrueTypeMetrics:
    def __init__(self, path: Path) -> None:
        data = Path(path).read_bytes()
        num_tables = struct.unpack_from(">H", data, 4)[0]
        tables: dict[str, tuple[int, int]] = {}
        for i in range(num_tables):
            tag, _checksum, offset, length = struct.unpack_from(">4sIII", data, 12 + 16 * i)
            tables[tag.decode("latin-1")] = (offset, length)
        head = tables["head"][0]
        self.units_per_em = struct.unpack_from(">H", data, head + 18)[0]
        hhea = tables["hhea"][0]
        self.ascender, self.descender = struct.unpack_from(">hh", data, hhea + 4)
        # libass (VSFilter-compatible) treats an ASS Fontsize as usWinAscent +
        # usWinDescent, not as the em size - so Fontsize 96 is a smaller em.
        os2 = tables["OS/2"][0]
        win_ascent, win_descent = struct.unpack_from(">HH", data, os2 + 74)
        self.win_height = (win_ascent + win_descent) or (self.ascender - self.descender)
        num_hmetrics = struct.unpack_from(">H", data, hhea + 34)[0]
        hmtx = tables["hmtx"][0]
        self._advances = [struct.unpack_from(">H", data, hmtx + 4 * i)[0] for i in range(num_hmetrics)]
        self._cmap = self._read_cmap(data, tables["cmap"][0])

    @staticmethod
    def _read_cmap(data: bytes, base: int) -> dict[int, int]:
        count = struct.unpack_from(">H", data, base + 2)[0]
        records = {}
        for i in range(count):
            platform, encoding, offset = struct.unpack_from(">HHI", data, base + 4 + 8 * i)
            records[(platform, encoding)] = base + offset
        for key in ((3, 10), (0, 4), (3, 1), (0, 3)):
            if key in records:
                sub = records[key]
                fmt = struct.unpack_from(">H", data, sub)[0]
                if fmt == 12:
                    return TrueTypeMetrics._cmap12(data, sub)
                if fmt == 4:
                    return TrueTypeMetrics._cmap4(data, sub)
        raise ValueError("font has no usable Unicode cmap")

    @staticmethod
    def _cmap12(data: bytes, sub: int) -> dict[int, int]:
        groups = struct.unpack_from(">I", data, sub + 12)[0]
        mapping: dict[int, int] = {}
        for i in range(groups):
            start, end, glyph = struct.unpack_from(">III", data, sub + 16 + 12 * i)
            if start > 0x2FFFF:
                continue
            for code in range(start, min(end, 0x2FFFF) + 1):
                mapping[code] = glyph + (code - start)
        return mapping

    @staticmethod
    def _cmap4(data: bytes, sub: int) -> dict[int, int]:
        seg_count = struct.unpack_from(">H", data, sub + 6)[0] // 2
        ends = struct.unpack_from(f">{seg_count}H", data, sub + 14)
        starts = struct.unpack_from(f">{seg_count}H", data, sub + 16 + 2 * seg_count)
        deltas = struct.unpack_from(f">{seg_count}h", data, sub + 16 + 4 * seg_count)
        range_base = sub + 16 + 6 * seg_count
        offsets = struct.unpack_from(f">{seg_count}H", data, range_base)
        mapping: dict[int, int] = {}
        for i in range(seg_count):
            for code in range(starts[i], ends[i] + 1):
                if code == 0xFFFF:
                    continue
                if offsets[i] == 0:
                    glyph = (code + deltas[i]) & 0xFFFF
                else:
                    addr = range_base + 2 * i + offsets[i] + 2 * (code - starts[i])
                    glyph = struct.unpack_from(">H", data, addr)[0]
                    if glyph:
                        glyph = (glyph + deltas[i]) & 0xFFFF
                mapping[code] = glyph
        return mapping

    def has_glyph(self, char: str) -> bool:
        return self._cmap.get(ord(char), 0) != 0

    def advance(self, char: str) -> int:
        glyph = self._cmap.get(ord(char), 0)
        if glyph < len(self._advances):
            return self._advances[glyph]
        return self._advances[-1]

    def width(self, text: str, size_px: float, spacing_px: float = 0.0) -> float:
        """Advance width of text at an em size of size_px (CSS-style font size)."""
        text = nfc(text)
        units = sum(self.advance(ch) for ch in text)
        return units * size_px / self.units_per_em + spacing_px * max(0, len(text) - 1)

    def ass_em(self, ass_fontsize: float) -> float:
        """Em size in pixels that libass uses for a given ASS Fontsize."""
        return ass_fontsize * self.units_per_em / self.win_height

    def ass_width(self, text: str, ass_fontsize: float, spacing_px: float = 0.0) -> float:
        return self.width(text, self.ass_em(ass_fontsize), spacing_px)

    def missing_glyphs(self, text: str) -> list[str]:
        return sorted({ch for ch in nfc(text) if not ch.isspace() and not self.has_glyph(ch)})


@lru_cache(maxsize=8)
def load(font_file: str) -> TrueTypeMetrics:
    return TrueTypeMetrics(FONTS_DIR / font_file)


def wrap_ass(text: str, metrics: TrueTypeMetrics, ass_fontsize: float, max_width: float,
             max_lines: int, spacing_px: float = 0.0) -> list[str] | None:
    """Greedy pixel wrap at an ASS Fontsize, then balance two-line results.

    Returns None when the text cannot fit in max_lines lines.
    """
    def width(value: str) -> float:
        return metrics.ass_width(value, ass_fontsize, spacing_px)

    words = tokens(text)
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if width(candidate) <= max_width or not current:
            if not current and width(word) > max_width:
                return None  # a single word wider than the line
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    if len(lines) > max_lines:
        return None
    if len(lines) == 2:
        best = lines
        best_score = max(width(line) for line in lines)
        for cut in range(1, len(words)):
            first, second = " ".join(words[:cut]), " ".join(words[cut:])
            w1, w2 = width(first), width(second)
            if w1 > max_width or w2 > max_width:
                continue
            if max(w1, w2) < best_score:
                best, best_score = [first, second], max(w1, w2)
        lines = best
    return lines
