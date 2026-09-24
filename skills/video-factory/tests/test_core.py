"""Unit and flow tests for the Video Factory core. No network, no browser.

Run: python3 -X utf8 tests/test_core.py   (stdlib unittest; also runs in the container)
Tests that need ffprobe are skipped when it is not installed.
"""

from __future__ import annotations

import copy
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import studio  # noqa: E402
from vfcore import backlog, briefs, captions, fontmetrics, jobs, package, tts  # noqa: E402
from vfcore.formats import get_format  # noqa: E402
from vfcore.paths import Studio  # noqa: E402
from vfcore.schema import validate_research, validate_review, validate_script  # noqa: E402
from vfcore.textutil import syllable_count  # noqa: E402
from vfcore.themes import get_theme  # noqa: E402

SHORT = get_format("short")
HAS_FFPROBE = shutil.which("ffprobe") is not None


def png_bytes(width: int, height: int, rgb: tuple[int, int, int] = (40, 80, 160)) -> bytes:
    """A solid-colour PNG built with zlib, so tests need no image library."""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def run_cli(*argv: str, stdin: str | None = None) -> tuple[int, str]:
    buf = io.StringIO()
    old_stdin = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        with redirect_stdout(buf):
            code = studio.main(list(argv))
    finally:
        sys.stdin = old_stdin
    return code, buf.getvalue()


class SchemaTests(unittest.TestCase):
    def test_examples_pass(self):
        errors, _ = validate_research(briefs.EXAMPLE_RESEARCH)
        self.assertEqual(errors, [])
        errors, _ = validate_script(briefs.EXAMPLE_SCRIPT, SHORT, {"F1", "F2"})
        self.assertEqual(errors, [])

    def test_number_without_fact_is_rejected(self):
        doc = copy.deepcopy(briefs.EXAMPLE_SCRIPT)
        doc["scenes"][2]["fact_ids"] = []
        errors, _ = validate_script(doc, SHORT, {"F1", "F2"})
        self.assertTrue(any("states a number" in e for e in errors), errors)

    def test_emoji_and_unknown_fact_rejected(self):
        doc = copy.deepcopy(briefs.EXAMPLE_SCRIPT)
        doc["scenes"][1]["narration"] += " 🔥"
        doc["scenes"][1]["fact_ids"] = ["F9"]
        errors, _ = validate_script(doc, SHORT, {"F1", "F2"})
        self.assertTrue(any("emoji" in e for e in errors), errors)
        self.assertTrue(any("F9" in e for e in errors), errors)

    def test_hook_first_and_cta_last(self):
        doc = copy.deepcopy(briefs.EXAMPLE_SCRIPT)
        doc["scenes"][0]["role"] = "body"
        doc["scenes"][3]["role"] = "cta"
        errors, _ = validate_script(doc, SHORT, {"F1", "F2"})
        self.assertTrue(any('must be "hook"' in e for e in errors), errors)
        self.assertTrue(any("must be the last scene" in e for e in errors), errors)

    def test_long_scene_and_wide_headline_rejected(self):
        doc = copy.deepcopy(briefs.EXAMPLE_SCRIPT)
        doc["scenes"][1]["narration"] = " ".join(["từ"] * 40)
        doc["scenes"][1]["emphasis"] = []
        doc["scenes"][4]["on_screen"] = "MƯỜI BA ĐIỀU QUAN TRỌNG NHẤT BẠN CẦN BIẾT"
        errors, _ = validate_script(doc, SHORT, {"F1", "F2"})
        self.assertTrue(any("syllables" in e for e in errors), errors)
        self.assertTrue(any("does not fit in 2 lines" in e for e in errors), errors)

    def test_facebook_caption_required(self):
        doc = copy.deepcopy(briefs.EXAMPLE_SCRIPT)
        del doc["social"]["facebook"]
        errors, _ = validate_script(doc, SHORT, {"F1", "F2"})
        self.assertTrue(any("facebook" in e for e in errors), errors)

    def test_on_screen_text_without_accents_is_rejected_but_english_is_not(self):
        doc = copy.deepcopy(briefs.EXAMPLE_SCRIPT)
        doc["scenes"][2]["visual"]["card"]["label"] = "nguoi truong thanh tri hoan kinh nien"
        doc["scenes"][7]["on_screen"] = "Save it for later"
        errors, _ = validate_script(doc, SHORT, {"F1", "F2"})
        accents = [e for e in errors if "drops the Vietnamese accents" in e]
        self.assertEqual(len(accents), 1, errors)
        self.assertIn("scene s3.visual.card.label", accents[0])
        self.assertIn('"người trưởng"', accents[0])

    def test_review_rules(self):
        review = copy.deepcopy(briefs.REVIEW_EXAMPLE)
        errors, _ = validate_review(review, "script", required_facts={"F1", "F2"}, scene_ids=["s1", "s3"])
        self.assertEqual(errors, [])
        review["verdict"] = "PASS"
        errors, _ = validate_review(review, "script", required_facts={"F1", "F2"}, scene_ids=["s1", "s3"])
        self.assertTrue(any("PASS but lists blocker" in e for e in errors), errors)
        review = copy.deepcopy(briefs.REVIEW_EXAMPLE)
        review["checked_facts"] = ["F1"]
        errors, _ = validate_review(review, "script", required_facts={"F1", "F2"}, scene_ids=["s1", "s3"])
        self.assertTrue(any("misses F2" in e for e in errors), errors)

    def test_an_older_deployed_version_hands_the_call_to_the_newest(self):
        with tempfile.TemporaryDirectory() as root:
            for version in ("9", "10", ".staging-11"):
                (Path(root) / version / "scripts").mkdir(parents=True)
                (Path(root) / version / "scripts" / "studio.py").write_text("", encoding="utf-8")
            newest = Path(root) / "10" / "scripts" / "studio.py"
            self.assertEqual(studio.newer_install(Path(root) / "9" / "scripts" / "studio.py"), newest)
            self.assertIsNone(studio.newer_install(newest))
            self.assertIsNone(studio.newer_install(Path(root) / ".staging-11" / "scripts" / "studio.py"))

    def test_syllables(self):
        self.assertEqual(syllable_count("Bạn có biết? Mỗi ngày, 70 nghìn suy nghĩ."), 9)


