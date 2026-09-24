"""What `studio.py emit` prints for each stage: the complete material an agent
needs, so a stage never depends on the agent remembering earlier context.

Examples are written WITH Vietnamese diacritics on purpose: an unaccented example
once taught a model to drop accents across a whole pipeline (comingwave G2).
Example URLs live on example.org so a copied example is caught at review.
"""

from __future__ import annotations

import datetime as dt
import json

from .formats import FormatSpec
from .jobs import VN_TZ, JobState, image_prompt, reviews, script_review_sha, submit_steps
from .paths import studio_cmd
from .schema import script_fact_refs
from .textutil import SYLLABLES_PER_SECOND
from .tts import VI_KNOWN_LOANWORDS, VI_LEXICON
from .util import read_json

EXAMPLE_RESEARCH = {
    "schema": "vf.research.v1",
    "topic": "Vì sao chúng ta hay trì hoãn",
    "angle": "Trì hoãn là cách não né cảm xúc khó chịu, không phải do lười - nên cách chữa là xử lý cảm xúc.",
    "audience": "Người đi làm 20-35 tuổi hay bị deadline dí",
    "sources": [
        {"id": "S1", "url": "https://example.org/pychyl-emotion-regulation", "title": "Procrastination and emotion regulation",
         "publisher": "Example Journal", "date": "2013"},
        {"id": "S2", "url": "https://example.org/ferrari-chronic-procrastination", "title": "Still Procrastinating?",
         "publisher": "Example Press", "date": "2010"},
    ],
    "facts": [
        {"id": "F1", "claim": "Trì hoãn là một vấn đề điều tiết cảm xúc hơn là quản lý thời gian.",
         "source_ids": ["S1"], "quote": "procrastination is an emotion-regulation problem, not a time-management problem",
         "confidence": "high"},
        {"id": "F2", "claim": "Khoảng 20% người trưởng thành trì hoãn kinh niên.",
         "source_ids": ["S2"], "quote": "about 20 percent of adults are chronic procrastinators", "confidence": "high"},
    ],
    "caveats": ["Tỷ lệ 20% là ước tính ở Mỹ, không phải số liệu Việt Nam."],
}

