"""Job state and the stage machine.

The stage is DERIVED from which artifacts exist and validate, never stored as a
flag a model could leave wrong: a run that dies mid-stage resumes exactly where
it stopped, and a truncated artifact is simply "not done yet". job.json only
records what cannot be derived - the request, revision counters, delivery and
approval facts, and an event history.

Reviews are bound to the sha of what they reviewed. The script review covers
research + everything spoken or shown as text; image prompts and motion are left
out of that hash, so the director can re-roll a picture without re-opening the
fact check. The video review is bound to the master file's sha.
"""

from __future__ import annotations

import copy
import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from . import RENDERER_VERSION
from .formats import FormatSpec, get_format
from .paths import JobPaths, Studio, studio_cmd
from .schema import (script_fact_refs, script_scene_ids, validate_research,
                     validate_review, validate_script)
from .textutil import slugify
from .util import (StudioError, canonical_json, read_json, sha256_file, sha256_text, utc_now, write_json,
                   write_json_numbered)

MAX_SCRIPT_REVISIONS = 2
MAX_VIDEO_REVISIONS = 2
VN_TZ = dt.timezone(dt.timedelta(hours=7))

TERMINAL = ("cancelled", "published", "escalated")


# --------------------------------------------------------------------------- job meta

def new_job_id(topic: str, now: dt.datetime | None = None) -> str:
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(VN_TZ)
    return f"vf-{now:%y%m%d-%H%M}-{slugify(topic, 18)}"


def create_job(studio: Studio, *, topic: str, brief: str, fmt: str, voice: str, rate: int,
               source: str, requested_by: str, theme: str | None = None) -> JobPaths:
    get_format(fmt)
    job_id = new_job_id(topic)
    paths = studio.job(job_id)
    suffix = 2
    while paths.root.exists():
        paths = studio.job(f"{job_id}-{suffix}")
        suffix += 1
    paths.root.mkdir(parents=True)
    meta = {
        "schema": "vf.job.v1",
        "id": paths.job_id,
        "created_at": utc_now(),
        "topic": topic.strip(),
        "brief": brief.strip(),
        "format": fmt,
        "voice": voice,
        "rate": rate,
        "theme": theme,
        "source": source,
        "requested_by": requested_by,
        "status": "active",
        "revisions": {"script": 0, "video": 0},
        "history": [{"at": utc_now(), "event": "created", "source": source}],
    }
    write_json(paths.meta, meta)
    return paths


def load_meta(paths: JobPaths) -> dict:
    meta = read_json(paths.meta)
    if not isinstance(meta, dict):
        raise StudioError(f"job {paths.job_id} not found (no job.json)")
    return meta


def save_meta(paths: JobPaths, meta: dict) -> None:
    write_json(paths.meta, meta)


def log_event(paths: JobPaths, meta: dict, event: str, **fields: Any) -> None:
    meta.setdefault("history", []).append({"at": utc_now(), "event": event, **fields})
    save_meta(paths, meta)


# --------------------------------------------------------------------------- hashes

def script_review_sha(research: Any, script: Any) -> str:
    """Hash of what the fact-check covers: everything except image prompts/motion."""
    stripped = copy.deepcopy(script) if isinstance(script, dict) else script
    if isinstance(stripped, dict):
        for key in ("image_style", "voice", "rate", "theme", "music"):   # presentation, not claims
            stripped.pop(key, None)
        for scene in stripped.get("scenes", []):
            if isinstance(scene, dict) and isinstance(scene.get("visual"), dict):
                visual = scene["visual"]
                if visual.get("kind") == "ai_image":
                    visual.pop("prompt", None)
                visual.pop("motion", None)
    return sha256_text(canonical_json({"research": research, "script": stripped}))[:16]


def image_prompt(scene: dict, script: dict, fmt: FormatSpec) -> str:
    """The exact prompt sent to create_image for an ai_image scene.

    The orientation hint asks for a full-bleed picture. It used to ask for "calm
    empty areas at top and bottom" to keep room for text, and the image model drew
    exactly that: a small scene with blurred bands and hard seams above and below
    it, on every picture of the first live run (2026-09-24). The headline and
    captions carry their own box and outline, so they need no empty background.
    """
    orientation = {"short": "vertical 9:16 full-bleed composition filling the whole frame edge to edge, "
                            "main subject in the centre, one continuous scene from top to bottom, no borders",
                   "long": "wide 16:9 full-bleed composition filling the whole frame, subject off-centre, no borders",
                   "square": "square 1:1 full-bleed composition filling the whole frame, subject centred, no borders"}[fmt.name]
    style = (script.get("image_style") or "").strip().rstrip(".")
    base = scene["visual"]["prompt"].strip().rstrip(".")
    # A director revising a picture has pasted the whole printed prompt back in,
    # which doubled the style and orientation text; add only what is missing.
    suffix = [part for part in (style, orientation, "no text, no letters, no captions, no logos, no watermark")
              if part and part.lower() not in base.lower()]
    return ", ".join([base, *suffix])


