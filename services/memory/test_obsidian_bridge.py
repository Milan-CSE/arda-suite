#!/usr/bin/env python3
"""Unit tests for obsidian_bridge.py."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import obsidian_bridge as ob


class TestObsidianBridge(unittest.TestCase):

    def test_sanitize_filename(self):
        clean = ob.sanitize_filename("hello/world:test?file*name")
        self.assertEqual(clean, "hello_world_test_file_name")

    def test_format_memory_note(self):
        item = {
            "id": "abc12345",
            "memory": "Bound to 8812 port using `mcp_transport`.",
            "run_id": "projX",
            "agent_id": "claude",
            "status": "active",
            "created_at": "2026-09-24T12:00:00",
        }
        content = ob.format_memory_note(item, entity_links=["Qdrant", "FastAPI"])
        self.assertIn('id: "abc12345"', content)
        self.assertIn('scope: "projX"', content)
        self.assertIn("- scope/projX", content)
        self.assertIn("- [[Qdrant]]", content)
        self.assertIn("- [[FastAPI]]", content)
        self.assertIn("- [[mcp_transport]]", content)
        self.assertIn("- **Scope:** `[[projX]]`", content)

    def test_format_rule_note(self):
        rule = {
            "id": "await-services",
            "scope": "sample-app",
            "rule": "Always await service calls.",
            "paths": ["src/services/**"],
            "severity": "high",
            "created_at": "2026-09-24T10:00:00",
        }
        content = ob.format_rule_note(rule)
        self.assertIn('rule_id: "await-services"', content)
        self.assertIn('severity: "high"', content)
        self.assertIn("- `src/services/**`", content)
        self.assertIn("Always await service calls.", content)

    def test_format_index_note(self):
        content = ob.format_index_note(["projA", "personal"], {"active": 10, "superseded": 2, "rules": 3})
        self.assertIn("# 🧠 Central Brain — Obsidian Knowledge Vault", content)
        self.assertIn("- **Active Memories:** `10`", content)
        self.assertIn("- [[projA]]", content)
        self.assertIn("- [[personal]]", content)

    @patch("mem._scroll")
    @patch("rules.list_rules")
    def test_export_vault(self, mock_rules, mock_scroll):
        mock_scroll.return_value = [
            {"id": "m1", "memory": "Active note", "run_id": "projA", "status": "active"},
            {"id": "m2", "memory": "Old note", "run_id": "projA", "status": "superseded"},
        ]
        mock_rules.return_value = [
            {"id": "r1", "scope": "projA", "rule": "Rule 1", "paths": ["*.py"], "severity": "normal"}
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            res = ob.export_vault(vault_dir=tmp_dir)
            self.assertEqual(res["active_memories"], 1)
            self.assertEqual(res["archived_memories"], 1)
            self.assertEqual(res["rules_exported"], 1)

            root = Path(tmp_dir)
            self.assertTrue((root / "Index.md").exists())
            self.assertTrue((root / "Rules" / "rule_r1.md").exists())
            self.assertTrue(any((root / "Memories" / "projA").glob("*.md")))
            self.assertTrue(any((root / "Archive").glob("*.md")))

    @patch("config.get_memory")
    def test_import_inbox(self, mock_get_memory):
        mock_mem = MagicMock()
        mock_mem.add.return_value = {"results": [{"event": "ADD", "memory": "Use Redis for caching."}]}
        mock_get_memory.return_value = mock_mem

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            inbox = root / "Inbox"
            inbox.mkdir()
            note_file = inbox / "caching_decision.md"
            note_file.write_text("---\nscope: backend\n---\n# Caching Rule\nUse Redis for caching.", encoding="utf-8")

            res = ob.import_inbox(vault_dir=tmp_dir)
            self.assertEqual(res["imported"], 1)
            self.assertEqual(res["notes"][0]["scope"], "backend")
            mock_mem.add.assert_called_once()
            args, kwargs = mock_mem.add.call_args
            self.assertIn("Use Redis for caching.", args[0])
            self.assertEqual(kwargs["run_id"], "backend")
            self.assertFalse(note_file.exists())  # moved out of Inbox
            self.assertTrue((root / "Memories" / "backend" / "user_caching_decision.md").exists())

    def test_get_vault_status(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "Index.md").write_text("# Index", encoding="utf-8")
            (root / "note.md").write_text("# Note", encoding="utf-8")

            st = ob.get_vault_status(vault_dir=tmp_dir)
            self.assertTrue(st["exists"])
            self.assertEqual(st["total_markdown_notes"], 2)
            self.assertTrue(st["has_index"])


if __name__ == "__main__":
    unittest.main()
