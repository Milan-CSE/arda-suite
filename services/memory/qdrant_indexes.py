#!/usr/bin/env python3
"""Ensure payload indexes exist in Qdrant for hardware-accelerated filtering.

Recommended by Qdrant for payload-based multitenancy and fast scoped queries:
- user_id: tenant / user isolation
- run_id: project scope isolation
- agent_id: agent attribution filtering
- status: active vs superseded filtering

Idempotent: safe to run multiple times.
"""

import os
import sys

from qdrant_client import QdrantClient
from qdrant_client.http import models

HOST = os.environ.get("MEM0_QDRANT_HOST", "localhost")
PORT = int(os.environ.get("MEM0_QDRANT_PORT", "6333"))
COLLECTION = "mem0_local"

FIELDS = [
    ("user_id", models.PayloadSchemaType.KEYWORD),
    ("run_id", models.PayloadSchemaType.KEYWORD),
    ("agent_id", models.PayloadSchemaType.KEYWORD),
    ("status", models.PayloadSchemaType.KEYWORD),
]


def ensure_indexes(host: str = HOST, port: int = PORT, collection: str = COLLECTION) -> bool:
    client = QdrantClient(host=host, port=port, timeout=10)
    try:
        collections = [c.name for c in client.get_collections().collections]
        if collection not in collections:
            print(f"Collection '{collection}' does not exist yet. Run mem0 once to initialize.")
            return False

        for field_name, schema_type in FIELDS:
            try:
                client.create_payload_index(
                    collection_name=collection,
                    field_name=field_name,
                    field_schema=schema_type,
                )
                print(f"Index created for '{field_name}' ({schema_type})")
            except Exception as e:
                # Qdrant raises if index already exists or on conflict
                print(f"Index for '{field_name}' already exists or skipped: {e}")
        return True
    except Exception as ex:
        print(f"Failed to connect or ensure indexes: {ex}")
        return False


if __name__ == "__main__":
    success = ensure_indexes()
    sys.exit(0 if success else 1)
