"""HTML cards (title, list, stat, quote, steps, compare, code) rendered on the
Chrome sidecar.

The sidecar mounts the workspace volume read-only, so it can open the card HTML
written into the job directory. Fonts are embedded as base64 data URIs: a
file:// page loading @font-face from another file:// URL runs into Chrome's
opaque-origin rules, and the sidecar has no Vietnamese-capable display font of
its own (only DejaVu). Code uses DejaVu Sans Mono, which the sidecar does have.

Card content stays inside the band between the headline and the captions: the
bottom of the safe area is reserved for burned-in captions.
"""

from __future__ import annotations

import base64
import html
import json
import subprocess
from functools import lru_cache
from pathlib import Path

from .formats import FormatSpec
from .paths import CDP_RENDER_JS, FONTS_DIR
from .themes import Theme
from .util import StudioError

FONT_FACES = (("BeVietnamPro-Medium.ttf", 500), ("BeVietnamPro-SemiBold.ttf", 600),
              ("BeVietnamPro-Bold.ttf", 700), ("BeVietnamPro-ExtraBold.ttf", 800),
              ("BeVietnamPro-Black.ttf", 900))


@lru_cache(maxsize=1)
def font_css() -> str:
    faces = []
    for file, weight in FONT_FACES:
        data = base64.b64encode((FONTS_DIR / file).read_bytes()).decode("ascii")
        faces.append("@font-face{font-family:'BVP';font-weight:%d;font-style:normal;"
                     "src:url(data:font/ttf;base64,%s) format('truetype');}" % (weight, data))
    return "\n".join(faces)


def content_box(fmt: FormatSpec, has_headline: bool) -> tuple[int, int, int, int]:
    """(left, top, width, height) of the card content area in CSS px."""
    caption_reserve = int(fmt.caption_size * 2.15)   # up to two caption lines
    top = fmt.safe_top + (int(fmt.headline_size * 1.55) if has_headline else 0)
    bottom = fmt.safe_bottom - caption_reserve
    return fmt.safe_left, top, fmt.safe_width, max(200, bottom - top)


def _e(text: str) -> str:
    """Escape card text; keep hyphenated tokens ("--version", "COVID-19") on one line."""
    out = []
    for token in (text or "").split(" "):
        escaped = html.escape(token, quote=True)
        out.append(f'<span class="nw">{escaped}</span>' if "-" in token else escaped)
    return " ".join(out)


def _layout_html(card: dict) -> str:
    layout = card["layout"]
    if layout == "title":
        sub = f'<div class="subtitle">{_e(card.get("subtitle", ""))}</div>' if card.get("subtitle") else ""
        return f'<div class="bar"></div><div class="big-title fit">{_e(card["title"])}</div>{sub}'
    if layout == "list":
        title = f'<div class="title">{_e(card["title"])}</div>' if card.get("title") else ""
        items = "".join(f'<li><span class="dot"></span><span>{_e(i)}</span></li>' for i in card["items"])
        return f'{title}<ul class="list fit">{items}</ul>'
    if layout == "stat":
        source = f'<div class="source">Nguồn: {_e(card["source"])}</div>' if card.get("source") else ""
        return (f'<div class="stat-value fit-one">{_e(card["value"])}</div>'
                f'<div class="stat-label">{_e(card["label"])}</div>{source}')
    if layout == "quote":
        author = f'<div class="author">— {_e(card["author"])}</div>' if card.get("author") else ""
        return f'<div class="qmark">“</div><div class="quote fit">{_e(card["quote"])}</div>{author}'
    if layout == "steps":
        title = f'<div class="title">{_e(card["title"])}</div>' if card.get("title") else ""
        steps = "".join(f'<li><span class="num">{n}</span><span>{_e(s)}</span></li>'
                        for n, s in enumerate(card["steps"], 1))
        return f'{title}<ol class="steps fit">{steps}</ol>'
    if layout == "compare":
        cols = []
        for side, cls in (("left", "a"), ("right", "b")):
            part = card[side]
            items = "".join(f"<li>{_e(i)}</li>" for i in part["items"])
            cols.append(f'<div class="col {cls}"><div class="col-title">{_e(part["title"])}</div><ul>{items}</ul></div>')
        return f'<div class="compare fit">{cols[0]}<div class="vs">VS</div>{cols[1]}</div>'
    if layout == "code":
        title = f'<div class="title">{_e(card["title"])}</div>' if card.get("title") else ""
        lines = "".join(f'<div class="ln"><span class="no">{n}</span><span class="src">{_e(line) or " "}</span></div>'
                        for n, line in enumerate(card["code"].splitlines(), 1))
        return f'{title}<div class="code fit">{lines}</div>'
    raise StudioError(f"unknown card layout {layout!r}")


