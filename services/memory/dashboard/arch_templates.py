#!/usr/bin/env python3
"""Architecture templates — the shapes a new architecture can start from.

Layering: this module is *below* architectures.py. It owns the master phase
catalog and the template catalog; it knows nothing about stored architectures,
so architectures.py can import it without a cycle. Anything needing the store's
location takes the directory as an argument rather than reaching for it.

A template is a named, ordered **subset** of the master phase catalog plus the
failure loops that belong to that shape. Templates never invent phases: the
protocol is the protocol, and a template only says which parts of it apply.

Schema
------
    {
      "id":          "bugfix",                       # slug, _ID_RE
      "name":        "Bug Fix",                      # required, <= 60 chars
      "description": "Reproduce → fix → verify → ship",
      "icon":        "🐛",                            # optional
      "tier":        "S",                            # S | M | L
      "phases":      ["discover", "build", "verify", "commit"],
      "loops":       [{"from": "verify", "to": "build", "label": "fail"}],
      "playbook":    {"build": {"agents": [], "skills": [], "rules": []}},
      "seed_tasks":  [{"phase": "build", "title": "…", "priority": "P2"}]
    }

`playbook` and `seed_tasks` are optional. A template with no `playbook` inherits
the global per-phase playbook, narrowed to its own phases — which is what every
built-in does, so one correction to phase_playbook.json reaches all of them.

User templates live in <arch dir>/templates/*.json and are read fresh on every
call. A user file whose id matches a built-in **shadows** it: that is the
supported way to adjust a built-in without editing this file. A malformed user
file is skipped with a warning and can never take the catalog down with it.
"""

import json
import os
import re
import tempfile
from pathlib import Path

# --- Master phase catalog ---------------------------------------------------
# The 16-phase AI Development Protocol, in execution order. Templates select
# ordered subsets of this; nothing may add to it.
PHASES = (
    "discover", "spec", "context", "design",
    "contracts", "decompose", "prioritize", "build",
    "verify", "review", "approve", "commit",
    "ci", "release", "observe", "learn",
)

TIERS = ("S", "M", "L")
MAX_NAME = 60
MAX_ID = 64
_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,%d}" % (MAX_ID - 1))
DEFAULT_TEMPLATE_ID = "full-16"
DEFAULT_LOOP_LABEL = "fail"


def _phases(*names):
    """Assert at import time that a built-in only names real phases, in order."""
    unknown = [n for n in names if n not in PHASES]
    assert not unknown, f"built-in template names unknown phases: {unknown}"
    ordered = sorted(names, key=PHASES.index)
    assert list(names) == ordered, f"built-in phases out of protocol order: {names}"
    return list(names)


def _loop(src, dst, label=DEFAULT_LOOP_LABEL):
    return {"from": src, "to": dst, "label": label}


# --- Built-in catalog -------------------------------------------------------
BUILTINS = [
    {
        "id": "full-16",
        "name": "Full Protocol",
        "description": "Every phase, discover through learn. For work that has to survive review, CI and release.",
        "icon": "🚀",
        "tier": "L",
        "phases": _phases(*PHASES),
        "loops": [
            _loop("verify", "build"),
            _loop("review", "build"),
            _loop("ci", "build"),
            _loop("approve", "spec", "reject"),
        ],
    },
    {
        "id": "feature",
        "name": "Feature on Existing Product",
        "description": "A change inside a codebase that already exists: design against what is there, then ship it.",
        "icon": "🧩",
        "tier": "M",
        "phases": _phases("spec", "context", "design", "build",
                          "verify", "review", "commit", "ci", "release"),
        "loops": [
            _loop("verify", "build"),
            _loop("review", "build"),
            _loop("ci", "build"),
        ],
    },
    {
        "id": "bugfix",
        "name": "Bug Fix",
        "description": "Reproduce → fix → verify → ship. The failing test comes first.",
        "icon": "🐛",
        "tier": "S",
        "phases": _phases("discover", "build", "verify", "commit"),
        "loops": [_loop("verify", "build")],
    },
    {
        "id": "code-review",
        "name": "Code Review",
        "description": "Review someone else's diff to a verdict: findings, approval, merge.",
        "icon": "🔍",
        "tier": "S",
        "phases": _phases("context", "review", "approve", "commit"),
        "loops": [_loop("approve", "review", "reject")],
    },
    {
        "id": "prototype",
        "name": "Prototype",
        "description": "Find out whether the idea works. No review gate, nothing to release.",
        "icon": "⚡",
        "tier": "S",
        "phases": _phases("discover", "build", "verify"),
        "loops": [_loop("verify", "build")],
    },
]


