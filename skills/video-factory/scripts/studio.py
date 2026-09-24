#!/usr/bin/env python3
"""Video Factory command line - the only interface agents use.

Every command prints a short, final answer for a model to act on: PASS/FAIL,
the errors to fix, and what to run next. Artifacts reach agents through this
stdout (exec), never through read_file.

    studio.py init | doctor | list | config show
    studio.py new --topic T [--brief B] [--format short|long|square] [--source manual|cron]
    studio.py next [--job J]              the single decision point
    studio.py emit --job J --stage S      material for a stage
    studio.py submit --job J --kind research|script|review_script|review_video --file <job inbox>/<kind>.json
    studio.py validate --job J
    studio.py attach --job J --scene S --file PATH
    studio.py redo --job J --scene S | revise-visual --job J --scene S --prompt P [--motion M]
    studio.py render --job J [--budget SECONDS]
    studio.py package --job J | delivered --job J --status sent [--message-id ID] | published --job J
    studio.py config set publish.facebook_reels.enabled=true|false
    studio.py override --job J --stage script|video --quote "<human words>" | cancel --job J --reason R
    studio.py feedback --job J --type visual|script [--scene S] --text T   (human change request)
    studio.py set --job J [--voice V] [--rate N] [--theme T]
    studio.py backlog add --topic T [--brief B] | backlog list | backlog take
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vfcore import backlog, briefs, jobs, package, render  # noqa: E402
from vfcore.formats import FORMATS  # noqa: E402
from vfcore.paths import CDP_RENDER_JS, FONTS_DIR, JobPaths, Studio, default_workspace, studio_cmd  # noqa: E402
from vfcore.schema import MOTIONS, validate_research  # noqa: E402
from vfcore.util import (StudioError, append_ndjson, read_json, sha256_file, utc_now,  # noqa: E402
                         write_json, write_json_numbered)

DEFAULT_CONFIG = {
    "schema": "vf.studio.v1",
    "brand": {"handle": "", "show": False},
    "defaults": {"format": "short", "voice": "vi-VN-HoaiMyNeural", "rate": 5, "theme": "midnight",
                 "music": "auto"},
    "music": {"volume_db": -20},
    "lexicon": {},
    "delivery": {"channel": "", "target": ""},
    "publish": {"facebook_reels": {"enabled": False}},
}
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
# create_image saves into <director workspace>/.../generated/<date>/; attach takes
# only those files, so a scene can never be pointed at some other file the
# gateway can read and have it rendered into a public video.
IMAGE_ROOT = Path("/app/workspace/vf-director")


# --------------------------------------------------------------------------- helpers

def out(text: str) -> None:
    sys.stdout.write(text.rstrip() + "\n")


def out_json(value: object) -> None:
    out(json.dumps(value, ensure_ascii=False, indent=1))


def studio_from(args: argparse.Namespace) -> Studio:
    return Studio(Path(args.workspace) if args.workspace else default_workspace())


def load_config(studio: Studio) -> dict:
    cfg = read_json(studio.config)
    if not isinstance(cfg, dict):
        raise StudioError(f"studio not initialised - run: {studio_cmd()} init")
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for key, value in cfg.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def job_paths(studio: Studio, job_id: str, *, open_only: bool = False):
    """Resolve a job. open_only refuses jobs that are cancelled, published or escalated:
    a stage still running when its job was cancelled must not keep writing into it."""
    if not re.fullmatch(r"vf-[a-z0-9-]{3,60}", job_id or ""):
        raise StudioError(f"invalid job id {job_id!r}")
    paths = studio.job(job_id)
    if not paths.meta.exists():
        raise StudioError(f"job {job_id} not found - run: {studio_cmd()} list")
    if open_only:
        status = jobs.load_meta(paths).get("status")
        if status in jobs.TERMINAL:
            raise StudioError(f"job {job_id} is {status}; nothing more can change in it")
    # write_file cannot create a file in a folder that does not exist yet, so the
    # inbox is created before any command prints it as a place to write to.
    paths.inbox.mkdir(parents=True, exist_ok=True)
    return paths


def metric(studio: Studio, **row: object) -> None:
    append_ndjson(studio.metrics, {"at": utc_now(), **row})


def payload_file(paths: JobPaths, given: str | None, kind: str) -> Path | None:
    """The inbox file to read, or None for stdin. Only the job's own inbox is accepted."""
    if not given:
        return None
    path = Path(given).resolve()
    inbox = paths.inbox.resolve()
    if path.parent != inbox:
        raise StudioError(f"--file must be inside {inbox.as_posix()}/ (write it there with write_file): "
                          f"{paths.inbox_file(kind).as_posix()}")
    if not path.is_file():
        raise StudioError(f"{path.as_posix()} does not exist - write the JSON there with write_file first")
    return path