class CaptionTests(unittest.TestCase):
    WORDS = [{"text": t, "start": i * 0.25, "end": i * 0.25 + 0.22}
             for i, t in enumerate(["Bạn", "có", "biết", "Mỗi", "ngày", "bộ", "não", "xử", "lý", "khoảng",
                                    "bảy", "mươi", "nghìn", "suy", "nghĩ"])]

    def test_alignment_keeps_punctuation_and_handles_expansion(self):
        tokens = captions.align("Bạn có biết? Mỗi ngày, bộ não xử lý khoảng 70 nghìn suy nghĩ.", self.WORDS, 4.0)
        texts = [t.text for t in tokens]
        self.assertEqual(texts[2], "biết?")
        self.assertTrue(all(t.start >= 0 and t.end >= t.start for t in tokens))
        seventy = tokens[texts.index("70")]
        self.assertGreaterEqual(seventy.start, self.WORDS[9]["end"] - 0.01)   # after "khoảng"
        self.assertLessEqual(seventy.end, self.WORDS[12]["end"] + 0.01)       # before/at "nghìn" end
        starts = [t.start for t in tokens]
        self.assertEqual(starts, sorted(starts))

    def test_chunks_fit_the_frame(self):
        tokens = captions.align("Bạn có biết? Mỗi ngày, bộ não xử lý khoảng 70 nghìn suy nghĩ.", self.WORDS, 4.0)
        chunks = captions.chunk(tokens, SHORT)
        font = fontmetrics.load(fontmetrics.CAPTION_FONT_FILE)
        for c in chunks:
            self.assertLessEqual(font.ass_width(c.text, SHORT.caption_size), SHORT.text_width)
        self.assertTrue(chunks[0].text.endswith("biết?"))

    def test_scene_ass(self):
        tokens = captions.align("Bạn có biết?", self.WORDS[:3], 1.0)
        ass, chunks = captions.build_scene_ass(SHORT, get_theme("midnight"), tokens=tokens, emphasis=["biết"],
                                               on_screen="Không phải vì lười", duration=2.0, offset=0.12)
        self.assertIn("Be Vietnam Pro ExtraBold", ass)
        self.assertIn("Headline", ass)
        self.assertEqual(ass.count("Dialogue: 1,"), 3)
        srt = captions.build_srt([(10.0, chunks)])
        self.assertIn("00:00:10,", srt)

    def test_font_metrics_match_libass_measurement(self):
        # Measured on a libass frame in the container: "Vì sao não bộ" at Fontsize 96 = ~436 px.
        width = fontmetrics.load(fontmetrics.HEADLINE_FONT_FILE).ass_width("Vì sao não bộ", 96)
        self.assertAlmostEqual(width, 437, delta=8)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vf-test-"))
        self.ws = str(self.tmp / "studio")
        code, _ = run_cli("--workspace", self.ws, "init")
        self.assertEqual(code, 0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _next(self, job: str) -> dict:
        code, text = run_cli("--workspace", self.ws, "next", "--job", job)
        self.assertEqual(code, 0, text)
        return json.loads(text)

    def test_stage_machine(self):
        code, text = run_cli("--workspace", self.ws, "new", "--topic", "Vì sao chúng ta hay trì hoãn")
        self.assertEqual(code, 0, text)
        job = text.split()[1]
        action = self._next(job)
        self.assertEqual((action["stage"], action["owner"]), ("script", "vf-scriptwriter"))
        self.assertIn("emit --job", action["delegate"]["task"])

        code, text = run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "script",
                             stdin=json.dumps(briefs.EXAMPLE_SCRIPT))
        self.assertIn("submit research.json first", text)
        code, text = run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "research",
                             stdin="```json\n" + json.dumps(briefs.EXAMPLE_RESEARCH, ensure_ascii=False) + "\n```")
        self.assertEqual(code, 0, text)
        code, text = run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "script",
                             stdin=json.dumps(briefs.EXAMPLE_SCRIPT, ensure_ascii=False))
        self.assertEqual(code, 0, text)

        action = self._next(job)
        self.assertEqual(action["stage"], "review_script")
        code, text = run_cli("--workspace", self.ws, "emit", "--job", job, "--stage", "review_script")
        self.assertIn("F2:", text)

        revise = copy.deepcopy(briefs.REVIEW_EXAMPLE)
        code, text = run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "review_script",
                             stdin=json.dumps(revise, ensure_ascii=False))
        self.assertEqual(code, 0, text)
        action = self._next(job)
        self.assertEqual(action["stage"], "script_revise")
        code, text = run_cli("--workspace", self.ws, "emit", "--job", job, "--stage", "script_revise")
        self.assertIn("REVISION", text)
        self.assertIn("ở Mỹ", text)

        fixed = copy.deepcopy(briefs.EXAMPLE_SCRIPT)
        fixed["scenes"][2]["narration"] = "Ở Mỹ, cứ 5 người trưởng thành thì có 1 người trì hoãn kinh niên."
        code, text = run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "script",
                             stdin=json.dumps(fixed, ensure_ascii=False))
        self.assertEqual(code, 0, text)
        paths = Studio(Path(self.ws)).job(job)
        self.assertEqual(jobs.load_meta(paths)["revisions"]["script"], 1)
        self.assertEqual(self._next(job)["stage"], "review_script")

        ok = {"schema": "vf.review.v1", "stage": "script", "verdict": "PASS", "issues": [],
              "checked_facts": ["F1", "F2"]}
        code, text = run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "review_script",
                             stdin=json.dumps(ok))
        self.assertEqual(code, 0, text)
        action = self._next(job)
        self.assertEqual(action["stage"], "assets")
        self.assertEqual(len(action["calls"]), 6)
        call = action["calls"][0]
        self.assertEqual(call["create_image"]["aspect_ratio"], "9:16")
        self.assertIn("no text", call["create_image"]["prompt"])

        # A prompt-only change must not reopen the fact check.
        code, text = run_cli("--workspace", self.ws, "revise-visual", "--job", job, "--scene", "s2",
                             "--prompt", "a person walking away from a storm cloud at dusk, no text")
        self.assertEqual(code, 0, text)
        self.assertEqual(self._next(job)["stage"], "assets")

        if not HAS_FFPROBE:
            return
        outside = self.tmp / "img.png"
        outside.write_bytes(png_bytes(768, 1376))
        code, text = run_cli("--workspace", self.ws, "attach", "--job", job, "--scene",
                             self._next(job)["calls"][0]["scene"], "--file", f"MEDIA:{outside}")
        self.assertEqual(code, 1, text)                       # not a create_image output
        image = self.tmp / "generated" / "2026-09-24" / "img.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(png_bytes(768, 1376))
        self.addCleanup(setattr, studio, "IMAGE_ROOT", studio.IMAGE_ROOT)
        studio.IMAGE_ROOT = self.tmp
        for c in self._next(job)["calls"]:
            code, text = run_cli("--workspace", self.ws, "attach", "--job", job, "--scene", c["scene"],
                                 "--file", f"MEDIA:{image}")
            self.assertEqual(code, 0, text)
        action = self._next(job)
        self.assertEqual(action["stage"], "render")
        self.assertIn("render --job", action["exec"])

    def _job_at_video_review(self) -> tuple[str, "object"]:
        """A job whose script passed review and whose 'render' is faked by a manifest."""
        code, text = run_cli("--workspace", self.ws, "new", "--topic", "Kiểm thử phản hồi")
        job = text.split()[1]
        run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "research",
                stdin=json.dumps(briefs.EXAMPLE_RESEARCH, ensure_ascii=False))
        run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "script",
                stdin=json.dumps(briefs.EXAMPLE_SCRIPT, ensure_ascii=False))
        code, text = run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "review_script",
                             stdin=json.dumps({"schema": "vf.review.v1", "stage": "script", "verdict": "PASS",
                                               "issues": [], "checked_facts": ["F1", "F2"]}))
        self.assertEqual(code, 0, text)
        paths = Studio(Path(self.ws)).job(job)
        from vfcore.util import sha256_file, write_json
        state = jobs.load_state(paths)
        paths.assets.mkdir(parents=True, exist_ok=True)
        for scene in state.script["scenes"]:
            if scene["visual"]["kind"] == "ai_image":
                (paths.assets / f"{scene['id']}.png").write_bytes(png_bytes(8, 8))
                write_json(paths.assets / f"{scene['id']}.json", {
                    "file": f"{scene['id']}.png", "sha256": "0" * 16,
                    "prompt_sha": jobs.prompt_sha(jobs.image_prompt(scene, state.script, state.fmt))})
        paths.out.mkdir(parents=True, exist_ok=True)
        paths.master.write_bytes(b"fake master")
        write_json(paths.manifest, {"master_sha": sha256_file(paths.master)[:16], "qa_ok": True,
                                    "input_sha": jobs.render_input_sha(jobs.load_state(paths), {})})
        return job, paths

    def test_video_review_reads_every_frame_in_one_turn(self):
        job, paths = self._job_at_video_review()
        action = jobs.next_action(Studio(Path(self.ws)), paths, {})
        self.assertEqual(action["stage"], "review_video")
        self.assertIn("Do not run emit again", action["delegate"]["task"])
        from vfcore.util import write_json
        write_json(paths.qa, {"ok": True, "soft": [], "contact_sheet": str(paths.contact)})
        code, text = run_cli("--workspace", self.ws, "emit", "--job", job, "--stage", "review_video")
        self.assertEqual(code, 0, text)
        scenes = briefs.EXAMPLE_SCRIPT["scenes"]
        self.assertIn(f"make all {len(scenes) + 1} read_image calls below in ONE turn", text)
        for scene in scenes:
            self.assertIn(f"{scene['id']}.jpg\n  prompt: One frame of a vertical social video (scene {scene['id']};", text)
        self.assertIn("a picture inside a picture", text)
        self.assertIn('"checked_scenes"', text)
        self.assertIn("deliver: false", text)
        last = text.rstrip().splitlines()[-1]
        self.assertTrue(last.startswith("NEXT: in one turn, call read_image once for each of"), last)

    def test_consecutive_feedback_on_one_cut_adds_up(self):
        job, paths = self._job_at_video_review()
        for scene in ("s1", "s2"):
            code, text = run_cli("--workspace", self.ws, "feedback", "--job", job, "--type", "visual",
                                 "--scene", scene, "--text", f"ảnh {scene} bị lồng khung")
            self.assertEqual(code, 0, text)
        action = jobs.next_action(Studio(Path(self.ws)), paths, {})
        self.assertEqual(action["stage"], "fix_visuals")
        self.assertEqual(sorted(action["scenes"]), ["s1", "s2"])
        self.assertEqual(len(jobs.reviews(paths, "video")), 1)

    def test_model_review_rounds_escalate_but_human_feedback_does_not(self):
        job, paths = self._job_at_video_review()
        revise = {"schema": "vf.review.v1", "stage": "video", "verdict": "REVISE",
                  "issues": [{"scene": "s1", "severity": "major", "type": "visual",
                              "problem": "chữ méo trong ảnh", "fix": "tạo lại ảnh"}],
                  "checked_scenes": [s["id"] for s in briefs.EXAMPLE_SCRIPT["scenes"]]}
        code, text = run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "review_video",
                             stdin=json.dumps(revise, ensure_ascii=False))
        self.assertEqual(code, 0, text)
        meta = jobs.load_meta(paths)
        meta["revisions"]["video"] = jobs.MAX_VIDEO_REVISIONS
        jobs.save_meta(paths, meta)
        action = jobs.next_action(Studio(Path(self.ws)), paths, {})
        self.assertEqual(action["stage"], "escalate")
        self.assertNotIn("commands", action)                  # nothing to run until the human answers
        action = jobs.next_action(Studio(Path(self.ws)), paths, {})
        self.assertEqual((action["stage"], action["action"]), ("escalated", "stop"))
        self.assertNotIn("--quote", action["human_commands"]["continue anyway"])   # the gateway supplies the words
        # The same situation raised by a person is never capped.
        code, text = run_cli("--workspace", self.ws, "feedback", "--job", job, "--type", "visual",
                             "--scene", "s2", "--text", "ảnh cảnh 2 tối quá")
        self.assertEqual(code, 0, text)
        action = jobs.next_action(Studio(Path(self.ws)), paths, {})
        self.assertEqual(action["stage"], "fix_visuals")
        self.assertEqual(action["scenes"], ["s2"])
        code, text = run_cli("--workspace", self.ws, "feedback", "--job", job, "--type", "script",
                             "--text", "đổi hook thành câu hỏi")
        action = jobs.next_action(Studio(Path(self.ws)), paths, {})
        self.assertEqual(action["stage"], "script_revise")

    def test_escalation_is_sent_to_the_review_channel_with_the_chat_id_as_text(self):
        job, paths = self._job_at_video_review()
        for kv in ("delivery.channel=vf-discord", "delivery.target=1552695501751586918"):
            code, text = run_cli("--workspace", self.ws, "config", "set", kv)
            self.assertEqual(code, 0, text)
        revise = {"schema": "vf.review.v1", "stage": "video", "verdict": "REVISE",
                  "issues": [{"scene": "s1", "severity": "major", "type": "visual",
                              "problem": "bàn tay sáu ngón", "fix": "tạo lại ảnh"}],
                  "checked_scenes": [s["id"] for s in briefs.EXAMPLE_SCRIPT["scenes"]]}
        run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "review_video",
                stdin=json.dumps(revise, ensure_ascii=False))
        meta = jobs.load_meta(paths)
        meta["revisions"]["video"] = jobs.MAX_VIDEO_REVISIONS
        jobs.save_meta(paths, meta)
        action = jobs.next_action(Studio(Path(self.ws)), paths, {})
        self.assertEqual(action["stage"], "escalate")
        cfg = studio.load_config(Studio(Path(self.ws)))
        self.assertEqual(cfg["delivery"]["target"], "1552695501751586918")   # never a rounded number
        message = package.escalation_message(paths, action["issues"])
        self.assertIn(f"job: {job}", message)
        self.assertIn("[s1] bàn tay sáu ngón", message)
        self.assertIn("cứ làm tiếp", message)

    def test_redo_takes_only_a_scene_of_the_script(self):
        job, paths = self._job_at_video_review()
        code, text = run_cli("--workspace", self.ws, "redo", "--job", job, "--scene", "../job")
        self.assertEqual(code, 1, text)
        self.assertTrue(paths.meta.exists())

    def test_config_values_are_typed_and_a_publish_switch_is_only_true_or_false(self):
        for kv in ("publish.facebook_reels.enabled=no", "defaults.rate=abc"):
            code, text = run_cli("--workspace", self.ws, "config", "set", kv)
            self.assertEqual(code, 1, text)
        self.assertFalse(studio.load_config(Studio(Path(self.ws)))["publish"]["facebook_reels"]["enabled"])

    def test_two_reviews_claiming_one_number_both_survive(self):
        from vfcore.util import write_json_numbered
        with tempfile.TemporaryDirectory() as root:
            first = write_json_numbered(Path(root), "video", 1, {"n": 1})
            second = write_json_numbered(Path(root), "video", 1, {"n": 2})
            self.assertEqual((first.name, second.name), ("video-01.json", "video-02.json"))

    def _gateway_receipt(self, receipt: dict) -> None:
        """Stand in for the gateway's GET /v1/runs/receipt for this test."""
        import http.server
        import threading

        body = json.dumps(receipt).encode("utf-8")

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                ok = self.headers.get("Authorization") == "Bearer tok-1"
                self.send_response(200 if ok else 404)
                self.end_headers()
                if ok:
                    self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(setattr, studio, "RECEIPT_URL", studio.RECEIPT_URL)
        studio.RECEIPT_URL = f"http://127.0.0.1:{server.server_port}/v1/runs/receipt"

    def test_override_takes_the_persons_reply_from_the_gateway_never_from_the_model(self):
        job, paths = self._job_at_video_review()
        override = ("--workspace", self.ws, "override", "--job", job, "--stage", "video")
        code, text = run_cli(*override)
        self.assertEqual(code, 1, text)                       # nothing escalated: nothing to override
        self.assertIn("only answers an escalated review", text)
        meta = jobs.load_meta(paths)
        meta["status"], meta["escalated_stage"] = "escalated", "video"
        jobs.save_meta(paths, meta)
        self.addCleanup(os.environ.pop, "GOCLAW_RUN_RECEIPT", None)
        os.environ.pop("GOCLAW_RUN_RECEIPT", None)
        code, text = run_cli(*override)                       # a cron run: no receipt at all
        self.assertEqual(code, 1, text)
        question = f"⚠️ Video Factory · x\njob: {job}\n\nReview video vẫn chưa đạt"
        cases = [({"human_reply": False}, "not started by an allowlisted person"),
                 ({"human_reply": True, "reply_to_content": "⚠️ Video Factory\njob: vf-other",
                   "current_message": "cứ làm tiếp"}, "not the escalation question"),
                 ({"human_reply": True, "reply_to_content": question, "current_message": "không được, làm lại"},
                  "does not say to continue")]
        cases.insert(1, ({"human_reply": True, "channel": "telegram-main", "reply_to_content": question,
                          "current_message": "cứ làm tiếp"}, "not the review channel"))
        for receipt, _ in cases[2:]:
            receipt["channel"] = "vf-discord"
        run_cli("--workspace", self.ws, "config", "set", "delivery.channel=vf-discord")
        os.environ["GOCLAW_RUN_RECEIPT"] = "tok-1"
        for receipt, reason in cases:
            self._gateway_receipt(receipt)
            code, text = run_cli(*override)
            self.assertEqual(code, 1, text)
            self.assertIn(reason, text)
        self.assertEqual(jobs.load_meta(paths)["status"], "escalated")
        self._gateway_receipt({"human_reply": True, "channel": "vf-discord", "reply_to_content": question,
                               "current_message": "cứ làm tiếp"})
        code, text = run_cli(*override)
        self.assertEqual(code, 0, text)
        self.assertEqual(jobs.load_meta(paths)["status"], "active")
        self.assertEqual(jobs.reviews(paths, "video")[-1]["_human_quote"], "cứ làm tiếp")
        message = package.review_message(*PackageTests.ARGS, "Caption.", [], "/tmp/p.mp4", publishable=False,
                                         overrides=["cứ làm tiếp"])
        self.assertIn("theo lời bạn: «cứ làm tiếp»", message)

    def test_voice_change_does_not_reopen_fact_check(self):
        job, paths = self._job_at_video_review()
        state = jobs.load_state(paths)
        before = jobs.script_review_sha(state.research, state.script)
        input_before = jobs.render_input_sha(state, {})
        code, text = run_cli("--workspace", self.ws, "set", "--job", job, "--voice", "vi-VN-NamMinhNeural")
        self.assertEqual(code, 0, text)
        state = jobs.load_state(paths)
        self.assertEqual(jobs.script_review_sha(state.research, state.script), before)
        self.assertEqual(jobs.script_voice(state), "vi-VN-NamMinhNeural")
        self.assertNotEqual(jobs.render_input_sha(state, {}), input_before)

    def test_backlog(self):
        import datetime as dt
        st = Studio(Path(self.ws))
        backlog.add(st, "Cách AI tạo ảnh hoạt động")
        backlog.add(st, "Vì sao bầu trời màu xanh")
        with self.assertRaises(Exception):
            backlog.add(st, "cách AI tạo ảnh hoạt động")
        code, text = run_cli("--workspace", self.ws, "backlog", "take")
        self.assertIn("CREATED vf-", text)
        first = text.split()[1]
        # A cron retry (the run outlived cron.job_timeout) continues the same video.
        code, text = run_cli("--workspace", self.ws, "backlog", "take")
        self.assertIn(f"RESUME {first}", text)
        self.assertEqual(len(backlog.pending(st)), 1)
        later = dt.datetime.now(dt.timezone.utc) + backlog.CRON_RESUME_WINDOW + dt.timedelta(hours=1)
        self.assertIsNone(backlog.unfinished_cron_job(st, now=later))
        run_cli("--workspace", self.ws, "cancel", "--job", first, "--reason", "test")
        code, text = run_cli("--workspace", self.ws, "backlog", "take")
        self.assertIn("CREATED vf-", text)
        run_cli("--workspace", self.ws, "cancel", "--job", text.split()[1], "--reason", "test")
        code, text = run_cli("--workspace", self.ws, "backlog", "take")
        self.assertIn("EMPTY", text)


    def test_submit_reads_the_job_inbox_and_nothing_else(self):
        code, text = run_cli("--workspace", self.ws, "new", "--topic", "Nộp qua tệp")
        job = text.split()[1]
        paths = Studio(Path(self.ws)).job(job)
        paths.inbox.mkdir(parents=True, exist_ok=True)
        inbox = paths.inbox_file("research")
        # "su", "host", "mount": words exec's shell guard refuses on a command line.
        research = copy.deepcopy(briefs.EXAMPLE_RESEARCH)
        research["caveats"].append("host su mount: the shell guard would have refused this as a heredoc")
        inbox.write_text(json.dumps(research, ensure_ascii=False), encoding="utf-8")
        code, text = run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "research",
                             "--file", str(inbox))
        self.assertEqual(code, 0, text)
        self.assertFalse(inbox.exists(), "a stored inbox file must be consumed")
        outside = self.tmp / "elsewhere.json"
        outside.write_text("{}", encoding="utf-8")
        code, text = run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "script",
                             "--file", str(outside))
        self.assertNotEqual(code, 0)
        self.assertIn("must be inside", text)

    def test_inbox_exists_before_it_is_printed_and_cancelled_jobs_are_frozen(self):
        code, text = run_cli("--workspace", self.ws, "new", "--topic", "Hủy giữa chừng")
        job = text.split()[1]
        paths = Studio(Path(self.ws)).job(job)
        self._next(job)
        self.assertTrue(paths.inbox.is_dir(), "write_file cannot create the inbox folder itself")
        run_cli("--workspace", self.ws, "cancel", "--job", job, "--reason", "test")
        code, text = run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "research",
                             stdin=json.dumps(briefs.EXAMPLE_RESEARCH, ensure_ascii=False))
        self.assertNotEqual(code, 0)
        self.assertIn("is cancelled", text)

    def test_first_model_verdict_is_final(self):
        code, text = run_cli("--workspace", self.ws, "new", "--topic", "Khóa kết luận")
        job = text.split()[1]
        run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "research",
                stdin=json.dumps(briefs.EXAMPLE_RESEARCH, ensure_ascii=False))
        run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "script",
                stdin=json.dumps(briefs.EXAMPLE_SCRIPT, ensure_ascii=False))
        code, text = run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "review_script",
                             stdin=json.dumps(briefs.REVIEW_EXAMPLE, ensure_ascii=False))
        self.assertEqual(code, 0, text)
        self.assertIn("RECORDED review_script: verdict=REVISE", text)
        self.assertNotIn("PASS", text.split("\n")[0])
        flip = {"schema": "vf.review.v1", "stage": "script", "verdict": "PASS", "issues": [],
                "checked_facts": ["F1", "F2"]}
        code, text = run_cli("--workspace", self.ws, "submit", "--job", job, "--kind", "review_script",
                             stdin=json.dumps(flip))
        self.assertIn("ALREADY RECORDED", text)
        self.assertEqual(self._next(job)["stage"], "script_revise")
        self.assertIn("STAGE_RESULT: script_revise DONE", self._next(job)["delegate"]["task"])


