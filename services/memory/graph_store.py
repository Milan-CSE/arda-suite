#!/usr/bin/env python3
"""
Embedded Knowledge Graph (GraphRAG & Zettelkasten) for mem0-local.
Stores memories, entities, and bidirectional relations in local SQLite (~/.mem0/graph.db).
Stdlib-first: zero external daemons, microseconds latency, WAL-enabled.
"""

import collections
import json
import os
import re
import sqlite3
import time

DEFAULT_GRAPH_DB = os.path.expanduser("~/.mem0/graph.db")

# Simple entity extractor for common tech terms, patterns, backticked code
_ENTITY_RE = re.compile(
    r"`([^`]+)`"  # `PostgreSQL`
    r"|\b([A-Z][a-zA-Z0-9_]+(?:Service|API|DB|Store|Engine|Client|Hook|Controller|Router)?)\b"  # PascalCase
    r"|(?i:\b(postgres|postgresql|sqlite|redis|mongodb|qdrant|ollama|fastapi|express|nextjs|react|vue|docker|tailwind|prisma|graphql|jwt|auth|oauth)\b)"
)


def get_db_path():
    return os.environ.get("MEM0_GRAPH_DB", DEFAULT_GRAPH_DB)


def init_db(db_path=None):
    path = db_path or get_db_path()
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10.0)
    except (sqlite3.OperationalError, OSError):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".graph.db")
        os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                label TEXT NOT NULL,
                properties TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS edges (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                properties TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                PRIMARY KEY (source_id, target_id, relation),
                FOREIGN KEY (source_id) REFERENCES nodes(id) ON DELETE CASCADE,
                FOREIGN KEY (target_id) REFERENCES nodes(id) ON DELETE CASCADE
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);")
    return conn


def add_node(node_id: str, kind: str = "memory", label: str = "", properties: dict = None, db_path=None) -> dict:
    if not node_id:
        raise ValueError("node_id cannot be empty")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    label = label or node_id
    props_json = json.dumps(properties or {}, ensure_ascii=False)
    conn = init_db(db_path)
    try:
        with conn:
            conn.execute("""
                INSERT INTO nodes (id, kind, label, properties, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind = excluded.kind,
                    label = excluded.label,
                    properties = excluded.properties,
                    updated_at = excluded.updated_at;
            """, (str(node_id), kind, label, props_json, now))
    finally:
        conn.close()
    return {"id": str(node_id), "kind": kind, "label": label, "properties": properties or {}, "updated_at": now}


def ensure_node(node_id: str, kind: str = "unknown", label: str = "", db_path=None):
    if not node_id:
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    label = label or str(node_id)
    conn = init_db(db_path)
    try:
        with conn:
            conn.execute("""
                INSERT OR IGNORE INTO nodes (id, kind, label, properties, updated_at)
                VALUES (?, ?, ?, '{}', ?);
            """, (str(node_id), kind, label, now))
    finally:
        conn.close()


def add_edge(
    source_id: str,
    target_id: str,
    relation: str = "relates_to",
    weight: float = 1.0,
    properties: dict = None,
    db_path=None,
) -> dict:
    if not source_id or not target_id:
        raise ValueError("source_id and target_id cannot be empty")
    # Ensure both nodes exist without overwriting if already present
    ensure_node(source_id, kind="unknown", label=str(source_id), db_path=db_path)
    ensure_node(target_id, kind="unknown", label=str(target_id), db_path=db_path)

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    props_json = json.dumps(properties or {}, ensure_ascii=False)
    conn = init_db(db_path)
    try:
        with conn:
            conn.execute("""
                INSERT INTO edges (source_id, target_id, relation, weight, properties, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, target_id, relation) DO UPDATE SET
                    weight = excluded.weight,
                    properties = excluded.properties,
                    created_at = excluded.created_at;
            """, (str(source_id), str(target_id), relation, weight, props_json, now))
    finally:
        conn.close()
    return {
        "source_id": str(source_id),
        "target_id": str(target_id),
        "relation": relation,
        "weight": weight,
        "properties": properties or {},
        "created_at": now,
    }