def prompt_sha(prompt: str) -> str:
    return sha256_text(prompt)[:16]


# --------------------------------------------------------------------------- state

@dataclass
class JobState:
    paths: JobPaths
    meta: dict
    fmt: FormatSpec
    research: Any = None
    script: Any = None
    research_errors: list[str] = field(default_factory=list)
    script_errors: list[str] = field(default_factory=list)
    script_warnings: list[str] = field(default_factory=list)

    @property
    def script_ok(self) -> bool:
        return (self.research is not None and self.script is not None
                and not self.research_errors and not self.script_errors)


def studio_lexicon(paths: JobPaths) -> dict[str, str]:
    """The studio's own pronunciation entries (studio.json "lexicon"), on top of the built-in ones.

    Read here, next to the job, so validation at submit time, the stage machine and
    the renderer all judge the narration with the same dictionary.
    """
    cfg = read_json(paths.root.parent.parent / "studio.json")
    lexicon = cfg.get("lexicon") if isinstance(cfg, dict) else None
    return {str(k): str(v) for k, v in lexicon.items()} if isinstance(lexicon, dict) else {}


def check_script(state: "JobState", doc: Any) -> tuple[list[str], list[str]]:
    """Validate a script for this job: its facts, pace, voice and pronunciation dictionary."""
    fact_ids = {f.get("id") for f in (state.research or {}).get("facts", []) if isinstance(f, dict)}
    probe = JobState(paths=state.paths, meta=state.meta, fmt=state.fmt, research=state.research, script=doc)
    return validate_script(doc, state.fmt, fact_ids, rate_percent=script_rate(probe),
                           voice=script_voice(probe), studio_lexicon=studio_lexicon(state.paths))


def load_state(paths: JobPaths) -> JobState:
    meta = load_meta(paths)
    fmt = get_format(meta["format"])
    state = JobState(paths=paths, meta=meta, fmt=fmt)
    state.research = read_json(paths.research)
    state.script = read_json(paths.script)
    if state.research is not None:
        state.research_errors, _ = validate_research(state.research)
    else:
        state.research_errors = ["research.json has not been submitted"]
    if state.script is not None:
        state.script_errors, state.script_warnings = check_script(state, state.script)
    else:
        state.script_errors = ["script.json has not been submitted"]
    return state


def overrides(state: JobState) -> dict:
    """Settings a human changed after seeing a cut; they win over the script's own."""
    return state.meta.get("overrides") or {}


def script_rate(state: JobState) -> int:
    if isinstance(overrides(state).get("rate"), int):
        return overrides(state)["rate"]
    rate = (state.script or {}).get("rate") if isinstance(state.script, dict) else None
    return int(rate if isinstance(rate, int) else state.meta.get("rate", 0))


def script_voice(state: JobState) -> str:
    script = state.script if isinstance(state.script, dict) else {}
    return (overrides(state).get("voice") or script.get("voice")
            or state.meta.get("voice") or "vi-VN-HoaiMyNeural")


def script_theme(state: JobState, default: str) -> str:
    return (overrides(state).get("theme") or (state.script or {}).get("theme")
            or state.meta.get("theme") or default)


def reviews(paths: JobPaths, stage: str) -> list[dict]:
    out = []
    if paths.reviews.exists():
        for file in sorted(paths.reviews.glob(f"{stage}-*.json")):
            doc = read_json(file)
            if isinstance(doc, dict):
                doc["_file"] = file.name
                out.append(doc)
    out.sort(key=lambda d: d.get("_submitted_at", ""))
    return out


def latest_review(paths: JobPaths, stage: str, target_sha: str) -> dict | None:
    matching = [r for r in reviews(paths, stage) if r.get("_target_sha") == target_sha]
    return matching[-1] if matching else None


def asset_record(paths: JobPaths, scene_id: str) -> dict | None:
    return read_json(paths.assets / f"{scene_id}.json")