class PronunciationTests(unittest.TestCase):
    def test_lexicon_respells_known_terms_but_never_the_word_ai(self):
        spoken = tts.spoken_text("Ai cũng dùng AI, ChatGPT và GPT-5 trên web.", None, tts.VI_LEXICON)
        self.assertEqual(spoken, "Ai cũng dùng ây ai, chát gi pi ti và gi pi ti 5 trên oép.")
        self.assertEqual(tts.foreign_tokens(spoken), [])

    def test_foreign_words_are_found_and_vietnamese_is_not(self):
        self.assertEqual(tts.foreign_tokens("Claude và Netflix, CEO của iPhone"), ["Claude", "Netflix", "CEO", "iPhone"])
        self.assertEqual(tts.foreign_tokens("Khuya rồi, nghiêng người, quyển sách, KHÔNG gì cả, bật/tắt 47%."), [])

    def test_script_with_unrespelled_foreign_word_is_rejected_until_tts_text(self):
        doc = copy.deepcopy(briefs.EXAMPLE_SCRIPT)
        doc["scenes"][1]["narration"] = "Claude cũng trì hoãn như bạn."
        doc["scenes"][1]["emphasis"] = []
        errors, _ = validate_script(doc, SHORT, {"F1", "F2"})
        self.assertTrue(any("would misread Claude" in e for e in errors), errors)
        doc["scenes"][1]["tts_text"] = "Cờ lốt cũng trì hoãn như bạn."
        errors, _ = validate_script(doc, SHORT, {"F1", "F2"})
        self.assertEqual(errors, [])

    def test_video_review_cannot_argue_facts(self):
        review = {"schema": "vf.review.v1", "stage": "video", "verdict": "REVISE", "checked_scenes": ["s1"],
                  "issues": [{"scene": "s1", "severity": "blocker", "type": "fact",
                              "problem": "GPT-5 chưa ra mắt", "fix": "đổi nguồn"}]}
        errors, _ = validate_review(review, "video", required_facts=set(), scene_ids=["s1"])
        self.assertTrue(any('"fact" does not belong' in e for e in errors), errors)

    def test_image_prompt_asks_for_a_full_bleed_picture(self):
        prompt = jobs.image_prompt(briefs.EXAMPLE_SCRIPT["scenes"][0], briefs.EXAMPLE_SCRIPT, SHORT)
        self.assertIn("full-bleed", prompt)
        self.assertNotIn("empty areas", prompt)

    def test_a_prompt_pasted_back_whole_is_not_suffixed_twice(self):
        scene = copy.deepcopy(briefs.EXAMPLE_SCRIPT["scenes"][0])
        printed = jobs.image_prompt(scene, briefs.EXAMPLE_SCRIPT, SHORT)
        scene["visual"]["prompt"] = printed
        self.assertEqual(jobs.image_prompt(scene, briefs.EXAMPLE_SCRIPT, SHORT), printed)


