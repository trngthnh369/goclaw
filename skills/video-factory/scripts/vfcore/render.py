"""Render a job: narration -> timeline -> scene clips -> soundtrack -> master,
preview, captions, frames and QA.

Resumable by design. Each scene clip, the soundtrack and the master are keyed by
a hash of everything that shapes them, and the review cut keeps its pass-1
statistics, so a second call only does what is missing or changed. A call that
runs out of its time budget stops cleanly between steps with status "partial".
"""

from __future__ import annotations

import math
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import RENDERER_VERSION, audio, captions, cards, media, qa
from .formats import FPS, FormatSpec
from .jobs import (asset_record, load_state, note_render, render_input_sha, script_rate, script_theme,
                   script_voice)
from .netguard import check_public_url
from .paths import JobPaths, Studio
from .themes import get_theme
from .tts import effective_lexicon, spoken_text, synthesize
from .util import (StudioError, canonical_json, read_json, sha256_file, sha256_text, utc_now, write_json,
                   write_text)

LEAD_IN = 0.12            # silence before each scene's narration (speech is trimmed tight)
TAIL = 0.20               # breath after it: with the trim margins ~0.65 s between scenes
LAST_TAIL = 1.00          # let the last scene (usually the CTA) sit
PREVIEW_LIMIT = 9_500_000  # Discord uploads without Nitro/boost cap at 10 MB
TTS_WORKERS = 2
# What each step costs at normal speed, in seconds per second of video, measured in the
# container: clips 62.7 s for 40.5 s of video; the rest on the 48.8 s live E2E #2 -
# audio 13.9 s, concat + mux 0.9 s, review cut 79.5 s over two passes, QA 21.1 s.
STEP_COST = {"clip": 1.55, "audio": 0.29, "master": 0.02, "cut_pass": 0.82, "qa": 0.43}
BUDGET_MARGIN = 1.3        # an estimate is trusted only with this much room on top


def render_config(studio_cfg: dict) -> dict:
    """The part of studio.json that changes pixels or sound (part of the render hash)."""
    return {
        "brand": (studio_cfg.get("brand") or {}).get("handle", "") if (studio_cfg.get("brand") or {}).get("show") else "",
        "music_db": (studio_cfg.get("music") or {}).get("volume_db", -20),
        "lexicon": studio_cfg.get("lexicon") or {},
        "default_theme": (studio_cfg.get("defaults") or {}).get("theme", "midnight"),
        "default_music": (studio_cfg.get("defaults") or {}).get("music", "auto"),
    }


class _Timer:
    """Wall time per phase, reported in the render result and the metrics log."""

    def __init__(self) -> None:
        self.phases: dict[str, float] = {}
        self._mark = time.monotonic()

    def lap(self, name: str) -> None:
        now = time.monotonic()
        self.phases[name] = round(self.phases.get(name, 0.0) + now - self._mark, 1)
        self._mark = now


class _Pace:
    """Keeps one render call inside its time budget on a machine whose speed changes.

    Every step is estimated as its normal cost times the slowdown observed last
    (kept in render/pace.json across calls). While the host was short of memory a
    render ran 5-8x slower than usual; with fixed estimates the unresumable tail
    outgrew the 600 s exec timeout and every retry started it over (2026-09-24).
    The first step of a call always runs, so every call makes progress.
    """

    def __init__(self, file: Path, budget_seconds: float) -> None:
        self.file = file
        self.budget = budget_seconds
        self.started = time.monotonic()
        saved = read_json(file, {}) or {}
        self.slowdown = max(1.0, float(saved.get("slowdown", 1.0)))
        self.worked = False

    def fits(self, normal_seconds: float) -> bool:
        if not self.worked:
            return True
        return self.elapsed() + normal_seconds * self.slowdown * BUDGET_MARGIN <= self.budget

    def done(self, normal_seconds: float, took: float) -> None:
        """Record a finished step and what it says about the machine's speed."""
        self.worked = True
        if normal_seconds >= 1.0:          # a step this small says nothing about speed
            self.slowdown = max(1.0, took / normal_seconds)
            write_json(self.file, {"slowdown": round(self.slowdown, 2), "at": utc_now()})

    def elapsed(self) -> float:
        return round(time.monotonic() - self.started, 1)