def missing_images(state: JobState) -> list[dict]:
    """ai_image scenes whose attached picture does not match the current prompt."""
    todo = []
    for scene in state.script.get("scenes", []):
        if scene.get("visual", {}).get("kind") != "ai_image":
            continue
        prompt = image_prompt(scene, state.script, state.fmt)
        record = asset_record(state.paths, scene["id"])
        file_ok = record and (state.paths.assets / record.get("file", "")).exists()
        if not file_ok or record.get("prompt_sha") != prompt_sha(prompt):
            todo.append({"scene": scene["id"], "prompt": prompt})
    return todo


def render_input_sha(state: JobState, render_config: dict) -> str:
    assets = {}
    for scene in state.script.get("scenes", []):
        record = asset_record(state.paths, scene["id"])
        if record:
            assets[scene["id"]] = record.get("sha256")
    payload = {"v": RENDERER_VERSION, "script": state.script, "assets": assets, "config": render_config,
               "voice": script_voice(state), "rate": script_rate(state),
               "theme": script_theme(state, render_config.get("default_theme", "midnight"))}
    return sha256_text(canonical_json(payload))[:16]


# --------------------------------------------------------------------------- next

def _delegate(agent: str, task: str, timeout: int = 600) -> dict:
    return {"agent_key": agent, "mode": "sync", "timeout": timeout, "task": task}


def _stage_task(stage: str, job_id: str, lines: list[str]) -> str:
    cmd = studio_cmd()
    head = [f"VF_STAGE: {stage}", f"JOB: {job_id}",
            f"STUDIO: {cmd}",
            f"1. Read your material: {cmd} emit --job {job_id} --stage {stage}"]
    # DONE, not PASS: a reviewer that had just recorded REVISE was told to end on
    # "PASS" and re-submitted its verdict as PASS to match (2026-09-24).
    tail = [f"Last line of your reply must be exactly: STAGE_RESULT: {stage} DONE"
            f" (or STAGE_RESULT: {stage} FAILED <reason> if you could not finish)."]
    return "\n".join(head + lines + tail)


def submit_steps(paths: JobPaths, kind: str) -> str:
    """How an agent hands a JSON artifact to the studio.

    Through a file, never a heredoc: exec's shell guard scans the whole command
    line, so JSON mentioning "su", "host" or "mount" was refused as a command
    (the first live script was blocked on `\\bsu\\b`). deliver:false because
    write_file otherwise attaches the file to the run's reply as a deliverable.
    """
    inbox = paths.inbox_file(kind).as_posix()
    return (f"write the JSON with write_file (path {inbox}, deliver: false), then straight away run: "
            f"{studio_cmd()} submit --job {paths.job_id} --kind {kind} --file {inbox} "
            f"(rewrite the file only to fix what submit reports)")


