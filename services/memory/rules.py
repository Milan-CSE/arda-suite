#!/usr/bin/env python3
"""Project- and directory-scoped conventions, loaded at SessionStart.

Spec: docs/superpowers/specs/2026-08-12-scoped-rules-design.md

Why this exists alongside the memory store: the same question can have opposite
correct answers in two directories of one repo. "Await promise-returning calls"
is right under `src/services/**` and wrong under `src/analytics/**`, where
fire-and-forget is deliberate so the request is not blocked. A flat recalled
memory cannot express that — it states the convention without the scope it holds
in, so it is either too broad or silently wrong somewhere. A rule carries its own
scope, and is rendered with it.

Two constraints shape the design:

* Rules live in `~/.mem0/rules/*.json`, not in the vector store. `recall_inject`
  fails silently when Qdrant is unreachable, on purpose — a session must start
  regardless. Rules kept there would disappear along with it, invisibly. Files
  are also diffable and hand-reviewable, which recall is not: rules are law, not
  a hint.
* Nothing here evaluates a path against a rule, because at SessionStart the file
  that will be edited is not known yet. Scope is *rendered*, and the model
  matches it. That is why there is no condition language: a glob is the only part
  of a scope that could be checked mechanically, and even it has nothing to be
  checked against until an edit happens.
"""

import fnmatch
import json
import os

RULES_DIR = os.path.expanduser("~/.mem0/rules")
SHARED = "personal"

# Paid on every session, so deliberately small: the conventions that actually
# bite, not a style guide. Kept separate from the memory budget in recall_inject
# — rules are few and deterministic, memories are the noisy side, and one must
# not starve the other.
MAX_RULES = 10
MAX_RULE_CHARS = 1200

SEVERITIES = ("high", "normal")
DEFAULT_SEVERITY = "normal"
STATUSES = ("active", "disabled")
DEFAULT_STATUS = "active"

_SEVERITY_RANK = {"high": 0, "normal": 1}


def _path_for(scope):
    return os.path.join(RULES_DIR, "%s.json" % scope)


def _str_list(value):
    """A list of non-empty strings, or []. A hand-edited file can hold anything;
    a wrong type here must degrade to "no scope stated", never to a crash."""
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def normalize(entry):
    """A rule with every optional field filled in, or None if it is not a rule.

    `id` and `instruction` are the only requirements — everything else has a
    sane default, so the shortest useful rule is two fields. An unknown
    severity or status falls back to the default rather than inventing a new
    class, because the value came from a hand-edited file and a typo must not
    quietly create a priority tier nothing else knows about.
    """
    if not isinstance(entry, dict):
        return None
    rid = entry.get("id")
    instruction = entry.get("instruction")
    if not isinstance(rid, str) or not rid.strip():
        return None
    if not isinstance(instruction, str) or not instruction.strip():
        return None

    severity = entry.get("severity")
    status = entry.get("status")
    return {
        "id": rid.strip(),
        "instruction": instruction.strip(),
        "paths": _str_list(entry.get("paths")),
        "exclude_paths": _str_list(entry.get("exclude_paths")),
        "severity": severity if severity in SEVERITIES else DEFAULT_SEVERITY,
        "status": status if status in STATUSES else DEFAULT_STATUS,
        "evidence": _str_list(entry.get("evidence")),
        "created_at": entry.get("created_at") or "",
        "updated_at": entry.get("updated_at") or "",
    }


