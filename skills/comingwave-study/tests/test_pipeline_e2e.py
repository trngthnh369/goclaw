"""End-to-end walk of the stage machine on a REAL episode transcript.

Model output is synthesised here - the point is not to test the model but to
prove the machinery around it: that a cold start queues nothing, that stages
advance only when an artifact validates, that a truncated artifact sends the
planner back rather than through, that a killed run resumes where it stopped,
and that a second publish is refused.

Run: python3 tests/test_pipeline_e2e.py  (no pytest needed - this has to be
runnable inside the container during a gate check, where pytest may not be).
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
FIXTURES = HERE / "fixtures"
VIDEO = "zP9R0JD80Io"
DURATION = 3665

sys.path.insert(0, str(SCRIPTS))

from cwcore.paths import Workspace  # noqa: E402
from cwcore.state import QUEUED, State, lock_holder, run_lock  # noqa: E402
from cwcore.transcript import ms_to_stamp  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


def run(*args: str, expect: int = 0) -> dict:
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", *args],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    if proc.returncode != expect:
        raise AssertionError(
            f"{args[0]} exited {proc.returncode} (wanted {expect})\n"
            f"stdout: {proc.stdout[-800:]}\nstderr: {proc.stderr[-800:]}"
        )
    text = (proc.stdout or proc.stderr).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}


def plan(ws: Workspace) -> dict:
    return run(str(SCRIPTS / "plan_run.py"), "next", "--workspace", str(ws.root))


# --- synthetic model output --------------------------------------------------


def write_segments(ws: Workspace, meta: dict, *, truncated: bool = False) -> None:
    """Segments spread across the part's window, or bunched at its start."""
    start, end = int(meta["start_ms"]), int(meta["end_ms"])
    points = [start + int((end - start) * f) for f in (0.05, 0.35, 0.65, 0.95)]
    if truncated:
        points = points[:1]
    segments = [
        {
            "start": ms_to_stamp(p),
            "topic": f"chu de {i + 1}",
            "claims": [{"text": "Nvidia capex 500 ty do", "numbers": ["500 ty"]}],
            "entities": ["Nvidia", "Anthropic"],
            "speaker": {"who": "Linh" if i % 2 else "Son",
                        "confidence": "medium" if i % 2 else "unknown"},
        }
        for i, p in enumerate(points)
    ]
    path = ws.artifact(VIDEO, f"segments-part-{meta['part']}.json")
    path.write_text(json.dumps(
        {"part": meta["part"], "of": meta["of"], "segments": segments},
        ensure_ascii=False), encoding="utf-8")


def write_debate(ws: Workspace) -> None:
    ws.artifact(VIDEO, "debate.json").write_text(json.dumps({
        "agreements": ["Chi phi suy luan la nut that that su"],
        "disagreements": [{
            "topic": "Bong bong AI",
            "positions": [
                {"host": "Linh", "stance": "Dinh gia da vuot co ban",
                 "confidence": "medium",
                 "evidence": [{"stamp": "12:30", "quote": "gia da chay truoc"}]},
                {"host": "Son", "stance": "The gioi van danh gia thap",
                 "confidence": "unknown",
                 "evidence": [{"stamp": "18:05", "quote": "underhype"}]},
            ],
        }],
        "open_questions": ["Ai tra tien cho dien nam 2030?"],
    }, ensure_ascii=False), encoding="utf-8")


