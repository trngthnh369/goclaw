#!/usr/bin/env python3
# gemini_bridge.py — minimal OpenAI-compatible /v1/chat/completions shim over the HOST gemini-cli.
#
# Why: the GoClaw container (and daily_report_run.py) needs an LLM, but the only stable/free option
# is the host gemini-cli (Google Ultra OAuth — no API key, no phone-gate, no token rotation death).
# gemini-cli is a host binary that can't run in the container. This tiny shim runs on the HOST and
# exposes gemini-cli as an OpenAI chat endpoint; the container reaches it at host.docker.internal:PORT.
#
# Run on HOST:  python gemini_bridge.py    (or set GEMINI_BRIDGE_PORT / GEMINI_BIN)
import json
import os
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("GEMINI_BRIDGE_PORT", "8765"))
# Resolve the real executable (on Windows npm installs gemini.CMD, which subprocess needs the
# full path to launch).
GEMINI = os.environ.get("GEMINI_BIN") or shutil.which("gemini") or "gemini"
# Default to a FAST model: the CLI default (gemini-3.1-pro-preview) is slow (~60-120s) and times
# out on bigger feeds; flash-lite returns clean JSON in ~14s. Still >= 3.1 (user requirement).
MODEL_FLAG = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
# Fixed empty-ish workdir so the agentic CLI doesn't explore real project files. Reused across
# calls (a fresh TemporaryDirectory can't be cleaned up because gemini-cli keeps it locked).
WORKDIR = os.environ.get("GEMINI_BRIDGE_WORKDIR",
                         os.path.join(os.path.expanduser("~"), ".gemini-bridge-workdir"))


RETRIES = int(os.environ.get("GEMINI_BRIDGE_RETRIES", "3"))


def _gemini_once(prompt: str) -> str:
    # --output-format json: structured envelope {response, stats} (no agentic preamble in stdout).
    # --approval-mode plan: read-only, so the CLI won't explore/edit files.
    # --skip-trust: the workdir isn't a trusted gemini workspace.
    # The actual prompt goes via STDIN (input=), NOT the -p arg: large/Vietnamese prompts passed as
    # a Windows command-line arg get mangled/dropped; stdin is reliable. -p carries only a directive.
    cmd = [GEMINI, "--output-format", "json", "--approval-mode", "plan", "--skip-trust"]
    if MODEL_FLAG:
        cmd += ["-m", MODEL_FLAG]
    cmd += ["-p", "Thực hiện đúng yêu cầu trong nội dung ở trên. Chỉ trả về JSON, không giải thích."]
    env = dict(os.environ, GEMINI_CLI_TRUST_WORKSPACE="true")
    # encoding=utf-8 is REQUIRED: gemini-cli emits UTF-8 (Vietnamese), but Windows subprocess
    # defaults to cp1252 -> mojibake without this.
    proc = subprocess.run(cmd, input=prompt, cwd=WORKDIR, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120, env=env)
    out = (proc.stdout or "").strip()
    try:
        env_obj = json.loads(out)
        if isinstance(env_obj, dict) and "response" in env_obj:
            return str(env_obj["response"]).strip()
    except Exception:  # noqa: BLE001
        pass
    return out


def run_gemini(prompt: str) -> str:
    # gemini-cli is an agentic CLI and intermittently returns empty/garbage; retry a few times.
    os.makedirs(WORKDIR, exist_ok=True)
    last = ""
    for attempt in range(1, RETRIES + 1):
        try:
            out = _gemini_once(prompt)
        except Exception as exc:  # noqa: BLE001
            print(f"[gemini-bridge] attempt {attempt} error: {exc}", flush=True)
            out = ""
        if out and ("[" in out or "{" in out):  # looks like it carries JSON
            return out
        print(f"[gemini-bridge] attempt {attempt} empty/no-json (len={len(out)}), retrying...", flush=True)
        last = out
    return last


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, obj: dict):
        out = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        self._json(200, {"status": "ok", "backend": "gemini-cli"})

    def do_POST(self):
        if "chat/completions" not in self.path:
            self._json(404, {"error": {"message": "not found"}})
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            msgs = body.get("messages", [])
            prompt = "\n\n".join(m.get("content", "") for m in msgs
                                 if m.get("role") in ("system", "user") and m.get("content"))
            content = run_gemini(prompt)
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": {"message": f"gemini-bridge: {exc}"}})
            return
        self._json(200, {
            "id": "gemini-bridge", "object": "chat.completion",
            "model": body.get("model", "gemini-cli"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    def log_message(self, *a):  # quiet
        pass


if __name__ == "__main__":
    print(f"gemini-bridge listening on 0.0.0.0:{PORT} (gemini={GEMINI})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
