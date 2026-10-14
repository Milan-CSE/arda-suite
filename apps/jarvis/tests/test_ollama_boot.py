"""arda.ollama_boot — açılışta Ollama garantisi."""
from __future__ import annotations

from arda import ollama_boot


def test_already_up_returns_fast_without_launch(monkeypatch):
    calls = {"launch": 0}
    monkeypatch.setattr(ollama_boot, "is_up", lambda host, timeout=2.0: True)
    monkeypatch.setattr(ollama_boot, "_launch", lambda: calls.__setitem__("launch", calls["launch"] + 1) or True)
    assert ollama_boot.ensure_ollama({}) is True
    assert calls["launch"] == 0  # açıksa başlatma denenmemeli


def test_down_then_launches_and_waits(monkeypatch):
    states = iter([False, False, True])  # ilk kontrol kapalı, başlat, sonra hazır
    monkeypatch.setattr(ollama_boot, "is_up", lambda host, timeout=2.0: next(states))
    launched = {"n": 0}
    monkeypatch.setattr(ollama_boot, "_launch", lambda: launched.__setitem__("n", 1) or True)
    monkeypatch.setattr(ollama_boot.time, "sleep", lambda s: None)
    assert ollama_boot.ensure_ollama({}, wait_sec=5) is True
    assert launched["n"] == 1


def test_launch_fails_returns_false(monkeypatch):
    monkeypatch.setattr(ollama_boot, "is_up", lambda host, timeout=2.0: False)
    monkeypatch.setattr(ollama_boot, "_launch", lambda: False)
    assert ollama_boot.ensure_ollama({}, wait_sec=1) is False


def test_autostart_disabled_only_probes(monkeypatch):
    probed = {"n": 0}
    monkeypatch.setattr(ollama_boot, "is_up", lambda host, timeout=2.0: probed.__setitem__("n", 1) or False)
    monkeypatch.setattr(ollama_boot, "_launch", lambda: (_ for _ in ()).throw(AssertionError("başlatmamalı")))
    assert ollama_boot.ensure_ollama({"brain": {"autostart": False}}) is False
    assert probed["n"] == 1


def test_never_times_out_when_down(monkeypatch):
    monkeypatch.setattr(ollama_boot, "is_up", lambda host, timeout=2.0: False)
    monkeypatch.setattr(ollama_boot, "_launch", lambda: True)
    monkeypatch.setattr(ollama_boot.time, "sleep", lambda s: None)
    # monotonic ilerlesin ki döngü wait_sec sonunda çıksın
    t = {"v": 0.0}
    def _mono():
        t["v"] += 0.6
        return t["v"]
    monkeypatch.setattr(ollama_boot.time, "monotonic", _mono)
    assert ollama_boot.ensure_ollama({}, wait_sec=2) is False
