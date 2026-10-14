#!/usr/bin/env python3
"""
Autonomous Maintenance & Consolidation Pipeline (Dream Daemon) for mem0-local.
Orchestrates:
  1. Hardware-accelerated Qdrant payload index verification
  2. Reflexion cycle processing (verified failure -> gotcha synthesis)
  3. Knowledge Graph synchronization & topological linking
  4. Memory compaction & hierarchical synthesis
  5. Staleness detection & garbage collection sweep
  6. Overall memory health assessment

Run interactively, via cron, or as a systemd timer on Raspberry Pi / Linux / macOS.
"""

import argparse
import datetime
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

MEM0_ROOT = os.path.dirname(os.path.abspath(__file__))
if MEM0_ROOT not in sys.path:
    sys.path.insert(0, MEM0_ROOT)

import config
import graph_store
import qdrant_indexes
import reflexion_engine
import compaction


def stage_indexes() -> Dict[str, Any]:
    """Stage 1: Ensure hardware payload indexes exist in Qdrant."""
    print("🔹 [Stage 1/6] Verifying Qdrant payload indexes...")
    try:
        ok = qdrant_indexes.ensure_indexes()
        return {"status": "ok" if ok else "warning", "indexes_ready": ok}
    except Exception as ex:
        print(f"  ⚠️ Warning: Qdrant indexes check skipped ({ex})")
        return {"status": "skipped", "error": str(ex)}


def stage_reflexion(dry_run: bool = False) -> Dict[str, Any]:
    """Stage 2: Process verified failure-fix cycles into durable gotchas."""
    print(f"🔹 [Stage 2/6] Running Reflexion Engine (dry_run={dry_run})...")
    try:
        lessons = reflexion_engine.run_reflexion(dry_run=dry_run)
        print(f"  Processed {len(lessons)} gotcha(s).")
        return {"status": "ok", "gotchas_created": len(lessons), "lessons": lessons}
    except Exception as ex:
        print(f"  ⚠️ Warning: Reflexion engine failed ({ex})")
        return {"status": "error", "error": str(ex)}


def stage_graph_sync() -> Dict[str, Any]:
    """Stage 3: Synchronize knowledge graph nodes, entities, and links from active memories."""
    print("🔹 [Stage 3/6] Synchronizing Knowledge Graph & Entities...")
    synced = 0
    errors = 0
    try:
        import mem as mem_cli
        # Scroll memories directly using mem_cli helper
        items = mem_cli._scroll(include_superseded=True)

        for item in items:
            mid = str(item.get("id") or "")
            text = item.get("memory") or ""
            scope = item.get("run_id") or "personal"
            status = item.get("status") or "active"

            if not mid or not text:
                continue

            try:
                graph_store.sync_memory_links(
                    memory_id=mid,
                    text=text,
                    metadata={"status": status, **item},
                    scope=scope,
                )
                synced += 1
            except Exception:
                errors += 1

        print(f"  Synchronized {synced} memory nodes into graph store ({errors} errors).")
        return {"status": "ok", "nodes_synced": synced, "errors": errors}
    except Exception as ex:
        print(f"  ⚠️ Warning: Graph sync skipped ({ex})")
        return {"status": "skipped", "error": str(ex)}


def stage_compaction(scope: str = "personal", dry_run: bool = False) -> Dict[str, Any]:
    """Stage 4: Consolidate fragmented memories in scope into synthesized notes."""
    print(f"🔹 [Stage 4/6] Running Memory Compaction for scope '{scope}' (dry_run={dry_run})...")
    try:
        res = compaction.compact_scope(scope=scope, dry_run=dry_run, min_cluster_size=2)
        clusters = res.get("clusters_found", 0)
        compacted = res.get("memories_compacted", 0)
        print(f"  Found {clusters} cluster(s), compacted {compacted} memory fragment(s).")
        return {"status": "ok", "result": res}
    except Exception as ex:
        print(f"  ⚠️ Warning: Compaction failed ({ex})")
        return {"status": "error", "error": str(ex)}


