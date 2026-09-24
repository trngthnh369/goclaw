"""Soundtrack: narration laid on the frame-exact scene timeline, an optional music
bed ducked under the voice, and two-pass loudness normalisation.

Music comes only from the studio library (<workspace>/music/, tracks the human
added and holds a licence for). A generated sine pad was tried and dropped: two
listening passes rated it 3/10 ("static drone, sterile MIDI"), and a bad bed is
worse than none.

No platform publishes a loudness target; -14 LUFS integrated with a -1.5 dBTP
ceiling is the common recommendation for TikTok/Reels/Shorts/YouTube.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from .paths import Studio
from .util import StudioError, run

SAMPLE_RATE = 48000
TARGET_LUFS = -14.0
TARGET_TP = -1.5
MUSIC_EXTS = (".mp3", ".m4a", ".wav", ".ogg", ".flac")


def _ffmpeg(args: list[str], timeout: float = 300) -> None:
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], timeout=timeout)


def scene_track(narration: Path, out: Path, *, lead: float, duration: float) -> None:
    """Narration delayed by `lead` seconds and padded/trimmed to exactly `duration`."""
    delay_ms = int(round(lead * 1000))
    _ffmpeg(["-i", str(narration), "-af",
             f"aresample={SAMPLE_RATE},adelay={delay_ms}:all=1,apad",
             "-ac", "1", "-ar", str(SAMPLE_RATE), "-t", f"{duration:.6f}", "-c:a", "pcm_s16le", str(out)])


def concat_tracks(tracks: list[Path], list_file: Path, out: Path) -> None:
    list_file.write_text("".join(f"file '{t.as_posix()}'\n" for t in tracks), encoding="utf-8")
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c:a", "pcm_s16le", str(out)])


def pick_library_track(studio: Studio, seed: str) -> Path | None:
    if not studio.music.exists():
        return None
    tracks = sorted(p for p in studio.music.iterdir() if p.suffix.lower() in MUSIC_EXTS)
    if not tracks:
        return None
    return random.Random(seed).choice(tracks)


def music_bed(source: Path, out: Path, duration: float) -> None:
    """Loop/trim a library track to the video length with fades."""
    _ffmpeg(["-stream_loop", "-1", "-i", str(source), "-t", f"{duration:.3f}", "-af",
             f"aresample={SAMPLE_RATE},afade=t=in:d=1.0,afade=t=out:st={max(0.0, duration - 2.0):.3f}:d=2",
             "-ac", "2", "-c:a", "pcm_s16le", str(out)], timeout=180)


def _measure(graph_in: list[str], graph: str) -> dict:
    proc = run(["ffmpeg", "-hide_banner", "-nostats", *graph_in, "-filter_complex", graph, "-f", "null", "-"],
               timeout=300)
    text = proc.stderr.decode("utf-8", "replace")
    match = re.search(r"\{\s*\"input_i\".*?\}", text, re.S)
    if not match:
        raise StudioError("loudnorm measurement produced no JSON")
    return json.loads(match.group(0))


def mix_and_normalize(voice: Path, bed: Path | None, out: Path, *, music_db: float) -> dict:
    """Duck the bed under the voice, then two-pass loudnorm to -14 LUFS. Returns the measurement."""
    inputs = ["-i", str(voice)]
    if bed is not None:
        inputs += ["-i", str(bed)]
        pre = (f"[0:a]aformat=channel_layouts=stereo,asplit=2[v1][v2];"
               f"[1:a]volume={music_db}dB[b];"
               f"[b][v2]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[bd];"
               f"[v1][bd]amix=inputs=2:normalize=0:duration=first[mix]")
    else:
        pre = "[0:a]aformat=channel_layouts=stereo[mix]"
    loud = f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA=11"
    measured = _measure(inputs, f"{pre};[mix]{loud}:print_format=json")
    second = (f"{loud}:measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
              f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
              f":offset={measured['target_offset']}:linear=true")
    _ffmpeg([*inputs, "-filter_complex", f"{pre};[mix]{second},aresample={SAMPLE_RATE}[o]", "-map", "[o]",
             "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", str(SAMPLE_RATE), str(out)])
    return measured