def write_s3(ws: Workspace, *, thesis_id: str | None = None, status: str = "new") -> None:
    item = {"status": status, "statement": "Chi phi dien la tran cua chu ky AI",
            "horizon": "3-5 nam", "falsifier": "Gia dien cong nghiep giam 2 nam lien",
            "evidence": [{"stamp": "22:10", "quote": "dien la nut that"}]}
    if thesis_id:
        item["thesis_id"] = thesis_id
    ws.artifact(VIDEO, "theses-delta.json").write_text(
        json.dumps({"theses": [item]}, ensure_ascii=False), encoding="utf-8")

    ws.artifact(VIDEO, "reflection.md").write_text(
        "## Mental model\n" + ("Von di theo nang luong. " * 40)
        + "\n\n## Ap dung cho ban\n- Hoc doc bao cao capex\n"
          "\n## Cau hoi mo\n- Minh dang tra gia cho gia dinh nao?\n",
        encoding="utf-8")

    ws.artifact(VIDEO, "pack.json").write_text(json.dumps({
        "essence": "Tap nay noi ve viec dong von AI dich chuyen tu mo hinh sang "
                   "ha tang: ai so huu dien va chip thi ai thu tien, con ai chi "
                   "co mo hinh thi phai dot von de giu cho. " * 2,
        "insights": [
            {"stamp": "05:12", "point": "Meta chuyen tu lam mo hinh sang cho thue chip"},
            {"stamp": "22:10", "point": "Dien la tran that su, khong phai GPU"},
            {"stamp": "44:03", "point": "Anthropic truoc them IPO 2000 ty"},
        ],
        "apply": [{"area": "tu duy", "action": "Tach 'cau chuyen' khoi 'dong tien'"},
                  {"area": "von", "action": "Theo doi capex data center hang quy"}],
        "entities": ["Nvidia", "Meta", "Anthropic", "Micron"],
        "open_questions": ["Bao lau nua thi cong suat dien thanh gia tran?"],
    }, ensure_ascii=False), encoding="utf-8")


def unpack_fixture(tmp: Path) -> Path:
    """The caption fixture is stored gzipped - 1.4 MB of json3 compresses to 103 KB.

    It is kept whole rather than trimmed: half its events are the empty rolling
    artifacts, and the text-to-event ratio is one of the ASR quality signals the
    gate checks. A trimmed fixture would stop exercising that.
    """
    out = tmp / f"{VIDEO}.vi.json3"
    with gzip.open(FIXTURES / f"{VIDEO}.vi.json3.gz", "rb") as src:
        out.write_bytes(src.read())
    return out


