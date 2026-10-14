"""macOS UI otomasyon araçları (AppleScript/System Events) + izin testleri.

Donanımsız: osascript çağrısı ctx.run'a enjekte edilir → üretilen AppleScript denetlenir,
gerçek System Events tetiklenmez.
"""
from types import SimpleNamespace

import pytest

from arda.agent_models import RiskLevel, ToolResult
from arda.agent_permission import decide
from arda.agent_tools import REGISTRY, TOOL_NAMES, ToolContext, _as_str, run_tool


def _fake_run(returncode=0, stdout="", stderr=""):
    calls = []

    def run(args, cwd=None):
        calls.append({"args": list(args), "cwd": cwd})
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    run.calls = calls
    return run


def _ctx(tmp_path, **kw):
    base = dict(workspace=tmp_path, run=_fake_run())
    base.update(kw)
    return ToolContext(**base)


def _script(ctx):
    """Son osascript çağrısının -e argümanı (üretilen AppleScript)."""
    args = ctx.run.calls[-1]["args"]
    assert args[0] == "osascript" and args[1] == "-e"
    return args[2]


class TestAsStr:
    def test_quotes_and_escapes(self):
        assert _as_str("hi") == '"hi"'
        assert _as_str('a"b') == '"a\\"b"'
        assert _as_str("a\\b") == '"a\\\\b"'


class TestUiType:
    def test_generates_keystroke_script(self, tmp_path):
        ctx = _ctx(tmp_path)
        r = run_tool("ui_type", {"text": "merhaba"}, ctx)
        assert r.ok is True
        assert _script(ctx) == 'tell application "System Events" to keystroke "merhaba"'

    def test_escapes_quotes_in_text(self, tmp_path):
        ctx = _ctx(tmp_path)
        run_tool("ui_type", {"text": 'de "selam"'}, ctx)
        assert 'keystroke "de \\"selam\\""' in _script(ctx)

    def test_empty_text_errors_without_running(self, tmp_path):
        ctx = _ctx(tmp_path)
        r = run_tool("ui_type", {"text": ""}, ctx)
        assert r.ok is False and ctx.run.calls == []

    def test_disabled_blocks(self, tmp_path):
        ctx = _ctx(tmp_path, ui_automation_enabled=False)
        r = run_tool("ui_type", {"text": "x"}, ctx)
        assert r.ok is False and "kapalı" in r.error and ctx.run.calls == []

    def test_failure_surfaces_accessibility_hint(self, tmp_path):
        ctx = _ctx(tmp_path, run=_fake_run(returncode=1, stderr=""))
        r = run_tool("ui_type", {"text": "x"}, ctx)
        assert r.ok is False and "Accessibility" in r.error


class TestUiTypeAppFocus:
    def test_app_param_focuses_before_typing(self, tmp_path):
        ctx = _ctx(tmp_path)
        r = run_tool("ui_type", {"text": "hi", "app": "TextEdit"}, ctx)
        assert r.ok is True
        calls = ctx.run.calls
        assert len(calls) == 2                       # önce odak, sonra tuşlama
        focus = calls[0]["args"][2]
        assert "activate" in focus and "set frontmost" in focus and "TextEdit" in focus
        assert calls[1]["args"][2] == 'tell application "System Events" to keystroke "hi"'

    def test_no_app_means_single_call(self, tmp_path):
        ctx = _ctx(tmp_path)
        run_tool("ui_type", {"text": "hi"}, ctx)
        assert len(ctx.run.calls) == 1               # odak adımı yok → öndeki uygulamaya


class TestUiKeystroke:
    def test_letter_with_modifier(self, tmp_path):
        ctx = _ctx(tmp_path)
        r = run_tool("ui_keystroke", {"key": "s", "modifiers": "command"}, ctx)
        assert r.ok is True
        assert _script(ctx) == 'tell application "System Events" to keystroke "s" using {command down}'

    def test_multiple_modifiers_aliases(self, tmp_path):
        ctx = _ctx(tmp_path)
        run_tool("ui_keystroke", {"key": "t", "modifiers": "cmd, shift"}, ctx)
        assert "using {command down, shift down}" in _script(ctx)

    def test_named_key_uses_key_code(self, tmp_path):
        ctx = _ctx(tmp_path)
        run_tool("ui_keystroke", {"key": "return"}, ctx)
        assert _script(ctx) == 'tell application "System Events" to key code 36'

    def test_unknown_modifier_errors(self, tmp_path):
        ctx = _ctx(tmp_path)
        r = run_tool("ui_keystroke", {"key": "s", "modifiers": "hyper"}, ctx)
        assert r.ok is False and "değiştirici" in r.error and ctx.run.calls == []

    def test_unsupported_multichar_key_errors(self, tmp_path):
        ctx = _ctx(tmp_path)
        r = run_tool("ui_keystroke", {"key": "abc"}, ctx)
        assert r.ok is False and ctx.run.calls == []

    def test_missing_key_errors(self, tmp_path):
        ctx = _ctx(tmp_path)
        r = run_tool("ui_keystroke", {}, ctx)
        assert r.ok is False

    def test_app_param_focuses_before_keystroke(self, tmp_path):
        ctx = _ctx(tmp_path)
        run_tool("ui_keystroke", {"key": "v", "modifiers": "command", "app": "TextEdit"}, ctx)
        calls = ctx.run.calls
        assert len(calls) == 2
        assert "set frontmost" in calls[0]["args"][2] and "TextEdit" in calls[0]["args"][2]
        assert calls[1]["args"][2] == 'tell application "System Events" to keystroke "v" using {command down}'


