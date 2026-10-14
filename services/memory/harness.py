#!/usr/bin/env python3
"""
Agent Execution & Evaluation Harness for mem0-local.
Orchestrates pre-flight context assembly, trajectory logging, safety guardrails,
and automated post-flight verification / reflexion loops.
Stdlib-first: zero external cloud dependencies.
"""

import datetime
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

TRAJECTORIES_DIR = Path(os.path.expanduser("~/.mem0/trajectories"))

# Destructive command patterns blocked by the safety guardrail
DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+(-[rfRF]+\s+|-[rR]\s+-[fF]\s+)(/|~|\$HOME|/\*)(?:\s|$)", re.I),
    re.compile(r"\b(drop\s+database|truncate\s+table)\b", re.I),
    re.compile(r"\bformat\s+[c-z]:", re.I),
    re.compile(r"\bmkfs(\.\w+)?\b", re.I),
    re.compile(r"\bdd\s+if=.*of=(/dev/|/dev/sd|/dev/nvme)\w+", re.I),
    # Fork bomb & process exhaustion patterns
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", re.I),
    # Reckless root/home recursive permission changes
    re.compile(r"\b(chmod|chown)\s+(-[rR]+\s+)?(777|000)\s+(/|~|\$HOME|/\*)(?:\s|$)", re.I),
    # Blind piping of remote downloads into shell or interpreter
    re.compile(r"\b(curl|wget)\b[^|]+\|\s*(sudo\s+)?(bash|sh|zsh|dash|ksh|python|perl|ruby)\b", re.I),
    # Catastrophic force-pushes to primary production branches
    re.compile(r"\bgit\s+push\s+.*(--force|-f)\b.*(main|master)\b", re.I),
    re.compile(r"\bgit\s+push\s+.*(main|master)\b.*(--force|-f)\b", re.I),
]


class SafetyViolationError(Exception):
    """Raised when a command violates safety guardrails."""
    pass


def check_command_safety(command: str) -> None:
    """Inspects a shell command for potentially catastrophic data loss patterns."""
    cmd = (command or "").strip()
    for pat in DANGEROUS_PATTERNS:
        if pat.search(cmd):
            raise SafetyViolationError(
                f"Command blocked by Safety Guardrail: '{cmd}' matches dangerous pattern."
            )


