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


def _ts_ge(raw: object, since: datetime | None) -> bool:
    """True if the collector timestamp is at/after `since`. Unparseable -> kept (fail-open: the
    collector already window-filtered; we only re-slice a WIDER file)."""
    if since is None:
        return True
    try:
        ts = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=TZ)
    return ts >= since


def synth_host_sessions(hd: dict | None, start_idx: int, since: datetime | None = None) -> list:
    """Antigravity convo -> 1 pseudo-session; git repo -> 1 pseudo-session (commit msgs as
    intents). Same shape as flatten_sessions output so the alias pipeline treats all three
    sources identically (D5 alias-first dedupe).

    `since` re-slices the file to the caller's window. REQUIRED for the daily run: on Friday the
    wrapper collects the whole WEEK into week.json and copies it to latest.json, so without this
    the daily report presented the entire week's commits as today's work."""
    out: list = []
    if not hd:
        return out
    idx = start_idx
    dropped = 0
    for r in hd.get("antigravity", []):
        if not _ts_ge(r.get("ts"), since):
            dropped += 1
            continue
        out.append({
            "idx": idx, "project": r.get("project_label", ""), "src": "antigravity",
            "name": str(r.get("title") or "antigravity session"),
            "edits": int(r.get("steps") or 0),
            "intent": [str(r.get("title") or "")[:140]] + [str(x)[:140] for x in (r.get("intents") or [])[:3]],
        })
        idx += 1
    for g in hd.get("git", []):
        commits = [c for c in g.get("commits", []) if _ts_ge(c.get("ts"), since)]
        dropped += len(g.get("commits", [])) - len(commits)
        msgs = [c.get("msg", "") for c in commits][:6]
        if not msgs:
            continue
        out.append({
            "idx": idx, "project": g.get("repo", ""), "src": "git",
            "name": f"{g.get('repo', '')} (commits)",
            "edits": len(commits),
            "intent": [m[:140] for m in msgs],
        })
        idx += 1
    if dropped:
        log(f"host digest: dropped {dropped} entries outside window (since={since.isoformat()})")
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
                # 6x200 rather than 3x140: the LLM decides task identity and sheet binding from
                # this text, and three truncated prompts were not enough to tell two tasks in the
                # same repo apart (it invented generic names like "Công việc không xác định").
                "intent": [str(x)[:200] for x in (s.get("prompts") or [])[:6]],
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


def _pattern_hit(pat: str, hay: str) -> bool:
    """Short patterns match on word boundaries only — 'pcs' used to fire inside unrelated words."""
    p = pat.lower()
    if len(p) < 6 and re.fullmatch(r"[\w\s-]+", p):
        return re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", hay) is not None
    return p in hay


def resolve_task(session: dict, aliases: list) -> tuple[str | None, str | None, bool]:
    """Map 1 session -> (task hiển thị, tên-trong-sheet|None, known).

    Scans EVERY alias and keeps the LONGEST matching pattern, i.e. the most specific one. First-hit
    made the order of task_aliases.json load-bearing: the catch-all {"match":["openclaw"]} sat above
    the specific entries, so any session merely mentioning openclaw was relabelled "Vận hành
    OpenClaw server" and its real work vanished into that bucket."""
    hay = (session["name"] + " " + " ".join(session.get("intent", []))).lower()
    best_len, best = -1, None
    for a in aliases:
        for pat in a.get("match", []):
            if pat and len(pat) > best_len and _pattern_hit(pat, hay):
                best_len, best = len(pat), a
    if best is None:
        return (session["name"], None, False)
    if best.get("ignore"):
        return (None, None, False)
    return (best.get("task") or session["name"], (best.get("sheet") or None), True)


HEX_NAME_RE = re.compile(r"^[0-9a-f]{6,}(-[0-9a-f]{4,})*$", re.I)
MIN_INTENT_CHARS = 20


def is_junk_group(g: dict) -> str | None:
    """Reason this group must NOT reach the report, or None. A session whose title is just its
    session-id (Claude writes one when no ai-title was generated) carries no meaning, and a group
    with almost no intent text makes the LLM invent a task ("Công việc không xác định / 5%")."""
    if g["known"]:
        return None  # an alias vouched for it — never drop
    text = " ".join(str(x) for x in g["intents"]).strip()
    if HEX_NAME_RE.match(g["name"].strip()) and len(text) < MIN_INTENT_CHARS:
        return "session-id name, no usable intent"
    if len(text) < MIN_INTENT_CHARS:
        return f"intent too thin ({len(text)} chars)"
    return None


