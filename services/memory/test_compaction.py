#!/usr/bin/env python3
"""Unit tests for compaction.py (Memory Compaction & Staleness Sweeper)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import compaction


class TestCompaction(unittest.TestCase):

    def test_extract_keywords(self):
        kw = compaction.extract_keywords("Always use PostgreSQL migrations with Prisma ORM in production.")
        self.assertIn("postgresql", kw)
        self.assertIn("migrations", kw)
        self.assertIn("prisma", kw)
        self.assertNotIn("with", kw)
        self.assertNotIn("in", kw)

    def test_cluster_memories(self):
        memories = [
            {"id": "m1", "memory": "PostgreSQL database migrations require Prisma schema locking.", "status": "active"},
            {"id": "m2", "memory": "Run Prisma migrations before starting PostgreSQL containers.", "status": "active"},
            {"id": "m3", "memory": "Frontend uses Tailwind CSS with dark mode toggle.", "status": "active"},
            {"id": "m4", "memory": "Superseded PostgreSQL tip", "status": "superseded"},
        ]
        clusters = compaction.cluster_memories(memories, min_cluster_size=2)
        self.assertEqual(len(clusters), 1)
        cluster_ids = [m["id"] for m in clusters[0]]
        self.assertIn("m1", cluster_ids)
        self.assertIn("m2", cluster_ids)
        self.assertNotIn("m3", cluster_ids)
        self.assertNotIn("m4", cluster_ids)

    @patch("graph_store.get_neighbors")
    def test_cluster_memories_graph_augmented(self, mock_neighbors):
        def fake_nbrs(node_id, direction="both"):
            if node_id in ("ga1", "ga2"):
                return [{"neighbor_id": "tech:qdrant"}, {"neighbor_id": "file:config.py"}]
            return []
        mock_neighbors.side_effect = fake_nbrs

        memories = [
            {"id": "ga1", "memory": "Vector dimension set to 768 cosine.", "status": "active"},
            {"id": "ga2", "memory": "Port mapped to 6333 localhost endpoint.", "status": "active"},
            {"id": "ga3", "memory": "Frontend button color is navy blue.", "status": "active"},
        ]
        clusters = compaction.cluster_memories(memories, min_cluster_size=2)
        self.assertEqual(len(clusters), 1)
        cluster_ids = [m["id"] for m in clusters[0]]
        self.assertIn("ga1", cluster_ids)
        self.assertIn("ga2", cluster_ids)
        self.assertNotIn("ga3", cluster_ids)

    def test_synthesize_cluster(self):
        cluster = [
            {"id": "m1", "memory": "Use PostgreSQL migrations."},
            {"id": "m2", "memory": "Always run Prisma schema validate."},
        ]
        summary = compaction.synthesize_cluster(cluster, scope="backend_service", use_llm=False)
        self.assertIn("Consolidated Architectural Synthesis (backend_service):", summary)
        self.assertIn("Use PostgreSQL migrations.", summary)
        self.assertIn("Always run Prisma schema validate.", summary)

    def test_compact_scope_dry_run(self):
        memories = [
            {"id": "1", "memory": "Docker container requires port 8080 mapping.", "status": "active"},
            {"id": "2", "memory": "Expose port 8080 in Docker compose for API service.", "status": "active"},
        ]
        res = compaction.compact_scope(scope="devops", dry_run=True, memories=memories)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["clusters_found"], 1)
        self.assertEqual(res["memories_compacted"], 2)
        self.assertNotIn("new_id", res["syntheses"][0])

    @patch("compaction.get_memory_client")
    def test_compact_scope_live(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.add.return_value = [{"id": "synth_123"}]
        mock_get_client.return_value = mock_client


        memories = [
            {"id": "1", "memory": "Docker container requires port 8080 mapping.", "status": "active"},
            {"id": "2", "memory": "Expose port 8080 in Docker compose for API service.", "status": "active"},
        ]

        res = compaction.compact_scope(scope="devops", dry_run=False, memories=memories)
        self.assertFalse(res["dry_run"])
        self.assertEqual(res["clusters_found"], 1)
        self.assertEqual(res["syntheses"][0]["new_id"], "synth_123")

        # Verify old memories were superseded
        self.assertEqual(mock_client.update.call_count, 2)
        mock_client.update.assert_any_call("1", metadata={"status": "superseded", "superseded_by": "synth_123"})
        mock_client.update.assert_any_call("2", metadata={"status": "superseded", "superseded_by": "synth_123"})

    def test_sweep_stale_memories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            existing_file = tmppath / "src" / "active_service.py"
            existing_file.parent.mkdir(parents=True)
            existing_file.write_text("# Active code")

            memories = [
                {"id": "m_valid", "memory": "Modify src/active_service.py to add auth handler.", "status": "active"},
                {"id": "m_stale", "memory": "Refactor legacy/deleted_handler.py before release.", "status": "active"},
            ]

            # Dry run
            sweep_dry = compaction.sweep_stale_memories(
                scope="proj",
                workspace_dir=tmpdir,
                dry_run=True,
                memories=memories,
            )
            self.assertEqual(sweep_dry["stale_count"], 1)
            self.assertEqual(sweep_dry["candidates"][0]["id"], "m_stale")
            self.assertIn("legacy/deleted_handler.py", sweep_dry["candidates"][0]["missing_files"])


if __name__ == "__main__":
    unittest.main()
