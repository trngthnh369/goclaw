#!/usr/bin/env python3
# daily_report_run.py — deterministic orchestrator for the daily report GENERATE step.
#
# Why: the agent (Zip) is unreliable at orchestrating a multi-step flow. This script owns the
# whole pipeline so the agent's only job is to run ONE exec. The LLM (Gemini ag-pro via the
# GoClaw gateway, agent zip-crazy) is reduced to a pure function (digest -> items JSON) with a
# deterministic fallback if that fails.
#
# Pipeline (v3, 2026-07-18 — review-BEFORE-render, D6):
#   1. digest_sessions.py                   -> Claude Code sessions (work projects only)
#   2. load_host_digest()                   -> git commits + Antigravity sessions (host collector,
#                                              /app/.claude-host/host-digest/latest.json)
#   3. merge -> group_tasks (alias-first)   -> task groups
#   4. LLM describe (chunked, count-checked, name/pct rules)  [fallback: mechanical]
#   5. write report.json + active.json (stage=review) + post TEXT review to Discord
#   -> user replies DUYỆT  -> daily_report_publish.py renders PNG + sends Zalo + sheet write-back.
#
# Flags: --hours N | --require-llm | --dry-run (print items, no state/post) | --no-post (state,
#        no Discord — Friday batch) | --post-pending (post any unposted review states — Friday)
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_report_sheet as drs  # noqa: E402  (reads/writes the weekly task sheet)

WORK = "/app/workspace/_daily-report"
DIGEST = f"{WORK}/digest_sessions.py"
HOST_DIGEST = "/app/.claude-host/host-digest/latest.json"
ACTIVE = f"{WORK}/active.json"
REPORT = f"{WORK}/report.json"
ACTIVE_WEEKLY = f"{WORK}/active_weekly.json"
REPORT_WEEKLY = f"{WORK}/report_weekly.json"

BASE = os.environ.get("GOCLAW_SELF_URL", "http://127.0.0.1:18790")