EXAMPLE_SCRIPT = {
    "schema": "vf.script.v1",
    "title": "Vì sao chúng ta hay trì hoãn?",
    "format": "short",
    "theme": "midnight",
    "image_style": "cinematic editorial illustration, soft volumetric light, muted teal and amber palette",
    "music": "auto",
    "scenes": [
        {"id": "s1", "role": "hook", "narration": "Bạn trì hoãn không phải vì lười.",
         "on_screen": "Không phải vì lười",
         "visual": {"kind": "ai_image", "motion": "zoom_in",
                    "prompt": "a young Vietnamese office worker staring at a laptop late at night, "
                              "an untouched checklist beside the keyboard, no text"},
         "fact_ids": ["F1"]},
        {"id": "s2", "role": "body",
         "narration": "Các nhà tâm lý học gọi đó là cách não né một cảm xúc khó chịu.",
         "on_screen": "Né cảm xúc, không né việc", "emphasis": ["cảm xúc khó chịu"],
         "visual": {"kind": "ai_image", "motion": "pan_right",
                    "prompt": "a person turning away from a dark storm cloud shaped like a calendar, "
                              "symbolic, no text"},
         "fact_ids": ["F1"]},
        {"id": "s3", "role": "body",
         "narration": "Cứ 5 người trưởng thành thì có 1 người trì hoãn kinh niên.",
         "visual": {"kind": "card", "card": {"layout": "stat", "value": "1/5",
                                             "label": "người trưởng thành trì hoãn kinh niên",
                                             "source": "Ferrari, 2010"}},
         "fact_ids": ["F2"]},
        {"id": "s4", "role": "body",
         "narration": "Khi né được việc khó, bạn thấy nhẹ nhõm ngay. Não nhớ cảm giác đó và lặp lại.",
         "on_screen": "Nhẹ nhõm = phần thưởng", "emphasis": ["nhẹ nhõm"],
         "visual": {"kind": "ai_image", "motion": "zoom_out",
                    "prompt": "a person exhaling with relief on a sofa while a pile of paperwork looms in "
                              "the background, warm lamp light, no text"},
         "fact_ids": ["F1"]},
        {"id": "s5", "role": "body", "narration": "Vậy chữa thế nào? Hãy thử ba bước nhỏ.",
         "visual": {"kind": "card", "card": {"layout": "steps", "title": "3 bước gỡ trì hoãn",
                                             "steps": ["Gọi tên cảm xúc đang né",
                                                       "Chia việc thành bước năm phút",
                                                       "Bắt đầu, chưa cần làm hay"]}},
         "fact_ids": ["F1"]},
        {"id": "s6", "role": "body",
         "narration": "Bước một, gọi tên cảm xúc bạn đang né: sợ sai, chán, hay quá tải.",
         "on_screen": "Sợ sai? Chán? Quá tải?",
         "visual": {"kind": "ai_image", "motion": "pan_left",
                    "prompt": "three translucent masks floating above a desk, each showing a different "
                              "emotion, soft studio light, no text"},
         "fact_ids": ["F1"]},
        {"id": "s7", "role": "body",
         "narration": "Bước hai, chia việc thành bước năm phút. Bước ba, cứ bắt đầu, chưa cần làm hay.",
         "on_screen": "Bắt đầu nhỏ thôi",
         "visual": {"kind": "ai_image", "motion": "pan_up",
                    "prompt": "a hand placing the first small domino in a long line of dominoes, "
                              "shallow depth of field, no text"}},
        {"id": "s8", "role": "cta", "narration": "Lưu video này lại cho lần trì hoãn tới nhé.",
         "on_screen": "Lưu lại cho lần sau",
         "visual": {"kind": "ai_image", "motion": "zoom_in",
                    "prompt": "a calm sunrise over a tidy desk with a single open notebook, hopeful mood, no text"}},
    ],
    "social": {
        "facebook": {"caption": "Trì hoãn không phải do lười. Đó là cách não né cảm xúc khó chịu. "
                                "Thử 3 bước nhỏ trong video và kể mình nghe bạn hay trì hoãn việc gì nhất?",
                     "hashtags": ["#tamly", "#kienthuc", "#trihoan"]},
        "tiktok": {"caption": "Bạn trì hoãn vì né cảm xúc chứ không phải vì lười", "hashtags": ["#learnontiktok", "#tamly"]},
        "youtube": {"title": "Vì sao chúng ta hay trì hoãn? #shorts",
                    "description": "Trì hoãn là vấn đề cảm xúc, không phải quản lý thời gian.", "tags": ["trì hoãn", "tâm lý"]},
    },
}

REVIEW_EXAMPLE = {
    "schema": "vf.review.v1",
    "stage": "script",
    "verdict": "REVISE",
    "issues": [
        {"scene": "s3", "severity": "blocker", "type": "fact",
         "problem": "Nguồn S2 nói 20% người trưởng thành ở Mỹ, lời đọc nói chung chung như số liệu toàn cầu.",
         "fix": "Thêm 'ở Mỹ' vào lời đọc và nhãn card, hoặc tìm nguồn có phạm vi rộng hơn."},
        {"scene": "s1", "severity": "minor", "type": "clarity",
         "problem": "Hook ổn nhưng có thể mạnh hơn bằng câu hỏi.", "fix": "Cân nhắc: 'Bạn nghĩ mình trì hoãn vì lười?'"},
    ],
    "checked_facts": ["F1", "F2"],
    "notes": "Cấu trúc và nhịp tốt.",
}


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1)