def read_file(scope):
    """Every normalized rule in one tier's file; [] on anything unexpected.

    Fail-open mirrors `recall_inject.collect`: this runs on SessionStart, and a
    broken rules file is not worth failing a session over. A malformed *entry*
    is skipped while its siblings survive — one bad hand-edit must not silently
    drop the rest of the file's conventions.
    """
    try:
        with open(_path_for(scope), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    entries = data.get("rules")
    if not isinstance(entries, list):
        return []
    out = []
    for entry in entries:
        norm = normalize(entry)
        if norm:
            out.append(norm)
    return out


def scope_text(rule):
    """The rule's scope as the model will read it.

    Always rendered, including the "all paths" case: an unqualified instruction
    reads as universal, which for a project-tier rule is exactly the
    over-generalisation this whole design exists to prevent.
    """
    paths = rule.get("paths") or []
    excludes = rule.get("exclude_paths") or []
    text = "paths: " + ", ".join(paths) if paths else "all paths"
    if excludes:
        text += " | except: " + ", ".join(excludes)
    return text


def render_line(rule):
    return "- (%s) [%s] %s — %s" % (rule.get("tier") or "-", rule["severity"],
                                    scope_text(rule), rule["instruction"])


def _sort_key(rule):
    """One order for both budget selection and rendering, so the printed block is
    exactly the truncated prefix of the sorted list and the two cannot drift.

    Severity dominates (a `high` rule surviving the budget matters more than
    which tier it came from), then personal before project, then id so ties are
    stable across runs and independent of the order the file happened to list.
    """
    return (_SEVERITY_RANK.get(rule["severity"], 1),
            0 if rule.get("tier") == SHARED else 1,
            rule["id"])


def load_rules(project_id, max_rules=MAX_RULES, max_chars=MAX_RULE_CHARS):
    """Active rules for a session in `project_id`: both tiers, ordered, trimmed.

    The project tier overrides the shared one by id. Rendering both would put
    two lines with the same id — and possibly contradictory instructions — in
    front of the model, which is the precise failure scoped rules exist to
    prevent, so the more specific one simply replaces the other.
    """
    try:
        shared = read_file(SHARED) if project_id != SHARED else []
        own = read_file(project_id)
        for rule in shared:
            rule["tier"] = SHARED
        for rule in own:
            rule["tier"] = project_id

        own_ids = {r["id"] for r in own}
        merged = [r for r in shared if r["id"] not in own_ids] + own
        merged = [r for r in merged if r["status"] == "active"]
        merged.sort(key=_sort_key)

        out, used = [], 0
        for rule in merged:
            # Dropped whole rather than truncated: half a rule is a misleading
            # rule, the same call `recall_inject.select` makes for memories.
            cost = len(render_line(rule))
            if used + cost > max_chars:
                continue
            used += cost
            out.append(rule)
            if len(out) >= max_rules:
                break
        return out
    except Exception:
        return []


def render(rules, project_id):
    """The injected block, or "" when there is nothing to inject.

    The header says "binding" on purpose. The memory block directly below it is
    framed as unverified background; without the contrast a convention reads
    like one more thing to second-guess.
    """
    if not rules:
        return ""
    lines = ["Scoped rules (project: %s). These are binding conventions for this "
             "project — apply the rule whose path scope matches the file you are "
             "editing." % project_id]
    lines.extend(render_line(r) for r in rules)
    return "\n".join(lines)


# --- authoring side ---------------------------------------------------------
# Used by `mem.py rules`. Kept here rather than in the CLI so the behaviour that
# matters (overlap detection, id replacement, atomic write) is testable without
# a subprocess, and so the CLI stays a thin argument parser.

def list_rules(scope, include_disabled=False):
    """One tier's rules as authored — unmerged, unsorted, unbudgeted.

    Deliberately not `load_rules`: an author needs to see disabled rules and the
    file's own contents, while a session needs the merged active view.
    """
    entries = read_file(scope)
    if include_disabled:
        return entries
    return [r for r in entries if r["status"] == "active"]


def overlap_warnings(new_rule, existing):
    """Scopes the new rule collides with — advisory, never a veto.

    Two rules covering the same paths are not automatically wrong (a `high` and
    a `normal` rule can share a directory), and no check here can tell a genuine
    contradiction from a legitimate pair. So this reports and the human decides;
    a heuristic that refused writes would be worse than the ambiguity.
    """
    warnings = []
    new_paths = set(_str_list(new_rule.get("paths")))
    for rule in existing:
        if rule.get("id") == new_rule.get("id"):
            continue                     # re-adding an id is an edit, not a clash
        if rule.get("status", DEFAULT_STATUS) != "active":
            continue
        other_paths = set(_str_list(rule.get("paths")))
        shared_globs = sorted(new_paths & other_paths)
        if shared_globs:
            warnings.append("scope %s also covered by rule '%s'"
                            % (", ".join(shared_globs), rule.get("id")))
        elif not new_paths and not other_paths:
            warnings.append("rule '%s' is also project-wide" % rule.get("id"))
    return warnings


def save_rules(scope, entries):
    """Atomic write, so an interrupted save cannot leave a half-written file that
    the fail-open reader would silently treat as "no rules"."""
    os.makedirs(RULES_DIR, exist_ok=True)
    path = _path_for(scope)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"rules": entries}, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def add_rule(scope, rule, now=None):
    """Add or replace a rule by id; returns overlap warnings (write still done).

    Replacing on a repeated id makes `add` the edit path too. `created_at` is
    carried over from the version being replaced — when the convention was first
    established is the part worth keeping.
    """
    import datetime

    stamp = now or datetime.date.today().isoformat()
    entries = read_file(scope)
    warnings = overlap_warnings(rule, entries)

    incoming = normalize(rule)
    if not incoming:
        raise ValueError("a rule needs a non-empty id and instruction")

    previous = next((r for r in entries if r["id"] == incoming["id"]), None)
    incoming["created_at"] = (previous or {}).get("created_at") or stamp
    incoming["updated_at"] = stamp

    kept = [r for r in entries if r["id"] != incoming["id"]]
    save_rules(scope, kept + [incoming])
    return warnings


def remove_rule(scope, rule_id):
    """Delete by id. False when nothing matched — a silent no-op would let a
    typo read as a successful deletion."""
    entries = read_file(scope)
    kept = [r for r in entries if r["id"] != rule_id]
    if len(kept) == len(entries):
        return False
    save_rules(scope, kept)
    return True


def matches_path(path: str, glob_pattern: str) -> bool:
    """Matches a file path against a glob pattern, supporting ** recursion and basenames."""
    p = path.replace("\\", "/").lstrip("/")
    g = glob_pattern.replace("\\", "/").lstrip("/")

    if g.endswith("/**"):
        base_dir = g[:-3]
        return p.startswith(base_dir + "/") or p == base_dir

    if "/**/" in g:
        prefix, suffix = g.split("/**/", 1)
        if p.startswith(prefix + "/"):
            rem = p[len(prefix) + 1:]
            return fnmatch.fnmatch(rem, suffix) or fnmatch.fnmatch(p, f"{prefix}/*/{suffix}")
        return False

    return fnmatch.fnmatch(p, g) or fnmatch.fnmatch(os.path.basename(p), g)


def rule_applies_to_file(rule: dict, file_path: str) -> bool:
    """Whether a rule applies to a specific file path."""
    if not file_path:
        return True
    paths = rule.get("paths") or []
    excludes = rule.get("exclude_paths") or []

    for exc in excludes:
        if matches_path(file_path, exc):
            return False

    if not paths:
        return True

    return any(matches_path(file_path, pat) for pat in paths)


def match_rules_for_file(project_id: str, file_path: str) -> list:
    """Return all active rules for a project that apply to a specific file."""
    all_rules = load_rules(project_id, max_rules=50, max_chars=10000)
    return [r for r in all_rules if rule_applies_to_file(r, file_path)]
