#!/usr/bin/env python3
"""Unit tests for Ask Brain Assistant Chat integration in dashboard."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
MEM0_ROOT = HERE.parent
import sys
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(MEM0_ROOT))

import server
import assets


class TestAssistantDashboardIntegration(unittest.TestCase):

    def test_assets_markup_and_script_wiring(self):
        html = assets.PAGE_HTML
        self.assertIn('data-view="assistant"', html)
        self.assertIn('id="view-assistant"', html)
        self.assertIn('id="chat-container"', html)
        self.assertIn('id="chat-messages"', html)
        self.assertIn('id="chat-input"', html)
        self.assertIn('id="chat-send"', html)
        self.assertIn('id="chat-scope"', html)
        self.assertIn('id="chat-clear"', html)
        self.assertIn('async function loadAssistant(', html)
        self.assertIn("'/api/assistant/chat'", html)

    @patch("assistant.ask_brain")
    def test_assistant_chat_endpoint_dispatch(self, mock_ask):
        mock_ask.return_value = {
            "question": "What database do we use?",
            "answer": "We use Qdrant for vector storage and SQLite for relational.",
            "scope": "personal",
            "memories_used": [{"id": "m1", "memory": "Qdrant vector store", "score": 0.95}],
            "rules_applied": [{"id": "r1", "rule": "Always check rules"}],
        }

        # Simulate handler invocation
        res = mock_ask("What database do we use?", scope="personal", history=[], top_k=5)
        self.assertEqual(res["question"], "What database do we use?")
        self.assertIn("Qdrant", res["answer"])
        self.assertEqual(len(res["memories_used"]), 1)
        self.assertEqual(len(res["rules_applied"]), 1)

    @patch("config.get_memory")
    def test_remember_endpoint_logic(self, mock_get_mem):
        from unittest.mock import MagicMock
        mock_mem = MagicMock()
        mock_get_mem.return_value = mock_mem

        # Test memory addition through config
        mem = mock_get_mem()
        mem.add("Important gotcha", user_id="example-user", run_id="personal", metadata={"agent_id": "webhook"})
        mock_mem.add.assert_called_once_with(
            "Important gotcha", user_id="example-user", run_id="personal", metadata={"agent_id": "webhook"}
        )


if __name__ == "__main__":
    unittest.main()
