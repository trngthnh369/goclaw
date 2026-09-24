"""Deliverables for a finished render: per-platform post texts, sources, and the
review message the director sends.

The review message is ONE chat message (Discord caps at 2000 bytes and approval
binds to a single message). When Reels publishing is on, the message is also the
thing that gets published: the gateway posts the attached review cut and the text
between the [caption] and [/caption] lines, byte for byte, once a person approves
that exact message. So the caption is never trimmed here - the script validator
caps its size instead - and a message that cannot carry it whole is an error.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from .jobs import JobState, latest_review, load_state, script_review_sha
from .paths import JobPaths
from .util import StudioError, read_json, sha256_file, utc_now, write_json, write_text

MESSAGE_BUDGET = 1900          # bytes, below Discord's 2000
PLATFORMS = ("facebook", "tiktok", "instagram")
CAPTION_OPEN, CAPTION_CLOSE = "[caption]", "[/caption]"   # parsed by the gateway's Reels publisher
REELS_MIN_SECONDS, REELS_MAX_SECONDS = 3.0, 90.0
REELS_MIN_HEIGHT = 960


def _post(block: dict) -> str:
    tags = " ".join(block.get("hashtags", []))
    return (block.get("caption", "").strip() + ("\n\n" + tags if tags else "")).strip()


def _domain(url: str) -> str:
    host = urlparse(url).hostname or url
    return host[4:] if host.startswith("www.") else host


def build_posts(script: dict, research: dict) -> dict[str, str]:
    social = script.get("social", {})
    fb = social.get("facebook", {})
    posts = {p: _post(social.get(p) or fb) for p in PLATFORMS}
    yt = social.get("youtube") or {}
    title = yt.get("title") or script.get("title", "")
    description = yt.get("description") or fb.get("caption", "")
    sources = "\n".join(f"- {s.get('title', '').strip()}: {s.get('url')}" for s in research.get("sources", []))
    tags = ", ".join(yt.get("tags", []))
    posts["youtube"] = f"TITLE: {title}\n\nDESCRIPTION:\n{description}\n\nNguồn:\n{sources}\n\nTAGS: {tags}".strip()
    return posts


def reels_blockers(fmt_name: str, duration: float, cut: dict) -> list[str]:
    """Why this cut cannot be published as a Reel (empty when it can)."""
    blockers = []
    if fmt_name != "short":
        blockers.append(f"format {fmt_name} is not 9:16")
    if not REELS_MIN_SECONDS <= duration <= REELS_MAX_SECONDS:
        blockers.append(f"{duration:.0f}s is outside Reels' {REELS_MIN_SECONDS:.0f}-{REELS_MAX_SECONDS:.0f}s")
    if int(cut.get("height") or 0) < REELS_MIN_HEIGHT:
        blockers.append(f"review cut is {cut.get('width')}x{cut.get('height')}, below 540x960")
    return blockers


def review_message(state_title: str, job_id: str, qa: dict, fmt_name: str, voice: str,
                   caption: str, sources: list[dict], preview_path: str, *, publishable: bool,
                   overrides: list[str] | None = None) -> str:
    aspect = {"short": "9:16", "long": "16:9", "square": "1:1"}[fmt_name]
    voice_name = voice.split("-")[-1].replace("Neural", "")
    head = (f"🎬 Video Factory · {state_title}\n"
            f"⏱ {qa['duration']:.0f}s · {aspect} · giọng {voice_name} · {qa['loudness_lufs']:.1f} LUFS\n"
            f"job: {job_id}\n\n")
    for quote in overrides or []:
        head += f"⚠️ Review chưa đạt, được cho qua theo lời bạn: «{quote}»\n\n"
    if publishable:
        body = f"{CAPTION_OPEN}\n{caption.strip()}\n{CAPTION_CLOSE}"
        ask = ("Trả lời \"duyệt\" (hoặc thả ✅) để đăng Reels lên fanpage đúng video và caption này, "
               "hoặc \"sửa: <góp ý>\" để làm lại.")
    else:
        body = f"Caption gợi ý:\n{caption.strip()}"
        ask = "Đăng Reels tự động đang tắt: \"duyệt\" chỉ chốt bản cuối. \"sửa: <góp ý>\" để làm lại."
    media_line = f"\nMEDIA:{preview_path}"
    for count in (4, 2, 0):   # drop source names before ever touching the caption
        src = ("\n\nNguồn: " + " · ".join(f"{s.get('id')} {_domain(s.get('url', ''))}" for s in sources[:count])
               if count and sources else "")
        message = head + body + src + "\n" + ask + media_line
        if len(message.encode("utf-8")) <= MESSAGE_BUDGET:
            return message
    raise StudioError(f"the review message is {len(message.encode('utf-8'))} bytes even without sources; "
                      f"max {MESSAGE_BUDGET} - shorten script.social.facebook.caption and re-package")


def escalation_message(paths: JobPaths, issues: list[str]) -> str:
    """The question a person answers when a review will not converge.

    For the video stage the current review cut is attached, so the person judges
    the same pictures the reviewer did.
    """
    state = load_state(paths)
    meta = state.meta
    kind = meta.get("escalated_stage", "video")
    title = state.script.get("title", paths.job_id) if state.script_ok else paths.job_id
    manifest = read_json(paths.manifest) or {}
    media = (f"\nMEDIA:{stage_for_chat(paths)}"
             if kind == "video" and manifest.get("review_cut") and paths.preview.exists() else "")
    for count in (6, 3, 1):   # drop issues before ever dropping the question or the video
        text = "\n".join([f"⚠️ Video Factory · {title}", f"job: {paths.job_id}", "",
                          f"Review {'kịch bản' if kind == 'script' else 'video'} vẫn chưa đạt sau nhiều vòng sửa:",
                          *[f"- {issue[:300]}" for issue in issues[:count]], "",
                          "Bạn quyết định (trả lời tin này): \"cứ làm tiếp\" để bỏ qua review, "
                          "\"sửa: <hướng dẫn>\" để làm lại theo ý bạn, hoặc \"huỷ\"."]) + media
        if len(text.encode("utf-8")) <= MESSAGE_BUDGET:
            break
    return text


def override_quotes(paths: JobPaths, state: JobState, manifest: dict) -> list[str]:
    """The human's words behind each review that was passed by override for what ships."""
    current = [latest_review(paths, "script", script_review_sha(state.research, state.script)),
               latest_review(paths, "video", manifest["master_sha"])]
    return [r.get("_human_quote") or r.get("notes", "") for r in current if r and r.get("_override")]


