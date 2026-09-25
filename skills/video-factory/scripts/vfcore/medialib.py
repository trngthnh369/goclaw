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
import json
import tempfile
import urllib.request
from pathlib import Path

from .audio import LIBRARY_FILE, MOODS, library_entries
from .paths import Studio
from .util import StudioError, run, write_json

MANIFEST = Path(__file__).resolve().parents[2] / "deploy" / "media_library.json"
USER_AGENT = "goclaw-video-factory/1.0 (media-sync)"
SFX_RATE = 48000
SFX_PEAK_DB = -1.0


def load_manifest(path: Path = MANIFEST) -> list[dict]:
    items = json.loads(path.read_text(encoding="utf-8")).get("items", [])
    for item in items:
        if item.get("dir") not in ("music", "sfx") or "/" in item.get("file", "/"):
            raise StudioError(f"media_library.json: bad entry {item.get('file')!r}")
        if item["dir"] == "music" and item.get("mood") not in MOODS:
            raise StudioError(f"media_library.json: {item['file']} needs a mood ({', '.join(MOODS)})")
    return items


def _download(url: str, dest: Path) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    dest.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _max_volume_db(path: Path) -> float:
    proc = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
               timeout=60)
    for line in proc.stderr.decode("utf-8", "replace").splitlines():
        if "max_volume:" in line:
            return float(line.split("max_volume:")[1].split("dB")[0])
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
    """Trim leading silence and bring the peak to -1 dBFS, so every sound mixes at one level."""
    trimmed = out.with_suffix(".trim.wav")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-af",
         "silenceremove=start_periods=1:start_threshold=-50dB", "-ac", "2", "-ar", str(SFX_RATE), str(trimmed)],
        timeout=60)
    gain = SFX_PEAK_DB - _max_volume_db(trimmed)
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(trimmed), "-af", f"volume={gain:.2f}dB",
         "-c:a", "pcm_s16le", str(out)], timeout=60)
    trimmed.unlink(missing_ok=True)


def sync(studio: Studio, items: list[dict]) -> dict:
    """Fetch and prepare what is missing. Returns counts; raises when any file failed."""
    done = {"fetched": 0, "kept": 0, "failed": []}
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
                done["kept"] += 1
                continue
            with tempfile.TemporaryDirectory() as tmp:
                raw = Path(tmp) / ("raw" + Path(item["url"]).suffix)
                try:
                    got = _download(item["url"], raw)
                except OSError as exc:
                    done["failed"].append(f"{item['file']}: download failed: {exc}")
                    continue
                if got != item["sha256"]:
                    done["failed"].append(f"{item['file']}: the source changed (sha256 {got[:12]}), not installed")
                    continue
                entry = {k: item[k] for k in item if k not in ("dir", "url")} | {"source_url": item["url"]}
                if folder_name == "sfx":
                    prepare_sfx(raw, target)
                    entry["peak_s"] = peak_offset(target)
                else:
                    target.write_bytes(raw.read_bytes())
                kept[item["file"]] = entry
                done["fetched"] += 1
        write_json(folder / LIBRARY_FILE, {"schema": "vf.media.v1", "items": sorted(kept.values(),
                                                                                   key=lambda e: e["file"])})
    if done["failed"]:
        raise StudioError("media-sync: " + "; ".join(done["failed"]))
    return done
