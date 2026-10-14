from unittest.mock import patch

from arda.__main__ import _effective_mode, _install_daemon_trigger
from arda.state import Controls


def test_daemon_health_imports_are_available():
    from arda.__main__ import permission_report, runtime_report

    assert callable(permission_report)
    assert callable(runtime_report)


def test_linux_menubar_falls_back_to_daemon():
    with patch("arda.__main__.platform_system", return_value="Linux"):
        assert _effective_mode({"app": {"mode": "menubar"}}) == "daemon"


def test_macos_menubar_is_preserved():
    with patch("arda.__main__.platform_system", return_value="Darwin"):
        assert _effective_mode({"app": {"mode": "menubar"}}) == "menubar"


def test_unknown_mode_is_safe_daemon():
    with patch("arda.__main__.platform_system", return_value="Linux"):
        assert _effective_mode({"app": {"mode": "unknown"}}) == "daemon"


def test_daemon_sigusr1_requests_manual_trigger(monkeypatch):
    controls = Controls(wake_enabled=False)
    handlers = {}
    monkeypatch.setattr("arda.__main__.signal.signal", lambda sig, handler: handlers.setdefault(sig, handler))
    _install_daemon_trigger(controls, "daemon")
    handlers[__import__("signal").SIGUSR1](None, None)
    assert controls.consume_manual() is True


def test_daemon_installs_sigterm_shutdown_handler(monkeypatch):
    controls = Controls()
    handlers = {}
    monkeypatch.setattr("arda.__main__.signal.signal", lambda sig, handler: handlers.setdefault(sig, handler))
    _install_daemon_trigger(controls, "daemon")
    handlers[__import__("signal").SIGTERM](None, None)
    assert controls.is_shutdown() is True