# --- Validation -------------------------------------------------------------
def validate(template):
    """Return a list of problems. Empty list means the template is usable."""
    problems = []
    if not isinstance(template, dict):
        return ["template must be a JSON object"]

    tid = template.get("id")
    if not isinstance(tid, str) or not _ID_RE.fullmatch(tid):
        problems.append(f"invalid id: {tid!r}")

    name = template.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append("name is required")
    elif len(name) > MAX_NAME:
        problems.append(f"name must be at most {MAX_NAME} characters")

    if template.get("tier") not in TIERS:
        problems.append(f"tier must be one of: {', '.join(TIERS)}")

    icon = template.get("icon", "")
    if not isinstance(icon, str):
        problems.append("icon must be a string")

    phases = template.get("phases")
    if not isinstance(phases, list) or not phases:
        problems.append("phases must be a non-empty list")
        phases = []
    else:
        unknown = [p for p in phases if p not in PHASES]
        if unknown:
            problems.append(f"unknown phases: {', '.join(map(str, unknown))}")
        if len(set(phases)) != len(phases):
            problems.append("phases contains duplicates")
        known = [p for p in phases if p in PHASES]
        if known != sorted(known, key=PHASES.index):
            problems.append("phases must follow the master protocol order")

    for loop in template.get("loops") or []:
        if not isinstance(loop, dict):
            problems.append("each loop must be an object")
            continue
        for end in ("from", "to"):
            if loop.get(end) not in phases:
                problems.append(f"loop {end} {loop.get(end)!r} is not one of this template's phases")
        if not isinstance(loop.get("label", DEFAULT_LOOP_LABEL), str):
            problems.append("loop label must be a string")

    book = template.get("playbook")
    if book is not None:
        if not isinstance(book, dict):
            problems.append("playbook must be an object")
        else:
            stray = [k for k in book if k not in phases]
            if stray:
                problems.append(f"playbook covers phases not in this template: {', '.join(stray)}")

    for task in template.get("seed_tasks") or []:
        if not isinstance(task, dict):
            problems.append("each seed task must be an object")
        elif task.get("phase") not in phases:
            problems.append(f"seed task phase {task.get('phase')!r} is not one of this template's phases")
        elif not str(task.get("title") or "").strip():
            problems.append("seed task title is required")
    return problems


def normalize(template):
    """A validated template with its optional fields filled in."""
    return {
        "id": template["id"],
        "name": template["name"].strip(),
        "description": str(template.get("description") or "").strip(),
        "icon": str(template.get("icon") or ""),
        "tier": template["tier"],
        "phases": list(template["phases"]),
        "loops": [{"from": l["from"], "to": l["to"],
                   "label": str(l.get("label") or DEFAULT_LOOP_LABEL)}
                  for l in (template.get("loops") or [])],
        "playbook": template.get("playbook"),
        "seed_tasks": [dict(t) for t in (template.get("seed_tasks") or [])],
    }


# --- User templates on disk -------------------------------------------------
def user_dir(arch_dir):
    return Path(arch_dir).expanduser() / "templates"


def _user_path(arch_dir, template_id):
    """id -> file path. The only place a template id becomes a path."""
    if not isinstance(template_id, str) or not _ID_RE.fullmatch(template_id):
        raise ValueError(f"invalid template id: {template_id!r}")
    base = user_dir(arch_dir).resolve()
    path = (base / f"{template_id}.json").resolve()
    if path.parent != base:
        raise PermissionError(f"outside the template store: {path}")
    return path


def is_builtin(template_id):
    return any(t["id"] == template_id for t in BUILTINS)


def catalog(arch_dir):
    """Built-ins plus user templates, newest definition winning.

    Returns (templates, warnings). A user file with a built-in's id shadows it;
    a broken one is skipped and named in the warnings so the panel can say what
    it ignored instead of silently showing a shorter list.
    """
    templates, warnings = {}, []
    for builtin in BUILTINS:
        problems = validate(builtin)
        if problems:                                  # a code bug, not user input
            raise AssertionError(f"built-in {builtin.get('id')!r}: {'; '.join(problems)}")
        templates[builtin["id"]] = dict(normalize(builtin), source="builtin")

    base = user_dir(arch_dir)
    if base.is_dir():
        for path in sorted(base.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                warnings.append(f"{path.name}: not valid JSON ({e})")
                continue
            if isinstance(raw, dict) and "id" not in raw:
                raw = dict(raw, id=path.stem)         # the filename is the id
            problems = validate(raw)
            if problems:
                warnings.append(f"{path.name}: {'; '.join(problems)}")
                continue
            shadowed = raw["id"] in templates
            templates[raw["id"]] = dict(normalize(raw), source="user",
                                        shadows_builtin=shadowed and is_builtin(raw["id"]))
    order = {t["id"]: i for i, t in enumerate(BUILTINS)}
    return (sorted(templates.values(), key=lambda t: (order.get(t["id"], len(order)), t["id"])),
            warnings)


def get(arch_dir, template_id):
    """One template by id, or None. User files shadow built-ins here too."""
    for template in catalog(arch_dir)[0]:
        if template["id"] == template_id:
            return template
    return None


def save_user(arch_dir, template):
    """Write a user template. Atomic, and never over a built-in's id."""
    problems = validate(template)
    if problems:
        raise ValueError("; ".join(problems))
    if is_builtin(template["id"]):
        raise FileExistsError(
            f"{template['id']!r} is a built-in template; pick another id"
            " (drop a file with this id in the templates directory to shadow it)")
    path = _user_path(arch_dir, template["id"])
    if path.exists():
        raise FileExistsError(f"template already exists: {template['id']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = normalize(template)
    tmp = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent),
        prefix=f".{path.stem}-", suffix=".tmp", delete=False)
    try:
        with tmp:
            json.dump(doc, tmp, indent=2, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp.name, path)
    except BaseException:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise
    return dict(doc, source="user")


def delete_user(arch_dir, template_id):
    """Delete a user template. Built-ins are code and cannot be deleted."""
    if is_builtin(template_id):
        raise PermissionError(f"{template_id!r} is a built-in template and cannot be deleted")
    path = _user_path(arch_dir, template_id)
    if not path.is_file():
        raise FileNotFoundError(f"no such template: {template_id}")
    path.unlink()
    return {"ok": True, "id": template_id}