def _review_cut(paths: JobPaths, fmt: FormatSpec, total: float, pace: _Pace) -> dict | None:
    """The two-pass review cut, resumable between its passes. None: out of time, call again.

    State lives in render/reviewcut.json, bound to the master's sha: which bitrate
    attempt is running and whether pass 1's statistics are already on disk.
    """
    state_file = paths.render / "reviewcut.json"
    master_sha = sha256_file(paths.master)[:16]
    state = read_json(state_file, {}) or {}
    if state.get("master") != master_sha:
        state = {"master": master_sha, "attempt": 0, "pass1": False}
    if state.get("done") and paths.preview.exists():
        return state["done"]
    width, height, budget = media.review_cut_size(fmt, total, PREVIEW_LIMIT)
    pass_cost = STEP_COST["cut_pass"] * total
    while True:
        video_kbps = max(400, int(budget * media.REVIEW_BITRATE_FACTORS[state["attempt"]]))
        if state["pass1"] and not media.has_review_passlog(paths.render):
            state["pass1"] = False
        for pass_no in (1, 2):
            if pass_no == 1 and state["pass1"]:
                continue
            if not pace.fits(pass_cost):
                write_json(state_file, state)
                return None
            started = time.monotonic()
            media.review_cut_pass(paths.master, paths.preview, width, height, video_kbps, paths.render, pass_no)
            pace.done(pass_cost, time.monotonic() - started)
            if pass_no == 1:
                state["pass1"] = True
                write_json(state_file, state)
        size = paths.preview.stat().st_size
        media.clear_review_passlog(paths.render)
        if size <= PREVIEW_LIMIT or state["attempt"] == len(media.REVIEW_BITRATE_FACTORS) - 1:
            state["done"] = {"bytes": size, "width": width, "height": height, "video_kbps": video_kbps}
            write_json(state_file, state)
            return state["done"]
        state.update(attempt=state["attempt"] + 1, pass1=False)
        write_json(state_file, state)


