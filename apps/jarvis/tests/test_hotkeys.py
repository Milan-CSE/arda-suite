"""arda.menubar global kısayolları — TEK listener thread (TIS/TSM eşzamanlılık çökmesi regresyonu).

Kök neden: her `pynput.GlobalHotKeys` kendi thread'ini başlatır; macOS backend'i o thread'de
`TISCopyCurrentKeyboardInputSource` çağırır. İki listener açılışta eşzamanlı kalkınca HIToolbox
"TIS/TSM API iki thread'de birden" deyip SIGABRT atar (uygulama açılmaz).

Bu yüzden tüm kısayollar TEK bir GlobalHotKeys örneğinde toplanmalı → tek thread → tek TIS init.
"""
from __future__ import annotations

import sys
import types

import pytest

from arda import menubar


class _FakeHotKeys:
    """pynput.keyboard.GlobalHotKeys taklidi — kaç kez kurulduğunu ve haritayı kaydeder."""
    instances: list["_FakeHotKeys"] = []

    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.started = False
        self.daemon = False
        _FakeHotKeys.instances.append(self)

    def start(self):
        self.started = True


@pytest.fixture
def fake_pynput(monkeypatch):
    _FakeHotKeys.instances = []
    pkg = types.ModuleType("pynput")
    kb = types.ModuleType("pynput.keyboard")
    kb.GlobalHotKeys = _FakeHotKeys
    pkg.keyboard = kb
    monkeypatch.setitem(sys.modules, "pynput", pkg)
    monkeypatch.setitem(sys.modules, "pynput.keyboard", kb)
    return _FakeHotKeys


class _FakeControls:
    def request_manual(self): ...
    def request_open_panel(self): ...
    def request_dictation(self): ...


def _cfg(hotkey="cmd+shift+d"):
    return {"dictation": {"hotkey": hotkey}}


def test_single_listener_thread_for_all_hotkeys(fake_pynput):
    """Dikte dahil tüm kısayollar TEK GlobalHotKeys örneğinde → tek listener thread."""
    hk = menubar._start_hotkeys(_FakeControls(), _cfg(), include_dictation=True)
    assert hk is not None
    assert len(fake_pynput.instances) == 1, "birden fazla listener thread = TIS eşzamanlılık çökmesi riski"
    mapping = fake_pynput.instances[0].mapping
    assert "<cmd>+<shift>+a" in mapping
    assert "<cmd>+<shift>+p" in mapping
    assert "<cmd>+<shift>+d" in mapping
    assert fake_pynput.instances[0].started is True


def test_single_listener_without_dictation(fake_pynput):
    """Dikte kapalıyken ⌘⇧D haritada yok ama yine TEK listener."""
    hk = menubar._start_hotkeys(_FakeControls(), _cfg(), include_dictation=False)
    assert hk is not None
    assert len(fake_pynput.instances) == 1
    mapping = fake_pynput.instances[0].mapping
    assert "<cmd>+<shift>+a" in mapping
    assert "<cmd>+<shift>+p" in mapping
    assert "<cmd>+<shift>+d" not in mapping


def test_missing_pynput_returns_none_no_crash(monkeypatch):
    """pynput yoksa ÇÖKME — None dön (menüden fallback çalışır)."""
    monkeypatch.setitem(sys.modules, "pynput", None)  # import -> ImportError
    hk = menubar._start_hotkeys(_FakeControls(), _cfg(), include_dictation=True)
    assert hk is None
