"""agent_executor testleri — plan→izin→onay→çalıştır→doğrula→özet orkestrasyonu."""
from arda.agent_models import Plan, PlanStep, RiskLevel, ToolResult
from arda.agent_executor import AgentRunDeps, _safe_confirm, run_agent
from arda.agent_skills import SkillContext
from arda.agent_tools import ToolContext
from types import SimpleNamespace
import time


def _plan(*steps) -> Plan:
    return Plan(summary="test planı", steps=tuple(steps))


def _fake_run_tool(results=None):
    results = results or {}
    calls = []

    def run_tool(name, params, ctx):
        calls.append((name, dict(params)))
        return results.get(name, ToolResult(ok=True, output=f"{name} tamam"))

    run_tool.calls = calls
    return run_tool


def _deps(tmp_path, plan_obj, **kw):
    base = dict(
        plan=lambda req: plan_obj,
        ctx=ToolContext(workspace=tmp_path),
        confirm=lambda msg: True,
        decide=lambda tool, params: RiskLevel.AUTO,
        run_tool=_fake_run_tool(),
        verify=lambda step, result, ctx: (result.ok, "ok"),
        max_steps=12,
    )
    base.update(kw)
    return AgentRunDeps(**base)


class TestPlanFailure:
    def test_plan_exception_returns_failed(self, tmp_path):
        def boom(req):
            raise RuntimeError("ollama down")

        deps = _deps(tmp_path, None, plan=boom)
        result = run_agent("x", deps=deps)
        assert result.status == "failed"
        assert result.spoken

    def test_empty_plan_returns_empty(self, tmp_path):
        result = run_agent("x", deps=_deps(tmp_path, _plan()))
        assert result.status == "empty"


class TestAutoExecution:
    def test_auto_step_runs_without_confirm(self, tmp_path):
        confirm_calls = []
        rt = _fake_run_tool()
        deps = _deps(
            tmp_path, _plan(PlanStep("list_dir", {}, "gör")),
            confirm=lambda m: confirm_calls.append(m) or True,
            run_tool=rt,
        )
        result = run_agent("x", deps=deps)
        assert rt.calls == [("list_dir", {})]
        assert confirm_calls == []          # auto → onay sorulmadı
        assert result.status == "done"

    def test_multiple_auto_steps_all_run(self, tmp_path):
        rt = _fake_run_tool()
        plan = _plan(
            PlanStep("list_dir", {}, "a"),
            PlanStep("read_file", {"path": "README.md"}, "b"),
        )
        run_agent("x", deps=_deps(tmp_path, plan, run_tool=rt))
        assert [c[0] for c in rt.calls] == ["list_dir", "read_file"]