class TrajectoryLogger:
    """Appends structured execution steps to ~/.mem0/trajectories/<run_id>.jsonl."""

    def __init__(self, run_id: str = None, trajectories_dir: Path = None):
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:10]}"
        self.dir = trajectories_dir or TRAJECTORIES_DIR
        try:
            self.dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            self.path = self.dir / f"{self.run_id}.jsonl"
        except OSError:
            # Fallback to current working directory
            self.dir = Path(".") / ".trajectories"
            self.dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            self.path = self.dir / f"{self.run_id}.jsonl"
        self.step_index = 0

    def log_step(self, action: str, details: dict) -> dict:
        self.step_index += 1
        entry = {
            "run_id": self.run_id,
            "step": self.step_index,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "action": action,
            **details,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry


def assemble_context(task: str, scope: str = "personal", file_paths: list[str] = None) -> dict:
    """Aggregates scoped memories, binding rules, matched skills, and knowledge graph relations."""
    context = {
        "task": task,
        "scope": scope,
        "memories": "",
        "rules": "",
        "skills": "",
        "graph": [],
    }

    # 1. Scoped memories via recall_inject
    try:
        import recall_inject
        rendered_mem, _ = recall_inject.collect(scope)
        context["memories"] = rendered_mem
    except Exception:
        pass

    # 2. Binding rules
    try:
        import rules
        matched_rules = []
        for fp in (file_paths or [""]):
            matched_rules.extend(rules.match_rules_for_file(scope, fp))
        if matched_rules:
            # Dedup
            seen = set()
            unique_rules = []
            for r in matched_rules:
                if r["id"] not in seen:
                    seen.add(r["id"])
                    unique_rules.append(r)
            context["rules"] = rules.render(unique_rules, scope)
    except Exception:
        pass

    # 3. Active skills
    try:
        import skills_engine
        matched_skills = skills_engine.match_skills_for_task(task, file_paths)
        context["skills"] = skills_engine.render_skills_prompt(matched_skills)
    except Exception:
        pass

    # 4. Graph multi-hop relations
    try:
        import graph_store
        entities = graph_store.extract_entities(task)
        start_nodes = [f"entity:{e}" for e in entities]
        if start_nodes:
            discovered = graph_store.multi_hop_query(start_nodes, max_hops=1)
            context["graph"] = discovered
    except Exception:
        pass

    return context


def run_command(command: str, cwd: str = None, timeout: int = 60) -> tuple[int, str]:
    """Runs a shell command safely, checking guardrails first."""
    check_command_safety(command)
    res = subprocess.run(
        command,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = (res.stdout or "") + (res.stderr or "")
    return res.returncode, output.strip()


def run_harness_task(
    task: str,
    scope: str = "personal",
    verify_command: str = None,
    cwd: str = None,
    file_paths: list[str] = None,
) -> dict:
    """Orchestrates an end-to-end task under harness supervision."""
    logger = TrajectoryLogger()
    logger.log_step("task_start", {"task": task, "scope": scope})

    # Pre-check task safety
    safety_passed = True
    safety_violation = ""
    try:
        check_command_safety(task)
        if verify_command:
            check_command_safety(verify_command)
    except SafetyViolationError as ex:
        safety_passed = False
        safety_violation = str(ex)
        logger.log_step("safety_violation", {"violation": safety_violation})

    # Assemble context
    ctx = assemble_context(task, scope=scope, file_paths=file_paths)
    logger.log_step("context_assembled", {
        "has_memories": bool(ctx.get("memories")),
        "has_rules": bool(ctx.get("rules")),
        "has_skills": bool(ctx.get("skills")),
        "graph_entities_count": len(ctx.get("graph", [])),
    })

    if not safety_passed:
        logger.log_step("task_end", {"status": "blocked", "verified": False})
        return {
            "status": "blocked",
            "run_id": logger.run_id,
            "trajectory_file": str(logger.path),
            "safety_passed": False,
            "safety_violation": safety_violation,
            "verified": False,
            "verification_passed": False,
            "verification_output": "",
            "reflexion_triggered": False,
            "context": ctx,
        }

    verified = True if not verify_command else False
    verification_output = ""
    initial_verify_failed = False
    reflexion_triggered = False

    # Pre-verification if command is provided
    if verify_command:
        code, out = run_command(verify_command, cwd=cwd)
        logger.log_step("pre_verification", {"command": verify_command, "exit_code": code, "output": out[:300]})
        if code != 0:
            initial_verify_failed = True

    # Post-verification if command is provided
    if verify_command:
        code, out = run_command(verify_command, cwd=cwd)
        logger.log_step("post_verification", {"command": verify_command, "exit_code": code, "output": out[:300]})
        verified = (code == 0)
        verification_output = out

        # Trigger reflexion engine if failure was resolved or persisted
        if initial_verify_failed and verified:
            try:
                import reflexion_engine
                logger.log_step("reflexion_triggered", {"reason": "failure_resolved"})
                reflexion_engine.main(["--dry-run"])
                reflexion_triggered = True
            except Exception:
                pass

    status = "completed" if verified else "failed"
    logger.log_step("task_end", {"status": status, "verified": verified})

    return {
        "status": status,
        "run_id": logger.run_id,
        "trajectory_file": str(logger.path),
        "safety_passed": True,
        "safety_violation": "",
        "verified": verified,
        "verification_passed": verified,
        "verification_output": verification_output,
        "reflexion_triggered": reflexion_triggered,
        "context": ctx,
    }