def next_action(studio: Studio, paths: JobPaths, render_config: dict) -> dict:
    """The single decision point. Returns what to do next and who does it."""
    state = load_state(paths)
    meta = state.meta
    job_id = paths.job_id
    cmd = studio_cmd()
    base = {"job": job_id, "format": state.fmt.name, "status": meta.get("status")}

    if meta.get("status") == "escalated":
        return {**base, "stage": "escalated", "owner": "human", "action": "stop",
                "say": f"Job {job_id} waits for the human's decision on the {meta.get('escalated_stage')} review. "
                       "Act only on the human's own message in this conversation, with one of human_commands.",
                "human_commands": _human_decision_commands(paths, meta.get("escalated_stage", "video"))}
    if meta.get("status") in TERMINAL:
        return {**base, "stage": meta["status"], "owner": None, "action": "stop",
                "say": f"Job {job_id} is {meta['status']}; nothing to do."}
    if meta.get("status") == "awaiting_approval":
        return {**base, "stage": "awaiting_approval", "owner": "human", "action": "stop",
                "say": "Delivered for review; waiting for the human to approve or request changes."}

    # 1. research + script -------------------------------------------------------
    if not state.script_ok:
        stage = "script_revise" if reviews(paths, "script") or reviews(paths, "video") else "script"
        errs = state.research_errors + state.script_errors
        note = [] if state.research is None and state.script is None else \
            [f"Current validation errors ({len(errs)}): " + " | ".join(errs[:8])]
        task = _stage_task(stage, job_id, [
            "2. Research with web_search/web_fetch, then write research.json and script.json exactly as emit describes.",
            f"3. research.json: {submit_steps(paths, 'research')}",
            f"4. script.json: {submit_steps(paths, 'script')}",
            "5. Fix every error submit prints and submit again until both print PASS.",
            *note,
        ])
        return {**base, "stage": stage, "owner": "vf-scriptwriter", "action": "delegate",
                "delegate": _delegate("vf-scriptwriter", task)}

    review_sha = script_review_sha(state.research, state.script)
    review = latest_review(paths, "script", review_sha)
    if review is None:
        task = _stage_task("review_script", job_id, [
            "2. Verify every cited fact against its source (web_fetch the URL); check clarity, hook and policy risks.",
            f"3. Decide, then submit ONE verdict: {submit_steps(paths, 'review_script')}",
            "4. It prints RECORDED once the verdict is stored. That verdict is final for this script version - "
            "do not submit again. If it prints FAIL, fix the format errors listed and submit again.",
        ])
        return {**base, "stage": "review_script", "owner": "vf-reviewer", "action": "delegate",
                "delegate": _delegate("vf-reviewer", task)}
    if review.get("verdict") == "REVISE":
        if not review.get("_human") and meta["revisions"].get("script", 0) >= MAX_SCRIPT_REVISIONS:
            return _escalate(base, paths, "script", review)
        task = _stage_task("script_revise", job_id, [
            "2. The reviewer asked for changes (emit prints them). Fix every blocker/major issue; keep what passed.",
            f"3. If facts changed, research.json: {submit_steps(paths, 'research')}",
            f"4. script.json: {submit_steps(paths, 'script')}",
        ])
        return {**base, "stage": "script_revise", "owner": "vf-scriptwriter", "action": "delegate",
                "delegate": _delegate("vf-scriptwriter", task)}

    # 2. images ------------------------------------------------------------------
    todo = missing_images(state)
    if todo:
        calls = []
        for item in todo:
            calls.append({
                "scene": item["scene"],
                "create_image": {"prompt": item["prompt"], "aspect_ratio": state.fmt.image_aspect,
                                 "filename_hint": f"{job_id}-{item['scene']}"},
                "then_exec": f"{cmd} attach --job {job_id} --scene {item['scene']} --file <MEDIA path returned by create_image>",
            })
        return {**base, "stage": "assets", "owner": "vf-director", "action": "run",
                "say": f"Generate {len(todo)} image(s): one create_image call per item, then attach it. "
                       f"Look at each picture with read_image first; if it has garbled text, extra limbs, "
                       f"does not match the scene, or is a picture inside a picture (blurred or different "
                       f"bands at the edges), re-roll it (same prompt) before attaching.",
                "calls": calls, "then": f"{cmd} next --job {job_id}"}

    # 3. render ------------------------------------------------------------------
    manifest = read_json(paths.manifest) or {}
    want = render_input_sha(state, render_config)
    master_ok = paths.master.exists() and manifest.get("input_sha") == want and manifest.get("qa_ok") is True
    if not master_ok:
        return {**base, "stage": "render", "owner": "vf-director", "action": "run",
                "say": "Render the video. The command is resumable: if it prints PARTIAL, run it again.",
                "exec": f"{cmd} render --job {job_id}", "then": f"{cmd} next --job {job_id}"}

    master_sha = manifest["master_sha"]
    vreview = latest_review(paths, "video", master_sha)
    if vreview is None:
        task = _stage_task("review_video", job_id, [
            "2. In ONE turn, call read_image once for every line under READ in emit's output (the contact sheet "
            "and every scene frame, each with its printed prompt); they run in parallel. Do not run emit again.",
            f"3. Decide, then submit ONE verdict: {submit_steps(paths, 'review_video')}",
            "4. It prints RECORDED once the verdict is stored. That verdict is final for this cut - "
            "do not submit again. If it prints FAIL, fix the format errors listed and submit again.",
        ])
        return {**base, "stage": "review_video", "owner": "vf-reviewer", "action": "delegate",
                "delegate": _delegate("vf-reviewer", task)}
    if vreview.get("verdict") == "REVISE":
        if not vreview.get("_human") and meta["revisions"].get("video", 0) >= MAX_VIDEO_REVISIONS:
            return _escalate(base, paths, "video", vreview)
        issues = [i for i in vreview.get("issues", []) if i.get("severity") in ("blocker", "major")]
        text_issues = [i for i in issues if i.get("type") in ("fact", "text", "clarity", "timing", "policy", "audio")]
        if text_issues:
            task = _stage_task("script_revise", job_id, [
                "2. The video review found problems in what is said or shown as text (emit prints them). "
                "Fix them in script.json; keep image prompts unless an issue names them.",
                f"3. script.json: {submit_steps(paths, 'script')}",
            ])
            return {**base, "stage": "script_revise", "owner": "vf-scriptwriter", "action": "delegate",
                    "delegate": _delegate("vf-scriptwriter", task), "video_revision": True}
        scenes = sorted({i.get("scene") for i in issues if i.get("scene") != "global"})
        return {**base, "stage": "fix_visuals", "owner": "vf-director", "action": "run",
                "say": "The video review wants new pictures. For each scene below, either re-roll the image "
                       "(redo) or rewrite its prompt (revise-visual), then continue.",
                "issues": issues,
                "commands": [f"{cmd} redo --job {job_id} --scene <id>",
                             f"{cmd} revise-visual --job {job_id} --scene <id> --prompt \"<new English prompt, no text>\""],
                "scenes": scenes, "then": f"{cmd} next --job {job_id}"}

    # 4. deliver -----------------------------------------------------------------
    delivery = read_json(paths.delivery) or {}
    if delivery.get("status") == "sent" and delivery.get("master_sha") == master_sha:
        meta["status"] = "awaiting_approval"
        save_meta(paths, meta)
        return {**base, "status": "awaiting_approval", "stage": "awaiting_approval", "owner": "human",
                "action": "stop", "say": "Delivered for review; waiting for the human."}
    return {**base, "stage": "deliver", "owner": "vf-director", "action": "run",
            "say": "Package the deliverables, send the review message exactly as printed, then record it.",
            "exec": f"{cmd} package --job {job_id}",
            "then": f"{cmd} delivered --job {job_id} --status sent"}