class RenderPaceTests(unittest.TestCase):
    def test_first_step_always_runs_then_estimates_follow_the_observed_slowdown(self):
        from vfcore import render
        with tempfile.TemporaryDirectory() as tmp:
            pace = render._Pace(Path(tmp) / "pace.json", budget_seconds=100)
            self.assertTrue(pace.fits(10_000))          # nothing done in this call yet
            pace.done(10, took=50)                      # the machine runs 5x slower
            self.assertEqual(pace.slowdown, 5.0)
            self.assertTrue(pace.fits(10))              # 10 * 5 * 1.3 = 65 s
            self.assertFalse(pace.fits(20))             # 130 s is past the budget
            self.assertEqual(render._Pace(Path(tmp) / "pace.json", 100).slowdown, 5.0)

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_review_cut_stopped_between_passes_resumes_at_pass_two(self):
        import subprocess
        from vfcore import render
        with tempfile.TemporaryDirectory() as tmp:
            paths = Studio(Path(tmp)).job("vf-test")
            paths.render.mkdir(parents=True)
            paths.master.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                            "testsrc=size=540x960:rate=30:duration=1", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=1", "-c:v", "libx264", "-preset", "ultrafast",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(paths.master)],
                           check=True, timeout=300, stdin=subprocess.DEVNULL)
            # A zero budget stops after the first step of each call: pass 1, then pass 2.
            self.assertIsNone(render._review_cut(paths, SHORT, 1.0, render._Pace(paths.render / "p", 0)))
            self.assertTrue(json.loads((paths.render / "reviewcut.json").read_text())["pass1"])
            cut = render._review_cut(paths, SHORT, 1.0, render._Pace(paths.render / "p", 0))
            self.assertEqual((cut["width"], cut["height"]), (1080, 1920))
            self.assertEqual(cut["bytes"], paths.preview.stat().st_size)
            self.assertFalse(list(paths.render.glob("reviewcut-*")))


