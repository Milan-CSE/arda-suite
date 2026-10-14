"""arda.panel — saf mantık (AppKit gerektirmez)."""
from __future__ import annotations

import pytest

from arda import panel
from arda.panel import ControlPanel, format_commands, parse_commands
from arda.models import AgentRuntimeEvent


class _FakeControls:
    def __init__(self, wake=True):
        self._wake = wake
    def wake_enabled(self):
        return self._wake


class _FakeMem:
    def __init__(self, *, enabled=True, items=None, raise_on=None):
        self._enabled = enabled
        self._items = items or []
        self._raise_on = raise_on or set()
        self.deleted = []
    @property
    def enabled(self):
        return self._enabled
    def list_all(self):
        if "list_all" in self._raise_on:
            raise RuntimeError("boom")
        return list(self._items)
    def search_raw(self, query):
        return [i for i in self._items if query in str(i)]
    def delete(self, memory_id):
        self.deleted.append(memory_id)


def _panel(*, controls=None, cfg=None, mem=None, saver=None):
    saved = []
    def _save(updates):
        saved.append(updates)
    p = ControlPanel(
        controls=controls or _FakeControls(),
        cfg=cfg if cfg is not None else {},
        mem_store=mem or _FakeMem(),
        on_record=lambda: True,
        on_cancel=lambda: None,
        on_toggle_wake=lambda: True,
        on_toggle_dictation=lambda: True,
        on_open_vault=lambda: None,
        save_local=saver or _save,
    )
    return p, saved


# --- parse/format komutlar ---
def test_parse_commands_valid_lines():
    text = "not al => note\ngörev ekle => task\n"
    assert parse_commands(text) == [
        {"say": "not al", "intent": "note"},
        {"say": "görev ekle", "intent": "task"},
    ]


def test_parse_commands_skips_bad_lines():
    text = "  \nsadece metin\nsay => \n => intent\nnot al => note"
    assert parse_commands(text) == [{"say": "not al", "intent": "note"}]


def test_format_commands_roundtrip():
    cmds = [{"say": "not al", "intent": "note"}]
    assert format_commands(cmds) == "not al => note"
    assert parse_commands(format_commands(cmds)) == cmds


def test_format_commands_ignores_non_dict():
    assert format_commands(["bad", {"say": "x", "intent": "y"}, {"say": "", "intent": "z"}]) == "x => y"


# --- Beni Tanı (profil) ---
def test_save_profile_builds_correct_dict_and_persists():
    p, saved = _panel(cfg={})
    p.save_profile(about="  Example User  ", vocabulary=" Jarvis, mem0 ", commands_text="not al => note")
    assert saved == [{"profile": {
        "about": "Example User",
        "vocabulary": "Jarvis, mem0",
        "commands": [{"say": "not al", "intent": "note"}],
    }}]


def test_save_profile_mutates_live_cfg():
    cfg = {"profile": {"about": "eski"}}
    p, _ = _panel(cfg=cfg)
    p.save_profile(about="yeni", vocabulary="", commands_text="")
    assert cfg["profile"]["about"] == "yeni"
    assert cfg["profile"]["commands"] == []


def test_save_profile_swallows_save_error():
    def _boom(_updates):
        raise RuntimeError("disk dolu")
    p, _ = _panel(cfg={}, saver=_boom)
    p.save_profile(about="x", vocabulary="", commands_text="")  # raise ETMEMELİ


def test_profile_fields_reads_cfg():
    cfg = {"profile": {"about": "A", "vocabulary": "V", "commands": [{"say": "s", "intent": "i"}]}}
    p, _ = _panel(cfg=cfg)
    assert p.profile_fields() == ("A", "V", "s => i")


def test_profile_fields_empty_when_no_block():
    p, _ = _panel(cfg={})
    assert p.profile_fields() == ("", "", "")


# --- Hafıza ---
def test_memory_enabled_true():
    p, _ = _panel(mem=_FakeMem(enabled=True))
    assert p.memory_enabled() is True


def test_load_memories_returns_empty_when_disabled():
    mem = _FakeMem(enabled=False, items=[{"id": 1}])
    p, _ = _panel(mem=mem)
    assert p.load_memories() == []


def test_load_memories_limits_results():
    items = [{"id": i} for i in range(80)]
    p, _ = _panel(mem=_FakeMem(items=items))
    assert len(p.load_memories(limit=50)) == 50


def test_load_memories_swallows_error():
    p, _ = _panel(mem=_FakeMem(raise_on={"list_all"}))
    assert p.load_memories() == []


def test_search_memories_empty_query_returns_empty():
    p, _ = _panel(mem=_FakeMem(items=[{"id": 1}]))
    assert p.search_memories("   ") == []


def test_delete_memory_calls_store():
    mem = _FakeMem()
    p, _ = _panel(mem=mem)
    assert p.delete_memory("abc") is True
    assert mem.deleted == ["abc"]


def test_memory_enabled_and_load_work_with_real_property_shape():
    # Regression: gerçek MemoryStore.enabled bir @property'dir (parensiz erişilir).
    # panel.enabled() (parenli) çağrısı TypeError fırlatır ve bare except tarafından
    # yutulup memory_enabled() hep False dönerdi. _FakeMem.enabled artık gerçek
    # arayüzü (property) yansıtıyor; bu test property erişiminde TypeError
    # olmadığını ve sonuçların akmasını kanıtlar.
    mem = _FakeMem(enabled=True, items=[{"id": 1, "memory": "not"}])
    p, _ = _panel(mem=mem)
    assert p.memory_enabled() is True
    assert p.load_memories() == [{"id": 1, "memory": "not"}]


def test_delete_memory_false_when_disabled():
    mem = _FakeMem(enabled=False)
    p, _ = _panel(mem=mem)
    assert p.delete_memory("abc") is False
    assert mem.deleted == []


# --- durum güncelleme / pencere yaşam döngüsü (AppKit'siz) ---
def test_update_noop_when_window_closed():
    p, _ = _panel()
    p.update("LISTENING", "Hazır")  # pencere yok → raise ETMEMELİ
    assert p._last_state == "LISTENING"


def test_runtime_projection_updates_without_native_window():
    p, _ = _panel()
    p.update_runtime(AgentRuntimeEvent("agent.run.started", "run_1", timestamp="2026-09-24T13:00:00+00:00"))
    p.update_runtime(AgentRuntimeEvent("agent.tool.started", "run_1", "step_1", timestamp="2026-09-24T13:00:01+00:00", metadata={"tool": "list_dir"}))
    assert p._runtime.snapshot.run_id == "run_1"
    assert p._runtime.snapshot.tools[0].tool == "list_dir"
    assert p._runtime.snapshot.status == "running"


def test_open_is_safe_without_pyobjc(monkeypatch):
    # pyobjc yokmuş gibi davran → open() çökmemeli, pencere kurulmamalı.
    monkeypatch.setattr(panel, "is_available", lambda: False)
    p, _ = _panel()
    p.open()
    assert p._window is None


def test_module_imports_without_pyobjc():
    # panel modülü AppKit olmadan import edilebilmeli (lazy import doğrulaması).
    import importlib
    importlib.reload(panel)  # import hatası → test fail
