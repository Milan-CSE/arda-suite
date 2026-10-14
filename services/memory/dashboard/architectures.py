#!/usr/bin/env python3
"""Architectures — the 16-phase AI Development Protocol, one JSON file per feature.

This is the only module that knows where architectures live and what shape they
have, the way `mem0_backend.py` is the only module that knows where memory lives.
`server.py` routes; it never touches this store's filesystem.

Storage: ~/.mem0/architectures/<id>.json, one file per architecture.

Two rules the rest of the code depends on:

- **Derived state is never persisted.** Per-phase task counts and progress are
  computed on every read. A stored derivation drifts from its source the first
  time anything writes around it; a computed one cannot.
- **Writes are atomic and serialized.** `ThreadingHTTPServer` runs handlers
  concurrently, so a read-modify-write pair without a lock loses tasks, and a
  partial write would replace a good file with a truncated one.
"""

import json
import os
import re
import secrets
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
MEM0_ROOT = HERE.parent
if str(MEM0_ROOT) not in sys.path:
    sys.path.insert(0, str(MEM0_ROOT))

from mem0_project import project_id  # noqa: E402
import arch_templates  # noqa: E402  — the layer below: phase + template catalogs
import arch_memory  # noqa: E402  — leaf; shells out to mem.py, fail-open
from arch_templates import PHASES, DEFAULT_TEMPLATE_ID  # noqa: E402

# --- Configuration ----------------------------------------------------------
ARCH_DIR = Path.home() / ".mem0" / "architectures"
SCHEMA_VERSION = 1

ARCH_STATUSES = ("active", "paused", "done", "archived")
PHASE_STATUSES = ("pending", "active", "done", "skipped")
TASK_STATUSES = ("todo", "doing", "blocked", "done")

# P0 (drop everything) .. P4 (someday). Ordered, so sorting by index sorts by
# urgency. Stored uppercase; callers may send either case.
TASK_PRIORITIES = ("P0", "P1", "P2", "P3", "P4")
TASK_PRIORITY_DEFAULT = "P2"

# Phases a machine may not walk past on its own. Approval is someone signing
# their name to the work, release puts it in front of users, and learn is the
# reflection that only means anything if a person does it. Finishing the tasks
# in these phases is not the same as being done with them.
MANUAL_PHASES = ("approve", "release", "learn")
AUTO_ADVANCE_LOG_LIMIT = 20

MAX_TITLE = 200
MAX_TEXT = 4000
MAX_ID = 64

_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,%d}" % (MAX_ID - 1))
_LOCK = threading.Lock()