class ContactSheetTests(unittest.TestCase):
    def test_video_review_example_is_a_valid_verdict(self):
        ids = [s["id"] for s in briefs.EXAMPLE_SCRIPT["scenes"]]
        errors, _ = validate_review(briefs.video_review_example(ids), "video", required_facts=set(), scene_ids=ids)
        self.assertEqual(errors, [])

    def test_grid_comes_out_full_and_never_taller_than_wide(self):
        from vfcore.qa import sheet_columns
        self.assertEqual([sheet_columns(n, True) for n in (6, 8, 9, 10, 12)], [3, 4, 3, 4, 4])
        self.assertEqual([sheet_columns(n, False) for n in (4, 6, 9)], [2, 3, 3])


class PackageTests(unittest.TestCase):
    ARGS = ("Vì sao chúng ta hay trì hoãn?", "vf-260924-1530-x", {"duration": 42.3, "loudness_lufs": -14.1},
            "short", "vi-VN-HoaiMyNeural")

    def test_publishable_message_carries_the_whole_caption_between_markers(self):
        caption = "Trì hoãn không phải do lười.\n\n#tamly #kienthuc"
        message = package.review_message(*self.ARGS, caption, briefs.EXAMPLE_RESEARCH["sources"],
                                         "/tmp/p.mp4", publishable=True)
        self.assertLessEqual(len(message.encode("utf-8")), 1900)
        self.assertIn(f"[caption]\n{caption}\n[/caption]", message)
        self.assertTrue(message.endswith("MEDIA:/tmp/p.mp4"))
        self.assertIn("job: vf-260924-1530-x", message)
        off = package.review_message(*self.ARGS, caption, [], "/tmp/p.mp4", publishable=False)
        self.assertNotIn("[caption]", off)

    def test_caption_is_never_trimmed(self):
        from vfcore.util import StudioError
        with self.assertRaises(StudioError):
            package.review_message(*self.ARGS, "Trì hoãn không phải do lười. " * 120, [], "/tmp/p.mp4",
                                   publishable=True)

    def test_reels_blockers(self):
        self.assertEqual(package.reels_blockers("short", 42.0, {"width": 1080, "height": 1920}), [])
        self.assertEqual(len(package.reels_blockers("long", 120.0, {"width": 1280, "height": 720})), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
