#!/usr/bin/env python3
"""Unit tests for universal mem0_client.py SDK."""

import unittest
from unittest.mock import MagicMock, patch

import mem0_client


class TestMem0Client(unittest.TestCase):

    def setUp(self):
        self.client = mem0_client.Mem0Client(scope="test_scope", agent="test_agent")

    @patch("mem0_client.Mem0Client._get_memory_client")
    def test_add_memory(self, mock_get_client):
        mock_mem = MagicMock()
        mock_mem.add.return_value = {"results": [{"id": "m123", "memory": "Uses Postgres"}]}
        mock_get_client.return_value = mock_mem

        res = self.client.add("Uses Postgres")
        self.assertIn("results", res)
        mock_mem.add.assert_called_once()
        call_kwargs = mock_mem.add.call_args[1]
        self.assertEqual(call_kwargs["run_id"], "test_scope")
        self.assertEqual(call_kwargs["agent_id"], "test_agent")

    @patch("mem0_client.Mem0Client._get_memory_client")
    def test_search_memory_filters_superseded(self, mock_get_client):
        mock_mem = MagicMock()
        mock_mem.search.return_value = {
            "results": [
                {"id": "m1", "memory": "Active Postgres note", "status": "active"},
                {"id": "m2", "memory": "Old SQLite note", "status": "superseded"},
            ]
        }
        mock_get_client.return_value = mock_mem

        hits = self.client.search("database", include_superseded=False)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["id"], "m1")

        all_hits = self.client.search("database", include_superseded=True)
        self.assertEqual(len(all_hits), 2)

    @patch("harness.assemble_context")
    def test_get_system_prompt(self, mock_assemble):
        mock_assemble.return_value = {
            "task": "Build feature",
            "scope": "test_scope",
            "memories": "- Always use UUID v4 for primary keys.",
            "rules": "## Rules\n- No direct SQL queries.",
            "skills": "## Skills\n- git-worktree-sandbox",
            "graph": [{"id": "tech:postgres", "label": "PostgreSQL"}],
        }

        prompt = self.client.get_system_prompt(task="Build feature")
        self.assertIn("# [SHARED BRAIN] Project Context: test_scope", prompt)
        self.assertIn("No direct SQL queries.", prompt)
        self.assertIn("UUID v4", prompt)
        self.assertIn("git-worktree-sandbox", prompt)
        self.assertIn("tech:postgres", prompt)

    @patch("harness.run_harness_task")
    def test_run_harness(self, mock_harness):
        mock_harness.return_value = {"status": "completed", "verified": True}
        res = self.client.run_harness("Run unit tests", verify_command="pytest")
        self.assertEqual(res["status"], "completed")
        self.assertTrue(res["verified"])


if __name__ == "__main__":
    unittest.main()
