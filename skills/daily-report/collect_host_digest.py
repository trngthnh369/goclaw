#!/usr/bin/env python3
# collect_host_digest.py — HOST-side collector for the daily/weekly report pipeline.
#
# Gathers two sources the container cannot see (D:\Projects and ~/.gemini are not mounted):
#   1. git commits across repos under --work-root (default D:\Projects\work)
#   2. Antigravity (Gemini IDE/CLI) sessions from conversation_summaries.db (SQLite, WAL)
# and writes a sanitized JSON digest into C:\Users\truon\.claude\host-digest\ — readable
# in-container via the existing ro mount /app/.claude-host/host-digest/.
#
# Contract (consumed by daily_report_run.load_host_digest):
#   (--hours N | --from ISO --to ISO) mutually exclusive; --out PATH required.
#   Output: {"schema":1, "generated_at", "window_from", "window_to", "git":[...],
#            "antigravity":[...], "health":{...}}
#   Exit 0 unless the output file cannot be written. Each section is best-effort
#   (failures land in health, never crash the run).
#
# Security: EVERY free-text field (commit msg, session title, intents) passes through
# sanitize_text() (redact() from digest_sessions.py + extra token-shaped drops) right before
# serialization — a runtime guard on every run, not a one-time check. No diffs, no filenames.
#
# Run (host, Windows): python collect_host_digest.py --hours 24 --out %USERPROFILE%\.claude\host-digest\latest.json
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from digest_sessions import redact  # noqa: E402  single source for redaction patterns

TZ = dt.timezone(dt.timedelta(hours=7))
DEFAULT_WORK_ROOT = r"D:\Projects\work"
DEFAULT_AGDB = os.path.expanduser(r"~\.gemini\antigravity-cli\conversation_summaries.db")
MAX_TEXT = 140
MAX_COMMITS_PER_REPO = 30
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", "_archive"}

# Commits by other people (upstream merges, colleagues) are not the user's work.
AUTHOR_PATTERNS = [
    "truongthinhnguyen30303@gmail.com",
    "trngthnh369",
    "it01@emallvietnam.vn",
]

# Extra token-shaped drops on top of redact() — commit msgs/previews have a different
# secret-exposure profile than curated Claude session text (arch-r1-f02).
_EXTRA_DROPS = [
    re.compile(r"https?://[^\s]*[:@][^\s]+"),      # URL carrying credentials
    re.compile(r"\b[A-Za-z0-9+/=_\-]{28,}\b"),     # long base64/opaque token
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),           # long hex
    re.compile(r"(?i)\b(password|passwd|secret|token|apikey|api_key)\s*[:=]\s*\S+"),
]

_health = {"git_repos_seen": 0, "git_errors": 0, "agdb_status": "missing",
           "dropped_by_sanitizer": 0, "history_joined": 0}


def sanitize_text(text: object) -> str:
    """Fail-closed sanitizer for every free-text field: redact() + drop token-shaped
    substrings + truncate. Applied at serialization time on EVERY run."""
    s = str(text or "")
    before = s
    s = redact(s)
    for pat in _EXTRA_DROPS:
        s = pat.sub("[DROPPED]", s)
    if s != before:
        _health["dropped_by_sanitizer"] += 1
    s = " ".join(s.split())
    return s[:MAX_TEXT]


def log(*a: object) -> None:
    print("[collect_host_digest]", *a, file=sys.stderr)


# ---- window ----------------------------------------------------------------

def parse_window(args: argparse.Namespace) -> tuple[dt.datetime, dt.datetime]:
    if args.hours is not None and (getattr(args, "from_iso", None) or args.to_iso):
        raise SystemExit("ARGS: --hours and --from/--to are mutually exclusive")
    if args.hours is not None:
        to = dt.datetime.now(TZ)
        return to - dt.timedelta(hours=args.hours), to
    if not (args.from_iso and args.to_iso):
        raise SystemExit("ARGS: need --hours N or both --from ISO and --to ISO")
    f, t = dt.datetime.fromisoformat(args.from_iso), dt.datetime.fromisoformat(args.to_iso)
    if f.tzinfo is None:
        f = f.replace(tzinfo=TZ)
    if t.tzinfo is None:
        t = t.replace(tzinfo=TZ)
    return f, t


# ---- git -------------------------------------------------------------------

def find_repos(root: str, maxdepth: int = 3) -> list[str]:
    """Repos = dirs containing .git (dir OR file — covers worktrees/submodules)."""
    repos: list[str] = []
    root = os.path.abspath(root)
    base_depth = root.rstrip("\\/").count(os.sep)
    for dirpath, dirnames, _filenames in os.walk(root):
        depth = dirpath.rstrip("\\/").count(os.sep) - base_depth
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        if os.path.exists(os.path.join(dirpath, ".git")):
            repos.append(dirpath)
            dirnames[:] = []  # don't descend into a repo looking for nested repos
            continue
        if depth >= maxdepth:
            dirnames[:] = []
    return repos


def repo_label(root: str, repo_path: str) -> str:
    """Top-level folder under work-root (matches project_label grouping in-container)."""
    rel = os.path.relpath(repo_path, root)
    return rel.replace("\\", "/").split("/")[0]


