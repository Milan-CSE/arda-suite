"""agent_models testleri — saf veri modelleri (LLM çıktısı doğrulama dahil)."""
import pytest

from arda.agent_models import (
    AgentRunResult,
    Plan,
    PlanStep,
    RiskLevel,
    StepLog,
    ToolResult,
)


class TestRiskLevel:
    def test_values(self):
        assert RiskLevel.AUTO == "auto"
        assert RiskLevel.CONFIRM == "confirm"
        assert RiskLevel.DENY == "deny"

    def test_is_str_enum(self):
        # str tabanlı → log/serialize'de doğrudan string gibi davranır
        assert RiskLevel.AUTO.value == "auto"
        assert f"{RiskLevel.DENY}" in ("RiskLevel.DENY", "deny")


class TestToolResult:
    def test_minimal_ok(self):
        r = ToolResult(ok=True)
        assert r.ok is True
        assert r.output == ""
        assert r.error == ""
        assert r.data is None

    def test_full(self):
        r = ToolResult(ok=False, output="5 öğe", error="boom", data={"n": 5})
        assert r.output == "5 öğe"
        assert r.error == "boom"
        assert r.data == {"n": 5}


class TestPlanStep:
    def test_from_dict_valid(self):
        s = PlanStep.from_dict({"tool": "list_dir", "params": {"path": "."}, "rationale": "klasörü gör"})
        assert s.tool == "list_dir"
        assert s.params == {"path": "."}
        assert s.rationale == "klasörü gör"

    def test_from_dict_defaults(self):
        s = PlanStep.from_dict({"tool": "list_dir"})
        assert s.params == {}
        assert s.rationale == ""

    def test_from_dict_coerces_param_values_to_str(self):
        # LLM bazen sayı/bool döndürür → değerler string'e normalize edilir (actions kalıbı)
        s = PlanStep.from_dict({"tool": "set_volume", "params": {"level": 50, "on": True}})
        assert s.params == {"level": "50", "on": "True"}

    def test_from_dict_missing_tool_raises(self):
        with pytest.raises(ValueError):
            PlanStep.from_dict({"params": {}})

    def test_from_dict_non_dict_raises(self):
        with pytest.raises(ValueError):
            PlanStep.from_dict("list_dir")

    def test_from_dict_bad_params_raises(self):
        with pytest.raises(ValueError):
            PlanStep.from_dict({"tool": "x", "params": ["not", "a", "dict"]})


class TestPlan:
    def test_from_dict_valid(self):
        p = Plan.from_dict(
            {
                "summary": "Projeyi incele",
                "steps": [
                    {"tool": "list_dir", "params": {}, "rationale": "kök"},
                    {"tool": "read_file", "params": {"path": "README.md"}, "rationale": "oku"},
                ],
            }
        )
        assert p.summary == "Projeyi incele"
        assert len(p.steps) == 2
        assert all(isinstance(s, PlanStep) for s in p.steps)
        assert p.steps[1].tool == "read_file"

    def test_from_dict_empty_steps_ok(self):
        p = Plan.from_dict({"summary": "boş", "steps": []})
        assert p.steps == ()

    def test_from_dict_missing_summary_defaults_empty(self):
        p = Plan.from_dict({"steps": []})
        assert p.summary == ""

    def test_from_dict_non_dict_raises(self):
        with pytest.raises(ValueError):
            Plan.from_dict([1, 2, 3])

    def test_from_dict_steps_not_list_raises(self):
        with pytest.raises(ValueError):
            Plan.from_dict({"summary": "x", "steps": {"tool": "list_dir"}})

    def test_steps_is_tuple_immutable(self):
        p = Plan.from_dict({"summary": "x", "steps": [{"tool": "list_dir"}]})
        assert isinstance(p.steps, tuple)


class TestStepLog:
    def test_construction(self):
        log = StepLog(
            tool="read_file",
            params={"path": "README.md"},
            rationale="oku",
            risk=RiskLevel.AUTO,
            decision="ran",
            ok=True,
            output="içerik...",
        )
        assert log.tool == "read_file"
        assert log.risk == RiskLevel.AUTO
        assert log.decision == "ran"
        assert log.ok is True
        assert log.error == ""
        assert log.verified is True


class TestAgentRunResult:
    def test_defaults(self):
        r = AgentRunResult(status="done", spoken="Bitti.")
        assert r.status == "done"
        assert r.spoken == "Bitti."
        assert r.logs == ()

    def test_with_logs(self):
        log = StepLog("list_dir", {}, "", RiskLevel.AUTO, "ran", True)
        r = AgentRunResult(status="done", spoken="ok", logs=(log,))
        assert len(r.logs) == 1
