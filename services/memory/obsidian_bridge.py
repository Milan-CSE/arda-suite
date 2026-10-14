#!/usr/bin/env python3
"""
Obsidian Knowledge Vault Bridge for mem0-local.
Bi-directional integration between mem0-local (vector/graph) and Obsidian (Markdown/Wikilinks):
  1. Exports memories, entities, and rules into an Obsidian Vault with YAML frontmatter.
  2. Generates bidirectional [[Wikilinks]] so Obsidian Graph View visualizes the AI brain.
  3. Provides Human-in-the-loop curation (human notes in vault can sync back to mem0).
"""

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

MEM0_ROOT = os.path.dirname(os.path.abspath(__file__))
if MEM0_ROOT not in sys.path:
    sys.path.insert(0, MEM0_ROOT)

import graph_store
import rules as rules_store

DEFAULT_VAULT_DIR = os.environ.get("MEM0_OBSIDIAN_VAULT", os.path.expanduser("~/mem0-vault"))


def sanitize_filename(name: str) -> str:
    """Make string safe for filesystem filename."""
    clean = re.sub(r'[/\\:*?"<>|]', "_", name).strip()
    return clean[:100] or "unnamed"


def format_memory_note(item: Dict[str, Any], entity_links: List[str] = None) -> str:
    """Render a memory item into Obsidian Markdown with frontmatter and wikilinks."""
    mid = str(item.get("id") or "")
    text = str(item.get("memory") or "").strip()
    scope = str(item.get("run_id") or "personal")
    agent = str(item.get("agent_id") or "user")
    status = str(item.get("status") or "active")
    created_at = str(item.get("created_at") or datetime.datetime.now().isoformat())

    links = entity_links or []
    # Also extract backticked code terms as inline wikilinks
    code_terms = re.findall(r"`([A-Za-z0-9_\-\.]{3,})`", text)
    for ct in code_terms:
        if ct not in links and len(ct) < 40:
            links.append(ct)

    tags = ["memory", f"scope/{scope}", f"status/{status}"]
    if "[GOTCHA]" in text:
        tags.append("type/gotcha")
    if "[ADD]" in text:
        tags.append("type/decision")

    frontmatter = [
        "---",
        f'id: "{mid}"',
        f'scope: "{scope}"',
        f'agent: "{agent}"',
        f'status: "{status}"',
        f'created_at: "{created_at}"',
        "tags:",
    ]
    for tag in tags:
        frontmatter.append(f"  - {tag}")
    frontmatter.append("---\n")

    # Body
    title = text.split("\n")[0][:80]
    body = [
        f"# {title}\n",
        f"> {text}\n",
        "## Metadata",
        f"- **Scope:** `[[{scope}]]`",
        f"- **Agent:** `{agent}`",
        f"- **Status:** `{status}`",
        f"- **Timestamp:** {created_at[:19].replace('T', ' ')}\n",
    ]

    if links:
        body.append("## Connected Entities & Concepts")
        for link in links:
            body.append(f"- [[{link}]]")
        body.append("")

    body.append("---\n*Part of [[Index|Shared Brain]] • Synced via `mem0-local`*")
    return "\n".join(frontmatter) + "\n".join(body) + "\n"


def format_rule_note(rule: Dict[str, Any]) -> str:
    """Render an architectural rule into an Obsidian Markdown note."""
    rid = rule.get("id") or "unnamed_rule"
    scope = rule.get("scope") or "personal"
    desc = rule.get("rule") or ""
    paths = rule.get("paths") or []
    severity = rule.get("severity") or "normal"
    created_at = rule.get("created_at") or ""

    frontmatter = [
        "---",
        f'rule_id: "{rid}"',
        f'scope: "{scope}"',
        f'severity: "{severity}"',
        "tags:",
        "  - rule",
        f"  - scope/{scope}",
        f"  - severity/{severity}",
        "---\n",
    ]

    body = [
        f"# Rule: {rid}\n",
        f"> **Enforced Rule:** {desc}\n",
        "## Scope & Target Paths",
        f"- **Scope:** `[[{scope}]]`",
        f"- **Severity:** `{severity.upper()}`",
    ]
    if paths:
        body.append("- **Applies to paths:**")
        for p in paths:
            body.append(f"  - `{p}`")
    body.append(f"- **Created:** {created_at[:19].replace('T', ' ')}\n")
    body.append("---\n*Part of [[Index|Shared Brain Rules]]*")

    return "\n".join(frontmatter) + "\n".join(body) + "\n"


