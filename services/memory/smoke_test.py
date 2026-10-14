#!/usr/bin/env python
"""
End-to-end smoke test: proves the fully-local mem0 stack works.
Adds a couple of facts, then searches for them.

Two things this learned the hard way:

* It asserts. The previous version printed "OK — 0 memories" and exited 0, so it
  reported a healthy stack while extracting nothing. A check that cannot fail is
  not a check.
* Each run uses a fresh user_id. The old fixed "smoke_test_user" accumulated
  hundreds of add/delete cycles, and in that state mem0 stopped extracting
  anything from the very same input a clean id handles fine — so the test failed
  for a reason that had nothing to do with the stack's health.
"""

import sys
import uuid

from config import get_memory

QUERY = "what food does the user eat?"


def main() -> int:
    print("init Memory (Ollama + local Qdrant)...")
    mem = get_memory()

    user = "smoke_test_" + uuid.uuid4().hex[:12]
    print(f"run as {user} (fresh id — no state carried between runs)")

    print("add memories...")
    convo = [
        {"role": "user", "content": "Hi, I'm Ada. I love sci-fi movies and I'm vegetarian."},
        {"role": "assistant", "content": "Nice to meet you Ada!"},
    ]
    try:
        res = mem.add(convo, user_id=user)
        stored = res.get("results", [])
        for item in stored:
            print(f"   stored: {item.get('memory','')}")

        print(f"search {QUERY!r}...")
        hits = mem.search(QUERY, filters={"user_id": user}, top_k=3).get("results", [])
        for h in hits:
            print(f"   -> {h.get('memory','')}")

        problems = []
        if not stored:
            problems.append("extraction returned no facts (LLM reachable but unproductive?)")
        if not hits:
            problems.append("search returned nothing (embedder or vector store?)")
    finally:
        # Never leave the store dirty, even when the assertions below fail.
        try:
            mem.delete_all(user_id=user)
        except Exception as ex:
            print(f"   cleanup failed for {user}: {ex}")

    if problems:
        print("\nFAIL — " + "; ".join(problems))
        return 1
    print(f"\nOK — {len(stored)} facts stored, {len(hits)} found by search. "
          "Local mem0 works.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
