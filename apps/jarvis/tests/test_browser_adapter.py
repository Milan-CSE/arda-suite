import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from arda.agent_models import RiskLevel
from arda.agent_permission import decide
from arda.agent_tools import ToolContext, cleanup_browser_tabs, run_tool
from arda.browser_adapter import BrowserError, CamofoxClient, validate_public_url


class _Headers:
    def __init__(self, content_type="application/json"):
        self.content_type = content_type

    def get_content_type(self):
        return self.content_type


class _Response:
    def __init__(self, body, status=200, content_type="application/json"):
        self.body = body
        self.status = status
        self.headers = _Headers(content_type)

    def read(self, _size=-1):
        return self.body


def _client(responses):
    calls = []

    def request_fn(request, timeout):
        calls.append((request, timeout))
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    return CamofoxClient("http://localhost:9377", api_key="secret", request_fn=request_fn), calls


def test_client_uses_camofox_contract_and_bearer_auth():
    client, calls = _client([_Response(b'{"ok":true}'), _Response(b'{"tabId":"t1","url":"about:blank"}')])
    assert client.health()["ok"] is True
    assert client.create_tab(user_id="jarvis", session_key="run-1")["tabId"] == "t1"
    request, timeout = calls[1]
    assert request.method == "POST"
    assert request.full_url.endswith("/tabs")
    assert request.headers["Authorization"] == "Bearer secret"
    assert timeout == 15.0
    assert json.loads(request.data) == {"userId": "jarvis", "sessionKey": "run-1"}


def test_private_and_non_http_destinations_are_rejected():
    for url in ("file:///tmp/x", "http://localhost:3000", "http://127.0.0.1:80", "http://169.254.169.254/latest"):
        with pytest.raises(BrowserError):
            validate_public_url(url)


def test_client_rejects_malformed_snapshot():
    client, _ = _client([_Response(b'{"url":"https://example.com"}')])
    with pytest.raises(BrowserError, match="snapshot şeması"):
        client.snapshot(user_id="jarvis", tab_id="t1")


def test_tool_permission_keeps_navigation_and_screenshot_confirmed():
    assert decide("browser_healthcheck", {}) is RiskLevel.AUTO
    assert decide("browser_snapshot", {"tab_id": "t1"}) is RiskLevel.AUTO
    assert decide("browser_create_tab", {}) is RiskLevel.CONFIRM
    assert decide("browser_navigate", {"url": "https://example.com"}) is RiskLevel.CONFIRM
    assert decide("browser_screenshot", {"tab_id": "t1"}) is RiskLevel.CONFIRM
    assert "browser_click" not in __import__("arda.agent_tools", fromlist=["REGISTRY"]).REGISTRY


def test_browser_tools_are_disabled_without_client(tmp_path):
    ctx = ToolContext(workspace=Path(tmp_path))
    result = run_tool("browser_healthcheck", {}, ctx)
    assert not result.ok
    assert "kapalı" in result.error


def test_browser_tab_access_is_run_owned(tmp_path):
    client = SimpleNamespace(snapshot=lambda **kwargs: {"url": "https://example.com", "snapshot": "ok"})
    ctx = ToolContext(workspace=Path(tmp_path), browser_client=client, browser_tabs=set())
    result = run_tool("browser_snapshot", {"tab_id": "foreign"}, ctx)
    assert not result.ok
    assert "ait olmayan" in result.error


def test_cleanup_closes_owned_tabs_without_masking_errors(tmp_path):
    closed = []
    client = SimpleNamespace(close_tab=lambda **kwargs: closed.append(kwargs))
    ctx = ToolContext(workspace=Path(tmp_path), browser_client=client, browser_tabs={"t1", "t2"})
    cleanup_browser_tabs(ctx)
    assert {entry["tab_id"] for entry in closed} == {"t1", "t2"}
    assert ctx.browser_tabs == set()
