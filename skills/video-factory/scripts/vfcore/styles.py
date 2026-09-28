"""One visual style per video.

Job vf-260925-1139 was escalated four times in three days because each scene's
prompt picked its own look (3D isometric, glowing orbs, a realistic photo) and the
reviewer flagged the mix. The style is now a preset chosen once per video: the
studio adds its wording to every ai_image prompt, and a scene prompt may only say
what is in the picture, never how it is drawn or lit.

The preset owns medium, palette and lighting; a scene's own "warm lamp light" or
"neon" would pull the palette apart as surely as "photorealistic" pulls the medium.
Wording picked from the C0 contact sheets (2026-09-28): the same four subject-only
prompts (calm, urgent, night, close-up) under each preset, at 2K.
"""
from __future__ import annotations

import re

DEFAULT_STYLE = "clay3d"

# Fixed prompt fragments the studio adds after the scene prompt (jobs.image_prompt).
ORIENTATION = {"short": "vertical 9:16 full-bleed composition filling the whole frame edge to edge, "
                         "main subject in the centre, one continuous scene from top to bottom, no borders",
               "long": "wide 16:9 full-bleed composition filling the whole frame, subject off-centre, no borders",
               "square": "square 1:1 full-bleed composition filling the whole frame, subject centred, no borders"}
NO_TEXT = "no text, no letters, no captions, no logos, no watermark"

STYLE_PRESETS: dict[str, dict[str, str]] = {
    "clay3d": {
        "prefix": ("Soft 3D clay-style illustration, rounded matte shapes, slightly isometric camera, "
                   "warm pastel palette of cream, coral and teal, soft studio lighting with gentle shadows"),
        "suffix": "cohesive handmade clay 3D render look",
        "label_vi": "3D đất sét",
        "reviewer": "soft 3D clay illustration: rounded matte shapes, cream/coral/teal pastel palette, soft studio light",
    },
    "cinematic_photo": {
        "prefix": ("Cinematic photograph, realistic people and places, 35mm lens, shallow depth of field, "
                   "warm neutral colour grade, soft natural light"),
        "suffix": "consistent film photography look",
        "label_vi": "ảnh chân thực",
        "reviewer": "realistic cinematic photograph: 35mm, shallow depth of field, warm neutral grade, soft natural light",
    },
    "flat": {
        "prefix": ("Flat vector illustration, clean geometric shapes, bold limited palette of navy, teal, "
                   "warm yellow and off-white, no gradients, even flat lighting"),
        "suffix": "consistent flat editorial illustration look",
        "label_vi": "minh hoạ phẳng",
        "reviewer": "flat vector illustration: clean geometric shapes, navy/teal/yellow/off-white palette, no gradients",
    },
}

# Words that set a medium, a palette or the lighting. A scene prompt that carries
# one fights the preset. Phrases are matched on word boundaries, so "a clay pot",
# "a flat road", "a 3D printer" and "a neon sign" stay allowed (GUARDED below).
STYLE_WORDS = (
    "photorealistic", "photo-realistic", "hyperrealistic", "illustration", "illustrated", "cinematic",
    "3d render", "3d rendering", "3d style", "claymation", "clay style", "isometric", "watercolor",
    "watercolour", "anime", "cartoon", "vector art", "vector style", "flat design", "flat style",
    "digital art", "digital painting", "oil painting", "pixel art", "low poly", "in the style of",
    "pastel", "monochrome", "golden hour", "moody lighting", "studio lighting", "volumetric light",
    "color palette", "colour palette", "neon lighting", "neon glow", "color grade", "colour grade",
)

_WORD_RES = [(w, re.compile(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])")) for w in STYLE_WORDS]


def preset(key: str | None) -> dict[str, str] | None:
    return STYLE_PRESETS.get(key or "")


def is_legacy_style(script: dict, meta: dict) -> bool:
    """A script from a job created before style presets: it keeps its own prompts.

    Jobs created since then carry a "style" key in their meta (create_job), so a new
    script that forgets to pick a style is an error rather than a silent legacy.
    """
    return "style" not in script and "style" not in meta


def studio_fragments() -> tuple[str, ...]:
    """Every piece of wording the studio itself adds around a scene prompt."""
    parts = [part for spec in STYLE_PRESETS.values() for part in (spec["prefix"], spec["suffix"])]
    return (*parts, *ORIENTATION.values(), NO_TEXT)


def scene_core(prompt: str) -> str:
    """A scene prompt without anything the studio added (any preset, orientation, no-text).

    A pasted-back prompt then yields the scene's own words whichever preset printed
    it, so a later style change never stacks two presets into one prompt.
    """
    text = prompt
    for part in sorted(studio_fragments(), key=len, reverse=True):
        text = re.sub(r"(?:,\s*)?" + re.escape(part) + r"(?:\s*,)?", ",", text, flags=re.IGNORECASE)
    return re.sub(r"\s*,(?:\s*,)+", ",", text).strip(" ,.")


def strip_studio_wording(prompt: str, fixed: tuple[str, ...] = ()) -> str:
    """A prompt with every preset's wording and the studio's fixed fragments removed.

    A director revising a picture pastes the whole printed prompt back; what the
    studio itself added must not count as the scene's own style words.
    """
    lowered = prompt.lower()
    for spec in STYLE_PRESETS.values():
        for part in (spec["prefix"], spec["suffix"]):
            lowered = lowered.replace(part.lower(), " ")
    for part in fixed:
        lowered = lowered.replace(part.lower(), " ")
    return lowered


def style_words_in(prompt: str, fixed: tuple[str, ...] = ()) -> list[str]:
    """Style, palette or lighting words the scene prompt itself carries."""
    text = strip_studio_wording(prompt, fixed)
    return [word for word, pattern in _WORD_RES if pattern.search(text)]
