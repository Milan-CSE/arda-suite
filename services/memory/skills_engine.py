#!/usr/bin/env python3
"""
Standard Skill Runtime & Manager for mem0-local / Agent Harness.
Parses, discovers, matches, and renders SKILL.md packages.
Conforms to 2026 Claude Code / Agentic SKILL.md specifications.
Stdlib-first: zero external dependencies required for core operations.
"""

import os
import re
from pathlib import Path

DEFAULT_PROJECT_SKILLS = [
    Path(__file__).parent / "skills",
    Path(__file__).parent / ".claude" / "skills",
]
DEFAULT_GLOBAL_SKILLS = [
    Path(os.path.expanduser("~/.claude/skills")),
    Path(os.path.expanduser("~/.mem0/skills")),
]


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML-style frontmatter from markdown content without requiring PyYAML."""
    metadata = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            raw_meta = parts[1]
            body = parts[2].strip()
            # Simple key: value line parser
            current_key = None
            for line in raw_meta.splitlines():
                line = line.rstrip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line and not line.startswith(" ") and not line.startswith("\t"):
                    key, val = line.split(":", 1)
                    current_key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if val:
                        metadata[current_key] = val
                    else:
                        metadata[current_key] = []
                elif current_key and (line.startswith("  - ") or line.startswith("    - ")):
                    val = line.split("-", 1)[1].strip().strip('"').strip("'")
                    if isinstance(metadata.get(current_key), list):
                        metadata[current_key].append(val)
    return metadata, body


def load_skill_from_dir(skill_dir: Path) -> dict | None:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        content = skill_md.read_text(encoding="utf-8", errors="replace")
        meta, instructions = parse_frontmatter(content)
        name = meta.get("name") or skill_dir.name
        desc = meta.get("description") or ""
        triggers = meta.get("triggers") or []
        if isinstance(triggers, str):
            triggers = [t.strip() for t in triggers.split(",") if t.strip()]

        scripts = []
        scripts_dir = skill_dir / "scripts"
        if scripts_dir.is_dir():
            scripts = [s.name for s in scripts_dir.iterdir() if s.is_file()]

        references = []
        ref_dir = skill_dir / "references"
        if ref_dir.is_dir():
            references = [r.name for r in ref_dir.iterdir() if r.is_file()]

        return {
            "name": name,
            "path": str(skill_dir),
            "description": desc,
            "triggers": triggers,
            "instructions": instructions,
            "scripts": scripts,
            "references": references,
            "metadata": meta,
        }
    except Exception:
        return None


def discover_skills(search_dirs: list[Path] = None) -> list[dict]:
    dirs = search_dirs if search_dirs is not None else (DEFAULT_PROJECT_SKILLS + DEFAULT_GLOBAL_SKILLS)
    found = {}
    for base in dirs:
        if not base.is_dir():
            continue
        try:
            for child in base.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    skill = load_skill_from_dir(child)
                    if skill and skill["name"] not in found:
                        found[skill["name"]] = skill
        except Exception:
            continue
    return sorted(found.values(), key=lambda s: s["name"])


def match_skills_for_task(prompt: str, file_paths: list[str] = None, skills: list[dict] = None) -> list[dict]:
    # Support positional invocation match_skills(prompt, skills)
    if isinstance(file_paths, list) and file_paths and isinstance(file_paths[0], dict):
        skills = file_paths
        file_paths = None

    if skills is None:
        skills = discover_skills()
    matched = []
    p_lower = (prompt or "").lower()
    files = [f.lower() for f in (file_paths or [])]


    for s in skills:
        name_lower = s["name"].lower()
        desc_lower = s["description"].lower()
        score = 0

        # Exact skill name mentioned
        if name_lower in p_lower:
            score += 10

        # Match triggers
        for trig in s.get("triggers", []):
            t_low = trig.lower()
            if t_low in p_lower:
                score += 5
            for fp in files:
                if t_low in fp:
                    score += 3

        # Match description keywords
        words = re.findall(r"\w{4,}", desc_lower)
        for w in words:
            if w in p_lower:
                score += 1

        if score > 0:
            matched.append((score, s))

    matched.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in matched]


match_skills = match_skills_for_task



def render_skills_prompt(skills: list[dict]) -> str:
    if not skills:
        return ""
    sections = [
        "## Active Skills & Procedural Guidance",
        "Follow the procedural requirements of the following matched skills:",
    ]
    for s in skills:
        sections.append(f"\n### Skill: {s['name']}")
        if s.get("description"):
            sections.append(f"> {s['description']}\n")
        sections.append(s.get("instructions", ""))
        if s.get("scripts"):
            sections.append(f"*Available helper scripts:* {', '.join(s['scripts'])}")
    return "\n".join(sections)


def create_skill(name: str, description: str, instructions: str, target_dir: Path = None) -> str:
    base = target_dir or (Path(__file__).parent / "skills")
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "scripts").mkdir(exist_ok=True)
    (skill_dir / "references").mkdir(exist_ok=True)

    skill_file = skill_dir / "SKILL.md"
    content = (
        "---\n"
        f"name: {name}\n"
        f"description: \"{description}\"\n"
        "---\n\n"
        f"{instructions.strip()}\n"
    )
    skill_file.write_text(content, encoding="utf-8")
    return str(skill_file)
