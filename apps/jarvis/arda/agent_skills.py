"""Allowlisted Markdown skill adapter for the existing agent runtime.

Skills are task context only. They never register tools or change permission policy.
Selection is explicit (``skill:<id>`` on the first non-empty line) so normal agent
requests keep their previous behavior and no second classifier is introduced.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[2] / "claudeskills"
ALLOWED_SKILLS = frozenset({"meeting_notes_extractor"})
_SELECTOR = re.compile(r"^skill:(?P<skill_id>[a-z0-9][a-z0-9_-]*)\s*$")
_MAX_BYTES = 128 * 1024


class SkillError(ValueError):
    """A skill is missing, malformed, or not explicitly allowlisted."""


@dataclass(frozen=True)
class SkillContext:
    skill_id: str
    source: str
    content_hash: str
    instructions: str


def _skill_path(skill_id: str) -> Path:
    if skill_id not in ALLOWED_SKILLS:
        raise SkillError(f"Skill yetkili değil: {skill_id}")
    path = (SKILL_ROOT / skill_id / "SKILL.md").resolve()
    root = SKILL_ROOT.resolve()
    if root not in path.parents or not path.is_file():
        raise SkillError(f"Skill bulunamadı: {skill_id}")
    if path.stat().st_size > _MAX_BYTES:
        raise SkillError(f"Skill çok büyük: {skill_id}")
    return path


def _parse_metadata(text: str, skill_id: str) -> str:
    lines = text.splitlines()
    if len(lines) < 4 or lines[0].strip() != "---":
        raise SkillError(f"Skill metadata başlangıcı geçersiz: {skill_id}")
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise SkillError(f"Skill metadata kapanışı eksik: {skill_id}") from exc

    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            raise SkillError(f"Skill metadata satırı geçersiz: {skill_id}")
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if key not in {"name", "description"} or not value:
            raise SkillError(f"Skill metadata alanı geçersiz: {skill_id}")
        metadata[key] = value
    if metadata.get("name") != skill_id or not metadata.get("description"):
        raise SkillError(f"Skill metadata adı uyuşmuyor: {skill_id}")
    body = "\n".join(lines[end + 1:]).strip()
    if not body:
        raise SkillError(f"Skill talimat gövdesi boş: {skill_id}")
    return body


def load_skill(skill_id: str) -> SkillContext:
    path = _skill_path(skill_id)
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    instructions = _parse_metadata(text, skill_id)
    return SkillContext(
        skill_id=skill_id,
        source=str(path),
        content_hash=hashlib.sha256(raw).hexdigest(),
        instructions=instructions,
    )


def select_skill(request: str) -> tuple[str, SkillContext | None]:
    """Return a clean request and selected skill; never infer a skill from prose."""
    lines = (request or "").splitlines()
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is None:
        return "", None
    match = _SELECTOR.match(lines[first].strip())
    if not match:
        return request, None
    skill_id = match.group("skill_id")
    clean = "\n".join(lines[first + 1:]).strip()
    if not clean:
        raise SkillError("Skill isteği boş olamaz")
    return clean, load_skill(skill_id)
