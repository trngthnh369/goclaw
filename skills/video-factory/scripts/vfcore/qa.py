"""Deterministic checks on the rendered master, plus the frames a reviewer looks at.

Hard checks decide whether the render stage is done at all; soft findings are
passed to the reviewer, who judges them with the frames in front of them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .formats import FPS, FormatSpec
from .media import extract_frame
from .paths import FONTS_DIR, JobPaths
from .util import run

LOUDNESS_TOLERANCE = 1.5     # LU around the target
SILENCE_MAX = 1.6            # seconds of continuous silence tolerated
CONTACT_WIDTH_PORTRAIT = 1080  # contact sheet width in px; tiles share it
CONTACT_WIDTH_WIDE = 1440
EMPTY_CELL = "0x3A3A3A"      # grey, so an empty grid cell does not read as a black frame


def probe(path: Path) -> dict:
    proc = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
               timeout=60)
    return json.loads(proc.stdout.decode("utf-8"))


def _fps(stream: dict) -> float:
    num, _, den = (stream.get("r_frame_rate") or "0/1").partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _filter_log(path: Path, args: list[str]) -> str:
    proc = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), *args, "-f", "null", "-"], timeout=300)
    return proc.stderr.decode("utf-8", "replace")


def loudness(path: Path) -> dict:
    text = _filter_log(path, ["-vn", "-af", "loudnorm=I=-14:TP=-1.5:print_format=json"])
    match = re.search(r"\{\s*\"input_i\".*?\}", text, re.S)
    data = json.loads(match.group(0)) if match else {}
    return {"lufs": float(data.get("input_i", "nan")), "true_peak": float(data.get("input_tp", "nan"))}


def silences(path: Path) -> list[tuple[float, float]]:
    text = _filter_log(path, ["-vn", "-af", "silencedetect=noise=-42dB:d=1.0"])
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", text)]
    return [(s, e) for s, e in zip(starts, ends)]


def black_segments(path: Path) -> list[tuple[float, float]]:
    text = _filter_log(path, ["-an", "-vf", "scale=240:-2,blackdetect=d=0.3:pix_th=0.08"])
    return [(float(a), float(b)) for a, b in
            re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", text)]


def sheet_columns(n: int, portrait: bool) -> int:
    """Columns for n tiles: no more rows than columns (a vision model shrinks a tall
    sheet, and the tiles with it), then the fewest empty cells, then larger tiles.

    A reviewer model once reported the black empty cells of a 4-column sheet as a
    defect of the video itself (2026-09-24), so the grid should come out full.
    """
    options = (3, 4) if portrait else (2, 3)
    return min(options, key=lambda cols: (-(-n // cols) > cols, (-n) % cols, cols))


def contact_sheet(frames: list[tuple[str, Path, float]], out: Path, fmt: FormatSpec) -> None:
    """Grid of scene frames with the scene id and start time burned in."""
    portrait = fmt.width < fmt.height
    cols = sheet_columns(len(frames), portrait)
    thumb_w = (CONTACT_WIDTH_PORTRAIT if portrait else CONTACT_WIDTH_WIDE) // cols // 2 * 2
    fontsize = max(20, thumb_w // 11)
    inputs: list[str] = []
    chains: list[str] = []
    font = (FONTS_DIR / "BeVietnamPro-Bold.ttf").as_posix().replace(":", "\\:")
    for i, (sid, path, start) in enumerate(frames):
        inputs += ["-i", str(path)]
        label = f"{sid}  {start:0.1f}s"
        chains.append(f"[{i}:v]scale={thumb_w}:-2,drawtext=fontfile='{font}':text='{label}':"
                      f"x=10:y=10:fontsize={fontsize}:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=6[t{i}]")
    n = len(frames)
    rows = (n + cols - 1) // cols
    thumb_h = int(thumb_w * fmt.height / fmt.width) // 2 * 2
    layout = "|".join(f"{(i % cols) * thumb_w}_{(i // cols) * thumb_h}" for i in range(n))
    graph = ";".join(chains) + ";" + "".join(f"[t{i}]" for i in range(n))
    if n == 1:
        graph += "null[g]"
    else:
        graph += f"xstack=inputs={n}:layout={layout}:fill={EMPTY_CELL}[g]"
    graph += f";[g]pad={cols * thumb_w}:{rows * thumb_h}:0:0:color={EMPTY_CELL}[o]"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", graph,
         "-map", "[o]", "-frames:v", "1", "-q:v", "3", str(out)], timeout=120)


def run_qa(paths: JobPaths, fmt: FormatSpec, timeline: list[dict], expected: float,
           preview_bytes: int, preview_limit: int) -> dict:
    info = probe(paths.master)
    video = next((s for s in info["streams"] if s.get("codec_type") == "video"), {})
    audio = next((s for s in info["streams"] if s.get("codec_type") == "audio"), {})
    duration = float(info.get("format", {}).get("duration", 0))
    loud = loudness(paths.master)
    silent = [s for s in silences(paths.master) if s[1] - s[0] > SILENCE_MAX]
    black = black_segments(paths.preview if paths.preview.exists() else paths.master)

    hard: list[str] = []
    soft: list[str] = []
    if (video.get("width"), video.get("height")) != (fmt.width, fmt.height):
        hard.append(f"resolution {video.get('width')}x{video.get('height')} != {fmt.size}")
    if abs(_fps(video) - FPS) > 0.01:
        hard.append(f"frame rate {_fps(video):.2f} != {FPS}")
    if video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p":
        hard.append(f"video must be h264/yuv420p, got {video.get('codec_name')}/{video.get('pix_fmt')}")
    if audio.get("codec_name") != "aac" or int(audio.get("sample_rate", 0)) != 48000:
        hard.append("audio must be AAC 48 kHz")
    if abs(duration - expected) > 0.25:
        hard.append(f"duration {duration:.2f}s differs from the timeline {expected:.2f}s")
    if not fmt.min_total <= duration <= fmt.max_total:
        hard.append(f"duration {duration:.1f}s outside {fmt.min_total:.0f}-{fmt.max_total:.0f}s")
    if loud["lufs"] != loud["lufs"] or abs(loud["lufs"] + 14) > LOUDNESS_TOLERANCE:
        hard.append(f"loudness {loud['lufs']} LUFS, target -14")
    if loud["true_peak"] == loud["true_peak"] and loud["true_peak"] > -0.9:
        soft.append(f"true peak {loud['true_peak']} dBTP is hot")
    for start, end in silent:
        soft.append(f"silence {start:.1f}-{end:.1f}s")
    for start, end in black:
        soft.append(f"black frames {start:.1f}-{end:.1f}s")
    if preview_bytes > preview_limit:
        soft.append(f"preview is {preview_bytes / 1e6:.1f} MB, over the chat limit")
    if not fmt.target_total[0] <= duration <= fmt.target_total[1]:
        soft.append(f"duration {duration:.0f}s is outside the {fmt.target_total[0]:.0f}-{fmt.target_total[1]:.0f}s sweet spot")

    frames: list[tuple[str, Path, float]] = []
    paths.frames.mkdir(parents=True, exist_ok=True)
    for item in timeline:
        at = item["start"] + item["duration"] * 0.62
        frame = paths.frames / f"{item['id']}.jpg"
        extract_frame(paths.master, at, frame)
        frames.append((item["id"], frame, item["start"]))
    contact_sheet(frames, paths.contact, fmt)
    first = timeline[0]
    extract_frame(paths.master, min(first["start"] + 1.0, first["start"] + first["duration"] * 0.6), paths.cover)

    return {
        "ok": not hard,
        "hard": hard,
        "soft": soft,
        "duration": round(duration, 3),
        "resolution": f"{video.get('width')}x{video.get('height')}",
        "fps": round(_fps(video), 3),
        "loudness_lufs": loud["lufs"],
        "true_peak_dbtp": loud["true_peak"],
        "master_bytes": paths.master.stat().st_size,
        "preview_bytes": preview_bytes,
        "frames": {sid: str(path) for sid, path, _ in frames},
        "contact_sheet": str(paths.contact),
        "cover": str(paths.cover),
    }