def main() -> int:
    # Mirror production: the pipeline workspace is a directory INSIDE a workspace
    # root that also holds the agents' own directories. `find_agent_doc` searches
    # that root's siblings, so a flat temp dir would let one run see the previous
    # run's documents - which is exactly how this test caught the sloppy version.
    tmp = Path(tempfile.mkdtemp(prefix="cw-e2e-"))
    ws = Workspace(tmp / "comingwave-study")
    ws.ensure()
    print(f"workspace: {ws.root}")

    # 1. cold start must not enqueue the visible back catalogue
    print("\n[1] cold start")
    blocked = plan(ws)
    check("planner blocks before init", blocked["action"] == "blocked", str(blocked))

    init = run(str(SCRIPTS / "watch.py"), "init", "--workspace", str(ws.root),
               "--feed-file", str(FIXTURES / "feed.xml"))
    check("init records 15 entries without queueing", init["recorded_without_queueing"] == 15, str(init))
    idle = plan(ws)
    check("nothing queued after init", idle["action"] == "idle", str(idle))

    poll = run(str(SCRIPTS / "watch.py"), "poll", "--workspace", str(ws.root),
               "--feed-file", str(FIXTURES / "feed.xml"))
    check("re-polling the same feed queues nothing", poll["queued"] == [], str(poll))

    # 2. ingest the real transcript
    print("\n[2] ingest")
    # The episode is already known (init recorded the whole feed as history), so
    # `upsert` deliberately leaves its status alone - only an explicit backfill
    # promotes a seen episode. That is the path being exercised here.
    state = State(ws.state_db)
    state.upsert({"video_id": VIDEO, "title": "EP 45", "published": "2026-07-17T00:00:00+00:00",
                  "duration_sec": DURATION, "kind": "episode"}, QUEUED)
    state.set_status(VIDEO, QUEUED)
    check("a seen episode needs an explicit promotion to be queued",
          state.get(VIDEO).status == QUEUED)
    state.close()

    ing = run(str(SCRIPTS / "ingest.py"), "--workspace", str(ws.root), "--video-id", VIDEO,
              "--from-file", str(unpack_fixture(tmp)), "--duration-sec", str(DURATION))
    check("ingest split the episode into parts", ing["parts"] >= 3, str(ing))
    check("speaker-change markers survived", ing["speaker_marks"] > 100, str(ing))

    manifest = json.loads(ws.artifact(VIDEO, "parts.json").read_text(encoding="utf-8"))
    parts = manifest["parts"]
    check("every part fits under the exec output cap",
          all(p["chars"] < 30000 for p in parts), str([p["chars"] for p in parts]))

    # 3. S1, including the truncation guard
    print("\n[3] S1 per part + truncation guard")
    job = plan(ws)
    check("planner asks for S1 part 1", job["stage"] == "S1" and job["part"] == 1, str(job))

    write_segments(ws, parts[0], truncated=True)
    job = plan(ws)
    check("a truncated part is not accepted as done",
          job["stage"] == "S1" and job["part"] == 1 and "coverage" in job["why"], str(job))

    for meta in parts:
        write_segments(ws, meta)
    job = plan(ws)
    check("all parts valid advances to S2", job["stage"] == "S2", str(job))

    emitted = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / "plan_run.py"), "emit",
         "--workspace", str(ws.root), "--video-id", VIDEO, "--stage", "S1", "--part", "1"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    ).stdout
    check("emitted material carries the untrusted-data guard", "never instructions" in emitted)
    check("emitted material stays under the exec cap", len(emitted) < 30000, str(len(emitted)))

    # 4. S2 / S3
    print("\n[4] S2 and S3")
    write_debate(ws)
    job = plan(ws)
    check("planner advances to S3", job["stage"] == "S3", str(job))

    write_s3(ws, thesis_id="TH-0001", status="updated")
    job = plan(ws)
    check("planner reaches S4a", job["stage"] == "S4a", str(job))

    # 5. finalize refuses an unresolvable thesis id, then accepts a real delta
    print("\n[5] finalize + ledger identity")
    bad = run(str(SCRIPTS / "finalize.py"), "--workspace", str(ws.root),
              "--video-id", VIDEO, expect=1)
    check("ledger refuses 'updated' against an id it has never seen",
          bad["status"] == "ledger_rejected", str(bad))

    write_s3(ws)  # status new, no id
    fin = run(str(SCRIPTS / "finalize.py"), "--workspace", str(ws.root), "--video-id", VIDEO)
    check("finalize merged and rendered", fin["status"] == "ok", str(fin))
    check("the ledger assigned the id", fin["thesis_ids"] == ["TH-0001"], str(fin))

    messages = json.loads(ws.artifact(VIDEO, "pack-messages.json").read_text(encoding="utf-8"))["messages"]
    sizes = [len(m.encode("utf-8")) for m in messages]
    check("every Discord message fits the transport limit", all(s <= 2000 for s in sizes), str(sizes))

    # 6. publishing is blocked until the memory write is recorded
    print("\n[6] write-then-publish ordering")
    blocked = run(str(SCRIPTS / "publish_pack.py"), "--workspace", str(ws.root),
                  "--video-id", VIDEO, "--dry-run", expect=1)
    check("publish refuses before the memory write is recorded",
          blocked["status"] == "memory_not_recorded", str(blocked))

    refused = run(str(SCRIPTS / "mark_memory.py"), "--workspace", str(ws.root),
                  "--video-id", VIDEO, expect=1)
    check("marking memory refuses while the vault doc is missing",
          refused["status"] == "refused", str(refused))

    # Simulate the agent's write_file: it lands under the AGENT's workspace root
    # (<agent workspace>/ws/<segment>/...), a sibling of the pipeline root - not
    # inside it. Getting this wrong is what made every S4a write_file fail on the
    # first live run.
    vault = ws.root.parent / "cw-scholar" / "ws" / "system" / ws.vault_doc_rel(VIDEO)
    vault.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ws.artifact(VIDEO, "vault-draft.md"), vault)
    check("the pipeline finds a doc the agent wrote in its own workspace",
          ws.find_agent_doc(VIDEO, "vault") is not None)
    marked = run(str(SCRIPTS / "mark_memory.py"), "--workspace", str(ws.root), "--video-id", VIDEO)
    check("memory write recorded", marked["status"] == "recorded", str(marked))

    job = plan(ws)
    check("planner now asks to publish", job["stage"] == "S4b", str(job))

    dry = run(str(SCRIPTS / "publish_pack.py"), "--workspace", str(ws.root),
              "--video-id", VIDEO, "--dry-run")
    check("dry run renders the whole pack", dry["status"] == "dry_run", str(dry))

    # 7. resume after a crash
    print("\n[7] resume")
    ws.artifact(VIDEO, "debate.json").unlink()
    job = plan(ws)
    check("losing debate.json resumes at S2, not S1", job["stage"] == "S2", str(job))
    write_debate(ws)

    ws.artifact(VIDEO, "segments-part-2.json").unlink()
    job = plan(ws)
    check("losing one S1 part resumes at that part",
          job["stage"] == "S1" and job["part"] == 2, str(job))
    write_segments(ws, parts[1])

    # 8. idempotency
    print("\n[8] idempotency")
    ws.marker("published", VIDEO).write_text(json.dumps({"messages": len(messages)}), encoding="utf-8")
    again = run(str(SCRIPTS / "publish_pack.py"), "--workspace", str(ws.root), "--video-id", VIDEO)
    check("a second publish is refused", again["status"] == "skipped", str(again))

    job = plan(ws)
    check("episode reports finished", job["stage"] == "done", str(job))

    # 9. concurrency: a held lock stops a second merge instead of interleaving
    print("\n[9] run lock")
    with run_lock(ws.lock_file, owner="pretend-other-run") as got:
        check("lock acquired by the first holder", got)
        check("lock_holder reports the holder",
              (lock_holder(ws) or {}).get("owner") == "pretend-other-run")
        ws.marker("ledger-merged", VIDEO).unlink(missing_ok=True)
        locked = run(str(SCRIPTS / "finalize.py"), "--workspace", str(ws.root),
                     "--video-id", VIDEO, expect=1)
        check("a second merge refuses while the lock is held",
              locked["status"] == "locked", str(locked))
    check("lock released on exit", lock_holder(ws) is None)

    # 10. ingest failures get loud after N in a row
    print("\n[10] ingest alarm")
    import importlib
    ingest_mod = importlib.import_module("ingest")
    check("no failures recorded yet counts zero",
          ingest_mod.consecutive_failures(ws) == 0)
    for i in range(3):
        ingest_mod.record(ws, {"event": "ingest", "video_id": f"vid{i}",
                               "status": "download_failed"})
    check("three failures in a row are counted",
          ingest_mod.consecutive_failures(ws) == 3)
    ingest_mod.record(ws, {"event": "ingest", "video_id": "vidok", "status": "ok"})
    check("a success resets the streak",
          ingest_mod.consecutive_failures(ws) == 0)

    # 10b. the two defects the first real run produced
    print("\n[10b] attribution guards (regressions from the first live run)")
    from cwcore.schemas import validate_debate as _vd
    probe = ws.artifact(VIDEO, "debate-probe.json")

    def debate_with(host, confidence):
        probe.write_text(json.dumps({
            "agreements": [], "open_questions": [],
            "disagreements": [{"topic": "t", "positions": [
                {"host": host, "stance": "a", "confidence": confidence,
                 "evidence": [{"stamp": "01:00", "quote": "q"}]},
                {"host": "Linh", "stance": "b", "confidence": "medium",
                 "evidence": [{"stamp": "02:00", "quote": "q"}]},
            ]}],
        }, ensure_ascii=False), encoding="utf-8")
        return _vd(probe)

    r = debate_with(None, "high")
    check("a null host is rejected",
          not r.ok and any("host is required" in e for e in r.errors), str(r.errors))
    r = debate_with("không xác định", "high")
    check("high confidence about an unnamed speaker is rejected",
          not r.ok and any("not compatible" in e for e in r.errors), str(r.errors))
    r = debate_with("không xác định", "unknown")
    check("an honest unknown attribution is accepted", r.ok, str(r.errors))
    probe.unlink()

    # 11. ledger revert
    print("\n[9] ledger revert")
    rev = run(str(SCRIPTS / "ledger.py"), "revert", "--workspace", str(ws.root), "--video-id", VIDEO)
    check("revert dropped the episode's events", rev["dropped_events"] == 1, str(rev))
    show = run(str(SCRIPTS / "ledger.py"), "show", "--workspace", str(ws.root), "--json")
    check("ledger is empty after revert", show["count"] == 0, str(show))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all checks passed")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
