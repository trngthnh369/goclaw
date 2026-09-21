#!/usr/bin/env python3
# plan_pipeline.py — plan-centric binding + progress for the daily report (2026-09-21).
#
# Old flow: session -> group -> LLM names it and GUESSES a % from a fixed band -> look for a sheet
# row (one row per item). That produced duplicate tasks (two HR sessions -> two new rows), packets
# reported as top-level tasks, and % re-guessed from scratch every day (AI Training 93->95->75->60).
#
# New flow: evidence groups are bound MANY-TO-ONE to the rows the user planned in the weekly tab,
# then ONE progress judgement is made per planned row, against the row's goal (column "Mô tả"),
# starting from the row's current % (the sheet is the source of truth), with code-enforced rules.
# Work that binds to no planned row is reported as "Ngoài kế hoạch" and only reaches the sheet when
# the user replies "thêm: U<n>". Ongoing operations ("Vận hành") carry no %.
#
# History (history/YYYY-MM-DD.json) holds APPROVED reports only (written at publish). It is a
# bridge for rows whose % cell is blank, never a floor above what the sheet says.
import hashlib
import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

TZ = timezone(timedelta(hours=7))
WORK = os.environ.get("DAILY_REPORT_WORK", "/app/workspace/_daily-report")
HISTORY_DIR = f"{WORK}/history"

DAILY_CAP = 25          # max % gain per day without verified completion evidence
UNVERIFIED_DONE_CAP = 95
HISTORY_DAYS = 10
MIN_DONE_QUOTE = 12     # a completion quote shorter than this proves nothing

# Report text is for the boss: no paths, file names, env/flag names, commit hashes, section refs.
# The prompt forbids them, but the model still leaks some ("Commit 7561e16", "outputs/_hr_rep"),
# so code removes them: an offending bullet is dropped, an offending token in a sentence is cut.
TECH_TOKEN_RE = re.compile(
    r"\S*[/\\]\S*"                         # paths, URLs
    r"|\S+\.(?:py|js|mjs|ts|sh|ps1|json|md|html|csv|xlsx|sql|yaml|yml)\b"
    r"|\b[0-9a-f]{7,40}\b"                    # commit hashes
    r"|\b[A-Za-z0-9]+_[A-Za-z0-9_]+\b"        # snake/SCREAMING_SNAKE identifiers
    r"|§\s*\d+"                               # section refs
    r"|\S+=\S+"                               # key=value (rc=0, flags)
)


def norm(s: object) -> str:
    return " ".join(unicodedata.normalize("NFC", str(s or "")).split()).strip().lower()


def goal_hash(goal: object) -> str:
    g = norm(goal)
    return hashlib.sha1(g.encode("utf-8")).hexdigest()[:10] if g else ""


# ---- history (approved reports only) ------------------------------------------------------------

def history_path(day: str) -> str:
    return f"{HISTORY_DIR}/{day}.json"


def write_history(report: dict, day: str) -> str:
    """Persist the report the user APPROVED. Called at publish only — a draft nobody approved must
    never become tomorrow's baseline."""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    rec = {"approved": True, "report_date": day, "sheet_tab": report.get("sheet_tab"),
           "approved_at": datetime.now(TZ).isoformat(), "items": report.get("items", [])}
    path = history_path(day)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False))
    os.replace(tmp, path)
    return path


