#!/usr/bin/env python3
"""Migrate ~/.mem0/architectures/*.json to the architectures.py schema (v1).

Written for the one document a parallel session left behind, but kept in the
repo because any other machine carrying the old shape needs the same conversion.

Two properties this script must have, and is tested for:

- **Idempotent.** Running it twice produces a byte-identical file. Nothing here
  stamps "now" — `updated_at` is preserved, never bumped — so a half-finished
  run can simply be re-run.
- **All or nothing per file.** The converted document is validated against the
  live `architectures` module's own enums *before* anything is written, and the
  write itself is atomic. A document that fails validation leaves the original
  untouched rather than landing half-converted.

Usage:
    bin/mem0-python dashboard/migrate_architectures.py [--dry-run] [--repo PATH]
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import architectures as arch  # noqa: E402
import arch_templates  # noqa: E402
import server  # noqa: E402

# Old vocabulary -> new. Both enums used "in_progress"; they mean different
# things and land on different words.
PHASE_STATUS_MAP = {"in_progress": "active"}
TASK_STATUS_MAP = {"in_progress": "doing"}
DEFAULT_TASK_PHASE = "build"      # the old file left every task unassigned
DEFAULT_REPO = "~/arda-suite/services/memory"


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def backup(arch_dir):
    """Copy every *.json into a timestamped sibling directory.

    Deliberately not `cp -r <dir> <dir>/.backup`: copying a directory into
    itself is how you get a recursive mess. Only the documents are copied.
    """
    dest = arch_dir / f".backup-{_stamp()}"
    dest.mkdir(parents=True, exist_ok=False)
    copied = []
    for path in sorted(arch_dir.glob("*.json")):
        shutil.copy2(path, dest / path.name)
        copied.append(path.name)
    return dest, copied


def _context_files(value):
    """['/a/b.md', …] or [{'path': …}, …] -> [{'path', 'exists'}], both idempotent.

    The flag is recomputed every run: it describes the filesystem now, not what
    it looked like when the entry was written.
    """
    out = []
    for item in value or []:
        path = item.get("path") if isinstance(item, dict) else item
        if not path:
            continue
        out.append({"path": str(path), "exists": Path(str(path)).expanduser().is_file()})
    return out


def _phases(doc):
    """`workflow` (old) or `phases` (new) -> the full 16-phase map."""
    source = doc.get("phases") if isinstance(doc.get("phases"), dict) else doc.get("workflow")
    source = source if isinstance(source, dict) else {}
    lowered = {str(k).lower(): v for k, v in source.items()}
    phases = {}
    for key in arch.PHASES:
        entry = lowered.get(key)
        entry = dict(entry) if isinstance(entry, dict) else {}
        status = str(entry.get("status") or "pending")
        entry["status"] = PHASE_STATUS_MAP.get(status, status)
        entry.setdefault("note", "")
        entry.setdefault("updated_at", None)
        phases[key] = entry
    return phases


def _tasks(doc, created_at, updated_at):
    tasks = []
    for index, raw in enumerate(doc.get("tasks") or []):
        if not isinstance(raw, dict):
            continue
        task = dict(raw)
        status = str(task.get("status") or "todo")
        task["status"] = TASK_STATUS_MAP.get(status, status)
        phase = str(task.get("phase") or "").lower()
        task["phase"] = phase if phase in arch.PHASES else DEFAULT_TASK_PHASE
        task["priority"] = arch._norm_priority(task.get("priority"))
        task["id"] = task.get("id") or f"task-{index:03d}"
        task.setdefault("title", "")
        task.setdefault("notes", "")
        task["created_at"] = task.get("created_at") or created_at
        task["updated_at"] = task.get("updated_at") or updated_at
        tasks.append(task)
    return tasks


def migrate_doc(doc, repo, project_id):
    """Old document -> v1 document. Pure: no clock, no disk, no mutation."""
    out = dict(doc)
    created_at = out.get("created_at") or ""
    updated_at = out.get("updated_at") or created_at

    # Renames — only when the new key is absent, so a second run is a no-op.
    if "title" not in out and "name" in out:
        out["title"] = out["name"]
    if "summary" not in out and "description" in out:
        out["summary"] = out["description"]
    out.pop("name", None)
    out.pop("description", None)

    out["phases"] = _phases(out)
    out.pop("workflow", None)

    current = str(out.get("current_phase") or "").lower()
    out["current_phase"] = current if current in arch.PHASES else arch.PHASES[0]

    status = str(out.get("status") or "active")
    out["status"] = status if status in arch.ARCH_STATUSES else "active"

    out["tasks"] = _tasks(out, created_at, updated_at)
    out["context_files"] = _context_files(out.get("context_files"))

    out["schema_version"] = arch.SCHEMA_VERSION
    out["repo"] = str(repo)
    out["project_id"] = project_id
    out["title"] = out.get("title") or out.get("id") or "untitled"
    out["summary"] = out.get("summary") or ""
    out["created_at"] = created_at
    out["updated_at"] = updated_at          # never bumped — see idempotency

    out.pop("progress", None)               # derived on read, never stored

    # Templates arrived after this document did. Anything predating them ran the
    # whole protocol, so full-16 is its true provenance, and its loops were the
    # four the flowchart used to hardcode — now stored as data on the document.
    out.setdefault("template_id", arch.DEFAULT_TEMPLATE_ID)
    if not out.get("loops"):
        template = arch_templates.get(arch.ARCH_DIR, out["template_id"])
        out["loops"] = [dict(l) for l in (template or {}).get("loops", [])]
    return out


def validate(doc):
    """Refuse to write anything the live module would later reject."""
    problems = []
    if doc.get("schema_version") != arch.SCHEMA_VERSION:
        problems.append("schema_version missing")
    if list(doc.get("phases") or {}) != list(arch.PHASES):
        problems.append("phases are not the 16 protocol phases in order")
    if not doc.get("template_id"):
        problems.append("template_id missing")
    for loop in doc.get("loops") or []:
        for end in ("from", "to"):
            if loop.get(end) not in (doc.get("phases") or {}):
                problems.append(f"loop {end} {loop.get(end)!r} is not a phase of this document")
    for key, entry in (doc.get("phases") or {}).items():
        if entry.get("status") not in arch.PHASE_STATUSES:
            problems.append(f"phase {key}: bad status {entry.get('status')!r}")
    if doc.get("current_phase") not in arch.PHASES:
        problems.append(f"bad current_phase {doc.get('current_phase')!r}")
    if doc.get("status") not in arch.ARCH_STATUSES:
        problems.append(f"bad status {doc.get('status')!r}")
    for task in doc.get("tasks") or []:
        if task.get("status") not in arch.TASK_STATUSES:
            problems.append(f"task {task.get('id')}: bad status {task.get('status')!r}")
        if task.get("phase") not in arch.PHASES:
            problems.append(f"task {task.get('id')}: bad phase {task.get('phase')!r}")
        if task.get("priority") not in arch.TASK_PRIORITIES:
            problems.append(f"task {task.get('id')}: bad priority {task.get('priority')!r}")
    if "progress" in doc:
        problems.append("stored progress survived")
    return problems


def _write_atomic(path, doc):
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
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="repo to bind documents to")
    ap.add_argument("--no-backup", action="store_true", help="skip the backup copy")
    args = ap.parse_args(argv)

    arch_dir = arch.ARCH_DIR.expanduser()
    if not arch_dir.is_dir():
        print(f"nothing to migrate: {arch_dir} does not exist")
        return 0

    # The repo must clear the same guard the API uses, or the migrated document
    # would be one the dashboard itself refuses to create.
    try:
        repo = server._resolve_repo(str(Path(args.repo).expanduser()))
    except Exception as e:
        print(f"ABORT: repo {args.repo!r} fails _resolve_repo: {e}", file=sys.stderr)
        return 2
    project_id = arch.project_id(cwd=str(repo))
    print(f"repo       : {repo}")
    print(f"project_id : {project_id}")

    documents = sorted(arch_dir.glob("*.json"))
    if not documents:
        print("nothing to migrate: no *.json files")
        return 0

    if args.no_backup or args.dry_run:
        print("backup     : skipped")
    else:
        dest, copied = backup(arch_dir)
        print(f"backup     : {dest}  ({len(copied)} file(s): {', '.join(copied)})")

    failed = 0
    for path in documents:
        try:
            before = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"  SKIP {path.name}: not valid JSON ({e})", file=sys.stderr)
            failed += 1
            continue
        after = migrate_doc(before, repo, project_id)
        problems = validate(after)
        if problems:
            print(f"  FAIL {path.name}: " + "; ".join(problems), file=sys.stderr)
            failed += 1
            continue
        changed = after != before
        if args.dry_run:
            print(f"  {'WOULD CHANGE' if changed else 'already current'} {path.name}")
            continue
        if changed:
            _write_atomic(path, after)
            print(f"  migrated {path.name}")
        else:
            print(f"  already current {path.name}   (idempotent no-op)")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
