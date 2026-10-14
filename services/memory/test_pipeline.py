#!/usr/bin/env python3
"""Unit tests for pipeline.py (Central Brain Dream Daemon & Maintenance Pipeline)."""

import unittest
from unittest.mock import MagicMock, patch

import pipeline


class TestPipeline(unittest.TestCase):

    @patch("qdrant_indexes.ensure_indexes")
    def test_stage_indexes(self, mock_indexes):
        mock_indexes.return_value = True
        res = pipeline.stage_indexes()
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["indexes_ready"])

    @patch("reflexion_engine.run_reflexion")
    def test_stage_reflexion(self, mock_re):
        mock_re.return_value = ["[GOTCHA] Always mock external APIs."]
        res = pipeline.stage_reflexion(dry_run=True)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["gotchas_created"], 1)

    @patch("mem._scroll")
    @patch("graph_store.sync_memory_links")
    def test_stage_graph_sync(self, mock_sync, mock_scroll):
        mock_scroll.return_value = [
            {"id": "m1", "memory": "Using PostgreSQL DB.", "run_id": "personal", "status": "active"},
            {"id": "m2", "memory": "Qdrant vector storage.", "run_id": "personal", "status": "active"},
        ]
        res = pipeline.stage_graph_sync()
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["nodes_synced"], 2)
        self.assertEqual(res["errors"], 0)

    @patch("compaction.compact_scope")
    def test_stage_compaction(self, mock_compact):
        mock_compact.return_value = {"clusters_found": 2, "memories_compacted": 5}
        res = pipeline.stage_compaction(scope="personal", dry_run=True)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["result"]["clusters_found"], 2)

    @patch("compaction.sweep_stale_memories")
    def test_stage_staleness_sweep(self, mock_sweep):
        mock_sweep.return_value = {"stale_found": 1, "stale_memories": [{"id": "s1"}]}
        res = pipeline.stage_staleness_sweep(scope="personal", dry_run=True)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["result"]["stale_found"], 1)

    @patch("mem._scroll")
    def test_stage_health_audit(self, mock_scroll):
        mock_scroll.side_effect = [
            [{"id": "1"}, {"id": "2"}],            # active
            [{"id": "1"}, {"id": "2"}, {"id": "3"}] # all
        ]
        res = pipeline.stage_health_audit(scope="personal")
        self.assertEqual(res["status"], "ok")
        metrics = res["metrics"]
        self.assertEqual(metrics["total_memories"], 3)
        self.assertEqual(metrics["active_memories"], 2)
        self.assertEqual(metrics["superseded_memories"], 1)
        self.assertGreaterEqual(metrics["health_score"], 50)

    @patch("pipeline.stage_indexes")
    @patch("pipeline.stage_reflexion")
    @patch("pipeline.stage_graph_sync")
    @patch("pipeline.stage_compaction")
    @patch("pipeline.stage_staleness_sweep")
    @patch("pipeline.stage_health_audit")
    @patch("pipeline.stage_obsidian_sync")
    def test_run_pipeline_all_stages(self, m_obs, m_health, m_sweep, m_compact, m_graph, m_ref, m_idx):
        m_idx.return_value = {"status": "ok"}
        m_ref.return_value = {"status": "ok"}
        m_graph.return_value = {"status": "ok"}
        m_compact.return_value = {"status": "ok"}
        m_sweep.return_value = {"status": "ok"}
        m_health.return_value = {"status": "ok"}
        m_obs.return_value = {"status": "ok"}

        report = pipeline.run_pipeline(scope="test_proj", dry_run=False)
        self.assertEqual(report["scope"], "test_proj")
        self.assertFalse(report["dry_run"])
        self.assertIn("indexes", report["stages"])
        self.assertIn("reflexion", report["stages"])
        self.assertIn("graph", report["stages"])
        self.assertIn("compact", report["stages"])
        self.assertIn("sweep", report["stages"])
        self.assertIn("health", report["stages"])
        self.assertIn("obsidian", report["stages"])
        self.assertIn("duration_seconds", report)


if __name__ == "__main__":
    unittest.main()
