"""Narration with edge-tts, keeping per-word timings for caption sync.

edge-tts 7.2.x defaults to SentenceBoundary; WordBoundary has to be requested.
For Vietnamese it reports one event per syllable, which is exactly one caption
token. The service is an unofficial consumer endpoint that fails intermittently
(NoAudioReceived, 503 under parallel load), so calls are sequential, retried with
backoff, and cached by a hash of what was spoken.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .textutil import nfc, syllable_count
from .util import StudioError, run, sha256_text

TTS_VERSION = "edge-7.2.8-wb3-silencetrim"
RETRY_DELAYS = (1.5, 3.0, 4.5, 6.0)
SILENCE_DB = -50

# The vi-VN voices read every word with Vietnamese spelling rules: raw, "AI" came
# out as "ai" (who), "ChatGPT" as "chát", "web" as "ốp", "Claude" as "cờ lốp"
# (listening test 2026-09-24). Each respelling below was synthesised with
# vi-VN-HoaiMyNeural and judged recognisable; "Claude", "chatbot", "prompt" and
# "smartphone" were judged wrong in every spelling tried, so they are left to a
# per-scene tts_text. All-caps keys match case-sensitively ("AI" must never touch
# the Vietnamese word "ai"); every other key matches in any case.
VI_LEXICON: dict[str, str] = {
    "AI": "ây ai", "GPT": "gi pi ti", "ChatGPT": "chát gi pi ti", "OpenAI": "ô pần ây ai",
    "Gemini": "giê mi nai", "Google": "gu gồ", "Microsoft": "mai crô sốp", "Anthropic": "en thờ rô pích",
    "YouTube": "diu túp", "Facebook": "phây búc", "TikTok": "tích tóc", "Zalo": "da lô",
    "iPhone": "ai phôn", "Excel": "ếch xeo", "Wi-Fi": "oai phai",
    "web": "oép", "app": "áp", "email": "i meo", "online": "on lai", "laptop": "láp tóp",
    "deadline": "đét lai", "robot": "rô bốt", "internet": "in tơ nét",
}

# Loanwords the voice already reads the Vietnamese way when written raw (same
# listening test): they need no respelling. "team", "follow" and "pizza" were
# misheard, so they are deliberately absent.
VI_KNOWN_LOANWORDS = frozenset({
    "video", "clip", "like", "share", "comment", "link", "livestream", "stress", "check", "fan",
    "game", "menu", "vitamin", "radio", "taxi",
})

_TONE_MARKS = {"̀", "́", "̃", "̉", "̣"}
_VI_SYLLABLE_RE = re.compile(
    r"^(?:ngh|ng|gh|gi|kh|nh|ph|qu|th|tr|ch|[bcdđghklmnpqrstvx])?"
    r"[aăâeêioôơuưy]{1,3}(?:ch|nh|ng|[cmnpt])?$")
_TOKEN_EDGE = "\"'“”‘’«»()[]{}.,;:!?…–—/\\*_~`|<>"
_LETTER_DIGIT_HYPHEN_RE = re.compile(r"(?<=[^\W\d_])-(?=\d)")


def effective_lexicon(voice: str, studio_lexicon: dict[str, str] | None) -> dict[str, str]:
    """Built-in respellings for Vietnamese voices, overridden by the studio's own."""
    base = dict(VI_LEXICON) if (voice or "").startswith("vi-") else {}
    base.update(studio_lexicon or {})
    return base


@dataclass
class Narration:
    audio: Path
    words: list[dict]        # [{"text", "start", "end"}] seconds, relative to the clip
    duration: float
    cached: bool


