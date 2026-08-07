#!/usr/bin/env python3
# daily_report_publish.py — deterministic RENDER + PUBLISH step (review-before-render, D6).
# Run when the user (owner) replies DUYỆT in the Discord review channel.
#
# For EACH pending state in order [daily, weekly] (per-state isolation — one failing never
# blocks or double-sends the other):
#   1. render PNG from report[_weekly].json (build_and_render.py --render-only)
#   2. send PNG to the Zalo TEAM AI group (threadType=Group via X-GoClaw-User-Id: group:<id>)
#   3. mark published IMMEDIATELY (atomic os.replace) — crash-window against double-send
#   4. daily only: write % back to the weekly sheet (best-effort; never into a past-week tab)
#   5. post the PNG back to the Discord review channel as a receipt
#
# Render failure -> state STAYS review + error posted to Discord; re-DUYỆT retries.
# Flags: --no-send = full render + state transitions but NO Zalo/Discord posts (safe test).
#
# Run: python3 /app/workspace/_daily-report/daily_report_publish.py [--no-send]
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

WORK = "/app/workspace/_daily-report"
BUILD = f"{WORK}/build_and_render.py"
BASE = os.environ.get("GOCLAW_SELF_URL", "http://127.0.0.1:18790")

STATES = (
    ("daily", f"{WORK}/active.json", f"{WORK}/report.json"),
    ("weekly", f"{WORK}/active_weekly.json", f"{WORK}/report_weekly.json"),
)


def _resolve_token() -> str:
    """Gateway token — exec env in GoClaw v3.14+ does NOT expose GOCLAW_GATEWAY_TOKEN;
    fall back to the chmod-600 token file in the workspace volume."""
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
ZALO_CHANNEL = "zalo-personal-bot"
ZALO_GROUP = "8709947833571143663"  # TEAM AI
DISCORD_CHANNEL = "discord-bot"
DISCORD_TARGET = "1512686472334147735"
TZ = timezone(timedelta(hours=7))

DEFAULT_PCT = {"done": 100, "doing": 50, "blocked": 30, "new": 10}
STATUS_LABEL = {"done": "Done", "blocked": "Blocked", "doing": "WIP", "new": "WIP"}


def log(*a: object) -> None:
    print("[publish]", *a, file=sys.stderr)


def _pct(item: dict) -> int:
    try:
        return max(0, min(100, int(round(float(item.get("percent"))))))
    except (TypeError, ValueError):
        return DEFAULT_PCT.get(item.get("progress", "doing"), 50)


