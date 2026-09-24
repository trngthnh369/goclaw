"""Validators for the three artifacts a model writes: research, script, review.

Each returns (errors, warnings). Errors block the stage; warnings are printed so
the model can improve, but never block. Error messages are written for the model
that has to fix them: they name the field and say what would pass.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from . import fontmetrics
from .formats import FormatSpec
from .textutil import SYLLABLES_PER_SECOND, fold_ascii, has_emoji, nfc, syllable_count
from .themes import THEMES
from .tts import effective_lexicon, foreign_tokens, spoken_text

SCENE_ID_RE = re.compile(r"^s([1-9][0-9]?)$")
SOURCE_ID_RE = re.compile(r"^S[1-9][0-9]?$")
FACT_ID_RE = re.compile(r"^F[1-9][0-9]?$")
VOICE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}-[A-Za-z]+Neural$")
HASHTAG_RE = re.compile(r"^#[^\s#]{1,60}$")
DIGIT_RE = re.compile(r"\d")
LETTERS_RE = re.compile(r"[^\W\d_]+")
# Card fields drawn as text; "code" is left out, it is meant to be verbatim.
CARD_TEXT_KEYS = ("title", "subtitle", "items", "steps", "value", "label", "source", "quote", "author")

VISUAL_KINDS = ("ai_image", "card", "screenshot")
MOTIONS = ("auto", "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "still")
CARD_LAYOUTS = ("title", "list", "stat", "quote", "steps", "compare", "code")
ROLES = ("hook", "body", "cta")
SEVERITIES = ("blocker", "major", "minor")
ISSUE_TYPES = ("fact", "clarity", "visual", "text", "audio", "timing", "policy", "other")
MUSIC_MODES = ("auto", "none", "library")
# The Reels caption travels inside ONE Discord review message (2000-byte cap) next
# to the title, sources and instructions, and is published byte for byte from it.
REELS_CAPTION_MAX_BYTES = 1200

Result = tuple[list[str], list[str]]


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _text(obj: dict, key: str, where: str, errors: list[str], *, required: bool = True,
          max_len: int | None = None, min_len: int = 1, no_emoji: bool = False) -> str:
    value = obj.get(key)
    if value is None or value == "":
        if required:
            errors.append(f"{where}.{key} is required (non-empty string)")
        return ""
    if not isinstance(value, str):
        errors.append(f"{where}.{key} must be a string")
        return ""
    value = value.strip()
    if len(value) < min_len:
        errors.append(f"{where}.{key} is too short ({len(value)} < {min_len} chars)")
    if max_len is not None and len(value) > max_len:
        errors.append(f"{where}.{key} is {len(value)} chars; max {max_len}")
    if no_emoji and has_emoji(value):
        errors.append(f"{where}.{key} contains emoji; the video fonts have no emoji glyphs - remove them")
    return value


def _str_list(obj: dict, key: str, where: str, errors: list[str], *, lo: int, hi: int,
              item_max: int, no_emoji: bool = True, required: bool = True) -> list[str]:
    value = obj.get(key)
    if value is None:
        if required:
            errors.append(f"{where}.{key} is required (list of {lo}-{hi} strings)")
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        errors.append(f"{where}.{key} must be a list of non-empty strings")
        return []
    if not lo <= len(value) <= hi:
        errors.append(f"{where}.{key} has {len(value)} items; need {lo}-{hi}")
    for i, item in enumerate(value):
        if len(item) > item_max:
            errors.append(f"{where}.{key}[{i}] is {len(item)} chars; max {item_max}")
        if no_emoji and has_emoji(item):
            errors.append(f"{where}.{key}[{i}] contains emoji; remove it")
    return [v.strip() for v in value]


# --------------------------------------------------------------------------- research

def validate_research(doc: Any) -> Result:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(doc, dict):
        return ["research must be a JSON object"], []
    if doc.get("schema") != "vf.research.v1":
        errors.append('research.schema must be "vf.research.v1"')
    _text(doc, "topic", "research", errors, max_len=200)
    _text(doc, "angle", "research", errors, min_len=10, max_len=400)

    sources = doc.get("sources")
    source_ids: set[str] = set()
    if not isinstance(sources, list) or not sources:
        errors.append("research.sources must be a non-empty list - every video needs at least one real source")
        sources = []
    if len(sources) > 15:
        errors.append(f"research.sources has {len(sources)} items; keep the 15 strongest")
    seen_urls: set[str] = set()
    for i, src in enumerate(sources):
        where = f"research.sources[{i}]"
        if not isinstance(src, dict):
            errors.append(f"{where} must be an object")
            continue
        sid = src.get("id")
        if not isinstance(sid, str) or not SOURCE_ID_RE.match(sid):
            errors.append(f'{where}.id must look like "S1", "S2", ...')
        elif sid in source_ids:
            errors.append(f"{where}.id {sid} is duplicated")
        else:
            source_ids.add(sid)
        url = src.get("url")
        if not _is_http_url(url):
            errors.append(f"{where}.url must be an http(s) URL you actually opened")
        elif url in seen_urls:
            errors.append(f"{where}.url is duplicated")
        else:
            seen_urls.add(url)
        _text(src, "title", where, errors, max_len=300)

    facts = doc.get("facts")
    if not isinstance(facts, list) or not facts:
        errors.append("research.facts must be a non-empty list")
        facts = []
    if len(facts) > 20:
        errors.append(f"research.facts has {len(facts)} items; keep at most 20")
    fact_ids: set[str] = set()
    for i, fact in enumerate(facts):
        where = f"research.facts[{i}]"
        if not isinstance(fact, dict):
            errors.append(f"{where} must be an object")
            continue
        fid = fact.get("id")
        if not isinstance(fid, str) or not FACT_ID_RE.match(fid):
            errors.append(f'{where}.id must look like "F1", "F2", ...')
        elif fid in fact_ids:
            errors.append(f"{where}.id {fid} is duplicated")
        else:
            fact_ids.add(fid)
        _text(fact, "claim", where, errors, max_len=400)
        refs = fact.get("source_ids")
        if not isinstance(refs, list) or not refs:
            errors.append(f"{where}.source_ids must list at least one source id")
        else:
            for ref in refs:
                if ref not in source_ids:
                    errors.append(f"{where}.source_ids references unknown source {ref!r}")
        quote = fact.get("quote")
        if quote is None or quote == "":
            warnings.append(f"{where}.quote is empty - a verbatim excerpt makes review much faster")
        elif not isinstance(quote, str) or len(quote) > 400:
            errors.append(f"{where}.quote must be a string of at most 400 chars")
        conf = fact.get("confidence", "high")
        if conf not in ("high", "medium", "low"):
            errors.append(f'{where}.confidence must be "high", "medium" or "low"')
        elif conf == "low":
            warnings.append(f"{where} is low-confidence - do not state it as fact in narration")
    return errors, warnings


def research_fact_ids(doc: Any) -> set[str]:
    if not isinstance(doc, dict):
        return set()
    return {f.get("id") for f in doc.get("facts", []) if isinstance(f, dict) and isinstance(f.get("id"), str)}


# --------------------------------------------------------------------------- script

def _validate_card(card: Any, where: str, fmt: FormatSpec, errors: list[str]) -> None:
    if not isinstance(card, dict):
        errors.append(f"{where}.card must be an object with a layout")
        return
    layout = card.get("layout")
    if layout not in CARD_LAYOUTS:
        errors.append(f"{where}.card.layout must be one of {', '.join(CARD_LAYOUTS)}")
        return
    wide = fmt.name == "long"
    if layout == "title":
        _text(card, "title", f"{where}.card", errors, max_len=70, no_emoji=True)
        _text(card, "subtitle", f"{where}.card", errors, required=False, max_len=120, no_emoji=True)
    elif layout == "list":
        _text(card, "title", f"{where}.card", errors, required=False, max_len=60, no_emoji=True)
        _str_list(card, "items", f"{where}.card", errors, lo=2, hi=5, item_max=70 if wide else 56)
    elif layout == "stat":
        _text(card, "value", f"{where}.card", errors, max_len=14, no_emoji=True)
        _text(card, "label", f"{where}.card", errors, max_len=90, no_emoji=True)
        _text(card, "source", f"{where}.card", errors, required=False, max_len=70, no_emoji=True)
    elif layout == "quote":
        _text(card, "quote", f"{where}.card", errors, max_len=180, no_emoji=True)
        _text(card, "author", f"{where}.card", errors, required=False, max_len=70, no_emoji=True)
    elif layout == "steps":
        _text(card, "title", f"{where}.card", errors, required=False, max_len=60, no_emoji=True)
        _str_list(card, "steps", f"{where}.card", errors, lo=2, hi=5, item_max=80 if wide else 60)
    elif layout == "compare":
        for side in ("left", "right"):
            part = card.get(side)
            if not isinstance(part, dict):
                errors.append(f"{where}.card.{side} must be an object with title and items")
                continue
            _text(part, "title", f"{where}.card.{side}", errors, max_len=30, no_emoji=True)
            _str_list(part, "items", f"{where}.card.{side}", errors, lo=1, hi=4, item_max=48 if wide else 32)
    elif layout == "code":
        _text(card, "title", f"{where}.card", errors, required=False, max_len=60, no_emoji=True)
        code = _text(card, "code", f"{where}.card", errors, max_len=1600)
        lines = code.splitlines()
        max_lines, max_cols = (20, 80) if wide else (14, 44)
        if len(lines) > max_lines:
            errors.append(f"{where}.card.code has {len(lines)} lines; max {max_lines} for {fmt.name}")
        for n, line in enumerate(lines):
            if len(line) > max_cols:
                errors.append(f"{where}.card.code line {n + 1} is {len(line)} chars; max {max_cols} for {fmt.name}")
                break


def _shown_texts(scene: dict) -> list[tuple[str, str]]:
    """(field, text) for everything a scene draws as text on the frame."""
    shown: list[tuple[str, str]] = []
    if isinstance(scene.get("on_screen"), str):
        shown.append(("on_screen", scene["on_screen"]))
    visual = scene.get("visual")
    card = visual.get("card") if isinstance(visual, dict) else None
    parts = [("visual.card", card)] if isinstance(card, dict) else []
    if isinstance(card, dict):
        parts += [(f"visual.card.{side}", card[side]) for side in ("left", "right") if isinstance(card.get(side), dict)]
    for prefix, part in parts:
        for key in CARD_TEXT_KEYS:
            value = part.get(key)
            values = value if isinstance(value, list) else [value]
            shown += [(f"{prefix}.{key}", v) for v in values if isinstance(v, str)]
    return shown


def _word_pairs(text: str) -> list[str]:
    words = LETTERS_RE.findall(nfc(text).lower())
    return [f"{a} {b}" for a, b in zip(words, words[1:])]


def accented_pairs(texts: list[str]) -> dict[str, str]:
    """ASCII-folded form -> the accented word pair the script writes, e.g.
    "lich su" -> "lịch sử"."""
    pairs: dict[str, str] = {}
    for text in texts:
        for pair in _word_pairs(text):
            folded = fold_ascii(pair)
            if folded != pair:
                pairs.setdefault(folded, pair)
    return pairs


def accentless_pair(text: str, accented: dict[str, str]) -> str | None:
    """The accented pair `text` writes without its accents, if any.

    A pair of words is only flagged when the script itself writes those words
    with accents elsewhere, so an English phrase on a card is never mistaken for
    stripped Vietnamese.
    """
    for pair in _word_pairs(text):
        if pair.isascii() and pair in accented:
            return accented[pair]
    return None


def _validate_visual(visual: Any, where: str, fmt: FormatSpec, errors: list[str], warnings: list[str]) -> None:
    if not isinstance(visual, dict):
        errors.append(f"{where}.visual must be an object with a kind")
        return
    kind = visual.get("kind")
    if kind not in VISUAL_KINDS:
        errors.append(f"{where}.visual.kind must be one of {', '.join(VISUAL_KINDS)}")
        return
    motion = visual.get("motion", "auto")
    if kind == "screenshot":
        if motion not in ("auto", "scroll", "still"):
            errors.append(f'{where}.visual.motion for a screenshot must be "scroll" or "still"')
    elif motion not in MOTIONS:
        errors.append(f"{where}.visual.motion must be one of {', '.join(MOTIONS)}")
    if kind == "ai_image":
        prompt = _text(visual, "prompt", f"{where}.visual", errors, min_len=20, max_len=1200)
        lowered = prompt.lower()
        if prompt and not any(p in lowered for p in ("no text", "without text", "no words", "no letters", "textless")):
            warnings.append(f'{where}.visual.prompt should say "no text" - image models garble lettering')
    elif kind == "card":
        _validate_card(visual.get("card"), f"{where}.visual", fmt, errors)
    elif kind == "screenshot":
        if not _is_http_url(visual.get("url")):
            errors.append(f"{where}.visual.url must be a public http(s) URL")
        if visual.get("viewport", "auto") not in ("auto", "mobile", "desktop"):
            errors.append(f'{where}.visual.viewport must be "mobile" or "desktop"')


def _validate_social(social: Any, errors: list[str], warnings: list[str]) -> None:
    if not isinstance(social, dict):
        errors.append("script.social must be an object with at least a facebook caption")
        return
    fb = social.get("facebook")
    if not isinstance(fb, dict):
        errors.append("script.social.facebook is required (it is the Reels caption)")
    elif isinstance(fb.get("caption"), str) and isinstance(fb.get("hashtags", []), list):
        if "[caption]" in fb["caption"].lower() or "[/caption]" in fb["caption"].lower():
            errors.append("script.social.facebook.caption must not contain [caption] or [/caption]")
        post = (fb["caption"].strip() + "\n\n" + " ".join(str(t) for t in fb.get("hashtags", []))).strip()
        size = len(post.encode("utf-8"))
        if size > REELS_CAPTION_MAX_BYTES:
            errors.append(f"script.social.facebook caption + hashtags is {size} bytes; max {REELS_CAPTION_MAX_BYTES} "
                          "(it is published from the one Discord review message) - shorten the caption")
    for platform, limit in (("facebook", 900), ("tiktok", 2200), ("instagram", 2200)):
        block = social.get(platform)
        if block is None:
            if platform != "facebook":
                warnings.append(f"script.social.{platform} missing - the package will reuse the facebook caption")
            continue
        if not isinstance(block, dict):
            errors.append(f"script.social.{platform} must be an object")
            continue
        _text(block, "caption", f"script.social.{platform}", errors, max_len=limit)
        tags = block.get("hashtags", [])
        if not isinstance(tags, list) or not all(isinstance(t, str) and HASHTAG_RE.match(t) for t in tags):
            errors.append(f'script.social.{platform}.hashtags must be a list like ["#kienthuc", "#ai"] (no spaces)')
        elif len(tags) > 8:
            errors.append(f"script.social.{platform}.hashtags has {len(tags)}; use 3-8")
    yt = social.get("youtube")
    if yt is not None:
        if not isinstance(yt, dict):
            errors.append("script.social.youtube must be an object")
        else:
            _text(yt, "title", "script.social.youtube", errors, max_len=100)
            _text(yt, "description", "script.social.youtube", errors, max_len=4800)
            tags = yt.get("tags", [])
            if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
                errors.append("script.social.youtube.tags must be a list of strings")


def validate_script(doc: Any, fmt: FormatSpec, fact_ids: set[str], *, rate_percent: int = 0,
                    voice: str | None = None, studio_lexicon: dict[str, str] | None = None) -> Result:
    """`voice` (the job's effective voice; defaults to the script's own) and
    `studio_lexicon` decide what the narration will sound like: a Vietnamese voice
    misreads foreign words, so each one must be covered by the lexicon or respelled
    in the scene's tts_text."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(doc, dict):
        return ["script must be a JSON object"], []
    doc_voice = doc.get("voice") if isinstance(doc.get("voice"), str) else None
    voice = voice or doc_voice or "vi-VN-HoaiMyNeural"
    lexicon = effective_lexicon(voice, studio_lexicon)
    if doc.get("schema") != "vf.script.v1":
        errors.append('script.schema must be "vf.script.v1"')
    _text(doc, "title", "script", errors, max_len=90, no_emoji=True)
    if doc.get("format") not in (None, fmt.name):
        errors.append(f'script.format must be "{fmt.name}" (set by the job)')
    script_voice = doc.get("voice")
    if script_voice is not None and (not isinstance(script_voice, str) or not VOICE_RE.match(script_voice)):
        errors.append('script.voice must be an edge-tts voice such as "vi-VN-HoaiMyNeural" or "vi-VN-NamMinhNeural"')
    rate = doc.get("rate")
    if rate is not None and (not isinstance(rate, int) or not -10 <= rate <= 20):
        errors.append("script.rate must be an integer percent between -10 and 20")
    if doc.get("theme") is not None and doc.get("theme") not in THEMES:
        errors.append(f"script.theme must be one of {', '.join(THEMES)}")
    if doc.get("music") is not None and doc.get("music") not in MUSIC_MODES:
        errors.append(f"script.music must be one of {', '.join(MUSIC_MODES)}")
    _text(doc, "image_style", "script", errors, required=False, max_len=300)

    scenes = doc.get("scenes")
    lo, hi = fmt.min_scenes, fmt.max_scenes
    headline_font = fontmetrics.load(fontmetrics.HEADLINE_FONT_FILE)
    caption_font = fontmetrics.load(fontmetrics.CAPTION_FONT_FILE)
    if not isinstance(scenes, list) or not lo <= len(scenes) <= hi:
        errors.append(f"script.scenes must be a list of {lo}-{hi} scenes for format {fmt.name}")
        scenes = scenes if isinstance(scenes, list) else []

    # A live script shipped "Lich su clipboard" on a card while its narration said
    # "lịch sử"; the reviewer passed it. Stripped accents are mechanical to catch.
    accented: dict[str, str] = {}
    if voice.startswith("vi-"):
        accented = accented_pairs(
            [s.get("narration", "") for s in scenes if isinstance(s, dict) and isinstance(s.get("narration"), str)]
            + [text for s in scenes if isinstance(s, dict) for _, text in _shown_texts(s)])

    seen: set[str] = set()
    total_syllables = 0
    for i, scene in enumerate(scenes):
        where = f"scenes[{i}]"
        if not isinstance(scene, dict):
            errors.append(f"{where} must be an object")
            continue
        sid = scene.get("id")
        if not isinstance(sid, str) or not SCENE_ID_RE.match(sid):
            errors.append(f'{where}.id must look like "s1", "s2", ...')
        elif sid in seen:
            errors.append(f"{where}.id {sid} is duplicated")
        else:
            seen.add(sid)
            where = f"scene {sid}"
        role = scene.get("role", "body")
        if role not in ROLES:
            errors.append(f"{where}.role must be hook, body or cta")
        if i == 0 and role != "hook":
            errors.append(f'{where} is the first scene; its role must be "hook"')
        if role == "cta" and i != len(scenes) - 1:
            errors.append(f'{where}: a "cta" scene must be the last scene')
        narration = _text(scene, "narration", where, errors, max_len=420, no_emoji=True)
        missing = caption_font.missing_glyphs(narration)
        if missing:
            errors.append(f"{where}.narration uses characters the caption font cannot draw: {' '.join(missing)}")
        tts_text = _text(scene, "tts_text", where, errors, required=False, max_len=600, no_emoji=True)
        spoken = spoken_text(narration, tts_text or None, lexicon)
        foreign = foreign_tokens(spoken) if voice.startswith("vi-") else []
        if foreign:
            errors.append(
                f"{where}: the Vietnamese voice would misread {', '.join(foreign[:8])} - add \"tts_text\": the whole "
                "narration as it should be SPOKEN, with each of these written as Vietnamese syllables "
                "(e.g. \"CEO\" -> \"xi i âu\", \"vitamin B\" -> \"vi ta min bê\", \"Netflix\" -> \"nét phờ lích\"). "
                "Captions keep showing the narration.")
        syl = syllable_count(spoken)
        total_syllables += syl
        if syl > fmt.max_scene_syllables:
            errors.append(
                f"{where}.narration has {syl} syllables; max {fmt.max_scene_syllables} for {fmt.name} "
                "(split it into two scenes so the picture changes every few seconds)")
        if i == 0 and syl > 22:
            warnings.append(f"{where} (hook) has {syl} syllables - a hook lands best in under ~4 s (<= 18 syllables)")
        on_screen = _text(scene, "on_screen", where, errors, required=False,
                          max_len=fmt.headline_max_chars, no_emoji=True)
        if on_screen:
            if headline_font.missing_glyphs(on_screen):
                errors.append(f"{where}.on_screen uses characters the headline font cannot draw: "
                              f"{' '.join(headline_font.missing_glyphs(on_screen))}")
            elif fontmetrics.wrap_ass(on_screen, headline_font, fmt.headline_size, fmt.headline_width, 2) is None:
                errors.append(f"{where}.on_screen does not fit in 2 lines on a {fmt.size} frame; shorten it")
        emphasis = scene.get("emphasis", [])
        if not isinstance(emphasis, list) or not all(isinstance(e, str) for e in emphasis):
            errors.append(f"{where}.emphasis must be a list of short phrases from the narration")
        else:
            lowered = narration.lower()
            for phrase in emphasis:
                if phrase.lower() not in lowered:
                    errors.append(f"{where}.emphasis {phrase!r} does not occur in the narration")
        _validate_visual(scene.get("visual"), where, fmt, errors, warnings)
        for field, text in _shown_texts(scene) if accented else []:
            pair = accentless_pair(text, accented)
            if pair:
                errors.append(f'{where}.{field} "{text}" drops the Vietnamese accents (the script writes "{pair}"); '
                              "text shown on screen keeps every accent")
        refs = scene.get("fact_ids", [])
        if not isinstance(refs, list):
            errors.append(f"{where}.fact_ids must be a list")
            refs = []
        for ref in refs:
            if ref not in fact_ids:
                errors.append(f"{where}.fact_ids references {ref!r}, which is not in research.json")
        if (DIGIT_RE.search(narration) or DIGIT_RE.search(on_screen)) and not refs:
            errors.append(f"{where} states a number but has no fact_ids - cite the research fact that backs it "
                          "(a count of this video's own tips, like \"3 mẹo\", cites the facts of those tips)")
        if role == "body" and not refs:
            warnings.append(f"{where} has no fact_ids - fine for a transition, not for a claim")

    est = total_syllables / (SYLLABLES_PER_SECOND * (1 + (rate_percent or 0) / 100.0)) + 0.3 * len(scenes)
    if scenes and not fmt.min_total <= est <= fmt.max_total:
        errors.append(
            f"estimated duration {est:.0f}s is outside {fmt.min_total:.0f}-{fmt.max_total:.0f}s for {fmt.name} "
            f"({total_syllables} syllables)")
    elif scenes and not fmt.target_total[0] <= est <= fmt.target_total[1]:
        warnings.append(
            f"estimated duration {est:.0f}s; the sweet spot for {fmt.name} is "
            f"{fmt.target_total[0]:.0f}-{fmt.target_total[1]:.0f}s")
    _validate_social(doc.get("social"), errors, warnings)
    return errors, warnings


