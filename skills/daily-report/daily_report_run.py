#!/usr/bin/env python3
# daily_report_run.py — deterministic orchestrator for the daily report GENERATE step.
#
# Why: the agent (Zip, Codex) is unreliable at orchestrating a multi-step flow (it skips the
# render step, fumbles workspace-restricted file ops, etc.). This script owns the whole pipeline
# so the agent's only job is to run ONE exec. The LLM is reduced to a pure function (digest ->
# items JSON) via a single chat-completions sub-call, with a deterministic fallback if that fails.
#
# Pipeline:
#   1. run digest_sessions.py                -> projects + health
#   2. health gate (mount_status must be ok)
#   3. LLM analysis sub-call (-> report JSON)  [fallback: deterministic summary]
#   4. build_and_render.py                    -> /tmp/daily-report/report.png + active.json
#   5. POST caption + image to Discord review channel via /v1/tools/invoke
#
# Run: python3 /app/workspace/_daily-report/daily_report_run.py [--hours 24]
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
BUILD = f"{WORK}/build_and_render.py"
PNG_PATH = f"{WORK}/render/report.png"

BASE = os.environ.get("GOCLAW_SELF_URL", "http://127.0.0.1:18790")


def _resolve_token() -> str:
    """Gateway token. When the agent (Zip) runs these scripts via its exec tool, GoClaw v3.14+
    does NOT expose GOCLAW_GATEWAY_TOKEN to the exec env (security), so the gateway call 401s.
    Fall back to a chmod-600 token file written into the (container-only) workspace volume."""
    t = os.environ.get("GOCLAW_GATEWAY_TOKEN", "")
    if t:
        return t
    try:
        with open("/app/workspace/_daily-report/.gwtoken", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


TOKEN = _resolve_token()
USER_ID = "trngthnh369"
ANALYST_AGENT = os.environ.get("DAILY_REPORT_AGENT", "zip-crazy")
# If set (e.g. the host gemini-bridge http://host.docker.internal:8765/v1/chat/completions), the
# analysis call goes there directly (no auth) instead of the GoClaw gateway. Lets the report use
# the host gemini-cli (Google Ultra) — free, stable, no token-rotation death.
LLM_URL = os.environ.get("DAILY_REPORT_LLM_URL", "")

DISCORD_CHANNEL = "discord-bot"
DISCORD_TARGET = "1512686472334147735"

# Báo cáo công việc cho sếp → CHỈ lấy project trong workspace work, bỏ personal.
# Match theo substring path đã normalize (vd "projects/work"). Override qua env nếu cần.
WORK_FILTER = os.environ.get("DAILY_REPORT_WORK_FILTER", r"projects\work").lower().replace("\\", "/")

TZ = timezone(timedelta(hours=7))


def is_work_project(path: object) -> bool:
    norm = str(path or "").lower().replace("\\", "/")
    return WORK_FILTER in norm


def project_label(path: object):
    """Gom theo PROJECT TOP-LEVEL dưới work\\ (vd build-workflow, openclaw). Mọi sub-project
    trong build-workflow (ai-purchasing, ai-store-fanpage-repost, ai-worldcup-fb-engine...) gộp
    chung 1 nhóm "build-workflow". openclaw là 1 nhóm riêng."""
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
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise SystemExit(f"DIGEST_FAIL rc={proc.returncode} {proc.stderr[:300]}")
    return json.loads(proc.stdout)


VALID_PROGRESS = ("done", "doing", "blocked", "new")


def flatten_sessions(projects: list) -> tuple[list, list]:
    """Flat list of work sessions (1 session = 1 công việc), mỗi cái có idx ổn định + project
    label. Giữ thứ tự project. Bỏ tên file/path khỏi data feed LLM."""
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
    """Map 1 session -> (task hiển thị, tên-trong-sheet|None, known). Match alias theo substring
    trong tên session + intent. task=None nghĩa là IGNORE (việc vặt, bỏ khỏi báo cáo).
    Không khớp alias -> dùng tên session, không sheet, known=False."""
    hay = (session["name"] + " " + " ".join(session.get("intent", []))).lower()
    for a in aliases:
        for pat in a.get("match", []):
            if pat and pat.lower() in hay:
                if a.get("ignore"):
                    return (None, None, False)  # IGNORE
                return (a.get("task") or session["name"], (a.get("sheet") or None), True)
    return (session["name"], None, False)


def group_tasks(sessions: list, aliases: list) -> list:
    """Gộp session theo task (deterministic). Trả [{name, sheet, known, intents}]. Bỏ session ignore."""
    groups: dict[str, dict] = {}
    order: list = []
    for s in sessions:
        name, sheet, known = resolve_task(s, aliases)
        if name is None:  # ignored alias
            continue
        if name not in groups:
            groups[name] = {"name": name, "sheet": sheet, "known": known, "intents": []}
            order.append(name)
        g = groups[name]
        g["intents"].extend(s.get("intent", []))
        if sheet and not g["sheet"]:
            g["sheet"] = sheet
        if known:
            g["known"] = True
    return [groups[n] for n in order]


DESCRIBE_PROMPT = """Bạn viết chi tiết cho báo cáo công việc cuối ngày. Mỗi phần tử dưới đây là 1 TASK đã có tên. Với MỖI task, viết mô tả ngắn + trạng thái + % hoàn thành dựa trên "intents" (prompt user trong ngày).

CHỈ trả về MỘT JSON array (không markdown, không giải thích, không gọi tool), mỗi phần tử:
{"idx":<idx>,"name":"<xem quy tắc>","detail":"<chi tiết ≤14 từ>","progress":"done|doing|blocked|new","percent":<0-100>}

QUY TẮC BẮT BUỘC:
- GIỮ NGUYÊN idx. Nếu "known"=true → name GIỮ NGUYÊN y hệt. Nếu "known"=false → đổi name (slug kỹ thuật) thành tên công việc tiếng Việt đọc được, viết hoa đầu, KHÔNG gạch ngang.
- detail dựa trên "intents". TUYỆT ĐỐI KHÔNG nhắc tên file/đường dẫn/script (.py/.js/.sh/.mjs)/branch/hàm.
- progress suy từ intents (đang làm dở→doing; chờ/vướng→blocked; mới bàn→new; xong→done).
- percent: done≈90-100, doing 30-70, blocked 20-50, new 5-20.

TASKS:
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
            "sheet_name": g["sheet"],  # gỡ ở bước match_sheet
        })
    return items


def llm_chat(messages: list) -> str:
    """Một lượt LLM -> content. Dùng gemini-bridge (DAILY_REPORT_LLM_URL) nếu set, ngược lại GoClaw."""
    if LLM_URL:
        data = json.dumps({"messages": messages}).encode("utf-8")
        req = urllib.request.Request(LLM_URL, data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"]
    payload = {"model": f"agent:{ANALYST_AGENT}", "stream": False, "messages": messages}
    return http_post("/v1/chat/completions", payload, timeout=300)["choices"][0]["message"]["content"]


def llm_describe(groups: list) -> list | None:
    if not groups:
        return None
    feed = [{"idx": i, "name": g["name"], "known": g["known"], "intents": g["intents"][:4]}
            for i, g in enumerate(groups)]
    messages = [{"role": "user", "content": DESCRIBE_PROMPT + json.dumps(feed, ensure_ascii=False)}]
    try:
        content = llm_chat(messages)
    except Exception as exc:  # noqa: BLE001
        log("LLM describe failed:", exc)
        return None
    m = re.search(r"\[.*\]", content, re.S)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except Exception as exc:  # noqa: BLE001
        log("JSON parse failed:", exc)
        return None
    by_idx = {it["idx"]: it for it in arr if isinstance(it, dict) and "idx" in it}
    return _describe_items(groups, by_idx) if by_idx else None


def fallback_describe(groups: list) -> list:
    """Khi LLM fail — mô tả cơ học từ intent."""
    return _describe_items(groups, {})


def _norm(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def match_sheet(items: list, sheet_tasks: list) -> None:
    """Gắn sheet_match (TÊN task shet khớp) + is_new cho mỗi item. Match theo TÊN (không dùng STT
    vì nhiều task sheet để trống STT). Publish dùng sheet_match để tìm đúng dòng update %."""
    by_name = {_norm(t["name"]): t for t in sheet_tasks}
    for it in items:
        sname = it.pop("sheet_name", None)
        target = sname if sname else it["title"]
        t = by_name.get(_norm(target))
        it["sheet_match"] = t["name"] if t else None  # tên sheet chính xác để publish tìm dòng
        it["is_new"] = t is None


def fmt_window(digest: dict) -> tuple[str, str]:
    def f(iso, suffix=""):
        try:
            dt = datetime.fromisoformat(iso)
            return dt.strftime("%d/%m %H:%M") + suffix
        except Exception:  # noqa: BLE001
            return iso or ""
    return f(digest.get("window_from", "")), f(digest.get("window_to", ""), " (UTC+7)")


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


def main():
    hours = 24
    if "--hours" in sys.argv:
        hours = int(sys.argv[sys.argv.index("--hours") + 1])
    # --require-llm: if the LLM analysis fails, EXIT (code 3) WITHOUT posting instead of falling
    # back to mechanical descriptions. The fallback uses raw prompt snippets, which can leak file
    # paths/script names the report rule forbids — so the wrapper prefers to heal the LLM provider
    # (sync Codex token + restart) and retry rather than post a rule-violating report.
    require_llm = "--require-llm" in sys.argv
    if not TOKEN:
        raise SystemExit("FATAL: GOCLAW_GATEWAY_TOKEN not set")

    digest = run_digest(hours)
    health = digest.get("health", {})
    if health.get("mount_status") not in ("ok",):
        post_discord(f"[daily-report] Nguồn session lỗi (mount_status={health.get('mount_status')}). Bỏ qua hôm nay.",
                     "daily-report source error")
        raise SystemExit(f"mount_status={health.get('mount_status')}")
    if health.get("events_in_window", 0) == 0 and health.get("source_files_seen", 0) > 0:
        post_discord("[daily-report] Không có hoạt động trong 24h — không tạo báo cáo.", "daily-report empty")
        raise SystemExit("no events in window")

    # Work-only: drop personal projects, normalize labels (build-workflow container ->
    # project con), merge sessions theo project, bỏ container trơ.
    all_projects = digest.get("projects", [])
    merged: dict[str, dict] = {}
    for p in all_projects:
        if not is_work_project(p.get("project")):
            continue
        label = project_label(p.get("project"))
        if not label:
            continue  # container trơ — không phải việc thật
        merged.setdefault(label, {"project": label, "sessions": []})["sessions"].extend(p.get("sessions", []))
    digest["projects"] = list(merged.values())
    log(f"projects: {len(all_projects)} total -> {len(digest['projects'])} work ({', '.join(merged)})")
    if not digest["projects"]:
        post_discord("[daily-report] Không có hoạt động ở dự án work trong 24h — không tạo báo cáo.",
                     "daily-report no work")
        raise SystemExit("no work projects in window")

    sessions, _order = flatten_sessions(digest["projects"])
    today = datetime.now(TZ).date()

    # Read the weekly task sheet (source of truth for task names + progress tracking).
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
    items = llm_describe(groups)
    source = "LLM"
    if items is None:
        if require_llm:
            log("LLM unavailable and --require-llm set: NOT posting fallback (exit 3 so wrapper can heal provider)")
            raise SystemExit(3)
        items = fallback_describe(groups)
        source = "fallback"
    match_sheet(items, sheet_tasks)
    log(f"analysis source: {source} | items: {len(items)}")

    report = {
        "items": items,
        "report_date": today.strftime("%Y-%m-%d"),
        "sheet_tab": sheet_tab,
    }

    # build + render (deterministic)
    proc = subprocess.run(
        ["python3", BUILD], input=json.dumps(report, ensure_ascii=False),
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0 or not os.path.exists(PNG_PATH):
        post_discord(f"[daily-report] Render lỗi: {proc.stderr[:200]}", "daily-report render error")
        raise SystemExit(f"BUILD_FAIL {proc.stderr[:300]}")
    log("rendered", PNG_PATH)

    # post to Discord for review
    date = report["report_date"]
    # Discord giữ nguyên UTF-8 (đã verify round-trip): dùng tiếng Việt CÓ DẤU cho dễ đọc.
    post_discord(
        f"📋 **Báo cáo công việc {date}** — bản nháp chờ duyệt ({source}).\n"
        f"➡️ Reply **DUYỆT** để đăng nhóm TEAM AI, hoặc **'sửa: <yêu cầu>'** để chỉnh.",
        "daily-report review caption",
    )
    post_discord(f"MEDIA:{PNG_PATH}", "daily-report review image")
    print(f"OK source={source} png={PNG_PATH}")


if __name__ == "__main__":
    main()
