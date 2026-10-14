#!/usr/bin/env python3
"""Unit tests for assistant.py."""

import unittest
from unittest.mock import MagicMock, patch

import assistant


class TestAssistant(unittest.TestCase):

    @patch("rules.list_rules")
    @patch("mcp_server._mem")
    @patch("mcp_server._merge_hits")
    def test_recall_context(self, mock_merge, mock_mem, mock_rules):
        mock_mem.return_value.search.return_value = {"results": []}
        mock_merge.return_value = [
            {"id": "m1", "memory": "Uses Qdrant vector store", "run_id": "personal", "score": 0.9}
        ]
        mock_rules.return_value = [{"id": "r1", "rule": "Always await services"}]

        ctx = assistant.recall_context(query="Which database?", scope="personal")
        self.assertEqual(len(ctx["memories"]), 1)
        self.assertEqual(ctx["memories"][0]["id"], "m1")
        self.assertEqual(len(ctx["rules"]), 1)

    @patch("assistant.recall_context")
    @patch("assistant.query_llm")
    def test_ask_brain(self, mock_llm, mock_ctx):
        mock_ctx.return_value = {
            "memories": [{"id": "1", "scope": "personal", "memory": "Qdrant on port 6333"}],
            "rules": [{"id": "r1", "rule": "Never leak tokens"}],
        }
        mock_llm.return_value = "Qdrant runs on port 6333."

        res = assistant.ask_brain("What is Qdrant port?", scope="personal")
        self.assertEqual(res["answer"], "Qdrant runs on port 6333.")
        self.assertEqual(len(res["memories_used"]), 1)
        self.assertEqual(len(res["rules_applied"]), 1)
        self.assertIn("timestamp", res)


if __name__ == "__main__":
    unittest.main()