class TestUiClickMenu:
    def test_generates_menu_click_script(self, tmp_path):
        ctx = _ctx(tmp_path)
        r = run_tool("ui_click_menu", {"app": "TextEdit", "menu": "File", "item": "Save"}, ctx)
        assert r.ok is True
        script = _script(ctx)
        assert 'click menu item "Save" of menu "File" of menu bar item "File" of menu bar 1' in script
        assert 'tell application "TextEdit" to activate' in script

    def test_missing_params_errors(self, tmp_path):
        ctx = _ctx(tmp_path)
        r = run_tool("ui_click_menu", {"app": "TextEdit", "menu": "File"}, ctx)
        assert r.ok is False and ctx.run.calls == []


class TestRunAppleScript:
    def test_runs_script(self, tmp_path):
        ctx = _ctx(tmp_path, run=_fake_run(stdout="ok"))
        r = run_tool("run_applescript", {"script": "return 1"}, ctx)
        assert r.ok is True and _script(ctx) == "return 1"

    def test_empty_errors(self, tmp_path):
        ctx = _ctx(tmp_path)
        r = run_tool("run_applescript", {"script": "  "}, ctx)
        assert r.ok is False and ctx.run.calls == []

    def test_disabled_blocks(self, tmp_path):
        ctx = _ctx(tmp_path, ui_automation_enabled=False)
        r = run_tool("run_applescript", {"script": "return 1"}, ctx)
        assert r.ok is False and ctx.run.calls == []


class TestActivateApp:
    def test_activates(self, tmp_path):
        ctx = _ctx(tmp_path)
        r = run_tool("activate_app", {"name": "Safari"}, ctx)
        assert r.ok is True
        assert _script(ctx) == 'tell application "Safari" to activate'

    def test_missing_name_errors(self, tmp_path):
        ctx = _ctx(tmp_path)
        assert run_tool("activate_app", {}, ctx).ok is False


class TestPermissionAppleScript:
    def test_pure_applescript_is_confirm(self):
        assert decide("run_applescript", {"script": 'tell application "Mail" to activate'}) == RiskLevel.CONFIRM

    def test_do_shell_script_destructive_is_denied(self):
        s = 'do shell script "sudo rm -rf ~/Documents"'
        assert decide("run_applescript", {"script": s}) == RiskLevel.DENY

    def test_do_shell_script_benign_is_confirm(self):
        s = 'do shell script "echo hi"'
        assert decide("run_applescript", {"script": s}) == RiskLevel.CONFIRM

    def test_do_shell_script_secret_read_is_denied(self):
        # AppleScript shell kapısı sır-yolu kapısını ATLAYAMAZ: safe_shell DENY ederdi.
        s = 'do shell script "cat ~/.ssh/id_rsa"'
        assert decide("run_applescript", {"script": s}) == RiskLevel.DENY

    def test_do_shell_script_dotenv_read_is_denied(self):
        s = 'do shell script "cat /Users/x/project/.env"'
        assert decide("run_applescript", {"script": s}) == RiskLevel.DENY

    def test_ui_tools_are_confirm(self):
        assert decide("ui_type", {"text": "x"}) == RiskLevel.CONFIRM
        assert decide("ui_keystroke", {"key": "s"}) == RiskLevel.CONFIRM
        assert decide("ui_click_menu", {"app": "A", "menu": "F", "item": "S"}) == RiskLevel.CONFIRM

    def test_activate_app_is_auto(self):
        assert decide("activate_app", {"name": "Safari"}) == RiskLevel.AUTO


class TestRegistry:
    def test_new_tools_registered(self):
        for name in ("activate_app", "ui_type", "ui_keystroke", "ui_click_menu", "run_applescript"):
            assert name in TOOL_NAMES

    def test_ui_tools_base_risk(self):
        assert REGISTRY["ui_type"].risk == RiskLevel.CONFIRM
        assert REGISTRY["run_applescript"].risk == RiskLevel.CONFIRM
        assert REGISTRY["activate_app"].risk == RiskLevel.AUTO