def format_index_note(scopes: List[str], counts: Dict[str, int]) -> str:
    """Generate main Index.md for the Obsidian vault."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "---",
        "title: Central Brain Vault Index",
        "tags:",
        "  - dashboard",
        "  - index",
        "---\n",
        "# 🧠 Central Brain — Obsidian Knowledge Vault\n",
        f"> Live interactive knowledge graph and memory archive synced at `{now}`.\n",
        "## 📊 Quick Overview",
        f"- **Active Memories:** `{counts.get('active', 0)}`",
        f"- **Superseded / Archived:** `{counts.get('superseded', 0)}`",
        f"- **Architectural Rules:** `{counts.get('rules', 0)}`",
        f"- **Graph Entities:** `{counts.get('entities', 0)}`\n",
        "## 📂 Scopes & Projects",
    ]
    for s in sorted(scopes):
        lines.append(f"- [[{s}]] (`Memories/{s}/`)")

    lines.extend([
        "\n## 🧭 Visual Graph Exploration",
        "- Open **Graph View** (`Ctrl+G` / `Cmd+G`) in Obsidian to explore memory connections and clusters.",
        "- Filter by tags: `#type/gotcha`, `#type/decision`, `#scope/personal`.",
        "\n---",
        "*Bi-directional brain sync provided by `mem0-local`.*",
    ])
    return "\n".join(lines) + "\n"


def export_vault(vault_dir: Optional[str] = None, scope: Optional[str] = None) -> Dict[str, Any]:
    """Export all memories, rules, and graph entities to the Obsidian vault directory."""
    root = Path(vault_dir or DEFAULT_VAULT_DIR)
    root.mkdir(parents=True, exist_ok=True)

    mem_dir = root / "Memories"
    archive_dir = root / "Archive"
    rules_dir = root / "Rules"
    entities_dir = root / "Entities"
    inbox_dir = root / "Inbox"

    for d in (mem_dir, archive_dir, rules_dir, entities_dir, inbox_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 1. Fetch memories from mem0 scroll
    import mem as mem_cli
    items = mem_cli._scroll(scope=scope or "", include_superseded=True)

    scopes_found = set()
    active_count = 0
    superseded_count = 0

    for item in items:
        mid = str(item.get("id") or "")
        item_scope = str(item.get("run_id") or "personal")
        scopes_found.add(item_scope)
        status = str(item.get("status") or "active")

        # Discover graph entity links for this memory
        links = []
        try:
            nbrs = graph_store.get_neighbors(mid, direction="both")
            for nb in nbrs:
                target_id = nb.get("neighbor_id", "")
                if target_id and not target_id.startswith("scope:"):
                    label = nb.get("label") or target_id
                    links.append(sanitize_filename(label))
        except Exception:
            pass

        note_content = format_memory_note(item, entity_links=links)
        safe_name = f"mem_{mid[:8]}_{sanitize_filename(item.get('memory', '')[:30])}.md"

        if status == "superseded":
            superseded_count += 1
            target_file = archive_dir / safe_name
        else:
            active_count += 1
            scope_dir = mem_dir / item_scope
            scope_dir.mkdir(parents=True, exist_ok=True)
            target_file = scope_dir / safe_name

        target_file.write_text(note_content, encoding="utf-8")

    # 2. Export Rules
    all_rules = rules_store.list_rules(scope=scope or "")
    for r in all_rules:
        rid = r.get("id") or "unnamed"
        rule_content = format_rule_note(r)
        rule_file = rules_dir / f"rule_{sanitize_filename(rid)}.md"
        rule_file.write_text(rule_content, encoding="utf-8")

    # 3. Export Scope Hub Notes
    for sc in scopes_found:
        sc_file = root / f"{sanitize_filename(sc)}.md"
        sc_content = f"# Scope: {sc}\n\nAll memories and rules associated with project `{sc}`.\n\n- [[Index|Back to Index]]\n"
        sc_file.write_text(sc_content, encoding="utf-8")

    # 4. Generate Index.md
    counts = {
        "active": active_count,
        "superseded": superseded_count,
        "rules": len(all_rules),
        "entities": 0,
    }
    index_file = root / "Index.md"
    index_file.write_text(format_index_note(list(scopes_found), counts), encoding="utf-8")

    summary = {
        "vault_path": str(root),
        "active_memories": active_count,
        "archived_memories": superseded_count,
        "rules_exported": len(all_rules),
        "scopes": list(scopes_found),
    }
    return summary


def import_inbox(vault_dir: Optional[str] = None) -> Dict[str, Any]:
    """Scan vault's Inbox/ directory for human-written notes and ingest into mem0."""
    root = Path(vault_dir or DEFAULT_VAULT_DIR)
    inbox = root / "Inbox"
    if not inbox.exists():
        return {"imported": 0, "notes": []}

    md_files = list(inbox.glob("*.md"))
    if not md_files:
        return {"imported": 0, "notes": []}

    from config import get_memory
    mem = get_memory()
    owner = os.environ.get("MEM0_OWNER", "default")
    imported_list = []

    for f in md_files:
        try:
            content = f.read_text(encoding="utf-8").strip()
            if not content:
                continue

            # Parse frontmatter if present
            scope = "personal"
            text = content
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    fm_text = parts[1]
                    text = parts[2].strip()
                    for line in fm_text.splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            k = k.strip().lower()
                            v = v.strip().strip('"').strip("'")
                            if k == "scope" and v:
                                scope = v

            # Clean markdown headings from text if needed
            clean_text = re.sub(r"^#+\s+.*$", "", text, flags=re.MULTILINE).strip()
            if not clean_text:
                clean_text = text

            res = mem.add(
                clean_text,
                user_id=owner,
                run_id=scope,
                agent_id="obsidian-user",
                metadata={"source": "obsidian_inbox"},
                infer=True,
            )

            # Move imported note from Inbox/ to Memories/<scope>/
            dest_dir = root / "Memories" / scope
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / f"user_{f.name}"
            f.replace(dest_file)

            imported_list.append({"file": f.name, "scope": scope, "results": res.get("results", [])})
        except Exception as ex:
            print(f"  ⚠️ Error importing {f.name}: {ex}", file=sys.stderr)

    return {"imported": len(imported_list), "notes": imported_list}