def get_node(node_id: str, db_path=None) -> dict | None:
    conn = init_db(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, kind, label, properties, updated_at FROM nodes WHERE id = ?", (str(node_id),))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "kind": row[1],
            "label": row[2],
            "properties": json.loads(row[3]),
            "updated_at": row[4],
        }
    finally:
        conn.close()


def get_neighbors(node_id: str, direction: str = "both", relation: str = None, db_path=None) -> list[dict]:
    """Get immediate neighbors of node_id."""
    conn = init_db(db_path)
    try:
        cur = conn.cursor()
        query_parts = []
        params = []

        if direction in ("out", "both"):
            q_out = """
                SELECT e.target_id, n.kind, n.label, e.relation, e.weight, 'outgoing' as direction
                FROM edges e
                JOIN nodes n ON e.target_id = n.id
                WHERE e.source_id = ?
            """
            params.append(str(node_id))
            if relation:
                q_out += " AND e.relation = ?"
                params.append(relation)
            query_parts.append(q_out)

        if direction in ("in", "both"):
            q_in = """
                SELECT e.source_id, n.kind, n.label, e.relation, e.weight, 'incoming' as direction
                FROM edges e
                JOIN nodes n ON e.source_id = n.id
                WHERE e.target_id = ?
            """
            params.append(str(node_id))
            if relation:
                q_in += " AND e.relation = ?"
                params.append(relation)
            query_parts.append(q_in)

        full_query = " UNION ALL ".join(query_parts)
        cur.execute(full_query, params)
        rows = cur.fetchall()
        return [
            {
                "neighbor_id": r[0],
                "kind": r[1],
                "label": r[2],
                "relation": r[3],
                "weight": r[4],
                "direction": r[5],
            }
            for r in rows
        ]
    finally:
        conn.close()


def find_path(start_id: str, end_id: str, max_depth: int = 3, db_path=None) -> list[str] | None:
    """BFS shortest path between start_id and end_id."""
    if start_id == end_id:
        return [start_id]

    visited = {str(start_id)}
    queue = collections.deque([[str(start_id)]])

    while queue:
        path = queue.popleft()
        if len(path) > max_depth + 1:
            continue
        curr = path[-1]
        for neighbor in get_neighbors(curr, direction="both", db_path=db_path):
            nid = neighbor["neighbor_id"]
            if nid == end_id:
                return path + [nid]
            if nid not in visited:
                visited.add(nid)
                queue.append(path + [nid])
    return None


def multi_hop_query(start_ids: list[str], max_hops: int = 2, db_path=None) -> list[dict]:
    """Expands from start_ids up to max_hops away, returning discovered memories and entities."""
    visited = set(str(s) for s in start_ids)
    frontier = set(str(s) for s in start_ids)
    discovered = []

    for hop in range(1, max_hops + 1):
        next_frontier = set()
        for nid in frontier:
            for neighbor in get_neighbors(nid, direction="both", db_path=db_path):
                nbr_id = neighbor["neighbor_id"]
                if nbr_id not in visited:
                    visited.add(nbr_id)
                    next_frontier.add(nbr_id)
                    discovered.append({
                        "node_id": nbr_id,
                        "kind": neighbor["kind"],
                        "label": neighbor["label"],
                        "reached_via": nid,
                        "relation": neighbor["relation"],
                        "hop": hop,
                    })
        frontier = next_frontier
        if not frontier:
            break
    return discovered


def extract_entities(text: str) -> list[str]:
    """Finds named tech entities and code anchors in text."""
    if not text:
        return []
    found = set()
    for m in _ENTITY_RE.finditer(text):
        for g in m.groups():
            if g and len(g.strip()) > 1:
                found.add(g.strip().lower())
    return sorted(found)


def sync_memory_links(memory_id: str, text: str, metadata: dict = None, scope: str = "personal", db_path=None):
    """Automatically parses and connects memory into the knowledge graph."""
    if not memory_id:
        return
    meta = metadata or {}
    status = meta.get("status") or "active"

    # Register memory node
    add_node(
        node_id=memory_id,
        kind="memory",
        label=text[:60] if text else memory_id,
        properties={"text": text, "scope": scope, "status": status, **meta},
        db_path=db_path,
    )

    # Register scope node and link
    if scope:
        add_node(node_id=f"scope:{scope}", kind="scope", label=scope, db_path=db_path)
        add_edge(memory_id, f"scope:{scope}", relation="in_scope", db_path=db_path)

    # Link bi-temporal supersedes
    if meta.get("supersedes"):
        old_id = str(meta["supersedes"])
        add_edge(memory_id, old_id, relation="supersedes", db_path=db_path)
        add_edge(old_id, memory_id, relation="superseded_by", db_path=db_path)

    # Link entities
    entities = extract_entities(text)
    for ent in entities:
        ent_id = f"entity:{ent}"
        add_node(node_id=ent_id, kind="entity", label=ent, db_path=db_path)
        add_edge(memory_id, ent_id, relation="mentions", db_path=db_path)


