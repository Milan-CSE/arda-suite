"""Unit tests for lessons_detect.py (transcript scanner for failure->fix cycles)."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch
import lessons_detect as ld


class TestLessonsDetect(unittest.TestCase):
    def test_normalize_and_hash(self):
        self.assertEqual(ld.normalize("  npm  run  test  "), "npm test")
        self.assertEqual(ld.normalize("pytest -v"), "pytest -v")
        self.assertEqual(ld.cmd_hash("npm run test"), ld.cmd_hash("npm test"))

    def test_is_test_command(self):
        self.assertTrue(ld.is_test_command("npm test"))
        self.assertTrue(ld.is_test_command("npm run test -- --grep auth"))
        self.assertTrue(ld.is_test_command("python -m unittest discover"))
        self.assertTrue(ld.is_test_command("pytest tests/"))
        self.assertTrue(ld.is_test_command("vitest run"))
        self.assertTrue(ld.is_test_command("git status && cargo test"))
        self.assertFalse(ld.is_test_command("git status"))
        self.assertFalse(ld.is_test_command("npm install"))

    def test_is_genuine_failure(self):
        # Normal failure
        self.assertTrue(ld.is_genuine_failure(True, "AssertionError: expected 1, got 2"))
        # Not an error
        self.assertFalse(ld.is_genuine_failure(False, "AssertionError"))
        # Excluded classifier / permission / tool errors
        self.assertFalse(ld.is_genuine_failure(True, "Permission for this action was denied"))
        self.assertFalse(ld.is_genuine_failure(True, "Denied by the Claude Code auto mode classifier"))
        self.assertFalse(ld.is_genuine_failure(True, "<tool_use_error>"))

    def test_days_between(self):
        self.assertEqual(ld._days_between("2026-09-10T00:00:00Z", "2026-09-20T00:00:00Z"), 10)
        self.assertIsNone(ld._days_between("invalid-date", "2026-09-20T00:00:00Z"))

    def test_scan_verified_cycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_path = os.path.join(tmpdir, "session-123.jsonl")
            events = [
                # 1. Test fails
                {
                    "timestamp": "2026-09-20T10:00:00Z",
                    "message": {
                        "content": [
                            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "npm test"}},
                            {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "FAIL test/user.test.ts"}
                        ]
                    }
                },
                # 2. File edited
                {
                    "timestamp": "2026-09-20T10:01:00Z",
                    "message": {
                        "content": [
                            {"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": "src/user.ts"}},
                            {"type": "tool_result", "tool_use_id": "t2", "content": "ok"}
                        ]
                    }
                },
                # 3. Test passes
                {
                    "timestamp": "2026-09-20T10:02:00Z",
                    "message": {
                        "content": [
                            {"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "npm run test"}},
                            {"type": "tool_result", "tool_use_id": "t3", "is_error": False, "content": "PASS 1 test passed"}
                        ]
                    }
                }
            ]
            with open(transcript_path, "w", encoding="utf-8") as f:
                for ev in events:
                    f.write(json.dumps(ev) + "\n")

            with patch("lessons_detect.PENDING_LOG", os.path.join(tmpdir, "pending.jsonl")), \
                 patch("lessons_detect.VERIFIED_LOG", os.path.join(tmpdir, "verified.jsonl")):
                res = ld.scan(transcript_path, "my-project", "2026-09-20T10:05:00Z", dry_run=False)
                self.assertEqual(len(res["verified"]), 1)
                v = res["verified"][0]
                self.assertEqual(v["command"], "npm run test")
                self.assertEqual(v["edited_files"], ["src/user.ts"])
                self.assertEqual(v["project"], "my-project")
                self.assertEqual(v["session"], "session-123")

    def test_scan_flaky_rerun_not_verified(self):
        """Failure followed immediately by pass WITHOUT edit is ignored (flaky)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_path = os.path.join(tmpdir, "session-456.jsonl")
            events = [
                # 1. Test fails
                {
                    "timestamp": "2026-09-20T10:00:00Z",
                    "message": {
                        "content": [
                            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "npm test"}},
                            {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "FAIL"}
                        ]
                    }
                },
                # 2. Test passes with NO edit in between
                {
                    "timestamp": "2026-09-20T10:01:00Z",
                    "message": {
                        "content": [
                            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "npm test"}},
                            {"type": "tool_result", "tool_use_id": "t2", "is_error": False, "content": "PASS"}
                        ]
                    }
                }
            ]
            with open(transcript_path, "w", encoding="utf-8") as f:
                for ev in events:
                    f.write(json.dumps(ev) + "\n")

            with patch("lessons_detect.PENDING_LOG", os.path.join(tmpdir, "pending.jsonl")), \
                 patch("lessons_detect.VERIFIED_LOG", os.path.join(tmpdir, "verified.jsonl")):
                res = ld.scan(transcript_path, "my-project", "2026-09-20T10:05:00Z", dry_run=True)
                self.assertEqual(len(res["verified"]), 0)


if __name__ == "__main__":
    unittest.main()