def _write_json_atomic(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


def http_post(path: str, payload: dict, extra_headers: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def render(report: dict) -> str:
    """report JSON -> PNG path (build_and_render --render-only)."""
    proc = subprocess.run(
        ["python3", BUILD, "--render-only"], input=json.dumps(report, ensure_ascii=False),
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"RENDER_FAIL {proc.stderr[:300]}")
    for line in proc.stdout.splitlines():
        if line.startswith("PNG="):
            png = line[4:].strip()
            if os.path.exists(png):
                return png
    raise RuntimeError("RENDER_FAIL no PNG in output")


def send_zalo(png: str, kind: str) -> None:
    resp = http_post(
        "/v1/tools/invoke",
        {
            "tool": "message",
            "args": {
                "action": "send",
                "channel": ZALO_CHANNEL,
                "target": ZALO_GROUP,
                "forward": True,
                "forward_reason": f"DUYỆT: đăng báo cáo {kind} nhóm TEAM AI",
                "message": f"MEDIA:{png}",
            },
        },
        {"X-GoClaw-User-Id": f"group:{ZALO_GROUP}"},
    )
    if "error" in resp:
        raise RuntimeError(f"ZALO_PUBLISH_FAIL {json.dumps(resp['error'])[:300]}")


def post_discord(message: str, reason: str) -> None:
    try:
        http_post("/v1/tools/invoke", {
            "tool": "message",
            "args": {"action": "send", "channel": DISCORD_CHANNEL, "target": DISCORD_TARGET,
                     "forward": True, "forward_reason": reason, "message": message},
        }, {"X-GoClaw-User-Id": USER_ID})
    except Exception as exc:  # noqa: BLE001  (receipt/error post is best-effort)
        log("discord post failed:", exc)


def write_sheet_daily(report: dict) -> str:
    """Daily % write-back. Guard: ensure_current_week_tab — NEVER write into a past-week tab
    (find_week_tab falls back to last week when the current tab is missing). Monotonic: an
    update never lowers an existing sheet %."""
    items = report.get("items", [])
    if not items:
        return "no items"
    sys.path.insert(0, WORK)
    import daily_report_sheet as drs  # lazily — Zalo publish must work even if sheet libs fail

    today = datetime.now(TZ).date()
    tab = drs.ensure_current_week_tab(today)
    pct_col = drs.ensure_pct_column(tab)
    tasks = drs.read_tasks(tab["title"])["tasks"]
    row_by_name = {drs._norm_name(t["name"]): t for t in tasks}

    updates, new_tasks = [], []
    for it in items:
        p = _pct(it)
        status = STATUS_LABEL.get(it.get("progress", "doing"), "WIP")
        sm = it.get("sheet_match")
        t = row_by_name.get(drs._norm_name(sm)) if sm else None
        if t and not it.get("is_new"):
            if t.get("pct") is not None and p < t["pct"]:
                p = t["pct"]  # % không lùi (re-check session must not regress a done task)
            updates.append({"row": t["row"], "percent": p, "status": status})
        elif it.get("skip_sheet"):
            # user replied "bỏ mới: N" during review -> report keeps the item, sheet does not
            log(f"skip sheet append (user rejected): {it.get('title', '')}")
        else:
            new_tasks.append({"name": it.get("title", ""), "percent": p,
                              "status": status, "note": it.get("note", "")})
    res = drs.write_progress(tab, pct_col, updates, new_tasks)

    # Remember the bindings the user just approved so tomorrow's run matches them deterministically
    # instead of re-asking the agent (whose answer varies between runs).
    import daily_report_run as dr
    learned = {dr._norm(it["group_key"]): it["sheet_match"]
               for it in items
               if it.get("sheet_match") and not it.get("is_new") and it.get("group_key")}
    if learned:
        try:
            dr.save_learned(learned)
            log(f"learned {len(learned)} binding(s)")
        except Exception as exc:  # noqa: BLE001
            log(f"save learned bindings failed: {exc}")

    return f"tab='{tab['title']}' updated={res['updated']} appended={res['appended']}"


def publish_one(kind: str, active_path: str, report_path: str, no_send: bool) -> str:
    """Returns one of: published | already | none | fail:<reason> (per-state isolation)."""
    if not (os.path.exists(active_path) and os.path.exists(report_path)):
        return "none"
    with open(active_path, encoding="utf-8") as fh:
        active = json.load(fh)
    if active.get("stage") == "published":
        return "already"
    if active.get("stage") != "review":
        return "none"
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)
    report.setdefault("kind", kind)

    try:
        png = render(report)
    except Exception as exc:  # noqa: BLE001
        log(f"{kind}: {exc}")
        if not no_send:
            post_discord(f"[{kind}-report] Render lỗi sau DUYỆT: {str(exc)[:200]} — reply DUYỆT để thử lại.",
                         f"{kind}-report render error")
        return f"fail:render"

    if no_send:
        log(f"{kind}: --no-send (render OK {png}; state unchanged)")
        return "published(dry)"

    try:
        send_zalo(png, kind)
    except Exception as exc:  # noqa: BLE001
        log(f"{kind}: {exc}")
        post_discord(f"[{kind}-report] Gửi Zalo lỗi: {str(exc)[:200]} — reply DUYỆT để thử lại.",
                     f"{kind}-report zalo error")
        return "fail:zalo"

    # mark published IMMEDIATELY after the send (crash between send and mark = the only
    # double-send window; keep it as small as possible)
    active["stage"] = "published"
    active["png_path"] = png
    active["published_at"] = datetime.now(TZ).isoformat()
    _write_json_atomic(active_path, active)

    sheet_msg = ""
    if kind == "daily":
        try:
            sheet_msg = write_sheet_daily(report)
        except Exception as exc:  # noqa: BLE001
            sheet_msg = f"SHEET_WRITE_FAIL {exc}"
            log(sheet_msg)

    # receipt back to the review channel (image the group actually received)
    post_discord(f"MEDIA:{png}", f"{kind}-report published receipt")
    log(f"{kind}: published png={png} sheet={sheet_msg}")
    return "published"


def main() -> None:
    no_send = "--no-send" in sys.argv
    if not TOKEN and not no_send:
        raise SystemExit("FATAL: GOCLAW_GATEWAY_TOKEN not set")

    results = {}
    for kind, active_path, report_path in STATES:
        try:
            results[kind] = publish_one(kind, active_path, report_path, no_send)
        except Exception as exc:  # noqa: BLE001  (isolation: never abort the loop)
            log(f"{kind}: unexpected {exc}")
            results[kind] = f"fail:{exc}"

    line = " ".join(f"{k}={v}" for k, v in results.items())
    if all(v in ("none", "already") for v in results.values()):
        print(f"NO_ACTIVE {line}" if all(v == "none" for v in results.values())
              else f"ALREADY_PUBLISHED {line}")
        raise SystemExit(0)
    if any(str(v).startswith("fail") for v in results.values()):
        print(f"PARTIAL {line}")
        raise SystemExit(1)
    print(f"OK published {line}")


if __name__ == "__main__":
    main()