def spoken_text(narration: str, override: str | None, lexicon: dict[str, str]) -> str:
    """What the voice actually says: tts_text if given, else the narration, with lexicon swaps.

    The lexicon applies to tts_text too, so a writer may keep "AI" in it. Longer
    keys go first so "ChatGPT" is replaced whole before "GPT" could match inside it.
    """
    text = nfc(override or narration)
    text = _LETTER_DIGIT_HYPHEN_RE.sub(" ", text)   # "GPT-5" is read "gi pi ti 5", never "trừ 5"
    for written in sorted(lexicon, key=len, reverse=True):
        text = _replace_word(text, written, nfc(lexicon[written]))
    return text


def _replace_word(text: str, written: str, spoken: str) -> str:
    flags = 0 if written.isupper() else re.IGNORECASE
    pattern = re.compile(rf"(?<![\w-]){re.escape(nfc(written))}(?![\w])", flags)
    return pattern.sub(spoken, text)


def _strip_tones(token: str) -> str:
    decomposed = unicodedata.normalize("NFD", token.lower())
    return unicodedata.normalize("NFC", "".join(ch for ch in decomposed if ch not in _TONE_MARKS))


def foreign_tokens(spoken: str) -> list[str]:
    """Words in `spoken` that a Vietnamese voice would read with Vietnamese spelling rules.

    Vietnamese writes one syllable per token, so a token that is not a single valid
    syllable (initial + vowel nucleus + final, tones ignored) is foreign: "Claude",
    "Google", "web". Acronyms ("AI", "CEO") and mixed-case names ("iPhone") are
    foreign even when they happen to spell a syllable. Numbers are left alone.
    """
    found: list[str] = []
    for raw in nfc(spoken).split():
        token = raw.strip(_TOKEN_EDGE)
        if not token or not any(ch.isalpha() for ch in token):
            continue
        for part in (p for p in re.split(r"[-/]", token) if p):
            letters = [ch for ch in part if ch.isalpha()]
            if not letters or (part.lower() in VI_KNOWN_LOANWORDS and not part[1:].isupper()):
                continue
            all_upper = all(ch.isupper() for ch in letters)
            # "KHÔNG" is a Vietnamese word in caps; "AI" and "CEO" are acronyms.
            is_acronym = part.isascii() and len(letters) >= 2 and all_upper
            mixed_case = not all_upper and any(ch.isupper() for ch in part[1:])
            has_digit_and_letter = any(ch.isdigit() for ch in part)
            if is_acronym or mixed_case or has_digit_and_letter or not _VI_SYLLABLE_RE.match(_strip_tones(part)):
                if token not in found:
                    found.append(token)
                break
    return found


async def _stream(text: str, voice: str, rate: int) -> tuple[bytes, list[dict]]:
    import edge_tts  # imported lazily: tests of other modules must not need it

    comm = edge_tts.Communicate(
        text, voice, rate=f"{rate:+d}%", boundary="WordBoundary",
        connect_timeout=15, receive_timeout=60,
    )
    audio = bytearray()
    words: list[dict] = []
    async for chunk in comm.stream():
        kind = chunk.get("type")
        if kind == "audio":
            audio.extend(chunk["data"])
        elif kind == "WordBoundary":
            start = chunk["offset"] / 1e7
            words.append({"text": chunk["text"], "start": round(start, 3),
                          "end": round(start + chunk["duration"] / 1e7, 3)})
    return bytes(audio), words


def probe_duration(path: Path) -> float:
    proc = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(path)], timeout=30)
    try:
        return float(proc.stdout.decode().strip())
    except ValueError as exc:
        raise StudioError(f"cannot read duration of {path.name}") from exc


def _speech_bounds(raw: Path, duration: float) -> tuple[float, float]:
    """Where the voice actually starts and stops, from the audio itself."""
    proc = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(raw), "-af",
                f"silencedetect=noise={SILENCE_DB}dB:d=0.05", "-f", "null", "-"], timeout=60)
    text = proc.stderr.decode("utf-8", "replace")
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", text)]
    lead_end = 0.0
    if starts and starts[0] <= 0.02 and ends:
        lead_end = ends[0]
    trail_start = duration
    if starts and starts[-1] > lead_end and (len(ends) < len(starts) or ends[-1] >= duration - 0.05):
        trail_start = starts[-1]
    return lead_end, trail_start


