"""agent_audit testleri — agent koşularının kalıcı denetim günlüğü (markdown)."""
from datetime import datetime

from arda.agent_models import AgentRunResult, RiskLevel, StepLog


def _result(status="done"):
    logs = (
        StepLog("read_file", {"path": "README.md"}, "oku", RiskLevel.AUTO, "ran", True, output="içerik", verified=True),
        StepLog("write_file", {"path": ".env"}, "sır", RiskLevel.DENY, "denied", False, error="politika", verified=False),
    )
    return AgentRunResult(status=status, spoken="Bitti.", logs=logs)


_NOW = lambda: datetime(2026, 6, 19, 14, 30, 5)


class TestFormatRun:
    def test_includes_request_status_summary(self):
        from arda.agent_audit import format_run

        md = format_run("projeyi incele", _result(), now=_NOW)
        assert "projeyi incele" in md
        assert "done" in md
        assert "Bitti." in md

    def test_includes_skill_provenance(self):
        from arda.agent_audit import format_run

        result = AgentRunResult(
            status="done", spoken="Bitti.", skill_id="meeting_notes_extractor",
            skill_source="/trusted/SKILL.md", skill_hash="a" * 64,
        )
        md = format_run("toplantıyı özetle", result, now=_NOW)
        assert "meeting_notes_extractor" in md
        assert "/trusted/SKILL.md" in md
        assert "a" * 64 in md

    def test_includes_run_lifecycle_fields(self):
        from arda.agent_audit import format_run

        result = AgentRunResult(
            status="timed_out", spoken="Süre doldu.", run_id="run_1",
            started_at="2026-09-24T13:00:00+00:00", duration_ms=1200.5,
            completed_steps=2, interrupted_step="step_3", termination_reason="deadline_after_tool",
        )
        md = format_run("incele", result, now=_NOW)
        assert "run_1" in md
        assert "1200.5" in md
        assert "step_3" in md
        assert "deadline_after_tool" in md

    def test_includes_each_step(self):
        from arda.agent_audit import format_run

        md = format_run("x", _result(), now=_NOW)
        assert "read_file" in md and "ran" in md
        assert "write_file" in md and "denied" in md      # engellenen adım da kayıtta

    def test_escapes_pipe_in_output(self):
        from arda.agent_audit import format_run

        r = AgentRunResult("done", "ok", (StepLog("safe_shell", {}, "", RiskLevel.AUTO, "ran", True, output="a|b|c"),))
        md = format_run("x", r, now=_NOW)
        assert "a\\|b\\|c" in md                           # tablo bozulmaz

    def test_empty_logs_ok(self):
        from arda.agent_audit import format_run

        md = format_run("x", AgentRunResult("empty", "yok"), now=_NOW)
        assert "x" in md

    def test_includes_step_params(self):
        # Audit "NE çalıştı"yı göstermeli (riskli araçlar için kritik)
        from arda.agent_audit import format_run

        md = format_run("x", _result(), now=_NOW)
        assert "path=README.md" in md
        assert "path=.env" in md

    def test_truncates_content_param(self):
        # Büyük dosya gövdesi günlüğü şişirmemeli → yalnızca uzunluk
        from arda.agent_audit import format_run

        big = AgentRunResult("done", "ok", (
            StepLog("write_file", {"path": "x.txt", "content": "A" * 5000}, "", RiskLevel.CONFIRM, "ran", True),
        ))
        md = format_run("x", big, now=_NOW)
        assert "content[5000 kr]" in md
        assert "A" * 200 not in md


class TestWriteRunLog:
    def test_creates_daily_file(self, tmp_path):
        from arda.agent_audit import write_run_log

        path = write_run_log("incele", _result(), log_dir=tmp_path, now=_NOW)
        assert path.name == "agent-2026-06-19.md"
        assert path.exists()
        assert "incele" in path.read_text(encoding="utf-8")

    def test_appends_same_day_without_overwrite(self, tmp_path):
        from arda.agent_audit import write_run_log

        write_run_log("ilk istek", _result(), log_dir=tmp_path, now=_NOW)
        path = write_run_log("ikinci istek", _result(), log_dir=tmp_path, now=_NOW)
        text = path.read_text(encoding="utf-8")
        assert "ilk istek" in text and "ikinci istek" in text   # ikisi de korunur
        assert "---" in text                                    # ayraçla eklendi

    def test_creates_dir_if_missing(self, tmp_path):
        from arda.agent_audit import write_run_log

        target = tmp_path / "logs" / "agent"
        path = write_run_log("x", _result(), log_dir=target, now=_NOW)
        assert path.exists()