def export_subgraph(start_id: str, max_hops: int = 2, db_path=None) -> dict:
    """Returns nodes and edges suitable for D3 / Cytoscape dashboard graphs."""
    nodes = {}
    edges = []
    seen_edges = set()

    root = get_node(start_id, db_path=db_path)
    if not root:
        return {"nodes": [], "edges": []}
    nodes[root["id"]] = root

    visited = {str(start_id)}
    frontier = {str(start_id)}

    for _ in range(max_hops):
        next_frontier = set()
        for nid in frontier:
            for nbr in get_neighbors(nid, direction="both", db_path=db_path):
                tid = nbr["neighbor_id"]
                edge_key = tuple(sorted([nid, tid])) + (nbr["relation"],)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({
                        "source": nid if nbr["direction"] == "outgoing" else tid,
                        "target": tid if nbr["direction"] == "outgoing" else nid,
                        "relation": nbr["relation"],
                        "weight": nbr["weight"],
                    })
                if tid not in visited:
                    visited.add(tid)
                    next_frontier.add(tid)
                    node_data = get_node(tid, db_path=db_path)
                    if node_data:
                        nodes[tid] = node_data
        frontier = next_frontier
        if not frontier:
            break

    return {"nodes": list(nodes.values()), "edges": edges}


def get_centrality_nodes(
    top_k: int = 10,
    kind: str = None,
    scope: str = None,
    db_path=None,
) -> list[dict]:
    """Calculates Degree Centrality across the knowledge graph.
    Identifies high-connectivity architectural hubs (in-degree, out-degree, total degree).
    
    Returns a list of dicts with:
      - id: node identifier
      - kind: node type (entity, memory, scope, etc.)
      - label: human-readable label
      - in_degree: number of incoming connections
      - out_degree: number of outgoing connections
      - total_degree: in_degree + out_degree
      - properties: parsed node properties dict
    """
    conn = init_db(db_path)
    try:
        cur = conn.cursor()
        where_clauses = []
        params = []

        if kind:
            where_clauses.append("n.kind = ?")
            params.append(kind)

        if scope:
            where_clauses.append("(n.properties LIKE ? OR n.id IN (SELECT source_id FROM edges WHERE target_id = ?))")
            params.append(f'%"scope": "{scope}"%')
            params.append(f"scope:{scope}")

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        query = f"""
            WITH edge_counts AS (
                SELECT source_id AS node_id, 1 AS is_out, 0 AS is_in FROM edges
                UNION ALL
                SELECT target_id AS node_id, 0 AS is_out, 1 AS is_in FROM edges
            )
            SELECT n.id, n.kind, n.label, n.properties,
                   COALESCE(SUM(c.is_out), 0) AS out_degree,
                   COALESCE(SUM(c.is_in), 0) AS in_degree,
                   COALESCE(COUNT(c.node_id), 0) AS total_degree
            FROM nodes n
            LEFT JOIN edge_counts c ON n.id = c.node_id
            {where_sql}
            GROUP BY n.id, n.kind, n.label, n.properties
            ORDER BY total_degree DESC, n.id ASC
            LIMIT ?
        """
        params.append(top_k)
        cur.execute(query, params)
        rows = cur.fetchall()

        results = []
        for r in rows:
            props = {}
            try:
                props = json.loads(r[3]) if r[3] else {}
            except Exception:
                pass
            results.append({
                "id": r[0],
                "kind": r[1],
                "label": r[2],
                "properties": props,
                "out_degree": int(r[4]),
                "in_degree": int(r[5]),
                "total_degree": int(r[6]),
            })
        return results
    finally:
        conn.close()