def trim_to_speech(raw: Path, out_wav: Path, words: list[dict], duration: float) -> tuple[list[dict], float]:
    """Cut edge-tts's own padding (~0.13 s before, ~0.8 s after the speech).

    Left in, it stacks with the scene gaps into 1.3 s pauses between sentences -
    the "long silences, disjointed pacing" a listening test flagged. The cut points
    come from silencedetect, NOT from word timings: a Vietnamese syllable with a
    rising or falling tone rings on past its WordBoundary duration, and trimming by
    timings clipped "phút" and "nhé" in the second listening test. Output is WAV:
    decoding the MP3 once here keeps encoder priming from shifting word timings.
    """
    lead_end, trail_start = _speech_bounds(raw, duration)
    start = max(0.0, lead_end - 0.03)
    if words:
        start = min(start, max(0.0, words[0]["start"] - 0.03))
    end = min(duration, trail_start + 0.12)
    if words:   # never inside the last word, whatever silencedetect thought
        end = max(end, min(duration, words[-1]["end"] + 0.30))
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw), "-af",
         f"atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,afade=t=out:st={max(0.0, end - start - 0.04):.3f}:d=0.04",
         "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)], timeout=60)
    shifted = [{"text": w["text"], "start": round(w["start"] - start, 3), "end": round(w["end"] - start, 3)}
               for w in words]
    return shifted, round(end - start, 3)


def synthesize(text: str, voice: str, rate: int, out_audio: Path, cache_dir: Path) -> Narration:
    """Synthesize `text` into out_audio (48 kHz mono WAV, speech only) with word timings."""
    key = sha256_text(f"{TTS_VERSION}|{voice}|{rate}|{nfc(text)}")[:24]
    cached_audio = cache_dir / f"{key}.wav"
    cached_words = cache_dir / f"{key}.json"
    out_audio.parent.mkdir(parents=True, exist_ok=True)
    if cached_audio.exists() and cached_words.exists():
        shutil.copyfile(cached_audio, out_audio)
        meta = json.loads(cached_words.read_text(encoding="utf-8"))
        return Narration(out_audio, meta["words"], meta["duration"], cached=True)

    last_error: Exception | None = None
    for attempt, delay in enumerate((0.0, *RETRY_DELAYS)):
        if delay:
            time.sleep(delay)
        try:
            audio, words = asyncio.run(_stream(text, voice, rate))
            if len(audio) < 2000:
                raise StudioError("edge-tts returned no audio")
            break
        except Exception as exc:  # network, NoAudioReceived, 503 - all retryable
            last_error = exc
    else:
        raise StudioError(f"edge-tts failed after {len(RETRY_DELAYS) + 1} attempts: {last_error}")

    raw = out_audio.with_suffix(".raw.mp3")
    raw.write_bytes(audio)
    raw_duration = probe_duration(raw)
    if len(words) < max(1, syllable_count(text) // 2):
        # The service dropped boundaries: spread syllables evenly over the audio.
        words = even_timings(text, raw_duration)
    words, duration = trim_to_speech(raw, out_audio, words, raw_duration)
    raw.unlink(missing_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out_audio, cached_audio)
    cached_words.write_text(json.dumps({"words": words, "duration": duration}, ensure_ascii=False),
                            encoding="utf-8")
    return Narration(out_audio, words, duration, cached=False)


def even_timings(text: str, duration: float) -> list[dict]:
    toks = [t for t in nfc(text).split() if any(ch.isalnum() for ch in t)]
    if not toks:
        return []
    step = duration / len(toks)
    return [{"text": t, "start": round(i * step, 3), "end": round((i + 1) * step, 3)}
            for i, t in enumerate(toks)]
