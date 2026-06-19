#!/usr/bin/env python3
"""
digest_sessions.py — Token-efficient digest of Claude Code session JSONL files.

Reads ~/.claude/projects/**/*.jsonl (mounted read-only at /app/.claude-host/projects
inside the GoClaw container), filters events to a rolling time window, and emits a
compact JSON digest of work done — grouped by project — for an LLM to analyze.

Design constraints (from plan-review hardening):
  - stdlib only (no tzdata on Alpine -> fixed UTC+7 offset, never zoneinfo).
  - recursive glob projects/**/*.jsonl (per-project subdirs).
  - hard output budget + deterministic truncation (token guard).
  - EXCLUDE secret-bearing files; redact token patterns before emitting.
  - health fields distinguish "no work found" from "could not read source".
  - never echo raw transcript verbatim; only short, redacted snippets.

Usage:
  python3 digest_sessions.py [--projects-dir DIR] [--hours N | --from ISO --to ISO]
                             [--max-bytes N] [--max-sessions N] [--tz-offset H]
Output: a single JSON object on stdout.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

# ---- config defaults -------------------------------------------------------
DEFAULT_PROJECTS_DIR = "/app/.claude-host/projects"
DEFAULT_HOURS = 24
DEFAULT_MAX_BYTES = 60_000          # ~15k tokens hard ceiling for digest JSON
DEFAULT_MAX_SESSIONS = 40
DEFAULT_TZ_OFFSET_H = 7             # Asia/Ho_Chi_Minh (UTC+7), no tzdata needed
PROMPT_SNIPPET_CHARS = 280
ASSISTANT_SNIPPET_CHARS = 200
MAX_PROMPTS_PER_SESSION = 12
MAX_FILES_PER_SESSION = 25

# Files we must never read even if they slip under the glob (defensive).
SECRET_NAME_PATTERNS = (
    ".credentials.json", "settings.local", ".env", "session-env",
)

# Redaction patterns for anything resembling a secret/token.
REDACT_PATTERNS = [
    re.compile(p) for p in (
        r"sk-[A-Za-z0-9_\-]{16,}",
        r"sk-ant-[A-Za-z0-9_\-]{16,}",
        r"ghp_[A-Za-z0-9]{20,}",
        r"gho_[A-Za-z0-9]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"xox[baprs]-[A-Za-z0-9\-]{10,}",
        r"AKIA[0-9A-Z]{16}",
        r"AIza[0-9A-Za-z_\-]{30,}",
        r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}",
        r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}",  # JWT
        r"[A-Fa-f0-9]{40,}",  # long hex (sha/tokens)
    )
]

NOISE_PREFIXES = (
    "<local-command-caveat", "<command-name>", "<command-message>",
    "<command-args>", "Caveat:", "<system-reminder>",
)

FILE_TOOL_NAMES = {"Write", "Edit", "MultiEdit", "NotebookEdit", "str_replace_editor"}


def redact(text: str) -> str:
    if not text:
        return text
    for pat in REDACT_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def parse_ts(s):
    """Parse an ISO-8601 timestamp (with trailing Z) -> aware UTC datetime, or None."""
    if not s or not isinstance(s, str):
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        d = dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc)
    except Exception:
        return None


def is_secret_path(path: str) -> bool:
    low = path.replace("\\", "/").lower()
    return any(pat in low for pat in SECRET_NAME_PATTERNS)


def extract_user_text(message) -> str | None:
    """Return a real user prompt text, or None if this is meta/tool noise."""
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    parts = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for it in content:
            if isinstance(it, dict) and it.get("type") == "text":
                parts.append(it.get("text", ""))
            # tool_result items are tool outputs, not prompts -> skip
    text = "\n".join(p for p in parts if p).strip()
    if not text:
        return None
    if any(text.startswith(pfx) for pfx in NOISE_PREFIXES):
        return None
    return text


def summarize(args):
    projects_dir = args.projects_dir
    tzoff = dt.timezone(dt.timedelta(hours=args.tz_offset))

    # window in UTC
    if args.from_iso and args.to_iso:
        w_from = parse_ts(args.from_iso)
        w_to = parse_ts(args.to_iso)
    else:
        w_to = dt.datetime.now(dt.timezone.utc)
        w_from = w_to - dt.timedelta(hours=args.hours)
    if w_from is None or w_to is None:
        return {"error": "bad_window", "mount_status": "error"}

    health = {
        "source_files_seen": 0,
        "files_in_window": 0,
        "events_in_window": 0,
        "parse_errors": 0,
        "mount_status": "ok",
    }

    if not os.path.isdir(projects_dir):
        health["mount_status"] = "fail"
        return {
            "window_from": w_from.astimezone(tzoff).isoformat(),
            "window_to": w_to.astimezone(tzoff).isoformat(),
            "projects": [], "health": health,
            "note": f"projects dir not found: {projects_dir}",
        }

    files = [f for f in glob.glob(os.path.join(projects_dir, "**", "*.jsonl"), recursive=True)
             if not is_secret_path(f)]
    health["source_files_seen"] = len(files)

    # session_key -> aggregate
    sessions = {}

    for fpath in files:
        try:
            raw = open(fpath, encoding="utf-8", errors="replace").read().splitlines()
        except Exception:
            health["parse_errors"] += 1
            continue

        # sidecar titles (no timestamp) keyed by sessionId, applied after window check
        titles = {}
        agents = {}
        file_had_window_event = False

        # first pass: collect titles/agent names (cheap)
        for ln in raw:
            if '"ai-title"' in ln or '"agent-name"' in ln:
                try:
                    d = json.loads(ln)
                except Exception:
                    continue
                if d.get("type") == "ai-title":
                    titles[d.get("sessionId")] = d.get("aiTitle")
                elif d.get("type") == "agent-name":
                    agents[d.get("sessionId")] = d.get("agentName")

        for ln in raw:
            if not ln.strip():
                continue
            try:
                d = json.loads(ln)
            except Exception:
                health["parse_errors"] += 1
                continue
            t = d.get("type")
            if t not in ("user", "assistant", "attachment"):
                continue
            ts = parse_ts(d.get("timestamp"))
            if ts is None or not (w_from <= ts <= w_to):
                continue

            file_had_window_event = True
            health["events_in_window"] += 1

            sid = d.get("sessionId") or os.path.basename(fpath)
            cwd = d.get("cwd") or "?"
            key = sid
            sess = sessions.get(key)
            if sess is None:
                sess = {
                    "session_id": sid,
                    "title": titles.get(sid),
                    "agent": agents.get(sid),
                    "project": cwd,
                    "branches": set(),
                    "first_ts": ts, "last_ts": ts,
                    "user_turns": 0, "assistant_turns": 0,
                    "prompts": [], "tool_counts": {}, "files_touched": set(),
                    "assistant_snippets": [],
                }
                sessions[key] = sess
            sess["last_ts"] = max(sess["last_ts"], ts)
            sess["first_ts"] = min(sess["first_ts"], ts)
            if d.get("gitBranch"):
                sess["branches"].add(d["gitBranch"])
            if cwd and cwd != "?":
                sess["project"] = cwd

            msg = d.get("message")
            if t == "user" and not d.get("isMeta"):
                txt = extract_user_text(msg)
                if txt:
                    sess["user_turns"] += 1
                    if len(sess["prompts"]) < MAX_PROMPTS_PER_SESSION:
                        sess["prompts"].append(redact(txt[:PROMPT_SNIPPET_CHARS]))
            elif t == "assistant" and isinstance(msg, dict):
                sess["assistant_turns"] += 1
                content = msg.get("content")
                if isinstance(content, list):
                    for it in content:
                        if not isinstance(it, dict):
                            continue
                        it_t = it.get("type")
                        if it_t == "tool_use":
                            name = it.get("name", "tool")
                            sess["tool_counts"][name] = sess["tool_counts"].get(name, 0) + 1
                            if name in FILE_TOOL_NAMES:
                                inp = it.get("input") or {}
                                fp = inp.get("file_path") or inp.get("path")
                                if fp and len(sess["files_touched"]) < MAX_FILES_PER_SESSION:
                                    sess["files_touched"].add(fp)
                        elif it_t == "text" and len(sess["assistant_snippets"]) < 3:
                            snip = (it.get("text") or "").strip()
                            if snip:
                                sess["assistant_snippets"].append(redact(snip[:ASSISTANT_SNIPPET_CHARS]))

        if file_had_window_event:
            health["files_in_window"] += 1

    if health["source_files_seen"] == 0:
        health["mount_status"] = "empty_source"

    # ---- build output, sorted by recency, with budget truncation -----------
    ordered = sorted(sessions.values(), key=lambda s: s["last_ts"], reverse=True)
    ordered = ordered[: args.max_sessions]

    def to_local(d):
        return d.astimezone(tzoff).isoformat(timespec="minutes")

    out_sessions = []
    for s in ordered:
        out_sessions.append({
            "session_id": s["session_id"][:8],
            "title": s["title"],
            "agent": s["agent"],
            "project": s["project"],
            "branches": sorted(s["branches"]),
            "from": to_local(s["first_ts"]),
            "to": to_local(s["last_ts"]),
            "user_turns": s["user_turns"],
            "assistant_turns": s["assistant_turns"],
            "tool_counts": s["tool_counts"],
            "files_touched": sorted(s["files_touched"]),
            "prompts": s["prompts"],
            "assistant_snippets": s["assistant_snippets"],
        })

    # group sessions by project for readability
    by_project = {}
    for os_ in out_sessions:
        by_project.setdefault(os_["project"], []).append(os_)
    projects = [{"project": p, "sessions": ss} for p, ss in by_project.items()]
    projects.sort(key=lambda x: max((s["to"] for s in x["sessions"]), default=""), reverse=True)

    result = {
        "window_from": to_local(w_from),
        "window_to": to_local(w_to),
        "tz": f"UTC+{args.tz_offset}",
        "projects": projects,
        "health": health,
    }

    # deterministic budget truncation: drop assistant_snippets, then prompts, then sessions
    def size(obj):
        return len(json.dumps(obj, ensure_ascii=False))

    if size(result) > args.max_bytes:
        for p in result["projects"]:
            for s in p["sessions"]:
                s["assistant_snippets"] = []
    if size(result) > args.max_bytes:
        for p in result["projects"]:
            for s in p["sessions"]:
                s["prompts"] = s["prompts"][:4]
    while size(result) > args.max_bytes and result["projects"]:
        # drop the project with the oldest activity (last in sorted list)
        result["projects"].pop()
        result["truncated"] = True

    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects-dir", default=DEFAULT_PROJECTS_DIR)
    ap.add_argument("--hours", type=float, default=DEFAULT_HOURS)
    ap.add_argument("--from", dest="from_iso", default=None)
    ap.add_argument("--to", dest="to_iso", default=None)
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    ap.add_argument("--max-sessions", type=int, default=DEFAULT_MAX_SESSIONS)
    ap.add_argument("--tz-offset", type=float, default=DEFAULT_TZ_OFFSET_H)
    args = ap.parse_args()
    # Force UTF-8 stdout regardless of host locale (Windows cp1252 / Alpine C.UTF-8).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        result = summarize(args)
    except Exception as e:  # never crash the cron turn; emit a health failure
        result = {"error": str(e), "health": {"mount_status": "error"}}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