def load_history(today: date, days: int = HISTORY_DAYS) -> list:
    """Approved records of the previous `days` days, newest first. Today's file is excluded: a
    same-day regenerate must not read its own earlier approval as 'yesterday'."""
    out = []
    for back in range(1, days + 1):
        day = (today - timedelta(days=back)).isoformat()
        try:
            with open(history_path(day), encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if rec.get("approved"):
            out.append(rec)
    return out


def history_baseline(history: list, row_name: str, ghash: str) -> dict:
    """Latest approved state of a row: {'pct', 'notes', 'goal_changed'}. A row whose goal text
    changed since that record gets no numeric baseline (the old % measured a different goal)."""
    notes: list = []
    base: dict = {"pct": None, "notes": notes, "goal_changed": False}
    key = norm(row_name)
    for rec in history:
        for it in rec.get("items", []):
            if it.get("plan") != "planned" or norm(it.get("sheet_match")) != key:
                continue
            if it.get("note") and len(notes) < 3:
                notes.append(f"{rec.get('report_date')}: {it['note']}")
            if base["pct"] is None and not base["goal_changed"]:
                if it.get("goal_hash", "") != ghash:
                    base["goal_changed"] = True
                elif isinstance(it.get("percent"), int):
                    base["pct"] = it["percent"]
    return base


def counted_uids(history: list) -> set:
    return {u for rec in history for it in rec.get("items", []) for u in it.get("uids", [])}


# ---- plan rows ---------------------------------------------------------------------------------

def ongoing_names(aliases: list) -> set:
    names: set = set()
    for a in aliases:
        if a.get("ongoing"):
            names.update(norm(x) for x in (a.get("task"), a.get("sheet")) if x)
    return names


def plan_rows(sheet_tasks: list, aliases: list) -> list:
    ongoing = ongoing_names(aliases)
    rows = []
    for i, t in enumerate(sheet_tasks):
        rows.append({"sidx": i, "name": t["name"].strip(), "goal": t.get("goal", ""),
                     "pct": t.get("pct"), "status": t.get("status", ""),
                     "ongoing": bool(t.get("ongoing")) or norm(t["name"]) in ongoing})
    return rows


# ---- binding ------------------------------------------------------------------------------------

BIND_PROMPT = """Bạn gán các NHÓM công việc thực tế (phiên làm việc, commit) vào DÒNG KẾ HOẠCH của sheet tuần.

CHỈ trả về MỘT JSON array (không markdown, không giải thích), mỗi nhóm đúng 1 phần tử:
{"gidx":<gidx>,"sheet_idx":<sidx hoặc null>,"reason":"<≤12 từ: nhóm này góp vào mục tiêu nào của dòng>","name":"<tên việc tiếng Việt nếu sheet_idx=null, ngược lại null>","detail":"<≤14 từ: việc ĐÃ LÀM, dựa trên result>","progress":"done|doing|blocked|new","percent":<0-100>,"same_as":<gidx nhỏ hơn nếu CÙNG một việc ngoài kế hoạch, ngược lại null>}

QUY TẮC:
- Căn cứ là "asked" + "result" (nội dung thật), KHÔNG phải độ giống chữ. Khác ngôn ngữ vẫn khớp nếu cùng việc.
- Packet / bước con / một phần của một dòng kế hoạch → gán vào dòng đó. NHIỀU nhóm được gán cùng MỘT sidx.
- Không chắc nhóm thuộc dòng nào, hoặc chỉ cùng dự án mà khác mục tiêu → sheet_idx = null. KHÔNG ép khớp vì chung chữ "AI"/"hệ thống"/"cập nhật".
- detail/name TUYỆT ĐỐI không nhắc tên file, đường dẫn, script, branch, hàm.
- name, detail, progress, percent, same_as chỉ cần cho nhóm sheet_idx=null (việc ngoài kế hoạch).

"""


def _group_feed(g: dict, gidx: int) -> dict:
    asked = [str(x)[:160] for x in g.get("intents", [])]
    asked = asked[:2] + asked[-2:] if len(asked) > 4 else asked
    return {"gidx": gidx, "project": g.get("project", ""), "name": g["name"],
            "asked": asked, "result": " | ".join(o[:300] for o in g.get("outcomes", [])[:3])}


def _parse_array(content: str) -> list | None:
    m = re.search(r"\[.*\]", content or "", re.S)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except ValueError:
        return None
    return [x for x in arr if isinstance(x, dict)] if isinstance(arr, list) else None


def bind_groups(groups: list, rows: list, learned: dict, llm) -> tuple[dict, dict, str]:
    """Returns (binding, unplanned_desc, source).
    binding: gidx -> {"kind": "row", "sidx", "tier"} | {"kind": "ongoing"} | {"kind": "unplanned"}
    unplanned_desc: gidx -> LLM description for unbound groups.

    Tiers: 0 learned (user-approved) -> 1 alias sheet / exact name -> ongoing alias -> 2 LLM.
    Ongoing rows are reachable through tiers 0-1 only: a many-to-one LLM bind would let a broad
    "Vận hành ..." row swallow real work that belongs under "Ngoài kế hoạch"."""
    by_name = {norm(r["name"]): r for r in rows}
    binding: dict = {}
    pending: list = []
    for gidx, g in enumerate(groups):
        target = learned.get(norm(g["name"])) or g.get("sheet") or g["name"]
        row = by_name.get(norm(target)) or by_name.get(norm(g["name"]))
        if row is not None:
            binding[gidx] = {"kind": "row", "sidx": row["sidx"], "tier": "exact"}
        elif g.get("ongoing"):
            binding[gidx] = {"kind": "ongoing"}
        else:
            pending.append(gidx)

    desc: dict = {}
    source = "LLM"
    cand_rows = [r for r in rows if not r["ongoing"]]
    if pending:
        arr = None
        if llm is not None:
            feed_rows = [{"sidx": r["sidx"], "name": r["name"], "goal": r["goal"][:300]} for r in cand_rows]
            prompt = (BIND_PROMPT + "ROWS:\n" + json.dumps(feed_rows, ensure_ascii=False)
                      + "\n\nGROUPS:\n" + json.dumps([_group_feed(groups[i], i) for i in pending],
                                                      ensure_ascii=False))
            arr = _parse_array(llm(prompt))
        valid = {r["sidx"] for r in cand_rows}
        got = {x.get("gidx"): x for x in (arr or []) if x.get("gidx") in pending}
        if arr is None or len(got) < len(pending):
            source = "fallback"  # missing or truncated answer: bind nothing by guess
            got = {}
        for gidx in pending:
            x = got.get(gidx) or {}
            s = x.get("sheet_idx")
            if isinstance(s, int) and s in valid and str(x.get("reason") or "").strip():
                binding[gidx] = {"kind": "row", "sidx": s, "tier": "llm",
                                 "reason": str(x["reason"])[:80]}
            else:
                binding[gidx] = {"kind": "unplanned"}
                desc[gidx] = x
    return binding, desc, source


# ---- per-row progress ---------------------------------------------------------------------------

ROW_PROMPT = """Bạn viết tiến độ cho từng DÒNG KẾ HOẠCH tuần, dựa trên bằng chứng công việc hôm nay.

CHỈ trả về MỘT JSON array (không markdown), mỗi dòng đúng 1 phần tử:
{"sidx":<sidx>,"summary":"<≤20 từ: việc ĐÃ LÀM hôm nay>","sub":["<≤10 từ>", ...tối đa 3],"progress":"done|doing|blocked","percent":<0-100>,"advanced_part":"<≤12 từ: phần nào của mục tiêu tiến lên>","done_quote":"<câu NGUYÊN VĂN chép từ result chứng minh TOÀN BỘ mục tiêu đã xong, hoặc null>","remaining":"<≤12 từ: còn lại gì để xong mục tiêu>","uncertain":<true nếu không chắc>}

QUY TẮC:
- percent = mức hoàn thành TÍCH LUỸ so với "goal" (mục tiêu của dòng), KHÔNG phải của riêng hôm nay.
- Có "prev_pct" → percent ≥ prev_pct; bằng chứng không làm mục tiêu tiến thêm → giữ nguyên prev_pct.
- done_quote chỉ điền khi result có câu chứng minh xong TOÀN BỘ mục tiêu (xong một packet/bước con KHÔNG tính).
- "goal" trống → ước lượng theo tên dòng và đặt uncertain=true.
- "ongoing"=true (việc vận hành liên tục) → chỉ cần summary và sub; percent=null, done_quote=null.
- summary/sub: kết quả cụ thể; TUYỆT ĐỐI không nhắc tên file, đường dẫn, script, branch, hàm.

"""


def clean_text(s: object) -> str:
    """Strip technical tokens from a sentence and tidy the leftover spacing/punctuation."""
    out = TECH_TOKEN_RE.sub("", str(s or ""))
    out = re.sub(r"\s+([,;.:])", r"\1", re.sub(r"\s{2,}", " ", out))
    # a cut token can leave a dangling connector/preposition at the end ("... vào")
    return re.sub(r"(?:\s*(?:và|vào|cho|của|tại|lên|trên|từ|,|;)\s*)+$", "", out).strip(" ,;")


def clean_bullets(items: list) -> list:
    """Bullets that carried a technical token are dropped whole (half a bullet reads worse)."""
    return [str(b).strip() for b in items if b and not TECH_TOKEN_RE.search(str(b))]


def describe_rows(feeds: list, llm) -> dict | None:
    """One call for every planned/ongoing row with evidence today. None on failure (count check)."""
    if not feeds or llm is None:
        return None if feeds else {}
    arr = _parse_array(llm(ROW_PROMPT + "ROWS:\n" + json.dumps(feeds, ensure_ascii=False)))
    if arr is None:
        return None
    want = {f["sidx"] for f in feeds}
    got = {x.get("sidx"): x for x in arr if x.get("sidx") in want}
    return got if len(got) == len(want) else None


def _as_pct(v: object) -> int | None:
    try:
        return max(0, min(100, int(round(float(v)))))
    except (TypeError, ValueError):
        return None


def enforce_progress(item: dict, prev_pct: int | None, evidence: str, has_goal: bool,
                     goal_changed: bool) -> None:
    """Code-level % rules, in precedence order:
    1. a row already at 100 stays 100; otherwise never below the baseline (prev_pct);
    2. a NEW 100 needs a done_quote found verbatim in today's evidence, else capped at 95;
    3. without that quote the gain is capped at +DAILY_CAP per day (only with a known baseline);
    4. missing goal / changed goal / first estimate are flagged for review (⚠️).
    A row without a goal (blank Mô tả) can never be verified complete: capped at 95 and +25/day."""
    flags = item.setdefault("flags", [])
    p = _as_pct(item.get("percent"))
    quote = str(item.get("done_quote") or "").strip()
    # with no written goal there is nothing the quote can prove complete
    verified = has_goal and len(quote) >= MIN_DONE_QUOTE and norm(quote) in norm(evidence)
    if p is None:
        p = prev_pct
    if prev_pct is not None and prev_pct >= 100:
        p = 100
    elif p is not None:
        if p >= 100 and not verified:
            p = UNVERIFIED_DONE_CAP
            flags.append("100% cần bằng chứng xong")
        if prev_pct is not None:
            if p - prev_pct > DAILY_CAP and not verified:
                p = prev_pct + DAILY_CAP
                flags.append(f"tăng >{DAILY_CAP}%/ngày, đã giới hạn")
            p = max(p, prev_pct)
    if prev_pct is None:
        flags.append("mốc đầu, % ước lượng")
    if not has_goal:
        flags.append("dòng chưa có mục tiêu (Mô tả)")
    if goal_changed:
        flags.append("mục tiêu đổi, bỏ mốc cũ")
    item["percent"] = p
    item["prev_pct"] = prev_pct
    if p == 100:
        item["progress"] = "done"
    elif item.get("progress") not in ("doing", "blocked"):
        item["progress"] = "doing"
    item["uncertain"] = bool(item.get("uncertain")) or bool(flags)


# ---- assembly -----------------------------------------------------------------------------------

def _evidence_blob(gs: list) -> str:
    return " ".join(" ".join(g.get("outcomes", [])) + " " + " ".join(g.get("intents", [])) for g in gs)


def _row_feed(row: dict, gs: list, base: dict) -> dict:
    ev = [{"asked": [str(x)[:160] for x in g.get("intents", [])[-3:]],
           "result": " | ".join(o[:300] for o in g.get("outcomes", [])[:3])} for g in gs]
    feed = {"sidx": row["sidx"], "name": row["name"], "goal": row["goal"][:400],
            "ongoing": row["ongoing"], "evidence": ev[:8]}
    if not row["ongoing"]:
        feed["prev_pct"] = base["pct"]
        feed["history"] = base["notes"]
    return feed


def _uids(gs: list) -> list:
    return [u for g in gs for u in g.get("uids", [])]


def build_plan_items(groups: list, rows: list, binding: dict, desc: dict, row_desc: dict | None,
                     history: list) -> list:
    """Items in display order: planned (P), ongoing (O), unplanned (U)."""
    by_row: dict = {}
    for gidx, b in binding.items():
        if b["kind"] == "row":
            by_row.setdefault(b["sidx"], []).append(gidx)
    planned, ongoing = [], []
    for row in rows:
        gidxs = by_row.get(row["sidx"])
        if not gidxs:
            continue  # no evidence today: the row keeps its sheet % and is not reported
        gs = [groups[i] for i in gidxs]
        d = (row_desc or {}).get(row["sidx"]) or {}
        ghash = goal_hash(row["goal"])
        base = history_baseline(history, row["name"], ghash)
        prev = row["pct"] if row["pct"] is not None else base["pct"]
        item = {
            "title": row["name"], "sheet_match": row["name"], "is_new": False,
            "note": clean_text(d.get("summary") or _mechanical_summary(gs)),
            "sub": clean_bullets(d.get("sub") or [])[:3],
            "remaining": clean_text(d.get("remaining")),
            "progress": d.get("progress") or "doing", "percent": d.get("percent"),
            "done_quote": d.get("done_quote"), "uncertain": bool(d.get("uncertain")),
            "group_key": gs[0]["name"], "group_keys": [g["name"] for g in gs],
            "uids": _uids(gs), "goal_hash": ghash, "units": len(gs),
            "fresh_bind": any(binding[i].get("tier") == "llm" for i in gidxs),
        }
        if row["ongoing"]:
            # no finish line: no %, and no "còn: ..." either
            item.update({"plan": "ongoing", "progress": "ongoing", "percent": None, "remaining": ""})
            ongoing.append(item)
            continue
        item["plan"] = "planned"
        if row_desc is None:  # LLM down: no guess, keep the baseline
            item["percent"] = prev
        enforce_progress(item, prev, _evidence_blob(gs), bool(row["goal"].strip()),
                         base["goal_changed"])
        if item["fresh_bind"]:
            item["uncertain"] = True
            item["flags"].append("gán dòng mới bởi LLM, kiểm tra")
        planned.append(item)

    ongoing += _standalone_ongoing(groups, binding)
    unplanned = _unplanned_items(groups, binding, desc)
    items = planned + ongoing + unplanned
    for prefix, part in (("P", planned), ("O", ongoing), ("U", unplanned)):
        for n, it in enumerate(part, 1):
            it["id"] = f"{prefix}{n}"
            it.pop("done_quote", None)
    return items


def _mechanical_summary(gs: list) -> str:
    for g in gs:
        for o in g.get("outcomes", []):
            if o:
                return " ".join(o.split()[:20])
    return str((gs[0].get("intents") or [""])[0])[:120]


def _standalone_ongoing(groups: list, binding: dict) -> list:
    """Ongoing alias groups with no sheet row: reported as 'Vận hành', never appended."""
    by_name: dict = {}
    for gidx, b in binding.items():
        if b["kind"] == "ongoing":
            by_name.setdefault(groups[gidx]["name"], []).append(groups[gidx])
    return [{"title": name, "sheet_match": None, "is_new": False, "plan": "ongoing",
             "progress": "ongoing", "percent": None, "note": clean_text(_mechanical_summary(gs)), "sub": [],
             "uncertain": False, "group_key": name, "group_keys": [name], "uids": _uids(gs),
             "units": len(gs)} for name, gs in by_name.items()]


def _unplanned_items(groups: list, binding: dict, desc: dict) -> list:
    """Unbound groups, clustered by the LLM's same_as (only among unbound groups)."""
    pending = [i for i, b in sorted(binding.items()) if b["kind"] == "unplanned"]
    parent = {i: i for i in pending}

    def find(i: int) -> int:
        while parent[i] != i:
            i = parent[i]
        return i

    for i in pending:
        j = (desc.get(i) or {}).get("same_as")
        if isinstance(j, int) and j in parent and j != i:
            parent[find(i)] = find(j)
    clusters: dict = {}
    for i in pending:
        clusters.setdefault(find(i), []).append(i)
    order = {"new": 0, "blocked": 1, "doing": 2, "done": 3}
    items = []
    for root in sorted(clusters, key=lambda r: min(clusters[r])):
        members = clusters[root]
        gs = [groups[i] for i in members]
        ds = [desc.get(i) or {} for i in members]
        d0 = ds[0]
        name = clean_text(d0.get("name")) or clean_text(gs[0]["name"]) or gs[0]["name"]
        notes = [str(d.get("detail") or "").strip() for d in ds if d.get("detail")]
        prog = max((d.get("progress") for d in ds if d.get("progress") in order),
                   key=lambda p: order[p], default="doing")
        pcts = [p for p in (_as_pct(d.get("percent")) for d in ds) if p is not None]
        items.append({"title": name, "sheet_match": None, "is_new": True, "add_sheet": False,
                      "plan": "unplanned", "progress": prog, "percent": max(pcts) if pcts else None,
                      "note": clean_text("; ".join(dict.fromkeys(notes))[:220] or _mechanical_summary(gs)),
                      "sub": [], "uncertain": not d0, "group_key": gs[0]["name"],
                      "group_keys": [g["name"] for g in gs], "uids": _uids(gs), "units": len(gs)})
    return items


def row_feeds(groups: list, rows: list, binding: dict, history: list) -> list:
    by_row: dict = {}
    for gidx, b in binding.items():
        if b["kind"] == "row":
            by_row.setdefault(b["sidx"], []).append(groups[gidx])
    feeds = []
    for row in rows:
        gs = by_row.get(row["sidx"])
        if gs:
            base = history_baseline(history, row["name"], goal_hash(row["goal"]))
            if row["pct"] is not None:
                base = {**base, "pct": row["pct"]}
            feeds.append(_row_feed(row, gs, base))
    return feeds


def drop_counted(groups: list, counted: set) -> list:
    """Units already counted in an approved report (same-day regenerate) are not new evidence."""
    out = []
    for g in groups:
        fresh = [u for u in g.get("uids", []) if u not in counted]
        if g.get("uids") and not fresh:
            continue
        out.append(g)
    return out