# Turkish folds to ASCII explicitly. str.lower() alone maps "İ" to "i" + U+0307
# and leaves ı/ş/ğ outside [a-z], so "İzleme Akışı" would slug to "izleme-ak".
_TR_ASCII = str.maketrans({
    "ı": "i", "İ": "i", "I": "i",
    "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})


# --- Small helpers ----------------------------------------------------------
def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fold(text):
    """ASCII-fold for slugs and search, Turkish-safe."""
    return str(text or "").translate(_TR_ASCII).lower()


def _clean(value, label, limit, required=True):
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise ValueError(f"{label} is required")
    if len(text) > limit:
        raise ValueError(f"{label} must be at most {limit} characters")
    return text


def _one_of(value, allowed, label):
    if value not in allowed:
        raise ValueError(f"{label} must be one of: {', '.join(allowed)}")
    return value


def _norm_priority(value):
    """Accept p0/P0/' p0 ', store uppercase. None means the default."""
    if value is None:
        return TASK_PRIORITY_DEFAULT
    return _one_of(str(value).strip().upper(), TASK_PRIORITIES, "priority")


def slugify(title):
    """Title -> url/filename-safe id. Raises when nothing usable survives."""
    slug = re.sub(r"[^a-z0-9]+", "-", _fold(title)).strip("-")[:MAX_ID].strip("-")
    if not _ID_RE.fullmatch(slug):
        raise ValueError(f"title has no usable characters for an id: {title!r}")
    return slug


# --- Path resolution (the only one) -----------------------------------------
def _arch_path(arch_id):
    """id -> file path. Every read, write and delete goes through here."""
    if not isinstance(arch_id, str) or not _ID_RE.fullmatch(arch_id):
        raise ValueError(f"invalid architecture id: {arch_id!r}")
    base = ARCH_DIR.expanduser().resolve()
    path = (base / f"{arch_id}.json").resolve()
    if path.parent != base:
        raise PermissionError(f"outside the architecture store: {path}")
    return path


def _unique_id(base_slug):
    """First free id for a slug: foo, foo-2, foo-3… Caller must hold _LOCK."""
    candidate, n = base_slug, 1
    while _arch_path(candidate).exists():
        n += 1
        suffix = f"-{n}"
        candidate = base_slug[:MAX_ID - len(suffix)].rstrip("-") + suffix
    return candidate


# --- Storage ----------------------------------------------------------------
def _parse(path):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path.name} is not valid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return doc


def _load(arch_id):
    path = _arch_path(arch_id)
    if not path.is_file():
        raise FileNotFoundError(f"no such architecture: {arch_id}")
    return _parse(path)


def _save(doc):
    """Write via a temp file in the same directory, then one atomic rename.

    A crash or a full disk mid-write must leave the previous version intact —
    losing an in-flight edit is recoverable, losing the architecture is not.
    """
    path = _arch_path(doc["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent),
        prefix=f".{path.stem}-", suffix=".tmp", delete=False,
    )
    try:
        with tmp:
            json.dump(doc, tmp, indent=2, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp.name, path)
    except BaseException:
        # Neither a clobbered original nor a stray temp file may survive.
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise
    return doc


# --- Derived state (computed on read, never stored) -------------------------
def _task_counts(tasks):
    counts = {"total": len(tasks), "todo": 0, "doing": 0, "blocked": 0, "done": 0}
    for task in tasks:
        status = task.get("status")
        if status in TASK_STATUSES:
            counts[status] += 1
    return counts


def _progress(counts):
    return (counts["done"] / counts["total"]) if counts["total"] else 0.0


def _overall_progress(order, phases, tasks):
    """How far through the *protocol* this architecture is.

    Counting finished tasks alone lies: one task, done, in phase 2 of 4 reads as
    100% complete when three phases have not been touched. Progress is phases
    behind you plus how far into the current one you are, over the whole chain —
    so it cannot reach 1.0 until the last phase is done.
    """
    if not order:
        return 0.0
    finished = sum(1 for p in order if (phases.get(p) or {}).get("status") in ("done", "skipped"))
    active = next((p for p in order if (phases.get(p) or {}).get("status") == "active"), None)
    fraction = 0.0
    if active:
        own = [t for t in tasks if t.get("phase") == active]
        if own:
            fraction = sum(1 for t in own if t.get("status") == "done") / len(own)
    value = (finished + fraction) / len(order)
    if finished < len(order):
        # Finishing the last phase's tasks is not the same as closing it. 100%
        # is reserved for a project with nothing left to mark, so it never
        # appears while a phase is still open.
        return min(value, 0.99)
    return min(1.0, value)


def phase_order(doc):
    """This architecture's own phases, in master protocol order.

    A template selects a subset, so the document — not the master catalog — is
    what says which phases exist here. Documents written before templates carry
    all 16, and fall back to exactly that.
    """
    stored = doc.get("phases")
    if not isinstance(stored, dict) or not stored:
        return list(PHASES)
    return [p for p in PHASES if p in stored]


def _derive(doc):
    """Return a copy carrying per-phase counts, progress and overall totals."""
    tasks = [t for t in (doc.get("tasks") or []) if isinstance(t, dict)]
    stored = doc.get("phases") or {}
    phases = {}
    for key in phase_order(doc):
        entry = stored.get(key) if isinstance(stored.get(key), dict) else {}
        counts = _task_counts([t for t in tasks if t.get("phase") == key])
        phases[key] = dict(entry, counts=counts, progress=_progress(counts))
    totals = _task_counts(tasks)
    return dict(doc, phases=phases, counts=totals,
                progress=_overall_progress(phase_order(doc), phases, tasks),
                task_progress=_progress(totals))


# --- Repo binding -----------------------------------------------------------
def _default_resolve_repo(value):
    """Fallback guard. server.py injects its own `_resolve_repo` instead, so the
    repo picker and the writer share one rule and cannot drift."""
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"not a directory: {path}")
    return path


# --- Public API -------------------------------------------------------------
def templates():
    """The template catalog, as the API serves it."""
    results, warnings = arch_templates.catalog(ARCH_DIR)
    return {"results": [_with_playbook(t) for t in results],
            "warnings": warnings, "count": len(results),
            "default": DEFAULT_TEMPLATE_ID}


def get_template(template_id):
    template = arch_templates.get(ARCH_DIR, template_id)
    if template is None:
        raise FileNotFoundError(f"no such template: {template_id}")
    return _with_playbook(template)


def create_template(body):
    return _with_playbook(arch_templates.save_user(ARCH_DIR, body))


def delete_template(template_id):
    return arch_templates.delete_user(ARCH_DIR, template_id)


def _with_playbook(template):
    """Fill in a template's playbook from the global one when it declares none.

    Built-ins carry no playbook of their own on purpose: one correction to
    phase_playbook.json then reaches every template that uses that phase.
    """
    if template.get("playbook"):
        book = {k: v for k, v in template["playbook"].items() if k in template["phases"]}
    else:
        book = {}
    full = load_playbook()
    return dict(template, playbook={p: book.get(p, full[p]) for p in template["phases"]})


def create_architecture(title, repo, summary="", *, resolve_repo=None,
                        template_id=DEFAULT_TEMPLATE_ID):
    title = _clean(title, "title", MAX_TITLE)
    summary = _clean(summary, "summary", MAX_TEXT, required=False)
    if not str(repo or "").strip():
        raise ValueError("repo is required")
    template = arch_templates.get(ARCH_DIR, template_id or DEFAULT_TEMPLATE_ID)
    if template is None:
        raise ValueError(f"unknown template: {template_id!r}")
    template = _with_playbook(template)

    # Resolved outside the lock: the guard and project-id lookup both shell out
    # to git, and holding the store lock across a subprocess serializes writers
    # behind an unrelated timeout.
    resolved = (resolve_repo or _default_resolve_repo)(str(repo).strip())
    scope = project_id(cwd=str(resolved))
    base_slug = slugify(title)
    now = _now()

    with _LOCK:
        ARCH_DIR.expanduser().mkdir(parents=True, exist_ok=True)
        doc = {
            "schema_version": SCHEMA_VERSION,
            "id": _unique_id(base_slug),
            "title": title,
            "summary": summary,
            "repo": str(resolved),
            "project_id": scope,
            # Provenance, plus a copy of everything the template decided. Copied
            # rather than referenced: editing a template later must not silently
            # rewrite the shape of work already under way.
            "template_id": template["id"],
            "status": "active",
            "current_phase": template["phases"][0],
            "phases": {k: {"status": "pending", "note": "", "updated_at": None}
                       for k in template["phases"]},
            "loops": [dict(l) for l in template["loops"]],
            "playbook": {k: dict(v) for k, v in template["playbook"].items()},
            "tasks": [],
            "created_at": now,
            "updated_at": now,
        }
        for seed in template["seed_tasks"]:
            doc["tasks"].append({
                "id": _new_task_id(doc["tasks"]),
                "phase": _one_of(seed.get("phase"), template["phases"], "seed task phase"),
                "title": _clean(seed.get("title"), "seed task title", MAX_TITLE),
                "status": "todo",
                "priority": _norm_priority(seed.get("priority")),
                "notes": str(seed.get("notes") or ""),
                "created_at": now,
                "updated_at": now,
            })
        _save(doc)
    return _derive(doc)


PLAYBOOK_FILE = HERE / "phase_playbook.json"
_PLAYBOOK_EMPTY = {"agents": [], "skills": [], "rules": []}


def load_playbook():
    """Which agents/skills/rules each phase reaches for.

    Data, not code, so the mapping can be corrected without a release. Read on
    every call rather than cached at import: the file is tiny, and an editor
    saving it should be visible on the next refresh. A missing or broken file
    degrades to empty lists — the playbook is guidance, never a reason for the
    panel to fail to load.
    """
    try:
        raw = json.loads(PLAYBOOK_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    book = {}
    for key in PHASES:
        entry = raw.get(key) if isinstance(raw.get(key), dict) else {}
        book[key] = {
            "agents": [str(x) for x in (entry.get("agents") or [])],
            "skills": [str(x) for x in (entry.get("skills") or [])],
            "rules": [str(x) for x in (entry.get("rules") or [])],
            "note": str(entry.get("note") or ""),
        }
    return book


def read_architecture(arch_id):
    doc = _derive(_load(arch_id))
    # The playbook copied in at create time wins; documents predating templates
    # fall back to the global one, narrowed to the phases they actually have.
    if not isinstance(doc.get("playbook"), dict) or not doc["playbook"]:
        full = load_playbook()
        doc["playbook"] = {p: full[p] for p in phase_order(doc)}
    doc.setdefault("loops", [])
    doc.setdefault("template_id", DEFAULT_TEMPLATE_ID)
    doc.setdefault("auto_advance_log", [])
    doc["auto_advance_disabled"] = bool(doc.get("auto_advance_disabled"))
    doc["auto_chain"] = bool(doc.get("auto_chain"))
    doc["auto_advance_blockers"] = auto_advance_blockers(doc)
    doc["manual_phases"] = list(MANUAL_PHASES)
    return doc


def update_architecture(arch_id, **fields):
    """Update title/summary/status/current_phase, and/or one phase's state.

    `phase` takes {"key": <phase>, "status": …, "note": …}.
    """
    # Validate everything before touching the store, so a bad request never
    # leaves a half-applied document behind.
    clean = {}
    if "title" in fields:
        clean["title"] = _clean(fields["title"], "title", MAX_TITLE)
    if "summary" in fields:
        clean["summary"] = _clean(fields["summary"], "summary", MAX_TEXT, required=False)
    if "status" in fields:
        clean["status"] = _one_of(fields["status"], ARCH_STATUSES, "status")
    if "current_phase" in fields:
        clean["current_phase"] = _one_of(fields["current_phase"], PHASES, "current_phase")

    phase_patch = None
    phase = fields.get("phase")
    if phase is not None:
        if not isinstance(phase, dict):
            raise ValueError("phase must be an object")
        phase_patch = {"key": _one_of(phase.get("key"), PHASES, "phase.key")}
        if "status" in phase:
            phase_patch["status"] = _one_of(phase["status"], PHASE_STATUSES, "phase.status")
        if "note" in phase:
            phase_patch["note"] = _clean(phase["note"], "phase.note", MAX_TEXT, required=False)

    now = _now()
    with _LOCK:
        doc = _load(arch_id)
        doc.update(clean)
        if phase_patch:
            key = phase_patch["key"]
            stored = doc.setdefault("phases", {})
            entry = dict(stored.get(key) or {})
            for field in ("status", "note"):
                if field in phase_patch:
                    entry[field] = phase_patch[field]
            entry["updated_at"] = now
            stored[key] = entry
        doc["updated_at"] = now
        _save(doc)
    return _derive(doc)


def auto_advance_blockers(doc):
    """Why this architecture will not advance itself right now.

    Every gate is named rather than folded into one boolean, so the panel can
    say which one is holding — "why didn't it move" is the first question anyone
    asks of an automation that didn't fire.
    """
    reasons = []
    if doc.get("auto_advance_disabled"):
        reasons.append("auto-advance is paused for this architecture")

    order = phase_order(doc)
    phase = doc.get("current_phase")
    if phase not in order:
        return reasons + ["current phase is not one of this architecture's phases"]
    if phase in MANUAL_PHASES:
        reasons.append(f"`{phase}` is a manual phase — a person advances it")
    if order.index(phase) >= len(order) - 1:
        reasons.append("this is the last phase")

    own = [t for t in (doc.get("tasks") or []) if t.get("phase") == phase]
    if not own:
        reasons.append("the phase has no tasks")
    else:
        if any(t.get("status") == "blocked" for t in own):
            reasons.append("a task in this phase is blocked")
        open_tasks = [t for t in own if t.get("status") != "done"]
        if open_tasks:
            reasons.append(f"{len(open_tasks)} task(s) still open")
    return reasons


def _auto_advance(doc):
    """Move a finished phase on, in place. Returns the move, or None.

    Mutates the document the caller is about to save rather than writing again:
    the task update and the advance it caused land in **one** atomic write, so
    two task reports finishing at once cannot both see "all done" and advance
    twice.
    """
    if auto_advance_blockers(doc):
        return None
    order = phase_order(doc)
    phase = doc["current_phase"]
    nxt = order[order.index(phase) + 1]
    now = _now()

    phases = doc.setdefault("phases", {})
    phases[phase] = dict(phases.get(phase) or {}, status="done", updated_at=now)
    phases[nxt] = dict(phases.get(nxt) or {}, status="active", updated_at=now)
    doc["current_phase"] = nxt

    move = {"from": phase, "to": nxt, "kind": "advance", "triggered_at": now,
            "tasks_completed": len([t for t in (doc.get("tasks") or [])
                                    if t.get("phase") == phase and t.get("status") == "done"])}
    log = doc.setdefault("auto_advance_log", [])
    log.append(move)
    del log[:-AUTO_ADVANCE_LOG_LIMIT]
    return move


def _remember_advance(doc, move):
    """Record a completed phase in the shared brain.

    Called after the lock is released and handed to a daemon thread: a mem0
    write loads a model and takes seconds, and every other writer would queue
    behind it if this happened inline.
    """
    if not move:
        return
    try:
        arch_memory.remember_async(doc.get("project_id"),
                                   arch_memory.advance_summary(doc.get("project_id"), move))
    except Exception:
        pass


def _recompute_current_phase(phases):
    """Where the work sits: the active phase, else the first unfinished one.

    Derived rather than remembered — a stored pointer goes stale the moment a
    phase is marked done from anywhere else.
    """
    order = [p for p in PHASES if p in phases] or list(PHASES)
    for key in order:
        if (phases.get(key) or {}).get("status") == "active":
            return key
    for key in order:
        if (phases.get(key) or {}).get("status", "pending") == "pending":
            return key
    return order[-1]


def bulk_update_workflow(arch_id, phases_patch):
    """Set several phases in one atomic write.

    The phase modal moves more than one phase at a time ("mark complete" also
    activates the next one). Sending that as N separate PATCHes would race and
    could leave the workflow half-moved; this is one lock, one disk write.

    phases_patch: {"discover": {"status": "done"}, "spec": {"status": "active"}}
    """
    if not isinstance(phases_patch, dict):
        raise ValueError("phases must be an object")
    if not phases_patch:
        raise ValueError("at least one phase is required")

    # Validate every phase before writing any: a bad key halfway through must
    # not leave the first half applied.
    clean = {}
    for key, patch in phases_patch.items():
        _one_of(key, PHASES, "phase key")
        if not isinstance(patch, dict):
            raise ValueError(f"phase {key} must be an object")
        entry = {}
        if "status" in patch:
            entry["status"] = _one_of(patch["status"], PHASE_STATUSES, f"phase {key} status")
        if "note" in patch:
            entry["note"] = _clean(patch["note"], f"phase {key} note", MAX_TEXT, required=False)
        clean[key] = entry

    now = _now()
    with _LOCK:
        doc = _load(arch_id)
        own = phase_order(doc)
        before = doc.get("current_phase")
        for key in clean:
            _one_of(key, own, "phase key (this architecture's template)")
        phases = doc.setdefault("phases", {})
        for key, entry in clean.items():
            merged = dict(phases.get(key) or {}, **entry)
            merged["updated_at"] = now
            phases[key] = merged
        doc["current_phase"] = _recompute_current_phase(phases)
        # Going back is a real event. Without this the log showed the same
        # advance twice with no explanation of how the phase got back there.
        after = doc["current_phase"]
        if before in own and after in own and own.index(after) < own.index(before):
            log = doc.setdefault("auto_advance_log", [])
            log.append({"from": before, "to": after, "kind": "rollback",
                        "triggered_at": now, "tasks_completed": 0})
            del log[:-AUTO_ADVANCE_LOG_LIMIT]
        doc["updated_at"] = now
        _save(doc)
    return _derive(doc)


def set_auto_chain(arch_id, on):
    """Let this architecture start its own next run, or stop it doing so.

    Off by default and deliberately separate from auto-advance: moving a phase
    is bookkeeping, launching an agent at a repo unattended is not.
    """
    with _LOCK:
        doc = _load(arch_id)
        doc["auto_chain"] = bool(on)
        doc["updated_at"] = _now()
        _save(doc)
    return read_architecture(arch_id)


def set_auto_advance(arch_id, enabled):
    """Pause or resume auto-advance for one architecture.

    Resuming re-evaluates immediately. The gate is otherwise only checked when a
    task is written, so a phase that finished while paused — or before this
    feature existed — would sit complete-but-not-advanced until something
    unrelated happened to touch it.
    """
    advanced = None
    with _LOCK:
        doc = _load(arch_id)
        doc["auto_advance_disabled"] = not bool(enabled)
        doc["updated_at"] = _now()
        if enabled:
            advanced = _auto_advance(doc)
        _save(doc)
    _remember_advance(doc, advanced)
    return dict(read_architecture(arch_id), auto_advanced=advanced)


def delete_architecture(arch_id):
    path = _arch_path(arch_id)
    with _LOCK:
        if not path.is_file():
            raise FileNotFoundError(f"no such architecture: {arch_id}")
        path.unlink()
    return {"ok": True, "id": arch_id}


def _new_task_id(tasks):
    seen = {t.get("id") for t in tasks}
    while True:
        candidate = "t-" + secrets.token_hex(3)
        if candidate not in seen:
            return candidate


def add_task(arch_id, phase, title, priority=TASK_PRIORITY_DEFAULT):
    phase = _one_of(phase, PHASES, "phase")
    title = _clean(title, "task title", MAX_TITLE)
    priority = _norm_priority(priority)
    now = _now()
    with _LOCK:
        doc = _load(arch_id)
        # Which phases exist depends on the document's template, so this half of
        # the check can only happen once the document is in hand.
        _one_of(phase, phase_order(doc), "phase (this architecture's template)")
        tasks = doc.setdefault("tasks", [])
        tasks.append({
            "id": _new_task_id(tasks),
            "phase": phase,
            "title": title,
            "status": "todo",
            "priority": priority,
            "notes": "",
            "created_at": now,
            "updated_at": now,
        })
        doc["updated_at"] = now
        _save(doc)
    return _derive(doc)


def update_task(arch_id, task_id, **fields):
    clean = {}
    if "title" in fields:
        clean["title"] = _clean(fields["title"], "task title", MAX_TITLE)
    if "status" in fields:
        clean["status"] = _one_of(fields["status"], TASK_STATUSES, "task status")
    if "phase" in fields:
        clean["phase"] = _one_of(fields["phase"], PHASES, "phase")
    if "priority" in fields:
        clean["priority"] = _norm_priority(fields["priority"])
    if "notes" in fields:
        clean["notes"] = _clean(fields["notes"], "notes", MAX_TEXT, required=False)

    now = _now()
    with _LOCK:
        doc = _load(arch_id)
        if "phase" in clean:
            _one_of(clean["phase"], phase_order(doc), "phase (this architecture's template)")
        task = next((t for t in doc.get("tasks") or []
                     if isinstance(t, dict) and t.get("id") == task_id), None)
        if task is None:
            raise FileNotFoundError(f"no such task: {task_id}")
        task.update(clean)
        task["updated_at"] = now
        doc["updated_at"] = now
        # Evaluated before the write, so the report and the advance it caused
        # are one save. The caller gets told what happened rather than having to
        # diff the document to notice.
        advanced = _auto_advance(doc)
        _save(doc)
    _remember_advance(doc, advanced)
    return dict(_derive(doc), auto_advanced=advanced)


def delete_task(arch_id, task_id):
    now = _now()
    with _LOCK:
        doc = _load(arch_id)
        tasks = doc.get("tasks") or []
        remaining = [t for t in tasks if not (isinstance(t, dict) and t.get("id") == task_id)]
        if len(remaining) == len(tasks):
            raise FileNotFoundError(f"no such task: {task_id}")
        doc["tasks"] = remaining
        doc["updated_at"] = now
        # Deleting the last open task finishes a phase just as surely as
        # completing it does.
        advanced = _auto_advance(doc)
        _save(doc)
    _remember_advance(doc, advanced)
    return dict(_derive(doc), auto_advanced=advanced)


def _summary_row(doc):
    """List rows carry counts and phase states but not the task list itself."""
    derived = _derive(doc)
    row = {k: derived.get(k) for k in (
        "schema_version", "id", "title", "summary", "repo", "project_id",
        "status", "current_phase", "created_at", "updated_at",
    )}
    row["template_id"] = derived.get("template_id") or DEFAULT_TEMPLATE_ID
    row["counts"] = derived["counts"]
    row["progress"] = derived["progress"]
    row["loop_count"] = len(derived.get("loops") or [])
    # Enough for the list view to draw a mini phase rail without a second call.
    row["phase_status"] = {k: derived["phases"][k].get("status", "pending")
                           for k in phase_order(doc)}
    return row


def list_architectures(repo=None, status=None, q=None):
    """Summary rows, plus the repo tallies and phase catalog the UI needs."""
    base = ARCH_DIR.expanduser()
    docs, errors = [], []
    if base.is_dir():
        for path in sorted(base.glob("*.json")):
            try:
                docs.append(_parse(path))
            except ValueError as e:
                # One corrupt file must not blank the whole panel.
                errors.append(str(e))

    # Tallies come from every document, so filtering never empties the picker.
    repos = {}
    for doc in docs:
        key = doc.get("repo") or ""
        entry = repos.setdefault(
            key, {"repo": key, "project_id": doc.get("project_id"), "count": 0})
        entry["count"] += 1

    repo_key = str(Path(repo).expanduser().resolve()) if repo else None
    needle = _fold(q).strip() or None
    rows = []
    for doc in docs:
        if status and doc.get("status") != status:
            continue
        if repo_key:
            # An empty repo must not resolve to the cwd — Path("").resolve() does,
            # which would make a repo-filtered list match documents bound to nothing.
            if not doc.get("repo"):
                continue
            try:
                if str(Path(doc["repo"]).expanduser().resolve()) != repo_key:
                    continue
            except OSError:
                continue
        if needle and needle not in _fold(f"{doc.get('title', '')} {doc.get('summary', '')}"):
            continue
        rows.append(_summary_row(doc))

    rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return {
        "results": rows,
        "count": len(rows),
        "phases": list(PHASES),
        "repos": sorted(repos.values(), key=lambda r: r["repo"]),
        "statuses": list(ARCH_STATUSES),
        "task_statuses": list(TASK_STATUSES),
        "phase_statuses": list(PHASE_STATUSES),
        "task_priorities": list(TASK_PRIORITIES),
        "task_priority_default": TASK_PRIORITY_DEFAULT,
        "errors": errors,
    }


def count_architectures():
    """Cheap count for the Overview tile — no parsing."""
    base = ARCH_DIR.expanduser()
    if not base.is_dir():
        return 0
    return sum(1 for _ in base.glob("*.json"))
