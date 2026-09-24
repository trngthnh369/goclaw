"""Caption timing and ASS generation.

Captions show the script's own text (with its punctuation and casing), timed by
the TTS word boundaries. The two token streams usually match one to one, but the
voice may expand a number or an acronym, so alignment tolerates insertions and
substitutions and interpolates any display token it could not pin down.

Each scene gets its own ASS file with scene-local times: the scene clips are
rendered separately and concatenated without re-encoding, and no caption ever
spans a scene cut.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import fontmetrics
from .formats import HEADLINE_BOX_PAD, FormatSpec
from .textutil import ass_escape, nfc, norm_token
from .themes import Theme, ass_color

SENTENCE_END = (".", "!", "?", "…", ";", ":")
CLAUSE_END = (",",)
MAX_CHUNK_TOKENS = {"short": 4, "square": 5, "long": 8}
MAX_CHUNK_SECONDS = 2.4


@dataclass
class Token:
    text: str
    start: float = -1.0
    end: float = -1.0

    @property
    def timed(self) -> bool:
        return self.start >= 0


@dataclass
class Chunk:
    tokens: list[Token]
    start: float
    end: float

    @property
    def text(self) -> str:
        return " ".join(t.text for t in self.tokens)


# --------------------------------------------------------------------------- alignment

def align(display_text: str, words: list[dict], total: float) -> list[Token]:
    """Give every display token a start/end from the TTS word timings."""
    display = [Token(t) for t in nfc(display_text).split()]
    spoken = [(norm_token(w["text"]), float(w["start"]), float(w["end"])) for w in words]
    spoken = [s for s in spoken if s[0]]
    i = j = 0
    while i < len(display) and j < len(spoken):
        key = norm_token(display[i].text)
        if not key:                      # pure punctuation token: timed later
            i += 1
            continue
        if key == spoken[j][0]:
            display[i].start, display[i].end = spoken[j][1], spoken[j][2]
            i += 1
            j += 1
            continue
        ahead = next((k for k in range(1, 4) if j + k < len(spoken) and spoken[j + k][0] == key), None)
        if ahead is not None:            # the voice inserted words (e.g. expanded a number)
            display[i].start = spoken[j][1]
            display[i].end = spoken[j + ahead][2]
            i += 1
            j += ahead + 1
            continue
        back = next((k for k in range(1, 4) if i + k < len(display)
                     and norm_token(display[i + k].text) == spoken[j][0]), None)
        if back is not None:             # display tokens the voice did not say: interpolate
            i += back
            continue
        display[i].start, display[i].end = spoken[j][1], spoken[j][2]   # substitution
        i += 1
        j += 1
    _interpolate(display, total)
    return display


def _interpolate(tokens: list[Token], total: float) -> None:
    n = len(tokens)
    idx = 0
    while idx < n:
        if tokens[idx].timed:
            idx += 1
            continue
        run_start = idx
        while idx < n and not tokens[idx].timed:
            idx += 1
        left = tokens[run_start - 1].end if run_start > 0 else 0.0
        right = tokens[idx].start if idx < n else max(left, total)
        span = max(0.0, right - left)
        weights = [max(1, len(t.text)) for t in tokens[run_start:idx]]
        acc = left
        for token, weight in zip(tokens[run_start:idx], weights):
            share = span * weight / sum(weights)
            token.start, token.end = acc, acc + share
            acc += share


# --------------------------------------------------------------------------- chunking

def chunk(tokens: list[Token], fmt: FormatSpec) -> list[Chunk]:
    font = fontmetrics.load(fontmetrics.CAPTION_FONT_FILE)
    max_tokens = MAX_CHUNK_TOKENS[fmt.name]
    chunks: list[Chunk] = []
    current: list[Token] = []

    def flush() -> None:
        if current:
            chunks.append(Chunk(list(current), current[0].start, current[-1].end))
            current.clear()

    for token in tokens:
        candidate = " ".join(t.text for t in (*current, token))
        too_wide = font.ass_width(candidate, fmt.caption_size) > fmt.text_width
        too_long = current and (token.end - current[0].start) > MAX_CHUNK_SECONDS
        if current and (too_wide or len(current) >= max_tokens or too_long):
            flush()
        current.append(token)
        if token.text.endswith(SENTENCE_END):
            flush()
        elif token.text.endswith(CLAUSE_END) and len(current) >= 2:
            flush()
    flush()
    # Hold each chunk until the next one starts, so captions never blink off mid-sentence.
    for a, b in zip(chunks, chunks[1:]):
        if 0 <= b.start - a.end < 0.6:
            a.end = b.start
    return chunks


# --------------------------------------------------------------------------- ASS

def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def ass_header(fmt: FormatSpec, theme: Theme) -> str:
    margin_l = fmt.safe_left
    margin_r = fmt.width - fmt.safe_right
    white = ass_color("#FFFFFF")
    black = ass_color("#000000")
    shadow = ass_color("#000000", alpha=0x70)
    box = ass_color("#000000", alpha=0x4C)
    brand_col = ass_color("#FFFFFF", alpha=0x40)
    brand_size = max(34, fmt.caption_size // 2)
    return "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {fmt.width}",
        f"PlayResY: {fmt.height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{fontmetrics.CAPTION_FONT_NAME},{fmt.caption_size},{white},{white},{black},{shadow},"
        f"0,0,0,0,100,100,0,0,1,{fmt.outline},3,2,{margin_l},{margin_r},{fmt.caption_margin_v},1",
        f"Style: Headline,{fontmetrics.HEADLINE_FONT_NAME},{fmt.headline_size},{white},{white},{box},{box},"
        f"0,0,0,0,100,100,0,0,3,{HEADLINE_BOX_PAD},0,8,{margin_l},{margin_r},{fmt.headline_margin_v},1",
        f"Style: Brand,{fontmetrics.CAPTION_FONT_NAME},{brand_size},{brand_col},{brand_col},{shadow},{shadow},"
        f"0,0,0,0,100,100,1,0,1,2,0,7,{margin_l},{margin_r},{max(40, fmt.safe_top - 110)},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])


def _emphasis_flags(tokens: list[Token], phrases: list[str]) -> list[bool]:
    keys = [norm_token(t.text) for t in tokens]
    flags = [False] * len(tokens)
    for phrase in phrases:
        target = [norm_token(p) for p in nfc(phrase).split() if norm_token(p)]
        if not target:
            continue
        for start in range(len(keys) - len(target) + 1):
            if keys[start:start + len(target)] == target:
                for k in range(start, start + len(target)):
                    flags[k] = True
    return flags


def caption_events(chunks: list[Chunk], all_tokens: list[Token], emphasis: list[str],
                   theme: Theme) -> list[str]:
    """One Dialogue per spoken word: the current word pops in the accent colour."""
    flags = dict(zip(map(id, all_tokens), _emphasis_flags(all_tokens, emphasis)))
    accent = ass_color(theme.accent)
    accent2 = ass_color(theme.accent2)
    lines: list[str] = []
    for chunk_ in chunks:
        toks = chunk_.tokens
        for k, current in enumerate(toks):
            start = chunk_.start if k == 0 else current.start
            end = toks[k + 1].start if k + 1 < len(toks) else chunk_.end
            if end - start < 0.02:
                continue
            parts = []
            for tok in toks:
                text = ass_escape(tok.text)
                if tok is current:
                    parts.append("{\\c" + accent + "&\\fscx112\\fscy112\\t(0,90,\\fscx100\\fscy100)}"
                                 + text + "{\\r}")
                elif flags.get(id(tok)):
                    parts.append("{\\c" + accent2 + "&}" + text + "{\\r}")
                else:
                    parts.append(text)
            fade = "{\\fad(70,0)}" if k == 0 else ""
            lines.append(f"Dialogue: 1,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{fade}{' '.join(parts)}")
    return lines


def headline_event(text: str, start: float, end: float, fmt: FormatSpec) -> str | None:
    if not text:
        return None
    font = fontmetrics.load(fontmetrics.HEADLINE_FONT_FILE)
    lines = fontmetrics.wrap_ass(text, font, fmt.headline_size, fmt.headline_width, 2)
    if not lines:   # the validator rejects this case; never silently overflow the frame
        lines = fontmetrics.wrap_ass(text, font, fmt.headline_size, fmt.headline_width, 3) or [text]
    body = "\\N".join(ass_escape(line) for line in lines)
    cx = (fmt.safe_left + fmt.safe_right) // 2
    y = fmt.headline_margin_v
    anim = f"{{\\an8\\pos({cx},{y})\\fad(160,120)\\fscx96\\fscy96\\t(0,160,\\fscx100\\fscy100)}}"
    return f"Dialogue: 2,{_ts(start)},{_ts(end)},Headline,,0,0,0,,{anim}{body}"


def brand_event(handle: str, start: float, end: float) -> str | None:
    if not handle:
        return None
    return f"Dialogue: 0,{_ts(start)},{_ts(end)},Brand,,0,0,0,,{ass_escape(handle)}"


def build_scene_ass(fmt: FormatSpec, theme: Theme, *, tokens: list[Token], emphasis: list[str],
                    on_screen: str, duration: float, offset: float, brand: str = "") -> tuple[str, list[Chunk]]:
    """ASS for one scene. `offset` shifts word times (narration lead-in)."""
    shifted = [Token(t.text, t.start + offset, t.end + offset) for t in tokens]
    chunks = chunk(shifted, fmt)
    if chunks:
        chunks[-1].end = min(duration - 0.02, max(chunks[-1].end, chunks[-1].tokens[-1].end + 0.35))
    events: list[str] = []
    headline = headline_event(on_screen, 0.0, duration, fmt)
    if headline:
        events.append(headline)
    brand_line = brand_event(brand, 0.0, duration)
    if brand_line:
        events.append(brand_line)
    events.extend(caption_events(chunks, shifted, emphasis, theme))
    return ass_header(fmt, theme) + "\n" + "\n".join(events) + "\n", chunks


# --------------------------------------------------------------------------- SRT

def _srt_ts(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(timeline: list[tuple[float, list[Chunk]]]) -> str:
    """timeline = [(scene_start, chunks_with_scene_local_times), ...]"""
    out: list[str] = []
    n = 0
    for scene_start, chunks in timeline:
        for chunk_ in chunks:
            n += 1
            out.append(str(n))
            out.append(f"{_srt_ts(scene_start + chunk_.start)} --> {_srt_ts(scene_start + chunk_.end)}")
            out.append(chunk_.text)
            out.append("")
    return "\n".join(out)
