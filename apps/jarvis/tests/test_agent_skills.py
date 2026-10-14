from pathlib import Path

import pytest

from arda import agent_skills


def _write_skill(root: Path, skill_id: str, body: str = "# Talimat") -> Path:
    path = root / skill_id / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"---\nname: {skill_id}\ndescription: test skill\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def test_loads_allowlisted_skill_and_hashes_source(tmp_path, monkeypatch):
    path = _write_skill(tmp_path, "meeting_notes_extractor", "## Türkçe çıktı")
    monkeypatch.setattr(agent_skills, "SKILL_ROOT", tmp_path)
    skill = agent_skills.load_skill("meeting_notes_extractor")
    assert skill.skill_id == "meeting_notes_extractor"
    assert skill.source == str(path.resolve())
    assert len(skill.content_hash) == 64
    assert "Türkçe çıktı" in skill.instructions


def test_missing_skill_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_skills, "SKILL_ROOT", tmp_path)
    with pytest.raises(agent_skills.SkillError, match="bulunamadı"):
        agent_skills.load_skill("meeting_notes_extractor")


def test_malformed_metadata_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "meeting_notes_extractor" / "SKILL.md"
    path.parent.mkdir()
    path.write_text("# no frontmatter\n", encoding="utf-8")
    monkeypatch.setattr(agent_skills, "SKILL_ROOT", tmp_path)
    with pytest.raises(agent_skills.SkillError, match="metadata"):
        agent_skills.load_skill("meeting_notes_extractor")


def test_unknown_skill_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_skills, "SKILL_ROOT", tmp_path)
    with pytest.raises(agent_skills.SkillError, match="yetkili değil"):
        agent_skills.load_skill("unknown_skill")


def test_selection_is_explicit_and_strips_selector(tmp_path, monkeypatch):
    _write_skill(tmp_path, "meeting_notes_extractor")
    monkeypatch.setattr(agent_skills, "SKILL_ROOT", tmp_path)
    request, skill = agent_skills.select_skill("\nskill:meeting_notes_extractor\nToplantıyı özetle")
    assert request == "Toplantıyı özetle"
    assert skill is not None
    assert agent_skills.select_skill("toplantıyı özetle") == ("toplantıyı özetle", None)


def test_unauthorized_selector_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_skills, "SKILL_ROOT", tmp_path)
    with pytest.raises(agent_skills.SkillError, match="yetkili değil"):
        agent_skills.select_skill("skill:not_allowlisted\nİşle")
