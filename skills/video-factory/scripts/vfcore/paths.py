"""Filesystem layout.

The studio lives in its own workspace, /app/workspace/video-factory, listed in
system_configs.allowed_paths so all three agents reach it. Agents never read
artifacts with read_file: `studio.py emit` prints what a stage needs, because
read_file caps at 50 000 chars and exec at 30 000 - both silently.

The skill itself runs from /app/data/skills-store/video-factory/<version>/. exec
denies /app/data except skills-store/, so every command we hand an agent uses the
absolute path of THIS file's skill directory, never the repo bind mount.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = SKILL_DIR / "scripts"
STUDIO_PY = SCRIPTS_DIR / "studio.py"
ASSETS_DIR = SKILL_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
TEMPLATES_DIR = ASSETS_DIR / "templates"
CDP_RENDER_JS = SCRIPTS_DIR / "cdp_render.mjs"

DEFAULT_WORKSPACE = "/app/workspace/video-factory"


def default_workspace() -> Path:
    return Path(os.environ.get("VF_WORKSPACE", DEFAULT_WORKSPACE))


def studio_cmd() -> str:
    """The exact command prefix agents must use (absolute, version-pinned path)."""
    return f"python3 {STUDIO_PY.as_posix()}"


def python_exe() -> str:
    return sys.executable or "python3"


class Studio:
    """Studio-wide paths (config, backlog, metrics, caches)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def config(self) -> Path:
        return self.root / "studio.json"

    @property
    def backlog(self) -> Path:
        return self.root / "backlog.json"

    @property
    def jobs(self) -> Path:
        return self.root / "jobs"

    @property
    def metrics(self) -> Path:
        return self.root / "metrics" / "runs.ndjson"

    @property
    def cache(self) -> Path:
        return self.root / "_cache"

    @property
    def music(self) -> Path:
        return self.root / "music"

    def job(self, job_id: str) -> "JobPaths":
        return JobPaths(self.jobs / job_id)


class JobPaths:
    """Every artifact of one job, resolved from its directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def job_id(self) -> str:
        return self.root.name

    @property
    def meta(self) -> Path:
        return self.root / "job.json"

    @property
    def research(self) -> Path:
        return self.root / "research.json"

    @property
    def script(self) -> Path:
        return self.root / "script.json"

    @property
    def reviews(self) -> Path:
        return self.root / "reviews"

    @property
    def assets(self) -> Path:
        return self.root / "assets"

    @property
    def cards(self) -> Path:
        return self.root / "cards"

    @property
    def audio(self) -> Path:
        return self.root / "audio"

    @property
    def render(self) -> Path:
        return self.root / "render"

    @property
    def clips(self) -> Path:
        return self.render / "clips"

    @property
    def frames(self) -> Path:
        return self.render / "frames"

    @property
    def out(self) -> Path:
        return self.root / "out"

    @property
    def master(self) -> Path:
        return self.out / "master.mp4"

    @property
    def preview(self) -> Path:
        return self.out / "preview.mp4"

    @property
    def cover(self) -> Path:
        return self.out / "cover.jpg"

    @property
    def srt(self) -> Path:
        return self.out / "captions.srt"

    @property
    def contact(self) -> Path:
        return self.out / "contact.jpg"

    @property
    def qa(self) -> Path:
        return self.out / "qa.json"

    @property
    def manifest(self) -> Path:
        return self.out / "manifest.json"

    @property
    def inbox(self) -> Path:
        """Where agents write JSON for `submit --file` (write_file reaches the studio via allowed_paths)."""
        return self.root.parent.parent / "inbox" / self.job_id

    def inbox_file(self, kind: str) -> Path:
        return self.inbox / f"{kind}.json"

    @property
    def deliver(self) -> Path:
        return self.root / "deliver"

    @property
    def delivery(self) -> Path:
        return self.deliver / "delivery.json"
