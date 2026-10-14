# tests/test_panel_wiring.py
"""menubar ↔ panel entegrasyon kablolaması (rumps import etmeden)."""
from __future__ import annotations

import inspect

from arda import menubar


def test_menubar_run_accepts_mem_store():
    sig = inspect.signature(menubar.run)
    assert "mem_store" in sig.parameters
    assert sig.parameters["mem_store"].default is None


def test_doctor_report_combines_permissions_and_runtime(monkeypatch):
    monkeypatch.setattr("arda.permissions.permission_report", lambda: "izin raporu")
    monkeypatch.setattr("arda.platform_runtime.runtime_report", lambda: "runtime raporu")
    assert menubar.doctor_report() == "izin raporu\n\nruntime raporu"


def test_menubar_module_imports_without_rumps():
    # menubar rumps'ı run() içinde lazy import eder → modül importu rumps'sız çalışmalı.
    import importlib
    importlib.reload(menubar)  # import hatası → fail


def test_menubar_has_doctor_menu_item(monkeypatch):
    import sys
    import types
    from unittest.mock import MagicMock

    mock_rumps = types.ModuleType("rumps")

    class FakeMenuItem:
        def __init__(self, title, callback=None):
            self.title = title
            self.callback = callback
            self.state = False
        def set_callback(self, cb):
            self.callback = cb
        def add(self, item):
            pass

    class FakeApp:
        def __init__(self, *args, **kwargs):
            self.menu = []
            self.title = ""
            self.template = False
        def run(self):
            pass

    class FakeTimer:
        def __init__(self, target, interval):
            self.target = target
            self.interval = interval
        def start(self):
            pass

    mock_rumps.MenuItem = FakeMenuItem
    mock_rumps.App = FakeApp
    mock_rumps.Timer = FakeTimer
    alert_called = []
    mock_rumps.alert = lambda **kwargs: alert_called.append(kwargs) or 1

    monkeypatch.setitem(sys.modules, "rumps", mock_rumps)

    controls = MagicMock()
    controls.wake_enabled.return_value = False
    agent_deps = MagicMock()

    # Call run() but intercept run() on app to avoid blocking
    created_apps = []
    orig_init = None

    # We can inspect the inner ArdaApp class by executing run with intercepted ArdaApp.run
    def fake_app_run(self):
        created_apps.append(self)

    monkeypatch.setattr(FakeApp, "run", fake_app_run)
    menubar.run(agent_deps, ack="Tamam.", controls=controls, cfg={})

    assert len(created_apps) == 1
    app = created_apps[0]
    titles = [item.title for item in app.menu if isinstance(item, FakeMenuItem)]
    doctor_items = [t for t in titles if "İzinleri Denetle" in t]
    assert len(doctor_items) == 1

    # Test clicking the doctor callback
    app._on_check_permissions(None)
    assert len(alert_called) == 1

    # Test wake motoru menu item
    wake_items = [t for t in titles if "Wake motoru" in t]
    assert len(wake_items) == 1

    # Test setting wake backend
    cfg = {}
    persisted = {}
    monkeypatch.setattr(menubar, "_persist", lambda up: persisted.update(up))
    cb = app._make_set_wake_backend("vosk", "Vosk (offline)")
    cb(None)
    assert persisted.get("wake", {}).get("backend") == "vosk"