def _fmt_rules(fmt: FormatSpec, rate: int) -> str:
    sps = SYLLABLES_PER_SECOND * (1 + rate / 100)
    lo, hi = fmt.target_total
    return (f"Format {fmt.name}: {fmt.size}, {fmt.min_scenes}-{fmt.max_scenes} scenes, "
            f"<= {fmt.max_scene_syllables} syllables (Vietnamese words) per scene. "
            f"Voice speed ~{sps:.1f} syllables/s, so {lo:.0f}-{hi:.0f} s = about "
            f"{int(lo * sps)}-{int(hi * sps)} syllables in total (hard limits {fmt.min_total:.0f}-{fmt.max_total:.0f} s).")


CRAFT_RULES = """\
Craft rules (these decide whether people keep watching):
- s1 is the hook: <= 18 syllables, lands in under 4 seconds - a surprising fact, a sharp question or a bold claim. No greeting, no channel intro, no "Xin chào".
- One idea per scene; the picture changes every 3-6 s. Short spoken Vietnamese sentences, active voice.
- Numbers only when a research fact backs them (fact_ids). Write them the way they should be read ("70 nghìn", "2,5 giờ").
- The Vietnamese voice reads every word with Vietnamese spelling: raw "AI" sounds like "ai" (who), "web" like "ốp". Words it already knows: {lexicon}. For ANY other foreign word, name or acronym, add "tts_text" to that scene: the whole narration as it should be spoken, with the foreign words respelled as Vietnamese syllables ("CEO" -> "xi i âu", "Netflix" -> "nét phờ lích"). Captions keep showing "narration". Submit rejects a scene whose spoken text still has a foreign word.
- on_screen is an optional headline (max 2 lines) that ADDS a keyword, number or question - never a copy of the narration. Leave it empty on card scenes (the card is the text).
- Deliver the payoff before the end. The last scene has role "cta": one concrete ask (save, follow, or answer a specific question in the comments).
- Visuals: ai_image = English prompt describing subject, setting, light and mood, ALWAYS ending with "no text"; card = info-dense beats (stat, list, steps, compare, quote, code, title); screenshot = public URL, only for tutorials about a real website or tool. For a 45 s short about 5-7 ai_image and 2-4 cards works well; do not put two cards back to back.
- Image models cannot write: a picture whose subject is writing (a map with labels, a phone showing an app, street signs, a page of text) comes back with garbled pseudo-text and gets re-rolled or rejected. Show such things from a distance, blurred, or as a metaphor - and put the real words on a card or a screenshot instead.
- No emoji anywhere in narration, on_screen or cards (the video fonts cannot draw them). Emoji are fine in social captions.
- Cards and on_screen are written in Vietnamese with every accent, exactly like the narration ("Lịch sử clipboard", never "Lich su clipboard"): the video fonts draw every Vietnamese letter.
- Never invent statistics, quotes or studies. Every fact needs a source you opened. If evidence is thin, make fewer claims."""


