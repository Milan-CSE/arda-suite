import dataclasses

import pytest

from arda.models import AgentEvent, AgentState, Intent

VALID = {
    "intent": "note",
    "category": "internship",
    "title": "ORS Staj Notu",
    "clean_text": "Üretim hattı incelendi.",
    "tags": ["staj", "ORS"],
    "follow_up": ["Yüzey pürüzlülüğü"],
}


def test_from_dict_builds_intent():
    i = Intent.from_dict(VALID)
    assert i.intent == "note"
    assert i.category == "internship"
    assert i.tags == ("staj", "ORS")
    assert i.follow_up == ("Yüzey pürüzlülüğü",)


def test_from_dict_list_fields_are_immutable_tuples():
    """tags/follow_up tuple olmalı — frozen Intent içeriği de değişmez kalsın."""
    i = Intent.from_dict(VALID)
    assert isinstance(i.tags, tuple)
    assert isinstance(i.follow_up, tuple)


def test_from_dict_missing_field_raises():
    bad = {k: v for k, v in VALID.items() if k != "clean_text"}
    with pytest.raises(ValueError):
        Intent.from_dict(bad)


def test_from_dict_wrong_type_raises():
    bad = dict(VALID, tags="staj")  # liste olmalı, string verildi
    with pytest.raises(ValueError):
        Intent.from_dict(bad)


def test_from_dict_non_dict_raises():
    with pytest.raises(ValueError):
        Intent.from_dict("not a dict")


class TestConfidence:
    """Router belirsizlikte 'low' raporlar → pipeline yıkıcı eylemi sessizce yapmaz."""

    def test_defaults_high_when_absent(self):
        # Eski yük (confidence yok) → 'high' (geriye uyumlu davranış).
        assert Intent.from_dict(VALID).confidence == "high"

    def test_reads_low_confidence(self):
        assert Intent.from_dict(dict(VALID, confidence="low")).confidence == "low"

    def test_non_string_confidence_falls_back_to_high(self):
        assert Intent.from_dict(dict(VALID, confidence=0.3)).confidence == "high"


class TestActionFields:
    def test_action_fields_default_empty_for_note(self):
        # Eski not yükü (action/params yok) hâlâ geçerli → geriye uyumlu.
        i = Intent.from_dict(VALID)
        assert i.action == ""
        assert i.params == {}

    def test_parses_action_and_params(self):
        i = Intent.from_dict(
            dict(VALID, intent="action", action="open_app", params={"name": "Safari"})
        )
        assert i.intent == "action"
        assert i.action == "open_app"
        assert i.params == {"name": "Safari"}

    def test_param_values_coerced_to_str(self):
        # LLM bazen sayı döndürür (level=50); değerler string'e normalize edilmeli.
        i = Intent.from_dict(dict(VALID, intent="action", action="set_volume", params={"level": 50}))
        assert i.params == {"level": "50"}

    def test_non_dict_params_raises(self):
        with pytest.raises(ValueError):
            Intent.from_dict(dict(VALID, params=["nope"]))


class TestMemFields:
    """Router hafıza etiketi: OPSİYONEL + fail-safe — geçersizse routing bozulmaz."""

    def test_mem_fields_default_false_personal_when_absent(self):
        it = Intent.from_dict(dict(VALID))
        assert it.mem_durable is False
        assert it.mem_scope == "personal"

    def test_mem_fields_parsed_when_present(self):
        it = Intent.from_dict({**VALID, "mem_durable": True, "mem_scope": "jarvis"})
        assert it.mem_durable is True
        assert it.mem_scope == "jarvis"

    def test_mem_scope_falls_back_on_unknown_value(self):
        it = Intent.from_dict({**VALID, "mem_durable": True, "mem_scope": "garip"})
        assert it.mem_scope == "personal"  # yalnız personal|jarvis geçerli

    def test_mem_durable_non_bool_is_false(self):
        it = Intent.from_dict({**VALID, "mem_durable": "evet"})
        assert it.mem_durable is False  # bool değilse güvenli varsayılan


class TestAgentState:
    def test_has_all_lifecycle_states(self):
        names = {s.name for s in AgentState}
        assert names == {
            "LISTENING", "WOKE", "RECORDING",
            "TRANSCRIBING", "THINKING", "DONE", "ERROR",
        }

    def test_is_str_enum_usable_as_string(self):
        # str tabanlı enum → değer doğrudan string gibi kullanılabilir (log/JSON).
        assert AgentState.RECORDING == "recording"
        assert f"{AgentState.RECORDING}" or True  # str() patlamıyor


class TestAgentEvent:
    def test_minimal_event_has_defaults(self):
        ev = AgentEvent(state=AgentState.LISTENING)
        assert ev.state is AgentState.LISTENING
        assert ev.message == ""
        assert ev.source == "agent"
        assert isinstance(ev.timestamp, float) and ev.timestamp > 0

    def test_carries_state_message_timestamp_source(self):
        ev = AgentEvent(
            state=AgentState.DONE,
            message="Görev eklendi",
            timestamp=123.0,
            source="pipeline",
        )
        assert ev.state is AgentState.DONE
        assert ev.message == "Görev eklendi"
        assert ev.timestamp == 123.0
        assert ev.source == "pipeline"

    def test_is_frozen(self):
        ev = AgentEvent(state=AgentState.ERROR)
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.message = "x"  # type: ignore[misc]

    def test_timestamp_auto_set_per_instance(self):
        a = AgentEvent(state=AgentState.WOKE)
        b = AgentEvent(state=AgentState.WOKE)
        # Her örnek kendi zaman damgasını alır (paylaşılan default değil).
        assert a.timestamp > 0 and b.timestamp >= a.timestamp