def _resolve_token() -> str:
    """Gateway token. When the agent (Zip) runs these scripts via its exec tool, GoClaw v3.14+
    does NOT expose GOCLAW_GATEWAY_TOKEN to the exec env (security), so the gateway call 401s.
    Fall back to a chmod-600 token file written into the (container-only) workspace volume."""
    t = os.environ.get("GOCLAW_GATEWAY_TOKEN", "")
    if t:
        return t
    try:
        with open(f"{WORK}/.gwtoken", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


TOKEN = _resolve_token()
USER_ID = "trngthnh369"
ANALYST_AGENT = os.environ.get("DAILY_REPORT_AGENT", "zip-crazy")
LLM_URL = os.environ.get("DAILY_REPORT_LLM_URL", "")

DISCORD_CHANNEL = "discord-bot"
DISCORD_TARGET = "1512686472334147735"

# Báo cáo công việc cho sếp → CHỈ lấy project trong workspace work, bỏ personal.
WORK_FILTER = os.environ.get("DAILY_REPORT_WORK_FILTER", r"projects\work").lower().replace("\\", "/")

TZ = timezone(timedelta(hours=7))
LLM_CHUNK = 25  # max task groups per describe call (3x sources -> avoid mid-array truncation)


def is_work_project(path: object) -> bool:
    norm = str(path or "").lower().replace("\\", "/")
    return WORK_FILTER in norm


def project_label(path: object):
    """Gom theo PROJECT TOP-LEVEL dưới work\\ (vd build-workflow, openclaw)."""
    norm = str(path or "").replace("\\", "/")
    low = norm.lower()
    marker = "projects/work/"
    i = low.find(marker)
    if i == -1:
        seg = norm.rstrip("/").split("/")[-1]
        return seg or None
    segs = [s for s in norm[i + len(marker):].split("/") if s]
    return segs[0] if segs else None


def log(*a):
    print("[daily_report_run]", *a, file=sys.stderr)


def http_post(path: str, payload: dict, extra_headers: dict | None = None, timeout: int = 300) -> dict:
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "X-GoClaw-User-Id": USER_ID,
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_digest(hours: int) -> dict:
    proc = subprocess.run(
        ["python3", DIGEST, "--hours", str(hours), "--max-bytes", "60000"],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise SystemExit(f"DIGEST_FAIL rc={proc.returncode} {proc.stderr[:300]}")
    return json.loads(proc.stdout)


# ---- host digest (git + antigravity, collected on the Windows host) --------

def load_host_digest(path: str = HOST_DIGEST, max_age_h: float = 3.0) -> dict | None:
    """Shared loader for latest.json (daily) / week.json (weekly). Schema + freshness gated:
    a silently-dead collector must degrade us to sessions-only, never feed stale data.
    Distinct WARNs: dir absent (mount/overlay wrong) vs file stale (collector not running)."""
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        log(f"WARN host-digest DIR ABSENT ({d}) — mount/compose overlay sai? sessions-only")
        return None
    if not os.path.exists(path):
        log(f"WARN host-digest file missing ({os.path.basename(path)}) — collector chưa chạy? sessions-only")
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            hd = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        log("WARN host-digest unreadable:", exc)
        return None
    if hd.get("schema") != 1:
        log(f"WARN host-digest schema={hd.get('schema')} != 1 — bỏ qua")
        return None
    try:
        gen = datetime.fromisoformat(hd["generated_at"])
        age_h = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        log("WARN host-digest generated_at unparseable — bỏ qua")
        return None
    if age_h > max_age_h:
        log(f"WARN host-digest STALE ({age_h:.1f}h > {max_age_h}h) — sessions-only")
        return None
    return hd


def synth_host_sessions(hd: dict | None, start_idx: int) -> list:
    """Antigravity convo -> 1 pseudo-session; git repo -> 1 pseudo-session (commit msgs as
    intents). Same shape as flatten_sessions output so the alias pipeline treats all three
    sources identically (D5 alias-first dedupe)."""
    out: list = []
    if not hd:
        return out
    idx = start_idx
    for r in hd.get("antigravity", []):
        out.append({
            "idx": idx, "project": r.get("project_label", ""), "src": "antigravity",
            "name": str(r.get("title") or "antigravity session"),
            "edits": int(r.get("steps") or 0),
            "intent": [str(r.get("title") or "")[:140]] + [str(x)[:140] for x in (r.get("intents") or [])[:3]],
        })
        idx += 1
    for g in hd.get("git", []):
        msgs = [c.get("msg", "") for c in g.get("commits", [])][:6]
        if not msgs:
            continue
        out.append({
            "idx": idx, "project": g.get("repo", ""), "src": "git",
            "name": f"{g.get('repo', '')} (commits)",
            "edits": len(g.get("commits", [])),
            "intent": [m[:140] for m in msgs],
        })
        idx += 1
    return out


VALID_PROGRESS = ("done", "doing", "blocked", "new")


def flatten_sessions(projects: list) -> tuple[list, list]:
    """Flat list of work sessions (1 session = 1 công việc). Bỏ tên file/path khỏi data feed LLM."""
    sessions: list = []
    order: list = []
    for p in projects:
        label = p.get("project", "")
        if label and label not in order:
            order.append(label)
        for s in p.get("sessions", []):
            tools = s.get("tool_counts") or {}
            name = s.get("title") or s.get("agent") or s.get("session_id") or "session"
            sessions.append({
                "idx": len(sessions),
                "project": label,
                "src": "claude",
                "name": str(name),
                "edits": tools.get("Edit", 0) + tools.get("Write", 0),
                "intent": [str(x)[:140] for x in (s.get("prompts") or [])[:3]],
            })
    return sessions, order


ALIASES_PATH = f"{WORK}/task_aliases.json"


def load_aliases() -> list:
    try:
        with open(ALIASES_PATH, encoding="utf-8") as fh:
            return json.load(fh).get("aliases", [])
    except Exception as exc:  # noqa: BLE001
        log("aliases load failed:", exc)
        return []


def resolve_task(session: dict, aliases: list) -> tuple[str | None, str | None, bool]:
    """Map 1 session -> (task hiển thị, tên-trong-sheet|None, known)."""
    hay = (session["name"] + " " + " ".join(session.get("intent", []))).lower()
    for a in aliases:
        for pat in a.get("match", []):
            if pat and pat.lower() in hay:
                if a.get("ignore"):
                    return (None, None, False)  # IGNORE
                return (a.get("task") or session["name"], (a.get("sheet") or None), True)
    return (session["name"], None, False)


def group_tasks(sessions: list, aliases: list) -> list:
    """Gộp session theo task (deterministic, alias-first — D5). Unknown groups from different
    sources are kept SEPARATE (no label-merge: one project = many tasks); provenance in srcs."""
    groups: dict[str, dict] = {}
    order: list = []
    for s in sessions:
        name, sheet, known = resolve_task(s, aliases)
        if name is None:  # ignored alias
            continue
        if name not in groups:
            groups[name] = {"name": name, "sheet": sheet, "known": known, "intents": [], "srcs": []}
            order.append(name)
        g = groups[name]
        g["intents"].extend(s.get("intent", []))
        src = s.get("src", "claude")
        if src not in g["srcs"]:
            g["srcs"].append(src)
        if sheet and not g["sheet"]:
            g["sheet"] = sheet
        if known:
            g["known"] = True
    return [groups[n] for n in order]


def duplicate_candidates(groups: list) -> list:
    """Unknown groups sharing a normalized-token overlap with another group — surfaced in
    --dry-run so the user curates task_aliases.json instead of the code guessing merges."""
    cands = []
    toks = [set(re.findall(r"\w{4,}", g["name"].lower())) for g in groups]
    for i, g in enumerate(groups):
        if g["known"]:
            continue
        for j, h in enumerate(groups):
            if i != j and toks[i] and len(toks[i] & toks[j]) >= 2:
                cands.append(f"'{g['name']}' ({'+'.join(g['srcs'])}) ~ '{h['name']}'")
                break
    return cands


DESCRIBE_PROMPT = """Bạn viết chi tiết cho báo cáo công việc cuối ngày. Mỗi phần tử dưới đây là 1 TASK đã có tên; một số task kèm "sheet_pct" = % hiện tại trên sheet kế hoạch tuần.

CHỈ trả về MỘT JSON array (không markdown, không giải thích, không gọi tool), mỗi phần tử:
{"idx":<idx>,"name":"<xem quy tắc>","sheet_idx":<số hoặc null>,"detail":"<chi tiết ≤14 từ>","progress":"done|doing|blocked|new","percent":<0-100>,"uncertain":<true nếu bạn không chắc tên/%>}

QUY TẮC BẮT BUỘC:
- GIỮ NGUYÊN idx. Nếu "known"=true → name GIỮ NGUYÊN y hệt (tên chuẩn theo sheet — KHÔNG bịa tên mới). Nếu "known"=false → đổi name (slug kỹ thuật) thành tên công việc tiếng Việt đọc được, viết hoa đầu, KHÔNG gạch ngang.
- "sheet_idx": nếu SHEET_TASKS được cung cấp bên dưới, CHỌN task sheet phù hợp nhất với task này (dùng sidx). PHẢI KHỚP CHỦ ĐỀ — "AI Training" ≠ "AI competitor monitor", "OpenClaw" ≠ "AI News". Nếu không có task nào CÙNG CHỦ ĐỀ → null. KHÔNG ép khớp chỉ vì cùng có chữ "AI".
- detail dựa trên "intents". TUYỆT ĐỐI KHÔNG nhắc tên file/đường dẫn/script (.py/.js/.sh/.mjs)/branch/hàm.
- PHÂN LOẠI intent: intents chỉ là KIỂM TRA/check lại/verify/xem lại → task đã hoàn thành trước đó, giờ chỉ re-check → progress="done", percent giữ cao (≥ sheet_pct, thường 90-100). KHÔNG coi việc kiểm tra là việc mới.
- % KHÔNG LÙI: nếu có sheet_pct thì percent PHẢI ≥ sheet_pct (tiến độ không đi lùi vì 1 phiên re-check).
- progress suy từ intents (đang làm dở→doing; chờ/vướng→blocked; mới bàn→new; xong/kiểm tra lại→done).
- percent: done≈90-100, doing 30-70, blocked 20-50, new 5-20 (và luôn ≥ sheet_pct nếu có).
- Không chắc tên task hay % → "uncertain":true (sẽ hiển thị ⚠️ cho user sửa khi review).

"""

SHEET_TASKS_PROMPT = """SHEET_TASKS (danh sách task trong sheet kế hoạch tuần — dùng sidx để map):
"""


def _describe_items(groups: list, by_idx: dict) -> list:
    items = []
    for i, g in enumerate(groups):
        it = by_idx.get(i) or {}
        name = g["name"] if g["known"] else ((it.get("name") or "").strip() or g["name"])
        detail = (it.get("detail") or "").strip() or (g["intents"][0][:80] if g["intents"] else "")
        progress = it.get("progress") if it.get("progress") in VALID_PROGRESS else "doing"
        items.append({
            "title": name,
            "note": detail,
            "progress": progress,
            "percent": it.get("percent"),
            "uncertain": bool(it.get("uncertain")),
            "sheet_name": g["sheet"],
            "sheet_idx": it.get("sheet_idx"),
        })
    return items


def llm_chat(messages: list) -> str:
    if LLM_URL:
        data = json.dumps({"messages": messages}).encode("utf-8")
        req = urllib.request.Request(LLM_URL, data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"]
    payload = {"model": f"agent:{ANALYST_AGENT}", "stream": False, "messages": messages}
    return http_post("/v1/chat/completions", payload, timeout=300)["choices"][0]["message"]["content"]


def _describe_chunk(feed: list, sheet_feed: list | None = None) -> dict | None:
    """One describe call -> {idx: item}. None on any failure (caller falls back)."""
    prompt = DESCRIBE_PROMPT + "TASKS:\n" + json.dumps(feed, ensure_ascii=False)
    if sheet_feed:
        prompt += "\n\n" + SHEET_TASKS_PROMPT + json.dumps(sheet_feed, ensure_ascii=False)
    messages = [{"role": "user", "content": prompt}]
    try:
        content = llm_chat(messages)
    except Exception as exc:  # noqa: BLE001
        log("LLM describe failed:", exc)
        return None
    m = re.search(r"\[.*\]", content, re.S)
    if not m:
        log("LLM describe: no JSON array in response")
        return None
    try:
        arr = json.loads(m.group(0))
    except Exception as exc:  # noqa: BLE001
        log("JSON parse failed:", exc)
        return None
    by_idx = {it["idx"]: it for it in arr if isinstance(it, dict) and "idx" in it}
    want = {f["idx"] for f in feed}
    got = want & set(by_idx)
    if len(got) < len(want):
        # count-validate: a truncated response silently drops work items — treat as failure
        log(f"LLM describe: item count mismatch (want {len(want)}, got {len(got)}) — fallback")
        return None
    return by_idx


def llm_describe(groups: list, sheet_pct_by_name: dict | None = None,
                 sheet_tasks: list | None = None) -> list | None:
    """Chunked describe with per-chunk count validation. sheet_pct feeds the monotonic rule.
    sheet_tasks feeds the LLM so it can map work → sheet rows via sheet_idx."""
    if not groups:
        return None
    sheet_pct_by_name = sheet_pct_by_name or {}
    sheet_feed = None
    if sheet_tasks:
        sheet_feed = [{"sidx": i, "name": t["name"], "pct": t.get("pct"), "status": t.get("status", "")}
                      for i, t in enumerate(sheet_tasks)]
    feed_all = []
    for i, g in enumerate(groups):
        entry = {"idx": i, "name": g["name"], "known": g["known"], "intents": g["intents"][:4]}
        key = _norm(g.get("sheet") or g["name"])
        if key in sheet_pct_by_name and sheet_pct_by_name[key] is not None:
            entry["sheet_pct"] = sheet_pct_by_name[key]
        feed_all.append(entry)
    by_idx: dict = {}
    for start in range(0, len(feed_all), LLM_CHUNK):
        chunk = feed_all[start:start + LLM_CHUNK]
        res = _describe_chunk(chunk, sheet_feed)
        if res is None:
            return None
        by_idx.update(res)
    return _describe_items(groups, by_idx) if by_idx else None


def fallback_describe(groups: list) -> list:
    """Khi LLM fail — mô tả cơ học từ intent."""
    return _describe_items(groups, {})


def _norm(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _fuzzy_score(a: str, b: str) -> float:
    """Token-overlap ratio between two normalized strings. Returns 0.0-1.0.
    Uses 3+ char tokens to avoid common short-word false positives (e.g. 'ai')."""
    ta = set(re.findall(r"\w{3,}", _norm(a)))
    tb = set(re.findall(r"\w{3,}", _norm(b)))
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    return overlap / min(len(ta), len(tb))


FUZZY_THRESHOLD = 0.5


def match_sheet(items: list, sheet_tasks: list) -> None:
    """Gắn sheet_match (TÊN task sheet khớp) + is_new cho mỗi item.
    Priority: (1) LLM sheet_idx, (2) alias sheet_name exact, (3) fuzzy token overlap."""
    by_name = {_norm(t["name"]): t for t in sheet_tasks}
    used_sheet_names: set = set()
    for it in items:
        sname = it.pop("sheet_name", None)
        sidx = it.pop("sheet_idx", None)
        matched = None

        # (1) LLM sheet_idx — direct mapping from LLM, validated by fuzzy sanity check
        if sidx is not None and isinstance(sidx, int) and 0 <= sidx < len(sheet_tasks):
            candidate = sheet_tasks[sidx]
            if _norm(candidate["name"]) not in used_sheet_names:
                score = _fuzzy_score(it["title"], candidate["name"])
                if score >= 0.3:
                    matched = candidate

        # (2) alias-based exact match
        if matched is None:
            target = sname if sname else it["title"]
            matched = by_name.get(_norm(target))

        # (3) fuzzy token-overlap fallback
        if matched is None:
            title = it["title"]
            best_score, best_task = 0.0, None
            for t in sheet_tasks:
                if _norm(t["name"]) in used_sheet_names:
                    continue
                score = _fuzzy_score(title, t["name"])
                if score > best_score:
                    best_score, best_task = score, t
            if best_score >= FUZZY_THRESHOLD and best_task is not None:
                matched = best_task

        if matched is not None:
            it["sheet_match"] = matched["name"]
            used_sheet_names.add(_norm(matched["name"]))
        else:
            it["sheet_match"] = None
        it["is_new"] = matched is None


DEFAULT_PCT = {"done": 100, "doing": 50, "blocked": 30, "new": 10}


def enforce_monotonic(items: list, sheet_tasks: list) -> None:
    """Code-level guarantee of the %-không-lùi rule (prompt compliance is advisory): an item
    matched to a sheet row never carries a % LOWER than the sheet's current % — a re-check
    session must not make a finished task regress."""
    by_name = {_norm(t["name"]): t for t in sheet_tasks}
    for it in items:
        t = by_name.get(_norm(it.get("sheet_match")))
        spct = t.get("pct") if t else None
        if spct is None:
            continue
        try:
            pct = int(round(float(it.get("percent"))))
        except (TypeError, ValueError):
            pct = DEFAULT_PCT.get(it.get("progress", "doing"), 50)
        if pct < spct:
            it["percent"] = spct
            if spct >= 100:
                it["progress"] = "done"


# ---- D6: review state + TEXT review ----------------------------------------

def _write_json_atomic(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


def write_review_state(report: dict) -> None:
    """report.json + active.json at stage=review (kind=daily). PNG is rendered at PUBLISH
    (review-before-render, D6) — png_path stays empty here."""
    now = datetime.now(TZ)
    _write_json_atomic(REPORT, report)
    _write_json_atomic(ACTIVE, {
        "run_id": now.strftime("%Y%m%d-%H%M"),
        "kind": "daily",
        "stage": "review",
        "report_date": report.get("report_date", now.strftime("%Y-%m-%d")),
        "png_path": "",
        "posted": False,
        "created_at": now.isoformat(),
        "published_at": "",
    })


PROGRESS_LABEL = {"done": "Xong", "doing": "Đang làm", "blocked": "Blocked", "new": "Mới"}


def build_review_text(report: dict) -> str:
    """Human-reviewable TEXT of the report (D6). PNG comes only after DUYỆT."""
    kind = report.get("kind", "daily")
    lines: list = []
    if kind == "weekly":
        lines.append(f"📅 **Báo cáo tuần {report.get('week_tab', '')}** — bản nháp chờ DUYỆT.")
        if not report.get("refreshed", True):
            lines.append("⚠️ % chưa refresh (LLM lỗi) — số liệu lấy theo sheet hiện có.")
        secs = report.get("sections", {})
        for key, label in (("done", "✅ Hoàn thành"), ("doing", "🔨 Đang làm"),
                           ("blocked", "⛔ Blocked"), ("carry", "📌 Tồn đọng chuyển tuần sau")):
            rows = secs.get(key, [])
            if not rows:
                continue
            lines.append(f"\n**{label}** ({len(rows)})")
            for r in rows:
                pct = r.get("percent")
                pct_s = f" — {pct}%" if pct is not None else ""
                note = f" · {r['note']}" if r.get("note") else ""
                lines.append(f"• {r.get('title', '')}{pct_s}{note}")
    else:
        date_s = report.get("report_date", "")
        src = report.get("source", "")
        lines.append(f"📋 **Báo cáo công việc {date_s}** — bản nháp chờ DUYỆT ({src}).")
        for i, it in enumerate(report.get("items", []), 1):
            try:
                pct = int(round(float(it.get("percent"))))
            except (TypeError, ValueError):
                pct = DEFAULT_PCT.get(it.get("progress", "doing"), 50)
            label = PROGRESS_LABEL.get(it.get("progress", "doing"), "Đang làm")
            flag = " ⚠️" if it.get("uncertain") else ""
            new = " (mới)" if it.get("is_new") else ""
            lines.append(f"{i}. **{it.get('title', '')}** — {pct}% {label}{new}{flag}")
            if it.get("note"):
                lines.append(f"   ↳ {it['note']}")
    lines.append("\n➡️ Reply **DUYỆT** để render ảnh + đăng nhóm TEAM AI, hoặc **'sửa: <yêu cầu>'** "
                 "(daily) / **'sửa tuần: <yêu cầu>'** (weekly).")
    return "\n".join(lines)


def post_discord(message: str, forward_reason: str):
    payload = {
        "tool": "message",
        "args": {
            "action": "send",
            "channel": DISCORD_CHANNEL,
            "target": DISCORD_TARGET,
            "forward": True,
            "forward_reason": forward_reason,
            "message": message,
        },
    }
    resp = http_post("/v1/tools/invoke", payload, timeout=60)
    if "error" in resp:
        raise SystemExit(f"DISCORD_POST_FAIL {json.dumps(resp['error'])[:300]}")
    return resp


def post_text_chunked(text: str, reason: str) -> None:
    """Discord ~2000 char limit — chunk on line boundaries at 1800."""
    buf = ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > 1800:
            post_discord(buf, reason)
            buf = line
        else:
            buf = buf + "\n" + line if buf else line
    if buf:
        post_discord(buf, reason)


def post_pending() -> int:
    """Post TEXT review for every review-stage state not yet posted (Friday batch: daily with
    --no-post + weekly --report land here so ONE visible batch = one DUYỆT)."""
    posted = 0
    for active_path, report_path, reason in (
            (ACTIVE, REPORT, "daily-report review text"),
            (ACTIVE_WEEKLY, REPORT_WEEKLY, "weekly-report review text")):
        if not (os.path.exists(active_path) and os.path.exists(report_path)):
            continue
        with open(active_path, encoding="utf-8") as fh:
            active = json.load(fh)
        if active.get("stage") != "review" or active.get("posted"):
            continue
        with open(report_path, encoding="utf-8") as fh:
            report = json.load(fh)
        post_text_chunked(build_review_text(report), reason)
        active["posted"] = True
        _write_json_atomic(active_path, active)
        posted += 1
    print(f"OK posted={posted}")
    return posted


def fmt_window(digest: dict) -> tuple[str, str]:
    def f(iso, suffix=""):
        try:
            dt = datetime.fromisoformat(iso)
            return dt.strftime("%d/%m %H:%M") + suffix
        except Exception:  # noqa: BLE001
            return iso or ""
    return f(digest.get("window_from", "")), f(digest.get("window_to", ""), " (UTC+7)")


def main():
    hours = 24
    if "--hours" in sys.argv:
        hours = int(sys.argv[sys.argv.index("--hours") + 1])
    require_llm = "--require-llm" in sys.argv
    dry_run = "--dry-run" in sys.argv
    no_post = "--no-post" in sys.argv
    if not TOKEN and not dry_run:
        raise SystemExit("FATAL: GOCLAW_GATEWAY_TOKEN not set")
    if "--post-pending" in sys.argv:
        post_pending()
        return

    digest = run_digest(hours)
    health = digest.get("health", {})
    if health.get("mount_status") not in ("ok",):
        if not dry_run:
            post_discord(f"[daily-report] Nguồn session lỗi (mount_status={health.get('mount_status')}). Bỏ qua hôm nay.",
                         "daily-report source error")
        raise SystemExit(f"mount_status={health.get('mount_status')}")

    # Work-only Claude sessions, normalized by top-level project label.
    all_projects = digest.get("projects", [])
    merged: dict[str, dict] = {}
    for p in all_projects:
        if not is_work_project(p.get("project")):
            continue
        label = project_label(p.get("project"))
        if not label:
            continue
        merged.setdefault(label, {"project": label, "sessions": []})["sessions"].extend(p.get("sessions", []))
    digest["projects"] = list(merged.values())
    log(f"projects: {len(all_projects)} total -> {len(digest['projects'])} work ({', '.join(merged)})")

    sessions, _order = flatten_sessions(digest["projects"])

    # Host digest (git + antigravity) — freshness/schema gated; absent -> sessions-only.
    host = load_host_digest()
    host_sessions = synth_host_sessions(host, len(sessions))
    if host_sessions:
        log(f"host digest: +{len(host_sessions)} pseudo-sessions "
            f"(git+antigravity, agdb={((host or {}).get('health') or {}).get('agdb_status')})")
    sessions += host_sessions

    if not sessions:
        if not dry_run:
            post_discord("[daily-report] Không có hoạt động ở dự án work trong 24h — không tạo báo cáo.",
                         "daily-report no work")
        raise SystemExit("no work activity in window")

    today = datetime.now(TZ).date()

    # Weekly sheet (source of truth for task names + current %). READ path — find_week_tab's
    # past-week fallback is fine here; WRITES go through ensure_current_week_tab (publish).
    sheet_tab = None
    sheet_tasks: list = []
    try:
        tab = drs.find_week_tab(today)
        sheet_tab = tab["title"]
        sheet_tasks = drs.read_tasks(sheet_tab)["tasks"]
        log(f"sheet tab '{sheet_tab}': {len(sheet_tasks)} tasks")
    except Exception as exc:  # noqa: BLE001
        log("sheet read failed (report sẽ không map sheet):", exc)

    aliases = load_aliases()
    groups = group_tasks(sessions, aliases)
    log(f"aliases: {len(aliases)} | task groups: {len(groups)} ({', '.join(g['name'] for g in groups)})")

    sheet_pct = {_norm(t["name"]): t.get("pct") for t in sheet_tasks}
    items = llm_describe(groups, sheet_pct, sheet_tasks)
    source = "LLM"
    if items is None:
        if require_llm:
            log("LLM unavailable and --require-llm set: exit 3 so wrapper can retry")
            raise SystemExit(3)
        items = fallback_describe(groups)
        source = "fallback"
    match_sheet(items, sheet_tasks)
    enforce_monotonic(items, sheet_tasks)
    log(f"analysis source: {source} | items: {len(items)}")

    report = {
        "kind": "daily",
        "items": items,
        "report_date": today.strftime("%Y-%m-%d"),
        "sheet_tab": sheet_tab,
        "source": source,
    }

    if dry_run:
        print(json.dumps({"items": items, "duplicate_candidates": duplicate_candidates(groups)},
                         ensure_ascii=False, indent=1))
        return

    write_review_state(report)
    if no_post:
        print(f"OK source={source} kind=daily posted=false (state written; batch post later)")
        return
    post_text_chunked(build_review_text(report), "daily-report review text")
    with open(ACTIVE, encoding="utf-8") as fh:
        active = json.load(fh)
    active["posted"] = True
    _write_json_atomic(ACTIVE, active)
    print(f"OK source={source} kind=daily posted=true items={len(items)}")


if __name__ == "__main__":
    main()