def _escalate(base: dict, paths: JobPaths, kind: str, review: dict) -> dict:
    """Stop the job until a person decides.

    The status is stored, so every later `next` stops too. It used to be a stage
    only, and a director in a cron run, with no person in the conversation, ran
    `override` itself four seconds after being told to ask (2026-09-24).
    """
    issues = [f"[{i.get('scene')}] {i.get('problem')}" for i in review.get("issues", [])
              if i.get("severity") in ("blocker", "major")]
    rounds = MAX_SCRIPT_REVISIONS if kind == "script" else MAX_VIDEO_REVISIONS
    meta = load_meta(paths)
    if meta.get("status") != "escalated":
        meta["status"] = "escalated"
        meta["escalated_stage"] = kind
        log_event(paths, meta, "escalated", stage=kind, issues=issues[:6])
    return {**base, "status": "escalated", "stage": "escalate", "owner": "vf-director", "action": "ask_human",
            "say": f"The {kind} review still says REVISE after {rounds} rounds, so the job now waits for the human. "
                   "Tell the human, in Vietnamese, what is unresolved (issues) and the choices: continue anyway, "
                   "give direction, or cancel. Then end your run. Only the human's own later message decides; "
                   "a VF_CRON run never overrides or cancels.",
            "issues": issues[:6]}


def _human_decision_commands(paths: JobPaths, kind: str) -> dict[str, str]:
    cmd, job = studio_cmd(), paths.job_id
    direction = (f"{cmd} feedback --job {job} --type visual|script --scene <id|global> --text \"<direction>\""
                 if kind == "video" else
                 f"{cmd} cancel --job {job} --reason \"<direction>\", then {cmd} new --topic \"<topic>\" "
                 "--brief \"<the human's direction>\"")
    return {
        "continue anyway": f"{cmd} override --job {job} --stage {kind}   (the gateway supplies the person's words)",
        "give direction": direction,
        "cancel": f"{cmd} cancel --job {job} --reason \"<the human's words>\"",
    }


# --------------------------------------------------------------------------- submit reviews

