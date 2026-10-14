#!/usr/bin/env python3
"""
Universal Python Client for mem0-local.
Allows any AI agent framework (Google Antigravity, OpenAI, Gemini API, Anthropic,
LangChain, LlamaIndex, AutoGen, CrewAI, Aider) or custom Python script to interact
with mem0-local memories, rules, knowledge graph, and agent harness in a single line.
Stdlib-first: zero cloud dependencies.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

MEM0_ROOT = Path(__file__).resolve().parent


class Mem0Client:
    """Universal SDK Client for local memories, rules, skills, and evaluation harness."""

    def __init__(self, scope: str = "personal", agent: str = "python-sdk", workspace_dir: Optional[str] = None):
        self.scope = scope
        self.agent = agent
        self.workspace_dir = Path(workspace_dir).resolve() if workspace_dir else Path(".").resolve()

        # Ensure local mem0-local modules are on sys.path
        if str(MEM0_ROOT) not in sys.path:
            sys.path.insert(0, str(MEM0_ROOT))

    def _get_memory_client(self):
        try:
            from config import get_memory
            return get_memory()
        except Exception:
            return None

    def add(self, text: str, verbatim: bool = False, supersedes_id: str = "") -> dict:
        """Stores a durable memory or architectural decision."""
        client = self._get_memory_client()
        if not client:
            return {"error": "Memory store unavailable"}

        owner = os.environ.get("MEM0_OWNER", "default")
        metadata = {"status": "active"}
        if supersedes_id:
            metadata["supersedes"] = supersedes_id

        res = client.add(
            text,
            user_id=owner,
            run_id=self.scope,
            agent_id=self.agent,
            metadata=metadata,
            infer=not verbatim,
        )
        # Sync with Knowledge Graph
        try:
            import graph_store
            results = res.get("results", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
            for r in results:
                mid = str(r.get("id") or "")
                mtext = str(r.get("memory") or text)
                graph_store.sync_memory_links(mid, mtext, metadata=metadata, scope=self.scope)
        except Exception:
            pass

        return res

    def search(self, query: str, top_k: int = 5, include_superseded: bool = False) -> list[dict]:
        """Performs semantic search across memories in the current scope."""
        client = self._get_memory_client()
        if not client:
            return []

        owner = os.environ.get("MEM0_OWNER", "default")
        filters = {"user_id": owner, "run_id": self.scope}
        res = client.search(query, filters=filters, top_k=top_k)
        hits = res.get("results", []) if isinstance(res, dict) else (res or [])

        if not include_superseded:
            hits = [
                h for h in hits
                if (h.get("status") or (h.get("metadata") or {}).get("status") or "active") != "superseded"
            ]
        return hits

    def get_rules(self, file_path: str = "") -> list[dict]:
        """Retrieves active binding rules matching a file path or for the entire scope."""
        try:
            import rules as rules_store
            if file_path:
                return rules_store.match_rules_for_file(self.scope, file_path)
            return rules_store.list_rules(self.scope, include_disabled=False)
        except Exception:
            return []

    def get_skills(self, task_prompt: str = "") -> list[dict]:
        """Retrieves installed skills, optionally matched against a task description."""
        try:
            import skills_engine
            skills = skills_engine.discover_skills()
            if task_prompt:
                return skills_engine.match_skills_for_task(task_prompt, skills=skills)
            return skills
        except Exception:
            return []

    def assemble_context(self, task: str = "", file_paths: list[str] = None) -> dict:
        """Assembles memories, rules, skills, and graph links for injection into any LLM prompt."""
        try:
            import harness
            return harness.assemble_context(task=task, scope=self.scope, file_paths=file_paths)
        except Exception:
            return {"task": task, "scope": self.scope, "memories": "", "rules": "", "skills": "", "graph": []}

    def get_system_prompt(self, task: str = "", file_paths: list[str] = None) -> str:
        """Formats the assembled context into a clean markdown prompt snippet ready for system instruction."""
        ctx = self.assemble_context(task=task, file_paths=file_paths)
        lines = [
            f"# [SHARED BRAIN] Project Context: {self.scope}",
        ]
        if ctx.get("rules"):
            lines.append(f"\n{ctx['rules']}")
        if ctx.get("memories"):
            lines.append(f"\n## Durable Memories & Gotchas\n{ctx['memories']}")
        if ctx.get("skills"):
            lines.append(f"\n{ctx['skills']}")
        if ctx.get("graph"):
            lines.append("\n## Related Architectural Entities")
            for g in ctx["graph"][:6]:
                lines.append(f"- {g.get('id')}: {g.get('label', '')}")

        return "\n".join(lines).strip()

    def run_harness(self, task: str, verify_command: str = None) -> dict:
        """Runs a task through the evaluation harness with guardrails and trajectory recording."""
        try:
            import harness
            return harness.run_harness_task(
                task=task,
                scope=self.scope,
                verify_command=verify_command,
                cwd=str(self.workspace_dir),
            )
        except Exception as ex:
            return {"status": "error", "error": str(ex)}

    def get_health(self) -> dict:
        """Calculates quantitative memory health score."""
        try:
            import ide_bridge
            return ide_bridge.evaluate_health_score(scope=self.scope, workspace_dir=str(self.workspace_dir))
        except Exception as ex:
            return {"error": str(ex)}
