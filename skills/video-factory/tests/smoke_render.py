"""End-to-end render on the real toolchain: edge-tts (network), the Chrome sidecar
(cards), ffmpeg (clips, soundtrack, QA). Model output is replaced by the brief's
own examples and reviews by human overrides - this proves the machinery, not the
model.

Run inside the gateway container as the goclaw user, with a workspace the Chrome
sidecar can read (it mounts /app/workspace read-only):
    python3 -X utf8 tests/smoke_render.py /app/workspace/video-factory-smoke
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import studio  # noqa: E402
from vfcore import briefs  # noqa: E402

COLOURS = ("0x1d2b64:0xf8cdda", "0x0f2027:0x2c5364", "0x42275a:0x734b6d", "0x134e5e:0x71b280",
           "0x3a1c71:0xffaf7b", "0x232526:0x414345")


def cli(*argv: str, stdin: str | None = None) -> str:
    proc = subprocess.run([sys.executable, "-X", "utf8", str(HERE.parent / "scripts" / "studio.py"), *argv],
                          input=stdin.encode() if stdin else None, capture_output=True, timeout=900)
    text = proc.stdout.decode("utf-8", "replace")
    print(f"$ studio.py {' '.join(argv[2:5])} -> exit {proc.returncode}")
    print("  " + text.strip().replace("\n", "\n  ")[:1500])
    return text


def main() -> int:
    ws = sys.argv[1]
    cli("--workspace", ws, "init")
    created = cli("--workspace", ws, "new", "--topic", "Vì sao chúng ta hay trì hoãn", "--source", "smoke")
    job = created.split()[1]
    cli("--workspace", ws, "submit", "--job", job, "--kind", "research",
        stdin=json.dumps(briefs.EXAMPLE_RESEARCH, ensure_ascii=False))
    cli("--workspace", ws, "submit", "--job", job, "--kind", "script",
        stdin=json.dumps(briefs.EXAMPLE_SCRIPT, ensure_ascii=False))
    cli("--workspace", ws, "override", "--job", job, "--stage", "script", "--reason", "smoke test")
    action = json.loads(cli("--workspace", ws, "next", "--job", job))
    tmp = Path(ws) / "_smoke_images"
    tmp.mkdir(parents=True, exist_ok=True)
    for i, call in enumerate(action.get("calls", [])):
        c0, c1 = COLOURS[i % len(COLOURS)].split(":")
        img = tmp / f"{call['scene']}.png"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"gradients=s=768x1376:c0={c0}:c1={c1}:x0=0:y0=0:x1=768:y1=1376:d=1,"
                        f"drawgrid=w=96:h=96:t=2:c=white@0.25", "-frames:v", "1", str(img)], check=True)
        cli("--workspace", ws, "attach", "--job", job, "--scene", call["scene"], "--file", f"MEDIA:{img}")
    started = time.time()
    while True:
        out = cli("--workspace", ws, "render", "--job", job)
        if not out.startswith("PARTIAL"):
            break
    print(f"render wall time {time.time() - started:.1f}s")
    cli("--workspace", ws, "next", "--job", job)
    cli("--workspace", ws, "override", "--job", job, "--stage", "video", "--reason", "smoke test")
    cli("--workspace", ws, "package", "--job", job)
    print("JOB", job)
    return 0 if out.startswith("DONE") else 1


if __name__ == "__main__":
    sys.exit(main())