def get_vault_status(vault_dir: Optional[str] = None) -> Dict[str, Any]:
    """Check Obsidian Vault file counts and health."""
    root = Path(vault_dir or DEFAULT_VAULT_DIR)
    if not root.exists():
        return {"exists": False, "vault_path": str(root)}

    md_files = list(root.glob("**/*.md"))
    inbox_files = list((root / "Inbox").glob("*.md")) if (root / "Inbox").exists() else []
    return {
        "exists": True,
        "vault_path": str(root),
        "total_markdown_notes": len(md_files),
        "inbox_notes_pending": len(inbox_files),
        "has_index": (root / "Index.md").exists(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Obsidian Knowledge Vault Bridge for mem0-local")
    parser.add_argument("cmd", choices=["export", "import", "status"], default="export", nargs="?", help="command to run")
    parser.add_argument("--vault", default=None, help=f"vault root directory (default: {DEFAULT_VAULT_DIR})")
    parser.add_argument("--scope", default=None, help="export only specific scope")

    args = parser.parse_args()

    if args.cmd == "export":
        print(f"🔹 Exporting Shared Brain to Obsidian Vault at '{args.vault or DEFAULT_VAULT_DIR}'...")
        res = export_vault(vault_dir=args.vault, scope=args.scope)
        print(f"✅ Successfully exported:")
        print(f"   • Active Memories : {res['active_memories']}")
        print(f"   • Archived Notes  : {res['archived_memories']}")
        print(f"   • Enforced Rules  : {res['rules_exported']}")
        print(f"   • Scopes          : {', '.join(res['scopes'])}")
        print(f"\n📂 Vault Directory: {res['vault_path']}")
        print("💡 In Obsidian: 'Open folder as vault' -> select that path to see the interactive Graph View!")

    elif args.cmd == "import":
        print(f"🔹 Ingesting user notes from Obsidian Inbox at '{args.vault or DEFAULT_VAULT_DIR}/Inbox'...")
        res = import_inbox(vault_dir=args.vault)
        print(f"✅ Imported {res['imported']} note(s) into Central Brain.")
        for n in res.get("notes", []):
            print(f"   • {n['file']} -> scope: {n['scope']}")

    elif args.cmd == "status":
        st = get_vault_status(vault_dir=args.vault)
        print(json.dumps(st, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
