"""Long-running worker entrypoint for the Control Plane execution bridge.

The executor is deliberately supplied by an importable factory so the queue
and lease lifecycle can run independently of a provider-specific adapter.
"""
from __future__ import annotations

import argparse
import importlib
import os
import time
from pathlib import Path
from typing import Any

from arda.execution_bridge import ExecutionBridge
from arda.core import bootstrap_runtime_database, runtime_database_path
from arda.control_plane import ControlPlane
from arda.organization import OrganizationRegistry


def load_factory(spec: str) -> Any:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("executor factory must use module:attribute form")
    factory = getattr(importlib.import_module(module_name), attribute)
    return factory() if callable(factory) else factory


def watch(bridge: ExecutionBridge, *, poll_seconds: float = 2.0,
          autopilot_projects: tuple[str, ...] = ()) -> None:
    while True:
        plane = ControlPlane(bridge.db_path)
        projects = autopilot_projects
        if not projects:
            db = bootstrap_runtime_database(bridge.db_path)
            try:
                projects = tuple(OrganizationRegistry(db).autopilot_projects())
            finally:
                db.close()
        if projects:
            for project_id in projects:
                try:
                    plane.dispatch_next_task(project_id)
                except Exception:
                    # Dispatch failures are durable gate outcomes; the worker
                    # must keep servicing already queued jobs.
                    continue
        bridge.run_once()
        time.sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Arda Control Plane execution bridge worker")
    parser.add_argument("--db", type=Path, default=runtime_database_path())
    parser.add_argument("--executor-factory", default=os.environ.get("ARDA_BRIDGE_EXECUTOR_FACTORY"))
    parser.add_argument("--worker-id", default=os.environ.get("ARDA_BRIDGE_WORKER_ID"))
    parser.add_argument("--poll-seconds", type=float, default=float(os.environ.get("ARDA_BRIDGE_POLL_SECONDS", "2")))
    parser.add_argument("--autopilot-project", action="append", default=[])
    args = parser.parse_args(argv)
    if not args.executor_factory:
        raise SystemExit("ARDA_BRIDGE_EXECUTOR_FACTORY is required; refusing to run without an executor")
    executor = load_factory(args.executor_factory)
    projects = tuple(args.autopilot_project or filter(None, os.environ.get("ARDA_AUTOPILOT_PROJECTS", "").split(",")))
    watch(ExecutionBridge(args.db, executor, worker_id=args.worker_id),
          poll_seconds=args.poll_seconds, autopilot_projects=projects)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
