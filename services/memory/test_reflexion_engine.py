#!/usr/bin/env python3
"""Unit tests for reflexion_engine."""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import reflexion_engine as re


class TestReflexionEngine(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_log = re.VERIFIED_LOG
        re.VERIFIED_LOG = os.path.join(self._tmp.name, "verified-failures.jsonl")

    def tearDown(self):
        re.VERIFIED_LOG = self._orig_log
        self._tmp.cleanup()

    @patch("urllib.request.urlopen", side_effect=Exception("Connection refused"))
    def test_fallback_when_ollama_unreachable(self, mock_url):
        lesson = re.query_ollama_reflexion("pytest test_auth.py", ["src/auth.py"], "myproj")
        self.assertIn("pytest test_auth.py", lesson)
        self.assertIn("src/auth.py", lesson)

    @patch("reflexion_engine.query_ollama_reflexion")
    def test_process_record_invokes_mem_add(self, mock_query):
        mock_query.return_value = "Always import JWTError from jose."
        mock_mem = MagicMock()

        record = {
            "command": "pytest",
            "edited_files": ["app/auth.py"],
            "project": "sample-app",
        }
        res = re.process_record(record, dry_run=False, mem_instance=mock_mem)

        self.assertEqual(res, "[GOTCHA] Always import JWTError from jose.")
        mock_mem.add.assert_called_once()
        args, kwargs = mock_mem.add.call_args
        self.assertEqual(args[0], "[GOTCHA] Always import JWTError from jose.")
        self.assertEqual(kwargs["run_id"], "sample-app")
        self.assertEqual(kwargs["agent_id"], "reflexion")
        self.assertFalse(kwargs["infer"])

    @patch("reflexion_engine.query_ollama_reflexion")
    def test_run_reflexion_marks_processed_and_writes_file(self, mock_query):
        mock_query.return_value = "Fix applied."
        mock_mem = MagicMock()

        # Seed verified-failures.jsonl
        test_records = [
            {"command": "npm test", "edited_files": ["index.js"], "project": "demo", "processed": False},
            {"command": "pytest", "edited_files": ["test.py"], "project": "demo", "processed": True},
        ]
        with open(re.VERIFIED_LOG, "w", encoding="utf-8") as f:
            for r in test_records:
                f.write(json.dumps(r) + "\n")

        with patch("reflexion_engine.get_memory", return_value=mock_mem):
            results = re.run_reflexion(dry_run=False)

        self.assertEqual(len(results), 1)
        self.assertIn("Fix applied.", results[0])

        # Verify updated file
        records = re.load_verified()
        self.assertEqual(len(records), 2)
        self.assertTrue(records[0]["processed"])
        self.assertTrue(records[1]["processed"])
        self.assertIn("lesson", records[0])


if __name__ == "__main__":
    unittest.main()