def package(paths: JobPaths, *, publish_enabled: bool) -> dict:
    state = load_state(paths)
    manifest = read_json(paths.manifest)
    qa = read_json(paths.qa)
    if not manifest or not qa or not paths.master.exists():
        raise StudioError("nothing rendered yet - run render first")
    if not qa.get("ok"):
        raise StudioError("QA failed; fix the render before packaging: " + "; ".join(qa.get("hard", [])))
    if sha256_file(paths.master)[:16] != manifest["master_sha"]:
        raise StudioError("master.mp4 changed after render; re-render")
    cut = manifest.get("review_cut") or {}
    if not cut.get("sha256") or sha256_file(paths.preview) != cut["sha256"]:
        raise StudioError("the review cut does not match the render manifest; re-render")
    posts = build_posts(state.script, state.research)
    paths.deliver.mkdir(parents=True, exist_ok=True)
    for name, text in posts.items():
        write_text(paths.deliver / f"post-{name}.txt", text + "\n")
    sources_md = "\n".join(f"- [{s.get('id')}] {s.get('title', '')} - {s.get('url')}"
                           for s in state.research.get("sources", []))
    write_text(paths.deliver / "sources.md", sources_md + "\n")
    blockers = reels_blockers(state.fmt.name, float(qa.get("duration") or 0), cut)
    publishable = publish_enabled and not blockers
    staged = stage_for_chat(paths)
    message = review_message(state.script.get("title", paths.job_id), paths.job_id, qa, state.fmt.name,
                             manifest.get("voice", ""), posts["facebook"], state.research.get("sources", []),
                             str(staged), publishable=publishable,
                             overrides=override_quotes(paths, state, manifest))
    record = {
        "job": paths.job_id,
        "status": "packaged",
        "packaged_at": utc_now(),
        "master_sha": manifest["master_sha"],
        "master_sha256": sha256_file(paths.master),
        "preview_sha256": cut["sha256"],
        "reels_caption": posts["facebook"],
        "reels_publishable": publishable,
        "reels_blockers": blockers if publish_enabled else ["publishing is switched off"],
        "message": message,
        "staged_preview": str(staged),
    }
    write_json(paths.delivery, record)
    return record


def stage_for_chat(paths: JobPaths) -> Path:
    """Copy the preview where the message tool will attach it from.

    The message tool resolves MEDIA: paths against the calling agent's own
    workspace (plus /tmp), NOT against system_configs.allowed_paths - so a path in
    the studio workspace would go out as literal text. /tmp is the shared exception.
    """
    target_dir = Path(tempfile.gettempdir()) / "video-factory" / paths.job_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "preview.mp4"
    shutil.copyfile(paths.preview, target)
    return target
