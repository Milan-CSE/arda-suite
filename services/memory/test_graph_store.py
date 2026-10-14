"""Unit tests for graph_store.py (Embedded Knowledge Graph & GraphRAG)."""

import os
import tempfile
import unittest
import graph_store as gs


class TestGraphStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "graph.db")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_node_and_edge_creation(self):
        n1 = gs.add_node("mem:1", kind="memory", label="Uses PostgreSQL", db_path=self.db_path)
        self.assertEqual(n1["id"], "mem:1")
        self.assertEqual(n1["kind"], "memory")

        n2 = gs.add_node("mem:2", kind="memory", label="Uses Prisma ORM", db_path=self.db_path)
        e = gs.add_edge("mem:1", "mem:2", relation="depends_on", db_path=self.db_path)
        self.assertEqual(e["source_id"], "mem:1")
        self.assertEqual(e["target_id"], "mem:2")
        self.assertEqual(e["relation"], "depends_on")

        # Query node
        fetched = gs.get_node("mem:1", db_path=self.db_path)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["label"], "Uses PostgreSQL")

    def test_neighbors_query(self):
        gs.add_edge("mem:auth", "tech:jwt", relation="uses", db_path=self.db_path)
        gs.add_edge("mem:auth", "tech:fastapi", relation="built_with", db_path=self.db_path)
        gs.add_edge("mem:client", "mem:auth", relation="calls", db_path=self.db_path)

        # outgoing
        out = gs.get_neighbors("mem:auth", direction="out", db_path=self.db_path)
        self.assertEqual(len(out), 2)
        out_ids = {o["neighbor_id"] for o in out}
        self.assertEqual(out_ids, {"tech:jwt", "tech:fastapi"})

        # incoming
        inc = gs.get_neighbors("mem:auth", direction="in", db_path=self.db_path)
        self.assertEqual(len(inc), 1)
        self.assertEqual(inc[0]["neighbor_id"], "mem:client")

        # both
        both = gs.get_neighbors("mem:auth", direction="both", db_path=self.db_path)
        self.assertEqual(len(both), 3)

    def test_find_path(self):
        # A -> B -> C -> D
        gs.add_edge("A", "B", relation="to", db_path=self.db_path)
        gs.add_edge("B", "C", relation="to", db_path=self.db_path)
        gs.add_edge("C", "D", relation="to", db_path=self.db_path)

        path = gs.find_path("A", "D", max_depth=4, db_path=self.db_path)
        self.assertEqual(path, ["A", "B", "C", "D"])

        # No path to isolated node
        gs.add_node("Z", db_path=self.db_path)
        self.assertIsNone(gs.find_path("A", "Z", max_depth=3, db_path=self.db_path))

    def test_multi_hop_query(self):
        # Center -> Nbr1 -> Nbr2
        gs.add_edge("C", "N1", relation="relates", db_path=self.db_path)
        gs.add_edge("N1", "N2", relation="relates", db_path=self.db_path)
        gs.add_edge("N2", "N3", relation="relates", db_path=self.db_path)

        # 2 hops from C: reaches N1 (hop 1) and N2 (hop 2), but not N3 (hop 3)
        discovered = gs.multi_hop_query(["C"], max_hops=2, db_path=self.db_path)
        found_ids = {d["node_id"] for d in discovered}
        self.assertIn("N1", found_ids)
        self.assertIn("N2", found_ids)
        self.assertNotIn("N3", found_ids)

    def test_sync_memory_links(self):
        text = "Uses `PostgreSQL` and Prisma for database schema in auth service"
        gs.sync_memory_links("mem:100", text, metadata={"status": "active"}, scope="solveny", db_path=self.db_path)

        # Node created
        node = gs.get_node("mem:100", db_path=self.db_path)
        self.assertIsNotNone(node)
        self.assertEqual(node["properties"]["scope"], "solveny")

        # Scope linked
        neighbors = gs.get_neighbors("mem:100", direction="out", db_path=self.db_path)
        nbr_ids = {n["neighbor_id"] for n in neighbors}
        self.assertIn("scope:solveny", nbr_ids)
        self.assertIn("entity:postgresql", nbr_ids)
        self.assertIn("entity:prisma", nbr_ids)

    def test_export_subgraph(self):
        gs.add_edge("root", "child1", relation="parent_of", db_path=self.db_path)
        gs.add_edge("root", "child2", relation="parent_of", db_path=self.db_path)

        graph = gs.export_subgraph("root", max_hops=1, db_path=self.db_path)
        self.assertEqual(len(graph["nodes"]), 3)
        self.assertEqual(len(graph["edges"]), 2)

    def test_centrality_nodes(self):
        # Hub node connected to multiple entities
        gs.add_edge("mem:1", "tech:postgres", relation="uses", db_path=self.db_path)
        gs.add_edge("mem:2", "tech:postgres", relation="uses", db_path=self.db_path)
        gs.add_edge("mem:3", "tech:postgres", relation="uses", db_path=self.db_path)
        gs.add_edge("mem:1", "tech:fastapi", relation="uses", db_path=self.db_path)

        centrality = gs.get_centrality_nodes(top_k=5, db_path=self.db_path)
        self.assertTrue(len(centrality) >= 2)
        # tech:postgres has 3 incoming edges -> total_degree = 3
        top_node = centrality[0]
        self.assertEqual(top_node["id"], "tech:postgres")
        self.assertEqual(top_node["in_degree"], 3)
        self.assertEqual(top_node["out_degree"], 0)
        self.assertEqual(top_node["total_degree"], 3)


if __name__ == "__main__":
    unittest.main()