def stage_staleness_sweep(scope: str = "personal", workspace: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """Stage 5: Sweep stale memories referencing removed files."""
    print(f"🔹 [Stage 5/6] Auditing Staleness & Dead References (dry_run={dry_run})...")
    try:
        ws = workspace or os.getcwd()
        res = compaction.sweep_stale_memories(scope=scope, workspace_dir=ws, dry_run=dry_run)
        stale_count = res.get("stale_found", 0)
        print(f"  Detected {stale_count} stale memory item(s).")
        return {"status": "ok", "result": res}
    except Exception as ex:
        print(f"  ⚠️ Warning: Staleness sweep failed ({ex})")
        return {"status": "error", "error": str(ex)}


def stage_health_audit(scope: str = "personal", workspace: Optional[str] = None) -> Dict[str, Any]:
    """Stage 6: Generate final Central Brain health score and metrics."""
    print("🔹 [Stage 6/6] Computing Memory Health Metrics...")
    try:
        import mem as mem_cli
        # Calculate health metrics using mem_cli helper
        ws = workspace or os.getcwd()
        active_items = mem_cli._scroll(scope=scope, include_superseded=False)
        all_items = mem_cli._scroll(scope=scope, include_superseded=True)
        total = len(all_items)
        active = len(active_items)
        superseded = total - active

        # Health score: 100 base, penalize excessive staleness or zero compaction
        compaction_ratio = (superseded / total) if total > 0 else 0.0
        health_score = min(100, max(50, int(75 + (compaction_ratio * 25))))

        metrics = {
            "scope": scope,
            "total_memories": total,
            "active_memories": active,
            "superseded_memories": superseded,
            "compaction_ratio": round(compaction_ratio, 3),
            "health_score": health_score,
            "timestamp": datetime.datetime.now().isoformat(),
        }
        print(f"  Memory Health Score: {health_score}/100 (Total: {total}, Active: {active}, Superseded: {superseded})")
        return {"status": "ok", "metrics": metrics}
    except Exception as ex:
        print(f"  ⚠️ Warning: Health audit skipped ({ex})")
        return {"status": "skipped", "error": str(ex)}


def stage_obsidian_sync(vault_dir: Optional[str] = None) -> Dict[str, Any]:
    """Stage 7: Synchronize memories and knowledge graph into Obsidian Markdown Vault."""
    print("🔹 [Stage 7/7] Synchronizing Obsidian Knowledge Vault...")
    try:
        import obsidian_bridge
        res = obsidian_bridge.export_vault(vault_dir=vault_dir)
        print(f"  Obsidian Vault updated at '{res['vault_path']}' ({res['active_memories']} active notes).")
        return {"status": "ok", "result": res}
    except Exception as ex:
        print(f"  ⚠️ Warning: Obsidian sync skipped ({ex})")
        return {"status": "skipped", "error": str(ex)}


def run_pipeline(
    scope: str = "personal",
    workspace: Optional[str] = None,
    dry_run: bool = False,
    stages: Optional[List[str]] = None,
    vault_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Run all or selected stages of the Dream Daemon pipeline."""
    start_time = time.time()
    all_stages = ["indexes", "reflexion", "graph", "compact", "sweep", "health", "obsidian"]
    active_stages = stages or all_stages

    print(f"\n🧠 Starting Central Brain Maintenance Pipeline (scope='{scope}', dry_run={dry_run})")
    print("═" * 70)

    report: Dict[str, Any] = {
        "timestamp": datetime.datetime.now().isoformat(),
        "scope": scope,
        "dry_run": dry_run,
        "stages": {},
    }

    if "indexes" in active_stages:
        report["stages"]["indexes"] = stage_indexes()
    if "reflexion" in active_stages:
        report["stages"]["reflexion"] = stage_reflexion(dry_run=dry_run)
    if "graph" in active_stages:
        report["stages"]["graph"] = stage_graph_sync()
    if "compact" in active_stages:
        report["stages"]["compact"] = stage_compaction(scope=scope, dry_run=dry_run)
    if "sweep" in active_stages:
        report["stages"]["sweep"] = stage_staleness_sweep(scope=scope, workspace=workspace, dry_run=dry_run)
    if "health" in active_stages:
        report["stages"]["health"] = stage_health_audit(scope=scope, workspace=workspace)
    if "obsidian" in active_stages and not dry_run:
        report["stages"]["obsidian"] = stage_obsidian_sync(vault_dir=vault_dir)

    duration = round(time.time() - start_time, 2)
    report["duration_seconds"] = duration
    print("═" * 70)
    print(f"✅ Pipeline completed in {duration}s.\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Central Brain Maintenance & Consolidation Pipeline")
    parser.add_argument("--scope", default="personal", help="target scope (default: personal)")
    parser.add_argument("--workspace", default=None, help="workspace root path for file reference auditing")
    parser.add_argument("--dry-run", action="store_true", help="preview changes without modifying memory or disk")
    parser.add_argument("--json", action="store_true", help="output summary report as JSON")
    parser.add_argument(
        "--stages",
        nargs="+",
        choices=["indexes", "reflexion", "graph", "compact", "sweep", "health"],
        help="run only specific stages",
    )

    args = parser.parse_args()
    report = run_pipeline(
        scope=args.scope,
        workspace=args.workspace,
        dry_run=args.dry_run,
        stages=args.stages,
    )

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