def group_tasks(sessions: list, aliases: list) -> list:
    """Gộp session theo task (deterministic, alias-first — D5). Unknown groups from different
    sources are kept SEPARATE (no label-merge: one project = many tasks); provenance in srcs.
    Junk groups (session-id names / no usable intent) are dropped BEFORE the LLM so they can
    never become a report item nor an appended sheet row."""
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
    kept = []
    for n in order:
        reason = is_junk_group(groups[n])
        if reason:
            log(f"drop junk group '{n}': {reason}")
            continue
        kept.append(groups[n])
    return kept


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
{"idx":<idx>,"name":"<xem quy tắc>","same_as":<idx hoặc null>,"detail":"<chi tiết ≤14 từ>","progress":"done|doing|blocked|new","percent":<0-100>,"uncertain":<true nếu bạn không chắc tên/%>}

QUY TẮC BẮT BUỘC:
- GIỮ NGUYÊN idx. Nếu "known"=true → name GIỮ NGUYÊN y hệt (tên chuẩn theo sheet — KHÔNG bịa tên mới). Nếu "known"=false → đổi name (slug kỹ thuật) thành tên công việc tiếng Việt đọc được, viết hoa đầu, KHÔNG gạch ngang.
- "same_as": nếu task này VÀ một task khác trong danh sách thực chất là CÙNG MỘT công việc (cùng dự án, cùng mục tiêu — vd 2 phiên cùng làm công cụ điều phối hàng hoá), điền idx của task kia (idx NHỎ HƠN); ngược lại null. CHỈ gộp khi chắc chắn cùng một việc — khác mục tiêu/khác dự án thì để null dù tên na ná.
- detail dựa trên "intents". TUYỆT ĐỐI KHÔNG nhắc tên file/đường dẫn/script (.py/.js/.sh/.mjs)/branch/hàm.
- PHÂN LOẠI intent: intents chỉ là KIỂM TRA/check lại/verify/xem lại → task đã hoàn thành trước đó, giờ chỉ re-check → progress="done", percent giữ cao (≥ sheet_pct, thường 90-100). KHÔNG coi việc kiểm tra là việc mới.
- % KHÔNG LÙI: nếu có sheet_pct thì percent PHẢI ≥ sheet_pct (tiến độ không đi lùi vì 1 phiên re-check).
- progress suy từ intents (đang làm dở→doing; chờ/vướng→blocked; mới bàn→new; xong/kiểm tra lại→done).
- percent: done≈90-100, doing 30-70, blocked 20-50, new 5-20 (và luôn ≥ sheet_pct nếu có).
- Không chắc tên task hay % → "uncertain":true (sẽ hiển thị ⚠️ cho user sửa khi review).

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
            "same_as": it.get("same_as"),
            # deterministic key (session/alias name, NOT the LLM's title, which is reworded every
            # run) — what learned_bindings is keyed on
            "group_key": g["name"],
            # raw evidence kept for the binding adjudicator; stripped before report.json
            "evidence": " | ".join(str(x) for x in g["intents"][:4])[:600],
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