def read_payload(source: Path | None) -> object:
    raw = source.read_text(encoding="utf-8") if source else sys.stdin.read()
    raw = raw.strip()
    fence = re.match(r"^```[a-zA-Z]*\s*\n(.*)\n```$", raw, re.S)
    if fence:
        raw = fence.group(1)
    if not raw:
        raise StudioError("no JSON received - write it with write_file and pass --file <path>")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        lines = raw.splitlines()
        context = lines[exc.lineno - 1][max(0, exc.colno - 40): exc.colno + 40] if 0 < exc.lineno <= len(lines) else ""
        raise StudioError(f"invalid JSON at line {exc.lineno} col {exc.colno}: {exc.msg} near: {context!r}") from exc


def print_result(label: str, errors: list[str], warnings: list[str]) -> bool:
    if errors:
        out(f"FAIL {label}: {len(errors)} error(s) - fix all of them and resubmit")
        for err in errors[:25]:
            out(f"  ERROR {err}")
    else:
        out(f"PASS {label}")
    for warn in warnings[:12]:
        out(f"  WARN {warn}")
    return not errors


# --------------------------------------------------------------------------- commands

def cmd_init(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    for sub in (studio.jobs, studio.music, studio.cache, studio.metrics.parent):
        sub.mkdir(parents=True, exist_ok=True)
    if not studio.config.exists():
        write_json(studio.config, DEFAULT_CONFIG)
        out(f"created {studio.config}")
    if not studio.backlog.exists():
        write_json(studio.backlog, {"items": []})
    out(f"OK studio at {studio.root}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    checks: list[tuple[str, bool, str]] = []

    def probe(cmd: list[str]) -> tuple[bool, str]:
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=20)
            return proc.returncode == 0, (proc.stdout or proc.stderr).decode("utf-8", "replace").strip()[:120]
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)

    try:
        buildconf = subprocess.run(["ffmpeg", "-hide_banner", "-buildconf"], capture_output=True, timeout=20)
        has_ass = b"enable-libass" in buildconf.stdout + buildconf.stderr
    except (OSError, subprocess.TimeoutExpired):
        has_ass = False
    checks.append(("ffmpeg with libass", has_ass, "ffmpeg -buildconf"))
    checks.append(("ffprobe", probe(["ffprobe", "-version"])[0], ""))
    checks.append(("node", probe(["node", "--version"])[0], ""))
    checks.append(("cdp renderer present", CDP_RENDER_JS.exists(), str(CDP_RENDER_JS)))
    try:
        import edge_tts  # noqa: F401
        checks.append(("edge-tts", True, getattr(edge_tts, "__version__", "")))
    except ImportError as exc:
        checks.append(("edge-tts", False, str(exc)))
    fonts = sorted(p.name for p in FONTS_DIR.glob("*.ttf"))
    checks.append(("fonts", len(fonts) >= 5, ", ".join(fonts)))
    checks.append(("workspace writable", os.access(studio.root, os.W_OK) if studio.root.exists() else False,
                   str(studio.root)))
    checks.append(("studio.json", studio.config.exists(), str(studio.config)))
    bad = 0
    for name, passed, detail in checks:
        bad += 0 if passed else 1
        out(f"{'OK  ' if passed else 'FAIL'} {name} {detail}")
    return 1 if bad else 0


