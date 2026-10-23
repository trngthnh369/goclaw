"""Soundtrack: narration laid on the frame-exact scene timeline, an optional music
bed ducked under the voice, and two-pass loudness normalisation.

Music comes only from the studio library (<workspace>/music/, tracks the human
added and holds a licence for, or CC0 tracks `media-sync` pulled from the list in
deploy/media_library.json). A generated sine pad was tried and dropped: two
listening passes rated it 3/10 ("static drone, sterile MIDI"), and a bad bed is
worse than none. A library.json next to the tracks gives each one a mood; files
without an entry are still used, for any mood.

Transition sounds (<workspace>/sfx/, same library.json shape) are laid so their
loudest instant lands on each scene cut, where the voice is silent anyway.

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


MOODS = ("upbeat", "calm", "inspiring", "tech")
DEFAULT_SFX_DB = -14.0          # transition sounds (peak -1 dBFS) land about 8 LU under the voice
LIBRARY_FILE = "library.json"


def library_entries(folder: Path) -> dict[str, dict]:
    """library.json of a media folder, by file name ({} when there is none or it is unreadable)."""
    try:
        data = json.loads((folder / LIBRARY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = data.get("items", []) if isinstance(data, dict) else []
    return {e["file"]: e for e in items if isinstance(e, dict) and isinstance(e.get("file"), str)}


def _media_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in MUSIC_EXTS)


def pick_library_track(studio: Studio, seed: str, mood: str | None = None) -> Path | None:
    """A track for this job: one of the asked mood when the library has one, else any."""
    tracks = _media_files(studio.music)
    if not tracks:
        return None
    entries = library_entries(studio.music)
    if mood:
        matching = [t for t in tracks if entries.get(t.name, {}).get("mood") == mood]
        tracks = matching or tracks
    return random.Random(seed).choice(tracks)


def pick_transitions(studio: Studio, seed: str, count: int) -> list[tuple[Path, float]]:
    """`count` transition sounds with the offset of their loudest instant, varied but repeatable."""
    entries = library_entries(studio.sfx)
    sounds = [p for p in _media_files(studio.sfx) if entries.get(p.name, {}).get("kind", "whoosh") == "whoosh"]
    if not sounds or count <= 0:
        return []
    order = sounds[:]
    random.Random(seed).shuffle(order)
    return [(order[i % len(order)], float(entries.get(order[i % len(order)].name, {}).get("peak_s", 0.0)))
            for i in range(count)]


def transition_track(events: list[tuple[float, Path]], out: Path, duration: float) -> None:
    """Each sound starting at its time, mixed onto silence of exactly `duration` seconds."""
    inputs: list[str] = []
    chains: list[str] = []
    for index, (start, sound) in enumerate(events):
        inputs += ["-i", str(sound)]
        delay_ms = max(0, int(round(start * 1000)))
        chains.append(f"[{index}:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo,"
                      f"adelay={delay_ms}:all=1[s{index}]")
    mixed = "".join(f"[s{i}]" for i in range(len(events)))
    graph = ";".join(chains) + f";{mixed}amix=inputs={len(events)}:normalize=0,apad[o]"
    _ffmpeg([*inputs, "-filter_complex", graph, "-map", "[o]", "-t", f"{duration:.6f}",
             "-ac", "2", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(out)])


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


def integrated_loudness(path: Path) -> float:
    """Integrated loudness (LUFS) of a whole file."""
    return float(_measure(["-i", str(path)], "[0:a]loudnorm=print_format=json")["input_i"])


def bed_gain(voice: Path, bed: Path, below_voice_lu: float) -> float:
    """Gain that puts the bed `below_voice_lu` under the voice before ducking.

    Library tracks differ by 10 dB and more, so a fixed gain made one track
    inaudible and the next too loud. A fixed -20 dB left the first real track
    25 LU under the voice (2026-09-25), silent on a phone speaker.
    """
    return round(integrated_loudness(voice) - below_voice_lu - integrated_loudness(bed), 2)


def mix_and_normalize(voice: Path, bed: Path | None, out: Path, *, music_db: float,
                      transitions: Path | None = None, sfx_db: float = DEFAULT_SFX_DB) -> dict:
    """Duck the bed under the voice, then two-pass loudnorm to -14 LUFS. Returns the measurement.

    Transition sounds join the voice before the ducking, so the bed also dips under them.
    """
    inputs = ["-i", str(voice)]
    front = "[0:a]aformat=channel_layouts=stereo[vo]"
    if transitions is not None:
        inputs += ["-i", str(transitions)]
        front = (f"[0:a]aformat=channel_layouts=stereo[vd];[1:a]volume={sfx_db}dB[fx];"
                 f"[vd][fx]amix=inputs=2:normalize=0:duration=first[vo]")
    if bed is not None:
        bed_index = len(inputs) // 2
        inputs += ["-i", str(bed)]
        pre = (f"{front};[vo]asplit=2[v1][v2];"
               f"[{bed_index}:a]volume={music_db}dB[b];"
               f"[b][v2]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[bd];"
               f"[v1][bd]amix=inputs=2:normalize=0:duration=first[mix]")
    else:
        pre = f"{front};[vo]anull[mix]"
    loud = f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA=11"
    measured = _measure(inputs, f"{pre};[mix]{loud}:print_format=json")
    second = (f"{loud}:measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
              f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
              f":offset={measured['target_offset']}:linear=true")
    _ffmpeg([*inputs, "-filter_complex", f"{pre};[mix]{second},aresample={SAMPLE_RATE}[o]", "-map", "[o]",
             "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", str(SAMPLE_RATE), str(out)])
    return measured