class TestConfirmGate:
    def test_confirm_yes_runs(self, tmp_path):
        rt = _fake_run_tool()
        deps = _deps(
            tmp_path, _plan(PlanStep("write_file", {"path": "a", "content": "x"}, "yaz")),
            decide=lambda t, p: RiskLevel.CONFIRM, confirm=lambda m: True, run_tool=rt,
        )
        result = run_agent("x", deps=deps)
        assert rt.calls == [("write_file", {"path": "a", "content": "x"})]
        assert result.status == "done"

    def test_confirm_no_skips(self, tmp_path):
        rt = _fake_run_tool()
        deps = _deps(
            tmp_path, _plan(PlanStep("write_file", {"path": "a", "content": "x"}, "yaz")),
            decide=lambda t, p: RiskLevel.CONFIRM, confirm=lambda m: False, run_tool=rt,
        )
        result = run_agent("x", deps=deps)
        assert rt.calls == []               # reddedildi → çalışmadı
        assert result.logs[0].decision == "confirmed-no"
        assert result.status in ("partial", "denied")

    def test_confirm_prompt_describes_step(self, tmp_path):
        seen = []
        deps = _deps(
            tmp_path, _plan(PlanStep("write_file", {"path": "app.py", "content": "x"}, "düzelt")),
            decide=lambda t, p: RiskLevel.CONFIRM, confirm=lambda m: seen.append(m) or True,
        )
        run_agent("x", deps=deps)
        assert seen and "write_file" in seen[0]

    def test_confirm_failure_is_fail_closed(self, tmp_path):
        # confirm callback patlarsa adım ÇALIŞMAZ (güvenli taraf)
        rt = _fake_run_tool()
        def boom(msg):
            raise RuntimeError("dialog çöktü")
        deps = _deps(
            tmp_path, _plan(PlanStep("write_file", {"path": "a", "content": "x"}, "")),
            decide=lambda t, p: RiskLevel.CONFIRM, confirm=boom, run_tool=rt,
        )
        result = run_agent("x", deps=deps)
        assert rt.calls == []
        assert isinstance(result.spoken, str)

    def test_confirm_wait_is_interrupted_by_deadline(self, tmp_path):
        rt = _fake_run_tool()

        def slow_confirm(_message):
            time.sleep(0.05)
            return True

        deps = _deps(
            tmp_path,
            _plan(PlanStep("write_file", {"path": "a", "content": "x"}, "yaz")),
            decide=lambda _tool, _params: RiskLevel.CONFIRM,
            confirm=slow_confirm,
            run_tool=rt,
            run_timeout_sec=0.001,
        )
        result = run_agent("x", deps=deps)
        assert result.status == "timed_out"
        assert result.termination_reason == "deadline_after_tool"
        assert rt.calls == []


class TestDenyGate:
    def test_deny_step_never_runs(self, tmp_path):
        rt = _fake_run_tool()
        deps = _deps(
            tmp_path, _plan(PlanStep("read_file", {"path": ".env"}, "sır oku")),
            decide=lambda t, p: RiskLevel.DENY, run_tool=rt,
        )
        result = run_agent("x", deps=deps)
        assert rt.calls == []
        assert result.logs[0].decision == "denied"
        assert result.status == "denied"


class TestLogging:
    def test_log_captures_full_trace(self, tmp_path):
        rt = _fake_run_tool({"read_file": ToolResult(ok=True, output="okundu", data={"content": "abc"})})
        plan = _plan(PlanStep("read_file", {"path": "README.md"}, "açıklamayı oku"))
        result = run_agent("x", deps=_deps(tmp_path, plan, run_tool=rt))
        log = result.logs[0]
        assert log.tool == "read_file"
        assert log.params == {"path": "README.md"}
        assert log.rationale == "açıklamayı oku"
        assert log.decision == "ran"
        assert log.ok is True
        assert log.output == "okundu"

    def test_failed_tool_logged_as_error(self, tmp_path):
        rt = _fake_run_tool({"run_tests": ToolResult(ok=False, error="1 failed")})
        plan = _plan(PlanStep("run_tests", {}, "test"))
        result = run_agent("x", deps=_deps(tmp_path, plan, run_tool=rt))
        assert result.logs[0].decision == "error"
        assert result.status == "partial"


class TestMaxSteps:
    def test_respects_max_steps(self, tmp_path):
        rt = _fake_run_tool()
        steps = [PlanStep("list_dir", {"path": str(i)}, "") for i in range(10)]
        deps = _deps(tmp_path, _plan(*steps), run_tool=rt, max_steps=3)
        run_agent("x", deps=deps)
        assert len(rt.calls) == 3


