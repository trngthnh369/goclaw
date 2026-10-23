"""Vietnamese-aware text helpers.

Vietnamese writes one syllable per space-separated token, and edge-tts reports one
WordBoundary per token, so "syllables" here is simply "tokens that contain a
letter or digit". That count drives the duration estimate and the per-scene limit.
"""

from __future__ import annotations

import re
import unicodedata

# Emoji and pictographs have no glyph in the bundled fonts; libass would draw tofu.
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F900-\U0001F9FF"
    "\U00002B00-\U00002BFF"
    "\U0000FE0F"
    "\U0000200D"
    "]"
)
_WORD_CHAR_RE = re.compile(r"[\w]", re.UNICODE)
_EDGE_PUNCT = "\"'“”‘’«»()[]{}.,;:!?…-–—/\\*_~`|<>"

# Measured with edge-tts vi-VN voices at rate +0% (research probe + our own probe
# at +8%: 22 syllables in ~7.0 s). Used only for estimates; real timing comes
# from the synthesized audio.
SYLLABLES_PER_SECOND = 4.3


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def tokens(text: str) -> list[str]:
    """Whitespace tokens, keeping attached punctuation (used to rebuild captions)."""
    return [tok for tok in nfc(text).split() if tok]


def syllable_count(text: str) -> int:
    return sum(1 for tok in tokens(text) if _WORD_CHAR_RE.search(tok))


def estimate_seconds(text: str, rate_percent: int = 0) -> float:
    rate = SYLLABLES_PER_SECOND * (1 + rate_percent / 100.0)
    return syllable_count(text) / rate if rate > 0 else 0.0


def norm_token(token: str) -> str:
    """Comparison key for aligning TTS words with script tokens."""
    token = nfc(token).strip(_EDGE_PUNCT).lower()
    return token


def has_emoji(text: str) -> bool:
    return bool(_EMOJI_RE.search(text))


def fold_ascii(text: str) -> str:
    """Vietnamese -> plain ASCII (đ -> d, accents dropped)."""
    text = nfc(text).replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def slugify(text: str, max_len: int = 24) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", fold_ascii(text).lower()).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0] or slug[:max_len]
    return slug or "video"


def ass_escape(text: str) -> str:
    """Escape text for an ASS Dialogue line: braces start override blocks."""
    return (nfc(text).replace("\\", "\\\\").replace("{", "(").replace("}", ")")
            .replace("\n", " "))


def wrap_lines(text: str, max_chars: int, max_lines: int) -> list[str] | None:
    """Greedy wrap on spaces; None when the text cannot fit max_lines lines."""
    words = tokens(text)
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        return None
    return lines


def balanced_wrap(text: str, max_chars: int, max_lines: int) -> list[str] | None:
    """Wrap into the fewest lines, then even out line lengths (headline look)."""
    lines = wrap_lines(text, max_chars, max_lines)
    if lines is None or len(lines) < 2:
        return lines
    words = tokens(text)
    target = len(" ".join(words)) / len(lines)
    best = lines
    best_score = max(len(line) for line in lines)
    # Try every split point for the two-line case; it is the common one.
    if len(lines) == 2:
        for cut in range(1, len(words)):
            first, second = " ".join(words[:cut]), " ".join(words[cut:])
            if len(first) > max_chars or len(second) > max_chars:
                continue
            score = max(len(first), len(second))
            if score < best_score or (score == best_score and abs(len(first) - target) < abs(len(best[0]) - target)):
                best, best_score = [first, second], score
    return best