def emit_script(state: JobState, revise: bool) -> str:
    meta = state.meta
    cmd = studio_cmd()
    rate = int(meta.get("rate", 0))
    parts = [
        f"# Video Factory brief - job {meta['id']}",
        f"Topic: {meta['topic']}",
        f"Requester notes: {meta.get('brief') or '(none)'}",
        _fmt_rules(state.fmt, rate),
        f"Voice: {meta.get('voice')} at {rate:+d}%. Audience: Vietnamese viewers on TikTok, Facebook Reels, YouTube Shorts.",
        f"Today is {dt.datetime.now(VN_TZ):%Y-%m-%d}; prefer sources from the last two years for anything that changes fast.",
        "",
        CRAFT_RULES.format(lexicon=", ".join(sorted(set(VI_LEXICON) | VI_KNOWN_LOANWORDS, key=str.lower))),
    ]
    if revise:
        parts += ["", "## REVISION - fix these, keep everything that already works"]
        for kind in ("script", "video"):
            done = reviews(state.paths, kind)
            if done and done[-1].get("verdict") == "REVISE":
                for issue in done[-1].get("issues", []):
                    parts.append(f"- [{kind} review][{issue.get('severity')}][{issue.get('scene')}] "
                                 f"{issue.get('problem')} -> FIX: {issue.get('fix')}")
        errors = [e for e in state.research_errors + state.script_errors if "not been submitted" not in e]
        for error in errors[:15]:
            parts.append(f"- [validator] {error}")
        if state.script is not None:
            parts += ["", "## Current script.json (edit it; do not start over)", _dump(state.script)]
        if state.research is not None:
            parts += ["", "## Current research.json", _dump(state.research)]
    else:
        if state.research is not None and not state.research_errors:
            parts += ["", "## research.json is ALREADY SUBMITTED (a previous run was interrupted). Reuse it - "
                          "resubmit only if you add or fix facts - and go straight to script.json:",
                      _dump(state.research)]
        parts += [
            "",
            "## Step 1 - research.json (schema vf.research.v1). FORMAT EXAMPLE ONLY - do not copy its content or URLs:",
            _dump(EXAMPLE_RESEARCH),
            "",
            "## Step 2 - script.json (schema vf.script.v1). FORMAT EXAMPLE ONLY:",
            _dump(EXAMPLE_SCRIPT),
            "",
            "Card layouts and fields: title{title,subtitle?} list{title?,items[2-5]} stat{value<=14 chars,label,source?} "
            "quote{quote,author?} steps{title?,steps[2-5]} compare{left{title,items[1-4]},right{...}} code{title?,code}.",
            "Motions: auto, zoom_in, zoom_out, pan_left, pan_right, pan_up, pan_down, still (screenshots: scroll or still).",
            "Themes: midnight, ocean, sunset, forest, paper. Music: auto (a licensed track from the studio library when one exists, otherwise voice only) or none.",
            "social.facebook is required (it becomes the Reels caption); tiktok/instagram/youtube are optional.",
        ]
    parts += [
        "",
        "## Submit (each submit validates; fix every error it prints and submit again until it prints PASS)",
        f"- research.json: {submit_steps(state.paths, 'research')}",
        f"- script.json: {submit_steps(state.paths, 'script')}",
        "Never write into the job folder itself; submit is the only way in.",
    ]
    return "\n".join(parts)


