"""The sessions digest is built on the host (digest_sessions.py --out) and read from the
host-digest mount; the raw transcripts are no longer visible in the container."""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import daily_report_run as dr

SCRIPT = Path(__file__).resolve().parent.parent / "digest_sessions.py"


def test_out_writes_digest_with_generated_at(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    out = tmp_path / "sessions-latest.json"

    subprocess.run([sys.executable, str(SCRIPT), "--projects-dir", str(projects),
                    "--hours", "24", "--out", str(out)], check=True)

    digest = json.loads(out.read_text(encoding="utf-8"))
    assert "generated_at" in digest
    assert not (tmp_path / "sessions-latest.json.tmp").exists()


def test_missing_digest_reports_health_failure(tmp_path):
    digest = dr.load_sessions_digest(str(tmp_path / "absent.json"))
    assert digest["health"]["mount_status"] == "missing"


def test_stale_digest_is_flagged_not_used_as_today(tmp_path):
    path = tmp_path / "sessions-latest.json"
    old = datetime.now(timezone.utc) - timedelta(hours=5)
    path.write_text(json.dumps({"generated_at": old.isoformat(),
                                "health": {"mount_status": "ok"}, "projects": []}),
                    encoding="utf-8")

    assert dr.load_sessions_digest(str(path))["health"]["mount_status"] == "stale"


def test_fresh_digest_passes_through(tmp_path):
    path = tmp_path / "sessions-latest.json"
    path.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                                "health": {"mount_status": "ok"},
                                "projects": [{"project": "D:\\Projects\\work\\demo"}]}),
                    encoding="utf-8")

    digest = dr.load_sessions_digest(str(path))
    assert digest["health"]["mount_status"] == "ok"
    assert len(digest["projects"]) == 1