class TestSummary:
    def test_summary_mentions_test_output(self, tmp_path):
        rt = _fake_run_tool({"run_tests": ToolResult(ok=True, output="12 passed")})
        plan = _plan(PlanStep("run_tests", {}, "test"))
        result = run_agent("x", deps=_deps(tmp_path, plan, run_tool=rt))
        assert "12 passed" in result.spoken

    def test_custom_summarizer_used(self, tmp_path):
        plan = _plan(PlanStep("list_dir", {}, ""))
        deps = _deps(tmp_path, plan, summarize=lambda p, logs: "ÖZEL ÖZET")
        result = run_agent("x", deps=deps)
        assert result.spoken == "ÖZEL ÖZET"

    def test_summarizer_failure_falls_back(self, tmp_path):
        def boom(p, logs):
            raise RuntimeError("llm down")
        plan = _plan(PlanStep("list_dir", {}, ""))
        result = run_agent("x", deps=_deps(tmp_path, plan, summarize=boom))
        assert isinstance(result.spoken, str) and result.spoken


class TestRealIntegration:
    """Gerçek izin (decide) + araç (run_tool) + doğrulama (verify) ile uçtan uca — stub yok."""

    def test_deny_confirm_auto_gates_real(self, tmp_path):
        (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
        (tmp_path / "keep.txt").write_text("v", encoding="utf-8")
        plan = _plan(
            PlanStep("read_file", {"path": ".env"}, "sır oku"),
            PlanStep("write_file", {"path": "out.txt", "content": "x"}, "yaz"),
            PlanStep("list_dir", {}, "listele"),
        )
        # decide / run_tool / verify GERÇEK varsayılanlar (AgentRunDeps default'ları)
        deps = AgentRunDeps(
            plan=lambda req: plan,
            ctx=ToolContext(workspace=tmp_path),
            confirm=lambda m: False,       # yazma onaylanmaz
        )
        result = run_agent("x", deps=deps)
        by_tool = {l.tool: l for l in result.logs}
        assert by_tool["read_file"].decision == "denied"          # .env sırrı reddedildi
        assert by_tool["write_file"].decision == "confirmed-no"    # onaysız → atlandı
        assert by_tool["list_dir"].decision == "ran" and by_tool["list_dir"].ok
        assert not (tmp_path / "out.txt").exists()                 # yazma GERÇEKTEN olmadı

    def test_confirmed_edit_real(self, tmp_path):
        # MVP madde 9: küçük dosya düzenlemesini ONAY isteyerek yap — gerçek zincir.
        (tmp_path / "a.py").write_text("VERSION = '1.0'\n", encoding="utf-8")
        plan = _plan(PlanStep("edit_file", {"path": "a.py", "old": "1.0", "new": "2.0"}, "sürüm yükselt"))
        deps = AgentRunDeps(plan=lambda req: plan, ctx=ToolContext(workspace=tmp_path), confirm=lambda m: True)
        result = run_agent("x", deps=deps)
        assert result.status == "done"
        assert result.logs[0].verified is True
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "VERSION = '2.0'\n"

    def test_real_auto_read_runs_and_verifies(self, tmp_path):
        (tmp_path / "README.md").write_text("merhaba", encoding="utf-8")
        plan = _plan(PlanStep("read_file", {"path": "README.md"}, "oku"))
        deps = AgentRunDeps(plan=lambda req: plan, ctx=ToolContext(workspace=tmp_path))
        result = run_agent("x", deps=deps)
        assert result.status == "done"
        assert result.logs[0].verified is True


class TestProgressEvents:
    """Canlı UI için: her adımda THINKING + mesaj yayınlanır (menübar bunu gösterir)."""

    def test_emits_thinking_event_per_step(self, tmp_path):
        from arda.models import AgentState

        seen = []
        plan = _plan(PlanStep("list_dir", {}, "klasör"), PlanStep("read_file", {"path": "x"}, "oku"))
        run_agent("x", deps=_deps(tmp_path, plan), on_event=seen.append)
        thinking = [e for e in seen if e.state == AgentState.THINKING]
        assert len(thinking) >= 2                       # her adım için ilerleme olayı
        assert any("list_dir" in e.message for e in thinking)
        assert all(e.source == "agent" for e in thinking)

    def test_default_no_on_event_still_works(self, tmp_path):
        result = run_agent("x", deps=_deps(tmp_path, _plan(PlanStep("list_dir", {}, ""))))
        assert result.status == "done"

    def test_on_event_failure_does_not_break_run(self, tmp_path):
        def boom(_e):
            raise RuntimeError("ui kuyruğu çöktü")

        result = run_agent("x", deps=_deps(tmp_path, _plan(PlanStep("list_dir", {}, ""))), on_event=boom)
        assert result.status == "done"                  # olay hatası koşuyu bozmaz

    def test_structured_events_are_ordered_and_correlated(self, tmp_path):
        events = []
        result = run_agent("x", deps=_deps(tmp_path, _plan(PlanStep("list_dir", {}, "listele"))), on_event=events.append)
        runtime = [event.runtime for event in events if event.runtime is not None]
        names = [event.event_type for event in runtime]
        assert names == [
            "agent.run.started", "agent.planning.started", "agent.planning.completed",
            "agent.tool.started", "agent.tool.completed", "agent.run.completed",
        ]
        assert result.run_id and {event.run_id for event in runtime} == {result.run_id}
        assert runtime[3].step_id == "step_1"
        assert runtime[3].metadata == {"tool": "list_dir"}

    def test_cancel_during_planning_is_terminal_and_correlated(self, tmp_path):
        events = []
        result = run_agent(
            "x", deps=_deps(tmp_path, _plan(), should_stop=lambda: True), on_event=events.append
        )
        assert result.status == "cancelled"
        assert result.termination_reason == "cancelled_before_planning"
        assert events[-1].runtime.event_type == "agent.run.cancelled"

    def test_global_deadline_stops_before_first_tool(self, tmp_path):
        events = []
        result = run_agent(
            "x", deps=_deps(tmp_path, _plan(PlanStep("list_dir", {}, "listele")), run_timeout_sec=0.000001),
            on_event=events.append,
        )
        assert result.status == "timed_out"
        assert result.termination_reason == "deadline_before_planning"
        assert events[-1].runtime.event_type == "agent.run.timed_out"

    def test_global_deadline_after_tool_preserves_partial_logs(self, tmp_path):
        def slow_tool(name, params, ctx):
            time.sleep(0.01)
            return ToolResult(ok=True, output="tamam")

        result = run_agent(
            "x", deps=_deps(
                tmp_path, _plan(PlanStep("list_dir", {}, "listele"), PlanStep("read_file", {}, "oku")),
                run_tool=slow_tool, run_timeout_sec=0.001,
            )
        )
        assert result.status == "timed_out"
        assert len(result.logs) == 1
        assert result.termination_reason == "deadline_after_tool"

    def test_browser_cleanup_runs_on_timeout(self, tmp_path):
        closed = []
        client = SimpleNamespace(close_tab=lambda **kwargs: closed.append(kwargs))
        ctx = ToolContext(workspace=tmp_path, browser_client=client, browser_tabs={"t1"})
        deps = AgentRunDeps(plan=lambda req: _plan(), ctx=ctx, run_timeout_sec=0.000001)
        run_agent("x", deps=deps)
        assert closed == [{"user_id": "jarvis", "tab_id": "t1"}]


class TestAudit:
    """Kalıcı denetim günlüğü: her koşu (her çıkış yolu) audit callback'ine geçer."""

    def test_audit_receives_request_and_result(self, tmp_path):
        seen = []
        plan = _plan(PlanStep("list_dir", {}, ""))
        deps = _deps(tmp_path, plan, audit=lambda req, res: seen.append((req, res)))
        run_agent("incele", deps=deps)
        assert len(seen) == 1
        assert seen[0][0] == "incele"
        assert seen[0][1].status == "done"

    def test_audit_called_on_failed_plan(self, tmp_path):
        seen = []

        def boom(req):
            raise RuntimeError("ollama down")

        deps = _deps(tmp_path, None, plan=boom, audit=lambda req, res: seen.append(res))
        run_agent("x", deps=deps)
        assert seen and seen[0].status == "failed"     # başarısız koşu da kayda geçer

    def test_audit_failure_does_not_break_run(self, tmp_path):
        def boom(req, res):
            raise OSError("disk dolu")

        result = run_agent("x", deps=_deps(tmp_path, _plan(PlanStep("list_dir", {}, "")), audit=boom))
        assert result.status == "done"                 # günlük hatası koşuyu bozmaz

    def test_skill_provenance_is_attached_to_result(self, tmp_path):
        seen = []
        skill = SkillContext("meeting_notes_extractor", "/trusted/SKILL.md", "a" * 64, "özetle")
        deps = _deps(
            tmp_path,
            _plan(),
            skill=skill,
            audit=lambda _request, result: seen.append(result),
        )
        result = run_agent("toplantıyı özetle", deps=deps)
        assert result.skill_id == "meeting_notes_extractor"
        assert result.skill_source == "/trusted/SKILL.md"
        assert result.skill_hash == "a" * 64
        assert seen[0].skill_id == result.skill_id


class TestCancellation:
    """İptal: her adımdan önce should_stop() kontrol edilir; True → koşu nazikçe durur."""

    def test_cancel_midrun_stops_remaining_steps(self, tmp_path):
        rt = _fake_run_tool()
        steps = [PlanStep("list_dir", {"path": str(i)}, "") for i in range(5)]
        deps = _deps(tmp_path, _plan(*steps), run_tool=rt,
                     should_stop=lambda: len(rt.calls) >= 1)
        run_agent("x", deps=deps)
        assert len(rt.calls) == 1                       # iptal sonrası yeni adım çalışmadı

    def test_cancel_midrun_marks_status_cancelled(self, tmp_path):
        rt = _fake_run_tool()
        steps = [PlanStep("list_dir", {"path": str(i)}, "") for i in range(5)]
        deps = _deps(tmp_path, _plan(*steps), run_tool=rt,
                     should_stop=lambda: len(rt.calls) >= 1)
        result = run_agent("x", deps=deps)
        assert result.status == "cancelled"
        assert result.spoken                            # yine sesli geri-bildirim var

    def test_cancel_run_still_audited(self, tmp_path):
        seen = []
        steps = [PlanStep("list_dir", {"path": str(i)}, "") for i in range(3)]
        deps = _deps(tmp_path, _plan(*steps),
                     should_stop=lambda: True,          # ilk adımdan iptal
                     audit=lambda req, res: seen.append(res))
        run_agent("x", deps=deps)
        assert len(seen) == 1                           # iptal de denetim günlüğüne geçer

    def test_should_stop_exception_does_not_break_run(self, tmp_path):
        def boom():
            raise RuntimeError("iptal kontrolü çöktü")
        result = run_agent(
            "x", deps=_deps(tmp_path, _plan(PlanStep("list_dir", {}, "")), should_stop=boom))
        assert result.status == "done"                  # bozuk iptal kontrolü koşuyu durdurmaz

    def test_pending_approval_is_invalidated_on_cancel(self):
        import threading

        cancelled = threading.Event()
        stale_response = threading.Event()

        def dialog(_prompt, *, cancel_event):
            cancel_event.wait(timeout=1)
            stale_response.set()
            return True

        cancelled.set()
        assert _safe_confirm(dialog, "onay", interrupted=cancelled.is_set) is False
        assert stale_response.wait(timeout=1)


class TestNeverRaises:
    def test_decide_exception_is_contained(self, tmp_path):
        def boom(t, p):
            raise RuntimeError("policy çöktü")
        plan = _plan(PlanStep("list_dir", {}, ""))
        result = run_agent("x", deps=_deps(tmp_path, plan, decide=boom))
        # tek adım hatası tüm koşuyu öldürmemeli
        assert result.status in ("partial", "done", "failed")
