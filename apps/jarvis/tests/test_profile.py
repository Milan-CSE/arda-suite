"""Kullanıcı profili yardımcıları — saf, boş-güvenli (STT/router/answer'a metin sunar)."""
from arda import profile


def test_empty_cfg_returns_empty():
    assert profile.vocabulary({}) == ""
    assert profile.about_block({}) == ""
    assert profile.command_examples({}) == ""


def test_vocabulary_trimmed():
    assert profile.vocabulary({"profile": {"vocabulary": "  Example User, Sample User  "}}) == "Example User, Sample User"


def test_about_block_wraps_when_present():
    out = profile.about_block({"profile": {"about": "Adı Example User."}})
    assert out.startswith("\n\n")
    assert "Kullanıcı hakkında" in out
    assert "Adı Example User." in out


def test_about_block_empty_when_absent():
    assert profile.about_block({"profile": {}}) == ""


def test_command_examples_formats_pairs():
    cfg = {"profile": {"commands": [
        {"say": "not düş", "intent": "note"},
        {"say": "testleri çalıştır", "intent": "agent"},
    ]}}
    out = profile.command_examples(cfg)
    assert '- "not düş" -> note' in out
    assert '- "testleri çalıştır" -> agent' in out
    assert out.startswith("\n\n")


def test_command_examples_skips_incomplete():
    cfg = {"profile": {"commands": [{"say": "x"}, {"intent": "note"}, "bad"]}}
    assert profile.command_examples(cfg) == ""


def test_malformed_profile_types_return_empty():
    assert profile.vocabulary({"profile": "nope"}) == ""
    assert profile.about_block({"profile": {"about": 123}}) == ""
    assert profile.command_examples({"profile": {"commands": "nope"}}) == ""
