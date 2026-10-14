#!/usr/bin/env python3
"""The bridge between architecture runs and the shared brain.

What a run learned is worth more to the *next* run than to the one that learned
it. This module writes a run's outcome into mem0 and reads the project's past
lessons back into the next phase brief, so a failed approach is tried once
rather than every time.

Three rules hold everywhere in here:

- **Fail-open, always.** Ollama down, Qdrant down, `mem.py` missing, a timeout, a
  line that will not parse — every one of them means "no memory this time", never
  a failed run. Memory is an accelerator; a run that cannot remember is still a
  run that works. Same posture as `recall_inject.py`.
- **Never block.** Reads happen before a run starts and are bounded by a
  timeout; writes triggered from inside the store's lock are handed to a daemon
  thread, because a mem0 call loads a model and would hold every other writer
  behind it.
- **Nothing secret goes in.** Summaries are built from task titles, statuses and
  phases — never environment values, never log bodies. A memory is a durable
  plain-text file; whatever reaches it stays reachable.

Layering: this is a leaf. It shells out to the repo's own `mem.py` and imports
nothing from the dashboard, so both `runner.py` and `architectures.py` can use
it without a cycle.
"""

import os
import re
import subprocess
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

AGENT_ID = "arch-runner"
TIMEOUT = 15                      # seconds per memory call
RECALL_TOP = 5
MAX_TEXT = 1500                   # a memory is a fact, not a transcript

# `mem.py search` prints "  [2026-08-10 20:23] (scope/agent) 0.793  the fact"
_HIT = re.compile(r"^\s*\[(?P<when>[^\]]*)\]\s*\((?P<who>[^)]*)\)\s*(?P<score>[\d.]+)?\s+(?P<fact>.+)$")


def enabled():
    """The bridge is on unless it is switched off."""
    return os.environ.get("MEM0_ARCH_MEMORY", "1") != "0"


def _argv(*args):
    """How to invoke the memory CLI.

    MEM0_ARCH_MEM_CMD replaces the interpreter and script, which is how the
    tests exercise both sides without a running Ollama or Qdrant.
    """
    override = os.environ.get("MEM0_ARCH_MEM_CMD")
    if override:
        return override.split() + list(args)
    return [str(REPO / "bin" / "mem0-python"), str(REPO / "mem.py")] + list(args)


def _run(args):
    """Return stdout, or None if anything at all went wrong."""
    try:
        proc = subprocess.run(_argv(*args), capture_output=True, text=True,
                              timeout=TIMEOUT, cwd=str(REPO))
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def remember(scope, text, agent=AGENT_ID):
    """Store one fact. Returns 'written', 'skipped' or 'off'."""
    if not enabled():
        return "off"
    text = (text or "").strip()[:MAX_TEXT]
    if not text or not scope:
        return "skipped"
    return "written" if _run(["add", text, "--scope", scope, "--agent", agent]) is not None \
        else "skipped"


def remember_async(scope, text, agent=AGENT_ID):
    """Fire and forget.

    Used from paths that hold the architecture store's lock: a mem0 write loads
    a model and takes seconds, and every other writer would queue behind it.
    """
    if not enabled() or not scope or not text:
        return
    threading.Thread(target=remember, args=(scope, text, agent),
                     name="arch-memory-write", daemon=True).start()


def recall(scope, query, top=RECALL_TOP):
    """Past lessons for this project, most relevant first. [] when unavailable."""
    if not enabled() or not scope or not (query or "").strip():
        return []
    out = _run(["search", query.strip()[:400], "--scope", scope, "--top", str(top)])
    if not out:
        return []
    hits = []
    for line in out.splitlines():
        if "(no matches)" in line:
            continue
        match = _HIT.match(line)
        if not match:
            continue
        fact = match.group("fact").strip()
        if fact:
            hits.append({"when": match.group("when").strip(), "fact": fact})
    return hits[:top]


# --- The things worth remembering -------------------------------------------
def run_summary(architecture, record, tasks_done, failure_lines=()):
    """One compact fact about how a run went.

    Task titles and statuses only. The log body is deliberately left out: it is
    the one part of a run that could contain anything at all.
    """
    parts = [
        f"arch={architecture.get('id')} phase={record.get('phase')} "
        f"status={record.get('status')}"
    ]
    duration = _duration(record)
    if duration is not None:
        parts[0] += f" duration={duration}s"
    if tasks_done:
        titles = "; ".join(t[:80] for t in tasks_done[:5])
        parts.append(f"completed: {titles}")
    if failure_lines:
        parts.append("failed with: " + " / ".join(l[:120] for l in list(failure_lines)[-3:]))
    return " | ".join(parts)


def advance_summary(project_id, move):
    return (f"phase {move['from']} completed for {project_id}: "
            f"{move['tasks_completed']} task(s); advanced to {move['to']}")


def _duration(record):
    start, end = record.get("started_at"), record.get("ended_at")
    if not start or not end:
        return None
    try:
        from datetime import datetime
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        return int((datetime.strptime(end, fmt) - datetime.strptime(start, fmt)).total_seconds())
    except Exception:
        return None
