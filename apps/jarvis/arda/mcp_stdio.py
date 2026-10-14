"""Arda Core MCP stdio entrypoint for Nous Hermes Agent."""
from __future__ import annotations

import argparse
from pathlib import Path

from arda.core import Database
from arda.mcp_action_service import CoreActionService
from arda.mcp_read_service import CoreReadService
from arda.mcp_provider_service import CoreProviderEvaluationService
from arda.mcp_server import (
    ActionMCPGateway,
    EvaluationMCPGateway,
    IntakeMCPGateway,
    MCPExecutionBudget,
    ReadOnlyMCPGateway,
    serve_stdio,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Arda Core MCP gateway")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--audit-log", type=Path, default=None)
    parser.add_argument(
        "--profile", choices=("read-only", "intake", "evaluation", "action"), default="read-only",
        help="read-only is the default; intake exposes task creation only",
    )
    parser.add_argument("--max-tool-calls", type=int, default=8)
    parser.add_argument("--max-duration-seconds", type=float, default=180.0)
    args = parser.parse_args()
    if not args.db.exists():
        raise SystemExit(f"Core database does not exist: {args.db}")
    db = Database(args.db)
    try:
        execution_budget = MCPExecutionBudget(
            max_tool_calls=args.max_tool_calls,
            max_duration_seconds=args.max_duration_seconds,
        )
        read_service = CoreReadService(db)
        if args.profile == "intake":
            gateway = IntakeMCPGateway.from_core_services(
                read_service,
                CoreActionService(db),
                audit_path=args.audit_log,
                execution_budget=execution_budget,
            )
        elif args.profile == "evaluation":
            gateway = EvaluationMCPGateway.from_core_services(
                read_service,
                CoreProviderEvaluationService(db),
                audit_path=args.audit_log,
                execution_budget=execution_budget,
            )
        elif args.profile == "action":
            gateway = ActionMCPGateway.from_core_services(
                read_service,
                CoreActionService(db),
                audit_path=args.audit_log,
                execution_budget=execution_budget,
            )
        else:
            gateway = ReadOnlyMCPGateway.from_core_read_service(
                read_service, audit_path=args.audit_log, execution_budget=execution_budget
            )
        serve_stdio(gateway)
    finally:
        db.close()


if __name__ == "__main__":
    main()