def submit_review(paths: JobPaths, kind: str, doc: Any) -> tuple[list[str], list[str], dict | None]:
    """Record a model's verdict. Returns (errors, warnings, already_recorded).

    The first well-formed verdict for a version is final. The first live run showed
    why: one reviewer call recorded REVISE, PASS, REVISE, REVISE, PASS on the same
    script within two minutes, and because the latest one counted, it talked itself
    out of its own blockers. A human can still override or send feedback.
    """
    state = load_state(paths)
    stage = "script" if kind == "review_script" else "video"
    if not state.script_ok:
        return ["the script is not in a valid state; there is nothing to review yet"], [], None
    if stage == "script":
        target = script_review_sha(state.research, state.script)
        required = script_fact_refs(state.script)
    else:
        manifest = read_json(paths.manifest) or {}
        if not manifest.get("master_sha") or not paths.master.exists():
            return ["there is no rendered master to review yet"], [], None
        if sha256_file(paths.master)[:16] != manifest["master_sha"]:
            return ["master.mp4 changed since the manifest was written; re-render first"], [], None
        target = manifest["master_sha"]
        required = set()
    prior = [r for r in reviews(paths, stage) if r.get("_target_sha") == target
             and not r.get("_human") and not r.get("_override")]
    if prior:
        return [], [], prior[0]
    errors, warnings = validate_review(doc, stage, required_facts=required,
                                       scene_ids=script_scene_ids(state.script))
    if errors:
        return errors, warnings, None
    doc = dict(doc)
    doc["_target_sha"] = target
    doc["_submitted_at"] = utc_now()
    write_json_numbered(paths.reviews, stage, len(reviews(paths, stage)) + 1, doc)
    meta = load_meta(paths)
    log_event(paths, meta, f"review_{stage}", verdict=doc["verdict"], target=target)
    return [], warnings, None


def note_script_submitted(paths: JobPaths) -> None:
    """Count a revision when a new script replaces one that a MODEL review sent back.

    Rounds asked for by the human are not counted: the cap exists to stop two
    models from arguing forever, not to limit how often a person can ask for changes.
    """
    meta = load_meta(paths)
    state = load_state(paths)
    if not state.script_ok:
        return
    sha = script_review_sha(state.research, state.script)
    prior = reviews(paths, "script")
    if prior and prior[-1].get("verdict") == "REVISE" and not prior[-1].get("_human") \
            and prior[-1].get("_target_sha") != sha and meta.get("_last_counted_script_sha") != sha:
        meta["revisions"]["script"] = meta["revisions"].get("script", 0) + 1
        meta["_last_counted_script_sha"] = sha
    log_event(paths, meta, "script_submitted", sha=sha)


def note_render(paths: JobPaths, master_sha: str) -> None:
    meta = load_meta(paths)
    prior = reviews(paths, "video")
    if prior and prior[-1].get("verdict") == "REVISE" and not prior[-1].get("_human") \
            and prior[-1].get("_target_sha") != master_sha:
        meta["revisions"]["video"] = meta["revisions"].get("video", 0) + 1
    log_event(paths, meta, "rendered", master_sha=master_sha)


def record_human_feedback(paths: JobPaths, kind: str, scene: str, text: str) -> None:
    """Turn a person's change request on a delivered video into a REVISE review.

    kind "visual" re-rolls or re-prompts pictures (director); kind "script" sends
    the words back to the scriptwriter, which also re-opens the fact check.
    """
    manifest = read_json(paths.manifest) or {}
    if not manifest.get("master_sha"):
        raise StudioError("there is no rendered video to give feedback on yet")
    state = load_state(paths)
    scene_ids = [s["id"] for s in state.script.get("scenes", [])] if state.script_ok else []
    if scene != "global" and scene not in scene_ids:
        raise StudioError(f"scene must be one of {', '.join(scene_ids)} or global")
    issue = {"scene": scene, "severity": "blocker", "type": "visual" if kind == "visual" else "text",
             "problem": f"Human feedback: {text}", "fix": text}
    # People send change requests one message at a time. Each used to become its own
    # review, and only the newest one counts, so the first request was dropped
    # (2026-09-24: s5 lost to s9). Requests on the same cut now add up.
    current = latest_review(paths, "video", manifest["master_sha"])
    if current and current.get("_human"):
        target = paths.reviews / current.pop("_file")
        current["issues"].append(issue)
        current["_submitted_at"] = utc_now()
        write_json(target, current)
    else:
        write_json_numbered(paths.reviews, "video", len(reviews(paths, "video")) + 1, {
            "schema": "vf.review.v1", "stage": "video", "verdict": "REVISE", "issues": [issue],
            "checked_scenes": scene_ids, "_human": True,
            "_target_sha": manifest["master_sha"], "_submitted_at": utc_now()})
    meta = load_meta(paths)
    meta["status"] = "active"
    log_event(paths, meta, "human_feedback", kind=kind, scene=scene, text=text[:300])
