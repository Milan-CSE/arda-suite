"""brain router — 'agent' intent'i (çok-adımlı desktop agent) testleri. ollama mock'lu."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from arda.brain import classify

CFG = {"brain": {"model": "qwen3:8b", "host": "http://localhost:11434", "think": False, "keep_alive": "10m"}}

_BASE = {
    "intent": "note", "category": "daily", "title": "x", "clean_text": "y",
    "tags": [], "follow_up": [],
}


def _resp(payload):
    return SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))


def _patch(chat_return):
    inst = MagicMock()
    inst.chat.return_value = chat_return
    return patch("arda.brain.ollama.Client", MagicMock(return_value=inst)), inst.chat


class TestAgentRouting:
    def test_schema_enum_includes_agent(self):
        ctx, chat = _patch(_resp(_BASE))
        with ctx:
            classify("metin", cfg=CFG)
        enum = chat.call_args.kwargs["format"]["properties"]["intent"]["enum"]
        assert "agent" in enum
        assert enum[0] == "answered"          # sohbet-önce kuralı korunur

    def test_agent_payload_round_trips(self):
        payload = dict(_BASE, intent="agent", clean_text="bu projeyi incele")
        ctx, _ = _patch(_resp(payload))
        with ctx:
            result = classify("bu projeyi incele", cfg=CFG)
        assert result.intent == "agent"
        assert result.clean_text == "bu projeyi incele"   # istek clean_text'te taşınır

    def test_prompt_describes_agent_intent(self):
        from arda.brain import _SYSTEM_PROMPT

        assert '"agent"' in _SYSTEM_PROMPT
        assert "ÇOK ADIMLI" in _SYSTEM_PROMPT          # agent ayrımı prompt'ta