def script_fact_refs(doc: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(doc, dict):
        for scene in doc.get("scenes", []):
            if isinstance(scene, dict) and isinstance(scene.get("fact_ids"), list):
                refs.update(r for r in scene["fact_ids"] if isinstance(r, str))
    return refs


def script_scene_ids(doc: Any) -> list[str]:
    if not isinstance(doc, dict):
        return []
    return [s.get("id") for s in doc.get("scenes", []) if isinstance(s, dict) and isinstance(s.get("id"), str)]


# --------------------------------------------------------------------------- review

def validate_review(doc: Any, stage: str, *, required_facts: set[str], scene_ids: list[str]) -> Result:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(doc, dict):
        return ["review must be a JSON object"], []
    if doc.get("schema") != "vf.review.v1":
        errors.append('review.schema must be "vf.review.v1"')
    if doc.get("stage") != stage:
        errors.append(f'review.stage must be "{stage}"')
    verdict = doc.get("verdict")
    if verdict not in ("PASS", "REVISE"):
        errors.append('review.verdict must be "PASS" or "REVISE"')
    issues = doc.get("issues", [])
    if not isinstance(issues, list):
        errors.append("review.issues must be a list")
        issues = []
    serious = 0
    valid_targets = set(scene_ids) | {"global"}
    for i, issue in enumerate(issues):
        where = f"review.issues[{i}]"
        if not isinstance(issue, dict):
            errors.append(f"{where} must be an object")
            continue
        if issue.get("scene") not in valid_targets:
            errors.append(f'{where}.scene must be a scene id ({", ".join(scene_ids)}) or "global"')
        sev = issue.get("severity")
        if sev not in SEVERITIES:
            errors.append(f"{where}.severity must be blocker, major or minor")
        elif sev in ("blocker", "major"):
            serious += 1
        if issue.get("type") not in ISSUE_TYPES:
            errors.append(f"{where}.type must be one of {', '.join(ISSUE_TYPES)}")
        elif stage == "video" and issue.get("type") == "fact":
            errors.append(f'{where}.type "fact" does not belong in the video review: every fact was checked '
                          'against its source at the script stage. If on-screen text differs from the script, '
                          'use "text"; otherwise drop the issue')
        _text(issue, "problem", where, errors, max_len=600)
        _text(issue, "fix", where, errors, max_len=600)
    if verdict == "PASS" and serious:
        errors.append("review.verdict is PASS but lists blocker/major issues - a PASS cannot carry them; "
                      "the verdict is REVISE")
    if verdict == "REVISE" and not serious:
        errors.append("review.verdict is REVISE but no issue is blocker/major - list what must change")
    if stage == "script":
        checked = doc.get("checked_facts")
        if not isinstance(checked, list):
            errors.append("review.checked_facts must list every fact id you verified against its source")
        else:
            missing = sorted(required_facts - set(checked))
            if missing:
                errors.append(f"review.checked_facts misses {', '.join(missing)} - verify every fact the script cites")
    else:
        checked = doc.get("checked_scenes")
        if not isinstance(checked, list):
            errors.append("review.checked_scenes must list every scene id you looked at")
        else:
            missing = [s for s in scene_ids if s not in checked]
            if missing:
                errors.append(f"review.checked_scenes misses {', '.join(missing)} - look at every scene frame")
    return errors, warnings