def emit_review_script(state: JobState) -> str:
    script = state.script
    facts = {f["id"]: f for f in state.research.get("facts", [])}
    sources = {s["id"]: s for s in state.research.get("sources", [])}
    lines = [f"# Script review - job {state.meta['id']} (review sha {script_review_sha(state.research, script)})",
             f"Topic: {state.meta['topic']} | angle: {state.research.get('angle', '')}",
             f"Requester notes: {state.meta.get('brief') or '(none)'}",
             f"Today is {dt.datetime.now(VN_TZ):%Y-%m-%d}. Your own knowledge may be out of date: judge each claim by "
             "what its source says, never reject a product, model or event only because you do not recognise it.",
             "",
             "## Scenes"]
    for scene in script["scenes"]:
        visual = scene["visual"]
        lines.append(f"[{scene['id']}] ({scene.get('role', 'body')}) NARRATION: {scene['narration']}")
        if scene.get("tts_text"):
            lines.append(f"    SPOKEN AS: {scene['tts_text']}")
        if scene.get("on_screen"):
            lines.append(f"    ON SCREEN: {scene['on_screen']}")
        if visual["kind"] == "card":
            lines.append(f"    CARD: {json.dumps(visual['card'], ensure_ascii=False)}")
        elif visual["kind"] == "screenshot":
            lines.append(f"    SCREENSHOT: {visual.get('url')}")
        else:
            lines.append(f"    IMAGE: {visual.get('prompt', '')[:160]}")
        lines.append(f"    FACTS: {', '.join(scene.get('fact_ids', [])) or '-'}")
    lines += ["", "## Facts cited by the script (verify each one against its source)"]
    for fid in sorted(script_fact_refs(script), key=lambda x: int(x[1:])):
        fact = facts.get(fid, {})
        lines.append(f"{fid}: {fact.get('claim')} [{fact.get('confidence', 'high')}]")
        if fact.get("quote"):
            lines.append(f"    quote: \"{fact['quote']}\"")
        for sid in fact.get("source_ids", []):
            src = sources.get(sid, {})
            lines.append(f"    {sid}: {src.get('title')} - {src.get('url')}")
    if state.research.get("caveats"):
        lines += ["", "Writer's caveats: " + " | ".join(state.research["caveats"])]
    social = script.get("social", {}).get("facebook", {})
    lines += ["", f"Reels caption: {social.get('caption', '')} {' '.join(social.get('hashtags', []))}",
              "",
              "## Checklist",
              "1. Each cited fact: open the URL (web_fetch) and confirm the claim AND its scope (country, year, population). "
              "Unsupported, overstated or out-of-scope = blocker. A source on example.org or one that does not load = blocker.",
              "2. Numbers in narration/on_screen/cards match the facts exactly.",
              "3. Hook: would a scrolling viewer stop in the first 3 seconds? Flow: one idea per scene, payoff, clear CTA.",
              "4. Natural spoken Vietnamese. SPOKEN AS lines are what the voice says; the studio already rejects "
              "foreign words that are not respelled, so judge only whether a respelling sounds natural.",
              "5. Policy: no medical/financial/legal advice presented as certain, no defamation, no copyrighted characters or real people in image prompts.",
              "Severity: blocker = must not publish; major = clearly hurts quality; minor = optional polish.",
              "PASS only when there are no blocker/major issues. checked_facts must list every fact id above.",
              "Decide once. Your first recorded verdict is final for this script version.",
              "",
              "## Review schema (vf.review.v1) - EXAMPLE",
              _dump(REVIEW_EXAMPLE),
              "",
              f"Submit: {submit_steps(state.paths, 'review_script')}"]
    return "\n".join(lines)


def emit_review_video(state: JobState) -> str:
    qa = read_json(state.paths.qa) or {}
    manifest = read_json(state.paths.manifest) or {}
    sheet = qa.get("contact_sheet") or str(state.paths.contact)
    lines = [f"# Video review - job {state.meta['id']} (master sha {manifest.get('master_sha')})",
             f"Duration {qa.get('duration')}s, {qa.get('resolution')} @ {qa.get('fps')} fps, "
             f"loudness {qa.get('loudness_lufs')} LUFS, music: {qa.get('music')}",
             "Automatic findings: " + ("; ".join(qa.get("soft", [])) or "none"),
             "",
             f"Contact sheet (every scene, one frame each): {sheet}",
             "The sheet is a review aid: its grid, the labels and any grey empty cell are not part of the video.",
             "Per-scene frames (full resolution, captions burned in):"]
    timeline = {t["id"]: t for t in manifest.get("timeline", [])}
    frames = {s["id"]: qa.get("frames", {}).get(s["id"]) or str(state.paths.frames / f"{s['id']}.jpg")
              for s in state.script["scenes"]}
    for scene in state.script["scenes"]:
        slot = timeline.get(scene["id"], {})
        lines.append(f"[{scene['id']}] {frames[scene['id']]}  "
                     f"({slot.get('start', 0):.1f}s, {slot.get('duration', 0):.1f}s)")
        lines.append(f"    narration: {scene['narration']}")
        if scene.get("on_screen"):
            lines.append(f"    headline: {scene['on_screen']}")
        kind = scene["visual"]["kind"]
        lines.append(f"    visual: {kind}" + (f" - {scene['visual'].get('prompt', '')[:120]}" if kind == "ai_image" else ""))
    reads = [(sheet, contact_sheet_prompt())] + [(frames[s["id"]], frame_prompt(s)) for s in state.script["scenes"]]
    lines += ["",
              f"## READ - make all {len(reads)} read_image calls below in ONE turn",
              "They run in parallel, so one turn costs about as much as one picture. The whole review must "
              "finish within 10 minutes; reading one picture per turn does not.",
              *[f"- path: {path}\n  prompt: {prompt}" for path, prompt in reads],
              "Do not run emit again: this output is everything you need.",
              "",
              "## Checklist",
              "1. Text: headline and captions fully inside the frame, not cut off at the edges, not overlapping each other, "
              "readable against the picture. Captions appear a few words at a time in sync with the voice, so a frame "
              "shows only part of the sentence - that is by design, not truncation.",
              "2. Pictures: match what the narration says; fill the whole frame (no borders, blurred bands or visible "
              "seams); no garbled pseudo-text, extra fingers, warped faces or logos; consistent style across scenes.",
              "3. Cards: nothing overflowing, nothing clipped, numbers match the script.",
              "4. Would you stop scrolling on the first frame? Does the last frame carry the CTA?",
              "Facts are NOT part of this review: they were checked against their sources at the script stage. "
              "Report on-screen text only when it differs from the script above or is hard to read.",
              "Issue types: visual (a picture must change - name the scene), text/clarity/timing/audio (the words must change).",
              "Only report what you can see or what the automatic findings say. PASS when nothing is blocker/major.",
              "checked_scenes must list every scene id. Decide once: your first recorded verdict is final for this cut.",
              "",
              "## Review schema (vf.review.v1) - EXAMPLE (the shape only; report what you actually see)",
              _dump(video_review_example([s["id"] for s in state.script["scenes"]])),
              "",
              f"Submit: {submit_steps(state.paths, 'review_video')}",
              "",
              f"NEXT: in one turn, call read_image once for each of the {len(reads)} lines under READ; "
              "then decide and submit."]
    return "\n".join(lines)