def cmd_new(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    cfg = load_config(studio)
    d = cfg["defaults"]
    paths = jobs.create_job(studio, topic=args.topic, brief=args.brief or "", fmt=args.format or d["format"],
                            voice=args.voice or d["voice"], rate=args.rate if args.rate is not None else d["rate"],
                            source=args.source, requested_by=args.by or "", theme=args.theme)
    metric(studio, job=paths.job_id, event="created", source=args.source)
    out(f"CREATED {paths.job_id}")
    out(f"NEXT: {studio_cmd()} next --job {paths.job_id}")
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    cfg = load_config(studio)
    if not args.job:
        active = []
        for meta_file in sorted(studio.jobs.glob("*/job.json")):
            meta = read_json(meta_file) or {}
            if meta.get("status") == "active":
                active.append(meta["id"])
        if len(active) != 1:
            out_json({"action": "choose", "active_jobs": active,
                      "say": "Pass --job <id>." if active else "No active job. Create one with new or backlog take."})
            return 0
        args.job = active[0]
    paths = job_paths(studio, args.job)
    action = jobs.next_action(studio, paths, render.render_config(cfg))
    channel, target = cfg["delivery"].get("channel"), cfg["delivery"].get("target")
    if action.get("stage") == "escalate" and channel and target:
        # Every video that does not converge waits for a person (decided 2026-09-24),
        # so the question goes to the review channel even from a cron run.
        text = package.escalation_message(paths, action.get("issues", []))
        action["message_call"] = {"action": "send", "channel": channel, "target": str(target), "message": text,
                                  "forward": True, "forward_reason": "Video Factory asks the reviewer to decide"}
        action["say"] += " Send the question with ONE message tool call, arguments exactly as in message_call."
    if action.get("stage") == "deliver" and not channel:
        action["delivery_note"] = ("No delivery channel configured: after package, reply to the requester in this "
                                   "conversation with the message text (it ends with the MEDIA line), then run delivered.")
    out_json(action)
    return 0


def cmd_emit(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    paths = job_paths(studio, args.job)
    state = jobs.load_state(paths)
    if args.stage in ("script", "script_revise"):
        out(briefs.emit_script(state, revise=args.stage == "script_revise" or state.script is not None))
    elif args.stage == "review_script":
        if not state.script_ok:
            raise StudioError("script is not valid yet; nothing to review")
        out(briefs.emit_review_script(state))
    elif args.stage == "review_video":
        if not paths.qa.exists():
            raise StudioError("no render yet; nothing to review")
        out(briefs.emit_review_video(state))
    elif args.stage == "assets":
        for prompt in briefs.assets_help(state):
            out(prompt)
    else:
        raise StudioError(f"unknown stage {args.stage}")
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    paths = job_paths(studio, args.job, open_only=True)
    source = payload_file(paths, args.file, args.kind)
    doc = read_payload(source)

    def done() -> None:
        """Remove a stored inbox file so a later call cannot submit it again by mistake."""
        if source:
            source.unlink(missing_ok=True)

    if args.kind == "research":
        errors, warnings = validate_research(doc)
        if print_result("research.json", errors, warnings):
            write_json(paths.research, doc)
            done()
            state = jobs.load_state(paths)
            if state.script is not None and state.script_errors:
                out("NOTE the saved script.json no longer validates against this research - submit it again:")
                for err in state.script_errors[:10]:
                    out(f"  ERROR {err}")
            else:
                out(f"NEXT: script.json - {jobs.submit_steps(paths, 'script')}")
        metric(studio, job=paths.job_id, event="submit_research", ok=not errors)
        return 0 if not errors else 2
    if args.kind == "script":
        if read_json(paths.research) is None:
            raise StudioError("submit research.json first - the script cites its fact ids")
        state = jobs.load_state(paths)
        errors, warnings = jobs.check_script(state, doc)
        if print_result("script.json", errors, warnings):
            if isinstance(doc, dict):
                doc.setdefault("format", state.fmt.name)
            write_json(paths.script, doc)
            done()
            jobs.note_script_submitted(paths)
            out(f"NEXT: reply with STAGE_RESULT (the director continues with {studio_cmd()} next --job {paths.job_id})")
        metric(studio, job=paths.job_id, event="submit_script", ok=not errors)
        return 0 if not errors else 2
    if args.kind in ("review_script", "review_video"):
        errors, warnings, prior = jobs.submit_review(paths, args.kind, doc)
        stage = args.kind.removeprefix("review_")
        if prior is not None:
            done()
            out(f"ALREADY RECORDED {args.kind}: verdict={prior.get('verdict')} at {prior.get('_submitted_at')}. "
                "The first verdict for a version is final; nothing was changed.")
            out(f"NEXT: reply now, last line: STAGE_RESULT: review_{stage} DONE")
            metric(studio, job=paths.job_id, event=f"submit_{args.kind}", ok=False, refused="already_recorded")
            return 0
        verdict = doc.get("verdict") if isinstance(doc, dict) else None
        if errors:
            print_result(args.kind, errors, warnings)
        else:
            done()
            serious = sum(1 for i in doc.get("issues", []) if i.get("severity") in ("blocker", "major"))
            out(f"RECORDED {args.kind}: verdict={verdict} ({serious} blocker/major issue(s)). "
                "This verdict is final for this version - do not submit again.")
            for warn in warnings[:12]:
                out(f"  WARN {warn}")
            out(f"NEXT: reply now, last line: STAGE_RESULT: review_{stage} DONE")
        metric(studio, job=paths.job_id, event=f"submit_{args.kind}", ok=not errors, verdict=verdict)
        return 0 if not errors else 2
    raise StudioError(f"unknown kind {args.kind}")


def cmd_validate(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    paths = job_paths(studio, args.job)
    state = jobs.load_state(paths)
    ok1 = print_result("research.json", state.research_errors, [])
    ok2 = print_result("script.json", state.script_errors, state.script_warnings)
    if ok1 and ok2:
        jobs.note_script_submitted(paths)
    return 0 if ok1 and ok2 else 2


def _probe_image(path: Path) -> tuple[int, int]:
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=width,height", "-of", "csv=p=0", str(path)], capture_output=True, timeout=30)
    try:
        w, h = proc.stdout.decode().strip().split(",")[:2]
        return int(w), int(h)
    except ValueError as exc:
        raise StudioError(f"{path.name} is not a readable image") from exc


def cmd_attach(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    paths = job_paths(studio, args.job, open_only=True)
    state = jobs.load_state(paths)
    if not state.script_ok:
        raise StudioError("script is not valid; attach images after it passes review")
    scene = next((s for s in state.script["scenes"] if s["id"] == args.scene), None)
    if scene is None or scene["visual"]["kind"] != "ai_image":
        raise StudioError(f"scene {args.scene} is not an ai_image scene")
    src = Path(args.file.removeprefix("MEDIA:").strip()).resolve()
    root = IMAGE_ROOT.resolve()
    if not src.is_relative_to(root) or "generated" not in src.relative_to(root).parts:
        raise StudioError(f"attach takes only a picture create_image made (under {root}/.../generated/)")
    if not src.is_file() or src.suffix.lower() not in IMAGE_EXTS:
        raise StudioError(f"{src} is not an image file (png/jpg/webp)")
    width, height = _probe_image(src)
    paths.assets.mkdir(parents=True, exist_ok=True)
    dest = paths.assets / f"{args.scene}{src.suffix.lower()}"
    for old in paths.assets.glob(f"{args.scene}.*"):
        if old.suffix.lower() in IMAGE_EXTS:
            old.unlink()
    shutil.copyfile(src, dest)
    prompt = jobs.image_prompt(scene, state.script, state.fmt)
    write_json(paths.assets / f"{args.scene}.json", {
        "file": dest.name, "sha256": sha256_file(dest)[:16], "prompt_sha": jobs.prompt_sha(prompt),
        "source": str(src), "width": width, "height": height, "attached_at": utc_now()})
    note = "" if min(width, height) >= 700 else f" WARN low resolution {width}x{height}"
    remaining = len(jobs.missing_images(jobs.load_state(paths)))
    out(f"ATTACHED {args.scene} {width}x{height}{note}; images still missing: {remaining}")
    return 0


def cmd_redo(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    paths = job_paths(studio, args.job, open_only=True)
    state = jobs.load_state(paths)
    scene_ids = [s["id"] for s in state.script.get("scenes", [])] if state.script_ok else []
    if args.scene not in scene_ids:   # also keeps "../x" from naming a file outside assets/
        raise StudioError(f"scene must be one of {', '.join(scene_ids) or '(no valid script yet)'}")
    record = paths.assets / f"{args.scene}.json"
    if record.exists():
        record.unlink()
    out(f"OK {args.scene} will be regenerated; run: {studio_cmd()} next --job {args.job}")
    return 0


def cmd_revise_visual(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    paths = job_paths(studio, args.job, open_only=True)
    state = jobs.load_state(paths)
    script = state.script
    scene = next((s for s in (script or {}).get("scenes", []) if s.get("id") == args.scene), None)
    if scene is None:
        raise StudioError(f"scene {args.scene} not found")
    if args.prompt:
        if scene["visual"]["kind"] != "ai_image":
            raise StudioError("only ai_image scenes have a prompt")
        scene["visual"]["prompt"] = args.prompt.strip()
    if args.motion:
        if args.motion not in MOTIONS + ("scroll",):
            raise StudioError(f"motion must be one of {', '.join(MOTIONS)}")
        scene["visual"]["motion"] = args.motion
    errors, _ = jobs.check_script(state, script)
    if errors:
        raise StudioError("the change breaks the script: " + "; ".join(errors[:5]))
    write_json(paths.script, script)
    meta = jobs.load_meta(paths)
    jobs.log_event(paths, meta, "revise_visual", scene=args.scene)
    out(f"OK {args.scene} visual updated; run: {studio_cmd()} next --job {args.job}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    paths = job_paths(studio, args.job, open_only=True)
    cfg = load_config(studio)
    try:
        result = render.render(studio, paths, cfg, budget_seconds=args.budget)
    except StudioError as exc:
        metric(studio, job=args.job, event="render_error", error=str(exc)[:500])
        raise
    metric(studio, job=args.job, event="render",
           **{k: v for k, v in result.items() if k in ("status", "elapsed", "duration", "timings", "rendered")})
    if result["status"] == "partial":
        out(f"PARTIAL rendered {result['rendered']} scene(s), {result['remaining']} left - run the SAME command again.")
        return 0
    out(f"{'DONE' if result['status'] == 'done' else 'QA_FAILED'} {result['duration']}s video in {result['elapsed']}s "
        f"(scenes rendered {result['rendered']}, reused {result['skipped']}); preview {result['preview_mb']} MB")
    out("  timings " + " ".join(f"{k}={v}s" for k, v in result.get("timings", {}).items()))
    for item in result["qa_hard"]:
        out(f"  QA-HARD {item}")
    for item in result["qa_soft"]:
        out(f"  QA-SOFT {item}")
    out(f"NEXT: {studio_cmd()} next --job {args.job}")
    return 0 if result["status"] == "done" else 3


def cmd_package(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    paths = job_paths(studio, args.job, open_only=True)
    cfg = load_config(studio)
    record = package.package(paths, publish_enabled=cfg["publish"]["facebook_reels"].get("enabled") is True)
    channel, target = cfg["delivery"].get("channel"), cfg["delivery"].get("target")
    out(f"PACKAGED {args.job}: {paths.deliver}")
    out("Reels: publishable on approval" if record["reels_publishable"]
        else "Reels: NOT publishable (" + "; ".join(record["reels_blockers"]) + ")")
    if channel and target:
        call = {"action": "send", "channel": channel, "target": str(target), "message": record["message"],
                "forward": True, "forward_reason": "Video Factory review delivery to the configured review channel"}
        out("Send it with ONE message tool call, arguments exactly as below (text unchanged):")
        out(json.dumps(call, ensure_ascii=False))
    else:
        out("No delivery channel configured. Reply to the requester with exactly this text (keep the MEDIA line):")
        out("-----")
        out(record["message"])
        out("-----")
    out(f"After it is sent: {studio_cmd()} delivered --job {args.job} --status sent")
    return 0


def cmd_delivered(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    paths = job_paths(studio, args.job)
    record = read_json(paths.delivery)
    if not record:
        raise StudioError("package the job before marking it delivered")
    manifest = read_json(paths.manifest) or {}
    if record.get("master_sha") != manifest.get("master_sha"):
        raise StudioError("the package is stale (video re-rendered) - run package again")
    record.update({"status": args.status, "sent_at": utc_now(), "message_id": args.message_id or ""})
    write_json(paths.delivery, record)
    meta = jobs.load_meta(paths)
    if args.status == "sent":
        meta["status"] = "awaiting_approval"
    jobs.log_event(paths, meta, "delivered", status=args.status, message_id=args.message_id or "")
    metric(studio, job=args.job, event="delivered", status=args.status)
    out(f"OK {args.job} marked {args.status}; waiting for the human's decision.")
    return 0


def cmd_published(args: argparse.Namespace) -> int:
    """Record that the gateway published the approved review cut as a Reel."""
    studio = studio_from(args)
    paths = job_paths(studio, args.job)
    record = read_json(paths.delivery)
    if not record or record.get("status") != "sent":
        raise StudioError("only a delivered job (delivered --status sent) can be marked published")
    if not record.get("reels_publishable"):
        raise StudioError("this delivery was not a publishable Reels draft; nothing was published")
    record.update({"status": "published", "published_at": utc_now()})
    write_json(paths.delivery, record)
    meta = jobs.load_meta(paths)
    meta["status"] = "published"
    jobs.log_event(paths, meta, "published", target="facebook_reels")
    metric(studio, job=args.job, event="published", target="facebook_reels")
    out(f"OK {args.job} marked published")
    return 0


def cmd_override(args: argparse.Namespace) -> int:
    """Pass an escalated review on the human's word.

    The quote is stored with the review and printed in the review message the
    human receives, so an override the human never gave is in front of the one
    person who can catch it.
    """
    studio = studio_from(args)
    paths = job_paths(studio, args.job)
    status = jobs.load_meta(paths).get("status")
    if status != "escalated":
        raise StudioError(f"job {args.job} is {status}; override only answers an escalated review")
    if len(args.quote.strip()) < 2:
        raise StudioError("--quote must be the human's own words, verbatim")
    state = jobs.load_state(paths)
    if args.stage == "script":
        target = jobs.script_review_sha(state.research, state.script)
    else:
        target = (read_json(paths.manifest) or {}).get("master_sha")
        if not target:
            raise StudioError("nothing rendered to override")
    write_json_numbered(paths.reviews, args.stage, len(jobs.reviews(paths, args.stage)) + 1, {
        "schema": "vf.review.v1", "stage": args.stage, "verdict": "PASS", "issues": [],
        "notes": f"HUMAN OVERRIDE: {args.quote}", "_override": True, "_human_quote": args.quote.strip(),
        "_target_sha": target, "_submitted_at": utc_now()})
    meta = jobs.load_meta(paths)
    meta["status"] = "active"
    jobs.log_event(paths, meta, "override", stage=args.stage, quote=args.quote.strip()[:300])
    out(f"OK {args.stage} review overridden by the human; run: {studio_cmd()} next --job {args.job}")
    return 0


def cmd_feedback(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    paths = job_paths(studio, args.job)
    status = jobs.load_meta(paths).get("status")
    if status in ("cancelled", "published"):     # an escalated job takes direction and carries on
        raise StudioError(f"job {args.job} is {status}; nothing more can change in it")
    jobs.record_human_feedback(paths, args.type, args.scene, args.text)
    out(f"OK feedback recorded ({args.type}, {args.scene}); run: {studio_cmd()} next --job {args.job}")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    """Job-level settings a human may change after seeing a cut (voice, speed, theme)."""
    studio = studio_from(args)
    paths = job_paths(studio, args.job, open_only=True)
    meta = jobs.load_meta(paths)
    changed = {}
    if args.voice:
        if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}-[A-Za-z]+Neural", args.voice):
            raise StudioError("voice must look like vi-VN-HoaiMyNeural or vi-VN-NamMinhNeural")
        changed["voice"] = args.voice
    if args.rate is not None:
        if not -10 <= args.rate <= 20:
            raise StudioError("rate must be between -10 and 20")
        changed["rate"] = args.rate
    if args.theme:
        from vfcore.themes import THEMES
        if args.theme not in THEMES:
            raise StudioError(f"theme must be one of {', '.join(THEMES)}")
        changed["theme"] = args.theme
    if not changed:
        raise StudioError("nothing to change: pass --voice, --rate or --theme")
    meta.setdefault("overrides", {}).update(changed)
    if meta.get("status") == "awaiting_approval":
        meta["status"] = "active"
    jobs.log_event(paths, meta, "settings", **changed)
    out(f"OK {changed}; run: {studio_cmd()} next --job {args.job}")
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    paths = job_paths(studio, args.job)
    meta = jobs.load_meta(paths)
    meta["status"] = "cancelled"
    jobs.log_event(paths, meta, "cancelled", reason=args.reason)
    out(f"OK {args.job} cancelled")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    paths = job_paths(studio, args.job)
    meta = jobs.load_meta(paths)
    qa = read_json(paths.qa) or {}
    delivery = read_json(paths.delivery) or {}
    out_json({"job": meta["id"], "topic": meta["topic"], "format": meta["format"], "status": meta["status"],
              "revisions": meta.get("revisions"), "duration": qa.get("duration"), "qa_ok": qa.get("ok"),
              "qa_soft": qa.get("soft"), "delivery": delivery.get("status"),
              "master": str(paths.master) if paths.master.exists() else None,
              "last_events": meta.get("history", [])[-5:]})
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    rows = []
    for meta_file in sorted(studio.jobs.glob("*/job.json"), reverse=True)[: args.limit]:
        meta = read_json(meta_file) or {}
        rows.append(f"{meta.get('id')}  {meta.get('status'):<18} {meta.get('format'):<6} {meta.get('topic', '')[:60]}")
    out("\n".join(rows) if rows else "no jobs yet")
    return 0


def cmd_backlog(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    if args.action == "add":
        if not args.topic:
            raise StudioError("backlog add needs --topic")
        item = backlog.add(studio, args.topic, args.brief or "", args.format or "short")
        out(f"ADDED {item['id']}: {item['topic']} ({len(backlog.pending(studio))} pending)")
    elif args.action == "list":
        items = backlog.pending(studio)
        out("\n".join(f"{i['id']}  {i['format']:<6} {i['topic']}" for i in items) if items else "backlog is empty")
    elif args.action == "take":
        unfinished = backlog.unfinished_cron_job(studio)
        if unfinished:
            out(f"RESUME {unfinished} - the cron's current video is not finished; continue it, do not start another.")
            out(f"NEXT: {studio_cmd()} next --job {unfinished}")
            return 0
        items = backlog.pending(studio)
        if not items:
            out("EMPTY backlog - nothing to make today.")
            return 0
        item = items[0]
        cfg = load_config(studio)
        d = cfg["defaults"]
        paths = jobs.create_job(studio, topic=item["topic"], brief=item.get("brief", ""),
                                fmt=item.get("format") or d["format"], voice=d["voice"], rate=d["rate"],
                                source=f"backlog:{item['id']}", requested_by="cron")
        backlog.mark(studio, item["id"], "taken", paths.job_id)
        metric(studio, job=paths.job_id, event="created", source=f"backlog:{item['id']}")
        out(f"CREATED {paths.job_id} from {item['id']}: {item['topic']}")
        out(f"NEXT: {studio_cmd()} next --job {paths.job_id}")
    elif args.action == "remove":
        backlog.mark(studio, args.id, "removed")
        out(f"OK removed {args.id}")
    return 0


# Each settable key has one type. A Discord chat id stays text: 19 digits pass 2**53
# and a JSON reader using doubles (the gateway's tool arguments) rounds them. A
# publish switch accepts only true/false: "no" used to be stored as a string, and
# a non-empty string is truthy, so it switched publishing ON.
CONFIG_TYPES = {"publish.facebook_reels.enabled": bool, "brand.show": bool,
                "defaults.rate": int, "music.volume_db": float}


def _config_value(key: str, value: str) -> object:
    kind = CONFIG_TYPES.get(key, str)
    if kind is bool:
        if value.lower() not in ("true", "false"):
            raise StudioError(f"{key} must be true or false")
        return value.lower() == "true"
    try:
        return kind(value)
    except ValueError:
        raise StudioError(f"{key} must be a {kind.__name__}, got {value!r}") from None


def cmd_config(args: argparse.Namespace) -> int:
    studio = studio_from(args)
    cfg = load_config(studio)
    if args.action == "show":
        out_json(cfg)
        return 0
    key, _, value = (args.assign or "").partition("=")
    allowed = {"brand.handle", "brand.show", "defaults.format", "defaults.voice", "defaults.rate",
               "defaults.theme", "defaults.music", "music.volume_db", "delivery.channel", "delivery.target",
               "publish.facebook_reels.enabled"}
    if key not in allowed:
        raise StudioError(f"settable keys: {', '.join(sorted(allowed))}")
    raw = read_json(studio.config) or {}
    *parents, name = key.split(".")
    parsed = _config_value(key, value)
    if key == "defaults.format" and value not in FORMATS:
        raise StudioError(f"format must be one of {', '.join(FORMATS)}")
    node = raw
    for part in parents:
        node = node.setdefault(part, {})
    node[name] = parsed
    write_json(studio.config, raw)
    out(f"OK {key} = {parsed!r}")
    return 0


# --------------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="studio.py", description="Video Factory")
    parser.add_argument("--workspace", help="studio root (default /app/workspace/video-factory)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("doctor")
    p = sub.add_parser("new")
    p.add_argument("--topic", required=True)
    p.add_argument("--brief")
    p.add_argument("--format", choices=sorted(FORMATS))
    p.add_argument("--voice")
    p.add_argument("--rate", type=int)
    p.add_argument("--theme")
    p.add_argument("--source", default="manual")
    p.add_argument("--by")
    p = sub.add_parser("next")
    p.add_argument("--job")
    p = sub.add_parser("emit")
    p.add_argument("--job", required=True)
    p.add_argument("--stage", required=True,
                   choices=["script", "script_revise", "review_script", "review_video", "assets"])
    p = sub.add_parser("submit")
    p.add_argument("--job", required=True)
    p.add_argument("--kind", required=True, choices=["research", "script", "review_script", "review_video"])
    p.add_argument("--file")
    p = sub.add_parser("validate")
    p.add_argument("--job", required=True)
    p = sub.add_parser("attach")
    p.add_argument("--job", required=True)
    p.add_argument("--scene", required=True)
    p.add_argument("--file", required=True)
    p = sub.add_parser("redo")
    p.add_argument("--job", required=True)
    p.add_argument("--scene", required=True)
    p = sub.add_parser("revise-visual")
    p.add_argument("--job", required=True)
    p.add_argument("--scene", required=True)
    p.add_argument("--prompt")
    p.add_argument("--motion")
    p = sub.add_parser("render")
    p.add_argument("--job", required=True)
    p.add_argument("--budget", type=float, default=470)
    p = sub.add_parser("package")
    p.add_argument("--job", required=True)
    p = sub.add_parser("delivered")
    p.add_argument("--job", required=True)
    p.add_argument("--status", required=True, choices=["sent", "failed"])
    p.add_argument("--message-id")
    p = sub.add_parser("published")
    p.add_argument("--job", required=True)
    p = sub.add_parser("override")
    p.add_argument("--job", required=True)
    p.add_argument("--stage", required=True, choices=["script", "video"])
    p.add_argument("--quote", required=True, help="the human's own words, verbatim")
    p = sub.add_parser("cancel")
    p.add_argument("--job", required=True)
    p.add_argument("--reason", required=True)
    p = sub.add_parser("feedback")
    p.add_argument("--job", required=True)
    p.add_argument("--type", required=True, choices=["visual", "script"])
    p.add_argument("--scene", default="global")
    p.add_argument("--text", required=True)
    p = sub.add_parser("set")
    p.add_argument("--job", required=True)
    p.add_argument("--voice")
    p.add_argument("--rate", type=int)
    p.add_argument("--theme")
    p = sub.add_parser("status")
    p.add_argument("--job", required=True)
    p = sub.add_parser("list")
    p.add_argument("--limit", type=int, default=15)
    p = sub.add_parser("backlog")
    p.add_argument("action", choices=["add", "list", "take", "remove"])
    p.add_argument("--topic")
    p.add_argument("--brief")
    p.add_argument("--format", choices=sorted(FORMATS))
    p.add_argument("--id")
    p = sub.add_parser("config")
    p.add_argument("action", choices=["show", "set"])
    p.add_argument("assign", nargs="?")
    return parser


COMMANDS = {
    "init": cmd_init, "doctor": cmd_doctor, "new": cmd_new, "next": cmd_next, "emit": cmd_emit,
    "submit": cmd_submit, "validate": cmd_validate, "attach": cmd_attach, "redo": cmd_redo,
    "revise-visual": cmd_revise_visual, "render": cmd_render, "package": cmd_package,
    "delivered": cmd_delivered, "published": cmd_published, "override": cmd_override, "cancel": cmd_cancel,
    "status": cmd_status,
    "feedback": cmd_feedback, "set": cmd_set,
    "list": cmd_list, "backlog": cmd_backlog, "config": cmd_config,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except StudioError as exc:
        out(f"ERROR {exc}")
        return 1
    except Exception as exc:  # the agents parse ERROR lines; a traceback reads as noise
        out(f"ERROR unexpected {type(exc).__name__}: {exc}")
        return 2


def newer_install(script: Path | None = None) -> Path | None:
    """studio.py of the newest deployed version, when this is an older deployed one.

    A cron retry replays the agent's session, so the director kept calling version
    9's absolute paths after version 10 was deployed, and every command it ran
    printed version 9 paths again. The stage is derived from the job's artifacts,
    so handing the call to the newest version is safe at any point.
    """
    version_dir = (script or Path(__file__)).resolve().parent.parent
    if not version_dir.name.isdigit():
        return None
    newer = [p for p in version_dir.parent.iterdir()
             if p.name.isdigit() and int(p.name) > int(version_dir.name) and (p / "scripts" / "studio.py").is_file()]
    return max(newer, key=lambda p: int(p.name)) / "scripts" / "studio.py" if newer else None


if __name__ == "__main__":
    forward = newer_install()
    if forward:
        utf8 = ["-X", "utf8"] if sys.flags.utf8_mode else []
        os.execv(sys.executable, [sys.executable, *utf8, str(forward), *sys.argv[1:]])
    sys.exit(main())
