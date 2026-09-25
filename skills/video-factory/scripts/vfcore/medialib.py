"""The studio's CC0 media library: background tracks and transition sounds.

deploy/media_library.json lists every file with its source, licence and the
sha256 of the original download. `studio.py media-sync` fetches what is missing,
refuses a file whose bytes changed at the source, prepares transition sounds
(silence trimmed, peak at -1 dBFS) and writes library.json next to the files.
Files a person drops into music/ or sfx/ by hand are left alone and still used.
"""

from __future__ import annotations

import array
import hashlib
import http.client
import json
import math
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .audio import LIBRARY_FILE, MOODS, library_entries
from .paths import Studio
from .util import StudioError, run, write_json

MANIFEST = Path(__file__).resolve().parents[2] / "deploy" / "media_library.json"
USER_AGENT = "goclaw-video-factory/1.0 (media-sync)"
SFX_RATE = 48000
SFX_PEAK_DB = -1.0
MAX_DOWNLOAD = 32 * 1024 * 1024     # the largest listed track is ~5 MB
FETCH_ERRORS = (OSError, http.client.HTTPException, StudioError)


def load_manifest(path: Path = MANIFEST) -> list[dict]:
    """The committed list of CC0 files, validated before anything is fetched."""
    try:
        items = json.loads(path.read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError, AttributeError) as exc:
        raise StudioError(f"media_library.json is unreadable: {exc}") from None
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise StudioError("media_library.json: items must be a list of objects")
    for item in items:
        name = str(item.get("file", ""))
        if (item.get("dir") not in ("music", "sfx") or name in ("", ".", "..")
                or Path(name).name != name or "\\" in name):
            raise StudioError(f"media_library.json: bad entry {name!r}")
        if not str(item.get("url", "")).startswith("https://") or len(str(item.get("sha256", ""))) != 64:
            raise StudioError(f"media_library.json: {name} needs an https url and a sha256")
        if item["dir"] == "music" and item.get("mood") not in MOODS:
            raise StudioError(f"media_library.json: {name} needs a mood ({', '.join(MOODS)})")
    return items


def _download(url: str, dest: Path) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise StudioError(f"{url} is larger than {MAX_DOWNLOAD // 2**20} MB")
    dest.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _max_volume_db(path: Path) -> float:
    proc = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
               timeout=60)
    for line in proc.stderr.decode("utf-8", "replace").splitlines():
        if "max_volume:" in line:
            try:
                level = float(line.split("max_volume:")[1].split("dB")[0])
            except ValueError:
                raise StudioError(f"volumedetect printed an unreadable level for {path.name}") from None
            if not math.isfinite(level):
                raise StudioError(f"{path.name} is silent")
            return level
    raise StudioError(f"volumedetect found no level in {path.name}")


def peak_offset(path: Path) -> float:
    """Seconds from the start of a sound to its loudest sample."""
    proc = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(SFX_RATE),
                "-f", "s16le", "-"], timeout=60)
    samples = array.array("h", proc.stdout[: len(proc.stdout) // 2 * 2])
    if not samples:
        return 0.0
    loudest = max(range(len(samples)), key=lambda i: abs(samples[i]))
    return round(loudest / SFX_RATE, 3)


def prepare_sfx(source: Path, out: Path) -> None:
    """Trim leading silence and bring the peak to -1 dBFS, so every sound mixes at one level.

    Both passes write next to `source` (a temporary folder): a half-made file in sfx/
    would be picked as a transition sound.
    """
    trimmed = source.with_name("trimmed.wav")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-af",
         "silenceremove=start_periods=1:start_threshold=-50dB", "-ac", "2", "-ar", str(SFX_RATE), str(trimmed)],
        timeout=60)
    gain = SFX_PEAK_DB - _max_volume_db(trimmed)
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(trimmed), "-af", f"volume={gain:.2f}dB",
         "-c:a", "pcm_s16le", str(out)], timeout=60)


@dataclass
class SyncResult:
    fetched: int = 0
    kept: int = 0
    failed: list[str] = field(default_factory=list)


def _install(item: dict, folder_name: str, target: Path) -> dict:
    """Fetch, verify and prepare one listed file; returns its library entry."""
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / ("raw" + Path(item["url"]).suffix)
        got = _download(item["url"], raw)
        if got != item["sha256"]:
            raise StudioError(f"the source changed (sha256 {got[:12]}), not installed")
        entry = {k: item[k] for k in item if k not in ("dir", "url")} | {"source_url": item["url"]}
        if folder_name == "sfx":
            sound = Path(tmp) / "sound.wav"
            prepare_sfx(raw, sound)
            entry["peak_s"] = peak_offset(sound)
            target.write_bytes(sound.read_bytes())
        else:
            target.write_bytes(raw.read_bytes())
    return entry


def sync(studio: Studio, items: list[dict]) -> SyncResult:
    """Fetch and prepare what is missing. One bad file never stops the others; raises at the end."""
    done = SyncResult()
    for folder_name in ("music", "sfx"):
        folder = studio.root / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        entries = library_entries(folder)
        managed = {i["file"] for i in items if i["dir"] == folder_name}
        kept = {name: e for name, e in entries.items() if name not in managed}   # a person's own files
        for item in (i for i in items if i["dir"] == folder_name):
            target = folder / item["file"]
            known = entries.get(item["file"], {})
            if target.exists() and known.get("sha256") == item["sha256"]:
                kept[item["file"]] = known
                done.kept += 1
                continue
            try:
                kept[item["file"]] = _install(item, folder_name, target)
                done.fetched += 1
            except FETCH_ERRORS as exc:
                done.failed.append(f"{item['file']}: {exc}")
                if target.exists() and known:
                    kept[item["file"]] = known          # the file already there still works
        write_json(folder / LIBRARY_FILE, {"schema": "vf.media.v1", "items": sorted(kept.values(),
                                                                                   key=lambda e: e["file"])})
    if done.failed:
        raise StudioError("media-sync: " + "; ".join(done.failed))
    return done