def video_review_example(scene_ids: list[str]) -> dict:
    """A valid video verdict for this job's scenes. Without one, the first live video
    review invented its own field names and was refused five times (2026-09-24)."""
    return {
        "schema": "vf.review.v1",
        "stage": "video",
        "verdict": "REVISE",
        "issues": [
            {"scene": scene_ids[-1], "severity": "major", "type": "visual",
             "problem": "Ví dụ: biển hiệu trong ảnh có chữ méo, đọc không ra.",
             "fix": "Ví dụ: tạo lại ảnh, biển hiệu nhìn từ xa, không có chữ."},
        ],
        "checked_scenes": scene_ids,
        "notes": "Một dòng tóm tắt.",
    }


# Video review reads, 2026-09-24: reviewers that opened one frame per turn ran past the
# 10-minute delegate deadline; one that read only the contact sheet passed a
# picture-in-picture and pseudo-text on a phone screen that the full frames showed.
# So every frame is read, all in one turn: read_image is read-only, and the agent loop
# runs read-only calls of one turn in parallel.

def contact_sheet_prompt() -> str:
    """The sheet answers what single frames cannot: does it look like one video."""
    return ("Each labelled tile (s1, s2, ...) is one frame of a vertical social video; the grid itself is not "
            "part of the video. Do the scenes look like one consistent video (style, colours, typography)? "
            "Name any tile that stands out as wrong, and say why.")


def frame_prompt(scene: dict) -> str:
    return (f"One frame of a vertical social video (scene {scene['id']}; the voice says: "
            f"\"{scene['narration'][:160]}\"). Report: (a) a headline or caption cut off at an edge, overlapping "
            "other text, or hard to read; (b) garbled pseudo-text, a watermark or a logo; (c) extra or warped "
            "fingers, hands or faces; (d) the picture does not reach every edge - a border, blurred or different "
            "bands at the sides, a picture inside a picture, visible seams; (e) whether the picture fits what the "
            "voice says. Captions show a few words at a time, so half a sentence is by design.")


def assets_help(state: JobState) -> list[str]:
    return [image_prompt(s, state.script, state.fmt) for s in state.script["scenes"]
            if s["visual"]["kind"] == "ai_image"]
