"""Workspace layout.

The pipeline directory lives INSIDE the agent's own workspace on purpose.
`predefined` agents resolve to `<base>/<agentID>` (internal/workspace/resolver_impl.go)
and `RestrictToWorkspace` is forced true at creation (internal/http/agents.go:273),
so a sibling directory would be unreachable to the agent's file tools. Artifacts
still travel to the model through `exec` + stdout rather than `read_file`, because
`read_file` caps at 50 000 chars and `exec` at 30 000 — both silently.
"""

from __future__ import annotations

from pathlib import Path

PIPELINE_DIRNAME = "pipeline"


class Workspace:
    """Resolves every path the pipeline uses from one root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    # --- top level ---------------------------------------------------------

    @property
    def pipeline(self) -> Path:
        return self.root / PIPELINE_DIRNAME

    @property
    def state_db(self) -> Path:
        return self.pipeline / "state.sqlite"

    @property
    def lock_file(self) -> Path:
        return self.pipeline / "lock.json"

    @property
    def episodes(self) -> Path:
        return self.pipeline / "episodes"

    @property
    def thesis_events(self) -> Path:
        return self.pipeline / "thesis-events.ndjson"

    @property
    def ledger(self) -> Path:
        return self.pipeline / "theses-ledger.json"

    @property
    def outbox(self) -> Path:
        return self.pipeline / "outbox"

    @property
    def metrics(self) -> Path:
        return self.pipeline / "metrics" / "runs.ndjson"

    # --- per episode -------------------------------------------------------

    def episode(self, video_id: str) -> Path:
        return self.episodes / video_id

    def artifact(self, video_id: str, name: str) -> Path:
        return self.episode(video_id) / name

    def marker(self, kind: str, video_id: str) -> Path:
        """Delivery markers.

        Keyed on the *delivery*, never on the artifact file: an essay `.md` is
        written before it is sent, so keying on the file would mark an episode
        done for a send that never happened.
        """
        return self.outbox / f"{kind}-{video_id}.json"

    # --- durable knowledge (written through agent tools, not by scripts) ----

    # Relative paths: the agent passes these to `write_file`, which resolves them
    # inside its own workspace. Absolute paths would point at the pipeline root,
    # where the interceptors never look.
    def vault_doc_rel(self, video_id: str) -> str:
        return f"vault/comingwave/{video_id}.md"

    def memory_doc_rel(self, video_id: str) -> str:
        return f"memory/episodes/{video_id}.md"

    def find_agent_doc(self, video_id: str, kind: str = "vault") -> Path | None:
        """Locate a document the agent wrote, wherever its workspace root lands.

        Searched rather than computed on purpose: the agent's writable root is
        `<workspace>/ws/<segment>`, and that segment is not this script's business
        to reconstruct. Scripts run as the OS user, so they can look.
        """
        rel = self.vault_doc_rel(video_id) if kind == "vault" else self.memory_doc_rel(video_id)
        base = self.root.parent
        for pattern in (f"*/ws/*/{rel}", f"*/{rel}"):
            for hit in sorted(base.glob(pattern)):
                return hit
        return None

    def ensure(self) -> None:
        for d in (self.pipeline, self.episodes, self.outbox, self.metrics.parent):
            d.mkdir(parents=True, exist_ok=True)