def collect_git(root: str, w_from: dt.datetime, w_to: dt.datetime) -> list[dict]:
    out: list[dict] = []
    author_args: list[str] = []
    for a in AUTHOR_PATTERNS:
        author_args += ["--author", a]
    for repo in find_repos(root):
        _health["git_repos_seen"] += 1
        try:
            p = subprocess.run(
                ["git", "--no-pager", "-C", repo, "log", "--all", "--no-merges",
                 *author_args,
                 f"--since={w_from.isoformat()}", f"--until={w_to.isoformat()}",
                 "--date=iso-strict", "--pretty=%h%x09%ad%x09%s"],
                capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace",
            )
            if p.returncode != 0:
                _health["git_errors"] += 1
                continue
            commits = []
            for line in p.stdout.splitlines()[:MAX_COMMITS_PER_REPO]:
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                _sha, ad, msg = parts
                commits.append({"ts": ad, "msg": sanitize_text(msg)})
            if commits:
                out.append({"repo": repo_label(root, repo), "commits": commits})
        except Exception as exc:  # noqa: BLE001
            _health["git_errors"] += 1
            log("git error:", repo_label(root, repo), exc)
    # merge nested repos sharing one top-level label
    merged: dict[str, dict] = {}
    for r in out:
        m = merged.setdefault(r["repo"], {"repo": r["repo"], "commits": []})
        m["commits"].extend(r["commits"])
    return list(merged.values())


# ---- antigravity -----------------------------------------------------------

def _snapshot_db(src: str) -> str:
    """Consistent snapshot of a WAL SQLite DB via the backup API (copy2 of the main
    file misses WAL frames — verified journal_mode=wal). Returns temp file path."""
    tmp = os.path.join(tempfile.gettempdir(), "agdb_snapshot.db")
    src_uri = "file:" + src.replace("\\", "/") + "?mode=ro"
    with sqlite3.connect(src_uri, uri=True, timeout=10) as con:
        con.execute("PRAGMA busy_timeout=5000")
        with sqlite3.connect(tmp) as dst:
            con.backup(dst)
    return tmp


def _is_work_uri(uris_json: str) -> tuple[bool, str]:
    try:
        uris = json.loads(uris_json or "[]")
    except Exception:  # noqa: BLE001
        return False, ""
    for u in uris:
        norm = str(u).lower().replace("\\", "/")
        if "/projects/work/" in norm:
            m = re.search(r"/projects/work/([^/]+)", norm)
            return True, (m.group(1) if m else "")
    return False, ""


def collect_antigravity(agdb: str, w_from: dt.datetime, w_to: dt.datetime) -> list[dict]:
    if not os.path.exists(agdb):
        _health["agdb_status"] = "missing"
        return []
    try:
        snap = _snapshot_db(agdb)
    except Exception as exc:  # noqa: BLE001
        _health["agdb_status"] = "locked"
        log("agdb snapshot failed:", exc)
        return []
    rows: list[dict] = []
    try:
        con = sqlite3.connect(snap)
        cur = con.execute(
            "SELECT conversation_id, preview, step_count, last_modified_time,"
            " workspace_uris, app_data_dir FROM conversation_summaries")
        for cid, preview, steps, mtime, uris, appdir in cur.fetchall():
            try:
                ts = dt.datetime.fromisoformat(str(mtime).replace(" ", "T", 1))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=dt.timezone.utc)
            except Exception:  # noqa: BLE001
                continue
            if not (w_from <= ts.astimezone(TZ) <= w_to):
                continue
            is_work, label = _is_work_uri(uris)
            if not is_work:
                continue
            rows.append({
                "cid": str(cid),
                "title": sanitize_text(preview),
                "project_label": label,
                "steps": int(steps or 0),
                "ts": ts.astimezone(TZ).isoformat(),
                "source": "antigravity-ide" if appdir == "antigravity" else "antigravity-cli",
                "intents": [],
            })
        con.close()
        _health["agdb_status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        _health["agdb_status"] = "schema_error"
        log("agdb query failed:", exc)
        return []

    # secondary: history.jsonl user prompts joined by conversationId (best-effort)
    try:
        hist = os.path.join(os.path.dirname(agdb), "history.jsonl")
        if os.path.exists(hist):
            by_cid = {r["cid"]: r for r in rows}
            with open(hist, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    r = by_cid.get(str(e.get("conversationId", "")))
                    if r is None or len(r["intents"]) >= 4:
                        continue
                    # re-check workspace classification on the joined event too
                    ws = str(e.get("workspace", "")).lower().replace("\\", "/")
                    if "/projects/work/" not in ws:
                        continue
                    r["intents"].append(sanitize_text(e.get("display")))
                    _health["history_joined"] += 1
    except Exception as exc:  # noqa: BLE001
        log("history join skipped:", exc)

    for r in rows:
        r.pop("cid", None)
    return rows


# ---- main ------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=None)
    ap.add_argument("--from", dest="from_iso", default=None)
    ap.add_argument("--to", dest="to_iso", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--work-root", default=DEFAULT_WORK_ROOT)
    ap.add_argument("--agdb", default=DEFAULT_AGDB)
    args = ap.parse_args()

    w_from, w_to = parse_window(args)
    log(f"window {w_from.isoformat()} -> {w_to.isoformat()}")

    git_data: list[dict] = []
    ag_data: list[dict] = []
    try:
        git_data = collect_git(args.work_root, w_from, w_to)
    except Exception as exc:  # noqa: BLE001
        _health["git_errors"] += 1
        log("git section failed:", exc)
    try:
        ag_data = collect_antigravity(args.agdb, w_from, w_to)
    except Exception as exc:  # noqa: BLE001
        _health["agdb_status"] = "schema_error"
        log("antigravity section failed:", exc)

    payload = {
        "schema": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "window_from": w_from.isoformat(),
        "window_to": w_to.isoformat(),
        "git": git_data,
        "antigravity": ag_data,
        "health": _health,
    }
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.replace(tmp, out)
    log(f"OK repos={_health['git_repos_seen']} git_groups={len(git_data)} "
        f"ag_rows={len(ag_data)} agdb={_health['agdb_status']} out={out}")


if __name__ == "__main__":
    main()