def render(studio: Studio, paths: JobPaths, studio_cfg: dict, *, budget_seconds: float = 470) -> dict:
    timer = _Timer()
    state = load_state(paths)
    if not state.script_ok:
        raise StudioError("script/research are not valid; nothing to render")
    cfg = render_config(studio_cfg)
    fmt = state.fmt
    script = state.script
    scenes = script["scenes"]
    theme = get_theme(script_theme(state, cfg["default_theme"]))
    voice, rate = script_voice(state), script_rate(state)
    for sub in (paths.audio, paths.clips, paths.render / "prep", paths.cards, paths.out):
        sub.mkdir(parents=True, exist_ok=True)
    pace = _Pace(paths.render / "pace.json", budget_seconds)

    # 1. narration + timeline ------------------------------------------------------
    # Two requests in flight at most: the endpoint answers 503 at around four.
    lexicon = effective_lexicon(voice, cfg["lexicon"])
    texts = [spoken_text(s["narration"], s.get("tts_text"), lexicon) for s in scenes]
    with ThreadPoolExecutor(max_workers=TTS_WORKERS) as pool:
        narrations = list(pool.map(
            lambda pair: synthesize(pair[1], voice, rate, paths.audio / f"{pair[0]['id']}.wav", studio.cache / "tts"),
            zip(scenes, texts)))
    timeline: list[dict] = []
    cursor_frames = 0
    for index, (scene, text, narr) in enumerate(zip(scenes, texts, narrations)):
        tail = LAST_TAIL if index == len(scenes) - 1 else TAIL
        frames = math.ceil((LEAD_IN + narr.duration + tail) * FPS)
        timeline.append({"id": scene["id"], "start": cursor_frames / FPS, "frames": frames,
                         "duration": frames / FPS, "narration": str(narr.audio),
                         "narration_duration": narr.duration, "words": narr.words, "spoken": text})
        cursor_frames += frames
    total = cursor_frames / FPS
    timer.lap("tts")

    # 2. card / screenshot pictures (one browser batch) ----------------------------
    browser_jobs: list[dict] = []
    for scene in scenes:
        visual = scene["visual"]
        target = paths.cards / f"{scene['id']}.png"
        if visual["kind"] == "card":
            html_text = cards.card_html(visual["card"], fmt, theme, has_headline=bool(scene.get("on_screen")))
            key = sha256_text(html_text)[:16]
            if read_json(paths.cards / f"{scene['id']}.key.json", {}).get("key") == key and target.exists():
                continue
            html_file = paths.cards / f"{scene['id']}.html"
            write_text(html_file, html_text)
            browser_jobs.append({"mode": "html", "url": html_file.resolve().as_uri(), "out": str(target.resolve()),
                                 "width": fmt.width, "height": fmt.height, "scale": media.OVERSAMPLE,
                                 "_scene": scene["id"], "_key": key})
        elif visual["kind"] == "screenshot":
            key = sha256_text(canonical_json([visual.get("url"), visual.get("viewport"), fmt.name]))[:16]
            if read_json(paths.cards / f"{scene['id']}.key.json", {}).get("key") == key and target.exists():
                continue
            reason = check_public_url(visual["url"])
            if reason:
                raise StudioError(f"scene {scene['id']}: cannot capture {visual['url']}: {reason}")
            viewport = visual.get("viewport", "auto")
            if viewport == "auto":
                viewport = "desktop" if fmt.name == "long" else "mobile"
            browser_jobs.append({"mode": "url", "url": visual["url"], "out": str(target.resolve()),
                                 "viewport": viewport, "maxHeight": fmt.height * 3,
                                 "_scene": scene["id"], "_key": key})
    if browser_jobs:
        results = cards.render_cards([{k: v for k, v in j.items() if not k.startswith("_")} for j in browser_jobs],
                                     paths.render / "browser")
        failed = [f"{j['_scene']}: {results[j['out']]}" for j in browser_jobs if results.get(j["out"]) != "ok"]
        if failed:
            raise StudioError("browser render failed - " + "; ".join(failed))
        for job in browser_jobs:
            write_json(paths.cards / f"{job['_scene']}.key.json", {"key": job["_key"]})
        pace.worked = True
    timer.lap("browser")

    # 3. scene clips (resumable) -------------------------------------------------------
    clips: list[Path] = []
    clip_keys: list[str] = []
    chunks_by_scene: list[tuple[float, list]] = []
    rendered = skipped = 0
    for index, (scene, slot) in enumerate(zip(scenes, timeline)):
        visual = scene["visual"]
        kind = visual["kind"]
        motion = media.resolve_motion(kind, visual.get("motion", "auto"), index)
        tokens = captions.align(scene["narration"], slot["words"], slot["narration_duration"])
        ass_text, chunks = captions.build_scene_ass(
            fmt, theme, tokens=tokens, emphasis=scene.get("emphasis", []),
            on_screen=scene.get("on_screen", ""), duration=slot["duration"], offset=LEAD_IN, brand=cfg["brand"])
        chunks_by_scene.append((slot["start"], chunks))
        if kind == "ai_image":
            record = asset_record(paths, scene["id"]) or {}
            source = paths.assets / record.get("file", "")
            source_sha = record.get("sha256")
        else:
            source = paths.cards / f"{scene['id']}.png"
            source_sha = sha256_file(source)[:16]
        key = sha256_text(canonical_json({"v": RENDERER_VERSION, "fmt": fmt.name, "ass": ass_text,
                                          "src": source_sha, "motion": motion, "frames": slot["frames"],
                                          "kind": kind}))[:16]
        clip = paths.clips / f"{scene['id']}.mp4"
        key_file = paths.clips / f"{scene['id']}.key.json"
        clips.append(clip)
        clip_keys.append(key)
        if clip.exists() and read_json(key_file, {}).get("key") == key:
            skipped += 1
            continue
        clip_cost = slot["duration"] * STEP_COST["clip"]
        if not pace.fits(clip_cost):
            return {"status": "partial", "rendered": rendered, "skipped": skipped,
                    "remaining": len(scenes) - index, "elapsed": pace.elapsed(), "next": "clips"}
        clip_started = time.monotonic()
        ass_file = paths.clips / f"{scene['id']}.ass"
        write_text(ass_file, ass_text)
        if not source.exists():
            raise StudioError(f"scene {scene['id']}: picture {source.name} is missing")
        if kind == "screenshot":
            media.render_scroll_clip(source, clip, ass_file, slot["frames"], fmt, motion)
        else:
            prepared = paths.render / "prep" / f"{scene['id']}.png"
            if kind == "card":
                shutil.copyfile(source, prepared)      # already rendered at OVERSAMPLE x
            else:
                media.prepare_still(source, prepared, fmt)
            strength = 0.035 if kind == "card" else 0.10
            media.render_still_clip(prepared, clip, ass_file, slot["frames"], motion, fmt, strength=strength)
        write_json(key_file, {"key": key})
        pace.done(clip_cost, time.monotonic() - clip_started)
        rendered += 1
    timer.lap("clips")

    def partial(step: str) -> dict:
        # Stop between steps, never so close to the exec timeout that the agent sees a
        # kill instead of PARTIAL; every finished step is kept for the next call.
        return {"status": "partial", "rendered": rendered, "skipped": skipped, "remaining": 0,
                "elapsed": pace.elapsed(), "next": step}

    # 4. soundtrack (kept across calls, keyed by what it is made of) ------------------------
    music_mode = script.get("music") or cfg["default_music"]
    pick = audio.pick_library_track(studio, paths.job_id) if music_mode in ("auto", "library") else None
    music_used = f"library:{pick.name}" if pick is not None else "none"
    soundtrack = paths.render / "soundtrack.m4a"
    sound_file = paths.render / "soundtrack.key.json"
    sound_key = sha256_text(canonical_json({
        "v": RENDERER_VERSION, "voice": voice, "rate": rate, "music": music_used, "music_db": cfg["music_db"],
        "slots": [[t["id"], t["spoken"], t["frames"]] for t in timeline]}))[:16]
    sound = read_json(sound_file, {}) or {}
    if not (soundtrack.exists() and sound.get("key") == sound_key):
        if not pace.fits(STEP_COST["audio"] * total):
            return partial("audio")
        step_started = time.monotonic()
        tracks = []
        for slot in timeline:
            track = paths.audio / f"{slot['id']}.track.wav"
            audio.scene_track(Path(slot["narration"]), track, lead=LEAD_IN, duration=slot["duration"])
            tracks.append(track)
        voice_track = paths.render / "voice.wav"
        audio.concat_tracks(tracks, paths.render / "voice.txt", voice_track)
        bed = None
        if pick is not None:
            bed = paths.render / "bed.wav"
            audio.music_bed(pick, bed, total)
        measured = audio.mix_and_normalize(voice_track, bed, soundtrack, music_db=cfg["music_db"])
        sound = {"key": sound_key, "measured": {k: measured.get(k) for k in ("input_i", "input_tp")}}
        write_json(sound_file, sound)
        pace.done(STEP_COST["audio"] * total, time.monotonic() - step_started)
    timer.lap("audio")

    # 5. master, review cut, captions, QA ---------------------------------------------------
    master_file = paths.render / "master.key.json"
    master_key = sha256_text(canonical_json({"clips": clip_keys, "sound": sound_key}))[:16]
    if not (paths.master.exists() and (read_json(master_file, {}) or {}).get("key") == master_key):
        if not pace.fits(STEP_COST["master"] * total):
            return partial("master")
        step_started = time.monotonic()
        video_only = paths.render / "video.mp4"
        media.concat_clips(clips, paths.render / "clips.txt", video_only)
        media.mux(video_only, soundtrack, paths.master)
        write_json(master_file, {"key": master_key})
        pace.done(STEP_COST["master"] * total, time.monotonic() - step_started)
    timer.lap("mux")
    cut = _review_cut(paths, fmt, total, pace)
    if cut is None:
        return partial("review_cut")
    preview_bytes = cut["bytes"]
    timer.lap("review_cut")
    if not pace.fits(STEP_COST["qa"] * total):
        return partial("qa")
    write_text(paths.srt, captions.build_srt(chunks_by_scene))
    report = qa.run_qa(paths, fmt, [{k: t[k] for k in ("id", "start", "duration")} for t in timeline],
                       total, preview_bytes, PREVIEW_LIMIT)
    timer.lap("qa")
    report["timings"] = timer.phases
    report["music"] = music_used
    report["pre_normalization"] = sound.get("measured", {})
    write_json(paths.qa, report)
    master_sha = sha256_file(paths.master)[:16]
    manifest = {
        "input_sha": render_input_sha(state, cfg),
        "master_sha": master_sha,
        "qa_ok": report["ok"],
        "duration": report["duration"],
        "voice": voice, "rate": rate, "theme": theme.name,
        "timeline": [{k: t[k] for k in ("id", "start", "duration", "spoken")} for t in timeline],
        "renderer": RENDERER_VERSION,
        "review_cut": {**cut, "sha256": sha256_file(paths.preview)},
    }
    write_json(paths.manifest, manifest)
    note_render(paths, master_sha)
    return {"status": "done" if report["ok"] else "qa_failed", "rendered": rendered, "skipped": skipped,
            "elapsed": pace.elapsed(), "duration": report["duration"],
            "qa_hard": report["hard"], "qa_soft": report["soft"], "master": str(paths.master),
            "preview": str(paths.preview), "preview_mb": round(preview_bytes / 1e6, 2),
            "timings": timer.phases}