def card_html(card: dict, fmt: FormatSpec, theme: Theme, *, has_headline: bool) -> str:
    left, top, width, height = content_box(fmt, has_headline)
    scale = fmt.width / 1080 if fmt.name != "long" else 1.25
    fs = lambda px: f"{round(px * scale)}px"  # noqa: E731 - tiny local helper
    text_dark = "#0B0B0B"
    css = f"""
{font_css()}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{fmt.width}px;height:{fmt.height}px;overflow:hidden}}
body{{font-family:'BVP',sans-serif;color:{theme.text};
 background:radial-gradient(circle at 18% 12%,{theme.accent2}33 0,transparent 38%),
 radial-gradient(circle at 85% 88%,{theme.accent}29 0,transparent 42%),
 linear-gradient(160deg,{theme.bg1},{theme.bg2});}}
.grain{{position:absolute;inset:0;opacity:.06;background-image:repeating-linear-gradient(45deg,#fff 0 1px,transparent 1px 3px)}}
.box{{position:absolute;left:{left}px;top:{top}px;width:{width}px;height:{height}px;
 display:flex;flex-direction:column;justify-content:center;gap:{fs(26)}}}
.title{{font-weight:800;font-size:{fs(68)};line-height:1.15;color:{theme.text}}}
.bar{{width:{fs(120)};height:{fs(14)};border-radius:99px;background:{theme.accent}}}
.big-title{{font-weight:900;font-size:{fs(96)};line-height:1.08;letter-spacing:-0.5px}}
.subtitle{{font-weight:600;font-size:{fs(46)};line-height:1.3;color:{theme.muted}}}
ul,ol{{list-style:none;display:flex;flex-direction:column;gap:.54em}}
.list,.steps{{font-size:{fs(56)}}}
.list li,.steps li{{display:flex;align-items:flex-start;gap:.46em;font-weight:700;font-size:1em;line-height:1.22}}
.dot{{flex:0 0 auto;width:.46em;height:.46em;margin-top:.36em;border-radius:.12em;background:{theme.accent}}}
.num{{flex:0 0 auto;width:1.28em;height:1.28em;border-radius:50%;background:{theme.accent};color:{text_dark};
 font-weight:900;font-size:1em;display:flex;align-items:center;justify-content:center}}
.num+span{{padding-top:.02em}}
.stat-value{{font-weight:900;font-size:{fs(230)};line-height:1.08;padding-bottom:{fs(18)};color:{theme.accent};white-space:nowrap;letter-spacing:-2px}}
.stat-label{{font-weight:700;font-size:{fs(60)};line-height:1.22}}
.source{{font-weight:500;font-size:{fs(32)};color:{theme.muted}}}
.qmark{{font-weight:900;font-size:{fs(220)};line-height:.7;color:{theme.accent};height:{fs(120)}}}
.quote{{font-weight:700;font-size:{fs(60)};line-height:1.25}}
.author{{font-weight:600;font-size:{fs(40)};color:{theme.muted}}}
.compare{{display:flex;flex-direction:{'row' if fmt.width > fmt.height else 'column'};gap:.48em;font-size:{fs(50)};align-items:stretch}}
.compare .col{{flex:1 1 0}}
.nw{{white-space:nowrap}}
.col{{border-radius:.66em;padding:.7em .85em;background:#FFFFFF14;border:{fs(3)} solid #FFFFFF22}}
.compare ul{{gap:.3em}}
.col.a .col-title{{color:{theme.accent}}} .col.b .col-title{{color:{theme.accent2}}}
.col-title{{font-weight:900;font-size:1.18em;margin-bottom:.28em}}
.col li{{font-weight:600;font-size:1em;line-height:1.3;padding-left:.8em;text-indent:-.8em}}
.col li::before{{content:'•';display:inline-block;width:.8em;text-indent:0;color:{theme.muted}}}
.vs{{align-self:center;font-weight:900;font-size:1em;color:{theme.muted}}}
.code{{background:#0D1117;border-radius:{fs(26)};padding:{fs(30)} {fs(28)};border:{fs(3)} solid #30363D;
 font-family:'DejaVu Sans Mono',monospace;font-size:{fs(34)};line-height:1.45;color:#E6EDF3;overflow:hidden}}
.ln{{display:flex;white-space:pre}} .no{{width:{fs(56)};flex:0 0 auto;color:#6E7681;text-align:right;margin-right:{fs(22)}}}
.title,.big-title,.subtitle,.stat-label,.col li,li>span:last-child{{text-wrap:balance}} .quote{{text-wrap:pretty}}
"""
    # Shrink an overflowing block by its own font-size: children are sized in em, so
    # one number scales the whole list/steps/compare/code/quote block together.
    fit_js = """
const box=document.querySelector('.box');
function over(el){return box.scrollHeight>box.clientHeight+1||el.scrollWidth>el.clientWidth+1}
document.querySelectorAll('.fit').forEach(el=>{let s=parseFloat(getComputedStyle(el).fontSize);
 while(over(el)&&s>22){s-=2;el.style.fontSize=s+'px'}});
document.querySelectorAll('.fit-one').forEach(el=>{let s=parseFloat(getComputedStyle(el).fontSize);
 while(el.scrollWidth>el.clientWidth+1&&s>60){s-=4;el.style.fontSize=s+'px'}});
"""
    return (f'<!doctype html><html lang="vi"><head><meta charset="utf-8"><style>{css}</style></head>'
            f'<body><div class="grain"></div><div class="box">{_layout_html(card)}</div>'
            f"<script>{fit_js}</script></body></html>")


def render_cards(jobs: list[dict], work_dir: Path, timeout: float = 240) -> dict[str, str]:
    """Run cdp_render.mjs for a batch. jobs use absolute paths. Returns {out: 'ok'|error}."""
    if not jobs:
        return {}
    work_dir.mkdir(parents=True, exist_ok=True)
    spec = work_dir / "cdp-jobs.json"
    spec.write_text(json.dumps(jobs), encoding="utf-8")
    # Not util.run: the renderer exits 1 when ANY job failed but still reports each
    # job on stdout, and those per-job lines are what we need.
    try:
        proc = subprocess.run(["node", str(CDP_RENDER_JS), str(spec)], capture_output=True, timeout=timeout)
        output = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        output = f"renderer timed out after {timeout:.0f}s"
    except FileNotFoundError:
        output = "node is not installed"
    results: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split(" ", 2)
        if len(parts) >= 2 and parts[0] in ("OK", "ERR"):
            results[parts[1]] = "ok" if parts[0] == "OK" else (parts[2] if len(parts) > 2 else "error")
    for job in jobs:
        results.setdefault(job["out"], "no result from renderer: " + output[-300:])
    return results