def _describe_chunk(feed: list) -> dict | None:
    """One describe call -> {idx: item}. None on any failure (caller falls back)."""
    prompt = DESCRIBE_PROMPT + "TASKS:\n" + json.dumps(feed, ensure_ascii=False)
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
    Sheet ROWS are deliberately NOT in this prompt: binding is decided later by verify_bindings,
    so keeping the 31-row list out of every describe chunk shrinks the slowest call in the run."""
    if not groups:
        return None
    sheet_pct_by_name = sheet_pct_by_name or {}
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
        res = _describe_chunk(chunk)
        if res is None:
            return None
        by_idx.update(res)
    return _describe_items(groups, by_idx) if by_idx else None


def fallback_describe(groups: list) -> list:
    """Khi LLM fail — mô tả cơ học từ intent."""
    return _describe_items(groups, {})


def merge_duplicate_items(items: list) -> list:
    """Fold items that describe ONE task into one row.

    group_tasks keys on the raw session title, so the same work opened in three sessions became
    three tasks ("Công cụ điều phối hàng hóa" / "Điều phối hàng hóa nội vùng" / "Xây dựng công cụ
    phân phối hàng"). The duplication is only visible AFTER the LLM names things in Vietnamese.

    Pairing comes from the LLM's `same_as` field, NOT token overlap: Vietnamese titles are made of
    short syllables, so a token heuristic merged "Kiểm tra lỗi kết nối trên hệ thống quét" with
    "Kiểm tra mã nguồn nền tảng" (tested — a 5-way false merge that also hijacked a sheet binding).

    Guard that survives an LLM mistake: two items bound to DIFFERENT sheet rows are different tasks
    by definition and are never merged, so a bad `same_as` can only affect display, never a % write.
    Merge keeps the sheet-bound member, the highest %, the most advanced progress, unions notes."""
    order = {"new": 0, "blocked": 1, "doing": 2, "done": 3}
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def bound(i: int) -> str:
        return _norm(items[i].get("sheet_match"))

    for i, it in enumerate(items):
        j = it.get("same_as")
        if not isinstance(j, int) or not (0 <= j < len(items)) or j == i:
            continue
        if bound(i) and bound(j) and bound(i) != bound(j):
            log(f"merge REJECT '{it.get('title')}' ~ '{items[j].get('title')}': "
                f"bound to different sheet rows")
            continue
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    clusters: dict = {}
    for i, it in enumerate(items):
        clusters.setdefault(find(i), []).append(it)

    out = []
    for root in sorted(clusters, key=lambda r: min(items.index(x) for x in clusters[r])):
        members = clusters[root]
        if len(members) == 1:
            members[0].pop("same_as", None)
            out.append(members[0])
            continue
        # prefer a sheet-bound member as the surviving row (keeps the % write-back target)
        head = next((m for m in members if m.get("sheet_match")), members[0])
        merged = dict(head)
        notes, seen = [], set()
        for m in members:
            n = (m.get("note") or "").strip()
            if n and _norm(n) not in seen:
                seen.add(_norm(n))
                notes.append(n)
            if (m.get("percent") or 0) > (merged.get("percent") or 0):
                merged["percent"] = m.get("percent")
            if order.get(m.get("progress"), 0) > order.get(merged.get("progress"), 0):
                merged["progress"] = m.get("progress")
            merged["uncertain"] = merged.get("uncertain") or m.get("uncertain")
        merged["note"] = "; ".join(notes)[:220]
        merged.pop("same_as", None)
        log(f"merge {len(members)} items -> '{merged.get('title')}': "
            + " | ".join(m.get("title", "") for m in members))
        out.append(merged)
    return out


def _norm(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _dice_score(a: str, b: str) -> float:
    """Symmetric token overlap, used ONLY as the LLM-down fallback in match_sheet.

    The earlier score divided by the shorter side, so any short generic title scored 1.00 against
    a longer row containing its words ("Cập nhật mã nguồn hệ thống" -> "Cập nhật mã nguồn hệ thống
    tối ưu quảng cáo Meta", a different project). Dice penalises that (0.67)."""
    ta = set(re.findall(r"\w{3,}", _norm(a)))
    tb = set(re.findall(r"\w{3,}", _norm(b)))
    if not ta or not tb:
        return 0.0
    return 2 * len(ta & tb) / (len(ta) + len(tb))


# Blind guessing with no agent opinion needs a high bar; whatever it matches is flagged uncertain
# so the ⚠️ shows in the review text before any % is written back.
FUZZY_BLIND_THRESHOLD = 0.7


VERIFY_PROMPT = """Bạn đối chiếu công việc thực tế với DANH SÁCH TASK trong sheet kế hoạch tuần.

Với mỗi CANDIDATE, quyết định nó có phải LÀ MỘT trong các task của sheet hay không.

CHỈ trả về MỘT JSON array (không markdown, không giải thích ngoài field reason):
[{"idx":<idx>,"sheet_idx":<sidx hoặc null>,"reason":"<≤12 từ>"}]

QUY TẮC:
- Căn cứ chính là "evidence" (nội dung phiên làm việc thật), KHÔNG phải độ giống của chữ.
- Khác ngôn ngữ VẪN khớp nếu là cùng một việc: "Tính toán đặt hàng lại theo kích cỡ" = "AI Size Reorder Calculator". Tên viết tắt/khác cách gọi cũng vậy.
- Giống chữ nhưng KHÁC việc thì phải null: "Điều phối hàng hoá" ≠ "AI Product Trending", "Cập nhật mã nguồn dự án Byteflow" ≠ "Cập nhật mã nguồn hệ thống tối ưu quảng cáo Meta" (khác dự án), "AI Training" ≠ "AI competitor monitor".
- KHÔNG ép khớp chỉ vì cùng có chữ "AI"/"hệ thống"/"cập nhật".
- "proposed_sidx" là phỏng đoán trước đó — được phép bác bỏ hoặc đổi sang sidx khác.
- Không có dòng nào CÙNG một việc → sheet_idx = null (task mới, sẽ được đề xuất thêm vào sheet).
- MỖI sidx dùng cho TỐI ĐA 1 candidate.

"""


def verify_bindings(cands: list, sheet_tasks: list, used: set) -> dict | None:
    """Agent adjudicates task <-> sheet-row identity from session evidence. Returns
    {item_index: sheet_idx|None}, or None when the LLM is unavailable.

    This replaces lexical gating. Token overlap fails in both directions here: it accepted
    "Điều phối sản phẩm" -> "AI Product Trending" (0.5, different work, wrote 100% to the wrong
    row) and rejected "Tính toán đặt hàng lại theo kích cỡ" -> "AI Size Reorder Calculator" (0.0,
    the same task named in another language). Only something that reads the session can tell
    those apart, which is why the agent decides and the code only enforces uniqueness."""
    if not cands or not sheet_tasks:
        return {}
    avail = [{"sidx": i, "name": t["name"], "pct": t.get("pct")}
             for i, t in enumerate(sheet_tasks) if _norm(t["name"]) not in used]
    if not avail:
        return {}
    feed = [{"idx": i, "title": c["title"], "evidence": c.get("evidence", ""),
             "proposed_sidx": c.get("_proposed")} for i, c in enumerate(cands)]
    prompt = (VERIFY_PROMPT + "SHEET_TASKS:\n" + json.dumps(avail, ensure_ascii=False)
              + "\n\nCANDIDATES:\n" + json.dumps(feed, ensure_ascii=False))
    try:
        content = llm_chat([{"role": "user", "content": prompt}])
    except Exception as exc:  # noqa: BLE001
        log("verify bindings failed:", exc)
        return None
    m = re.search(r"\[.*\]", content, re.S)
    if not m:
        log("verify bindings: no JSON array in response")
        return None
    try:
        arr = json.loads(m.group(0))
    except Exception as exc:  # noqa: BLE001
        log("verify bindings: JSON parse failed:", exc)
        return None
    out: dict = {}
    taken: set = set()
    for r in arr:
        if not isinstance(r, dict):
            continue
        i, s = r.get("idx"), r.get("sheet_idx")
        if not isinstance(i, int) or not (0 <= i < len(cands)):
            continue
        if not isinstance(s, int) or not (0 <= s < len(sheet_tasks)):
            out[i] = None
            continue
        name = _norm(sheet_tasks[s]["name"])
        if name in used or name in taken:   # code enforces one row per item
            log(f"verify: sidx {s} already taken — '{cands[i]['title']}' -> new")
            out[i] = None
            continue
        taken.add(name)
        out[i] = s
        log(f"verify BIND '{cands[i]['title']}' -> '{sheet_tasks[s]['name']}' "
            f"({str(r.get('reason', ''))[:60]})")
    for i, c in enumerate(cands):
        if i not in out:
            out[i] = None
    return out


LEARNED_PATH = f"{WORK}/learned_bindings.json"


def load_learned() -> dict:
    """{normalized group_key -> sheet row name} confirmed by a previous DUYỆT.

    The agent decides binding from session evidence, but that decision is not stable run to run:
    on back-to-back dry-runs it bound the Lazada scanner task correctly once and missed it the
    next. Memoising what the user approved turns a recurring task into a deterministic match and
    shrinks the candidate list the agent has to adjudicate.

    Keyed on group_key (session/alias name), NOT the report title: the LLM rewords the title every
    run ("Kiểm tra lỗi token Lazada…" / "Kiểm tra lỗi mã xác thực Lazada" / "Sửa lỗi token…"), so a
    title-keyed memo almost never hits."""
    try:
        with open(LEARNED_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_learned(pairs: dict) -> None:
    """Called at publish time (post-DUYỆT) — consent is what makes a binding trustworthy."""
    cur = load_learned()
    cur.update(pairs)
    _write_json_atomic(LEARNED_PATH, cur)


def match_sheet(items: list, sheet_tasks: list) -> None:
    """Gắn sheet_match (TÊN task sheet khớp) + is_new cho mỗi item.

    (1) exact name (alias `sheet` field or an identical title) -> bind, no LLM needed.
    (2) everything else -> the AGENT adjudicates from session evidence (verify_bindings).
    (3) LLM unreachable -> conservative lexical fallback (Dice >= FUZZY_BLIND_THRESHOLD), flagged
        uncertain so ⚠️ shows in the review text.

    Blind token overlap is no longer a primary tier: it is wrong in both directions on this data
    (binds different projects that share generic Vietnamese words, misses the same task named in
    another language)."""
    by_name = {_norm(t["name"]): t for t in sheet_tasks}
    learned = load_learned()
    used_sheet_names: set = set()
    pending: list = []

    for it in items:
        sname = it.pop("sheet_name", None)
        sidx = it.pop("sheet_idx", None)
        # tier 0: a binding the user already approved for this session/alias group
        target = learned.get(_norm(it.get("group_key"))) or (sname if sname else it["title"])
        matched = by_name.get(_norm(target))
        if matched is not None and _norm(matched["name"]) not in used_sheet_names:
            it["sheet_match"] = matched["name"]
            it["is_new"] = False
            used_sheet_names.add(_norm(matched["name"]))
            log(f"match tier=exact '{it['title']}' -> '{matched['name']}'")
            continue
        it["_proposed"] = sidx if isinstance(sidx, int) else None
        pending.append(it)

    decided = verify_bindings(pending, sheet_tasks, used_sheet_names) if pending else {}

    for i, it in enumerate(pending):
        s = decided.get(i) if decided is not None else None
        if decided is None:
            # LLM down: lexical last resort so a day without the agent still writes back some %
            best_score, best_task = 0.0, None
            for t in sheet_tasks:
                if _norm(t["name"]) in used_sheet_names:
                    continue
                score = _dice_score(it["title"], t["name"])
                if score > best_score:
                    best_score, best_task = score, t
            if best_score >= FUZZY_BLIND_THRESHOLD and best_task is not None:
                it["sheet_match"] = best_task["name"]
                it["uncertain"] = True
                used_sheet_names.add(_norm(best_task["name"]))
                log(f"match tier=fallback-fuzzy score={best_score:.2f} "
                    f"'{it['title']}' -> '{best_task['name']}'")
            else:
                it["sheet_match"] = None
                log(f"match NONE '{it['title']}' (LLM down, không đủ căn cứ)")
        elif s is not None:
            it["sheet_match"] = sheet_tasks[s]["name"]
            used_sheet_names.add(_norm(sheet_tasks[s]["name"]))
            # a FRESH agent binding gets ⚠️ so you can veto it during review; once approved it
            # becomes a learned tier-0 match and stops being flagged
            it["uncertain"] = True
        else:
            it["sheet_match"] = None
            log(f"match NONE '{it['title']}' (task mới — chờ duyệt trước khi ghi sheet)")
        it["is_new"] = it["sheet_match"] is None

    for it in items:
        it.pop("_proposed", None)
        it.pop("evidence", None)


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
                           ("blocked", "⛔ Blocked"), ("nopct", "❔ Chưa có %")):
            rows = secs.get(key, [])
            if not rows:
                continue
            lines.append(f"\n**{label}** ({len(rows)})")
            for r in rows:
                pct = r.get("percent")
                pct_s = f" — {pct}%" if pct is not None else ""
                note = f" · {r['note']}" if r.get("note") else ""
                lines.append(f"• {r.get('title', '')}{pct_s}{note}")
        idle = int(secs.get("idle_count") or 0)
        if idle:
            lines.append(f"\n📌 **Tồn đọng**: {idle} task chưa động tới tuần này (xem sheet).")
        prop = secs.get("proposed_new") or []
        if prop:
            lines.append(f"\n🆕 **Task mới phát hiện, CHƯA ghi sheet** ({len(prop)}): "
                         + "; ".join(str(x) for x in prop[:10])
                         + ("…" if len(prop) > 10 else ""))
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
            if it.get("skip_sheet"):
                new = " (mới — ĐÃ BỎ, không ghi sheet)"
            elif it.get("is_new"):
                new = " (mới)"
            else:
                new = ""
            lines.append(f"{i}. **{it.get('title', '')}** — {pct}% {label}{new}{flag}")
            if it.get("note"):
                lines.append(f"   ↳ {it['note']}")
        # New tasks are the ONLY items that add rows to the weekly sheet. Surfacing them as an
        # explicit list turns DUYỆT into informed consent — they used to be appended silently,
        # which is how the tab grew 23 -> 31 rows in three days.
        proposed = [str(i) for i, it in enumerate(report.get("items", []), 1)
                    if it.get("is_new") and not it.get("skip_sheet")]
        if proposed:
            lines.append(f"\n🆕 **Task MỚI sẽ được thêm vào sheet** (mục {', '.join(proposed)}). "
                         f"Không muốn thêm mục nào → reply **'bỏ mới: <số>, <số>'**.")
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
    # `since` re-slices to the daily window: on Friday latest.json holds the WHOLE WEEK
    # (run_daily_report.ps1 copies week.json over it), which used to leak into "today's work".
    host = load_host_digest()
    host_sessions = synth_host_sessions(host, len(sessions),
                                        since=datetime.now(TZ) - timedelta(hours=hours))
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
    items = merge_duplicate_items(items)   # after match_sheet: bindings decide what may merge
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
