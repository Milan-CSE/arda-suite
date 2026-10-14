"""agent_verifier testleri — adım çıktısının gerçekten gerçekleştiğini doğrula."""
from arda.agent_models import PlanStep, ToolResult
from arda.agent_tools import ToolContext
from arda.agent_verifier import verify_step


def _ctx(tmp_path):
    return ToolContext(workspace=tmp_path)


class TestVerifyWriteFile:
    def test_verified_when_content_matches(self, tmp_path):
        (tmp_path / "a.txt").write_text("selam", encoding="utf-8")
        step = PlanStep("write_file", {"path": "a.txt", "content": "selam"}, "yaz")
        result = ToolResult(ok=True, output="yazıldı")
        ok, _ = verify_step(step, result, _ctx(tmp_path))
        assert ok is True

    def test_not_verified_when_content_differs(self, tmp_path):
        (tmp_path / "a.txt").write_text("baska icerik", encoding="utf-8")
        step = PlanStep("write_file", {"path": "a.txt", "content": "beklenen"}, "yaz")
        result = ToolResult(ok=True, output="yazıldı")
        ok, msg = verify_step(step, result, _ctx(tmp_path))
        assert ok is False
        assert msg

    def test_not_verified_when_file_missing(self, tmp_path):
        step = PlanStep("write_file", {"path": "yok.txt", "content": "x"}, "yaz")
        result = ToolResult(ok=True)
        ok, _ = verify_step(step, result, _ctx(tmp_path))
        assert ok is False


class TestVerifyEditFile:
    def test_verified_when_disk_matches_result_content(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 42\n", encoding="utf-8")
        step = PlanStep("edit_file", {"path": "a.py", "old": "x = 1", "new": "x = 42"}, "")
        result = ToolResult(ok=True, data={"content": "x = 42\n"})
        ok, _ = verify_step(step, result, _ctx(tmp_path))
        assert ok is True

    def test_not_verified_when_mismatch(self, tmp_path):
        (tmp_path / "a.py").write_text("baska\n", encoding="utf-8")
        step = PlanStep("edit_file", {"path": "a.py", "old": "x", "new": "y"}, "")
        result = ToolResult(ok=True, data={"content": "x = 42\n"})
        ok, _ = verify_step(step, result, _ctx(tmp_path))
        assert ok is False


class TestVerifyReadFile:
    def test_verified_with_content(self, tmp_path):
        step = PlanStep("read_file", {"path": "x"}, "oku")
        result = ToolResult(ok=True, data={"content": "abc"})
        ok, _ = verify_step(step, result, _ctx(tmp_path))
        assert ok is True

    def test_not_verified_without_content(self, tmp_path):
        step = PlanStep("read_file", {"path": "x"}, "oku")
        result = ToolResult(ok=True, data=None)
        ok, _ = verify_step(step, result, _ctx(tmp_path))
        assert ok is False


class TestVerifyGeneric:
    def test_failed_result_never_verified(self, tmp_path):
        step = PlanStep("list_dir", {}, "")
        result = ToolResult(ok=False, error="boom")
        ok, msg = verify_step(step, result, _ctx(tmp_path))
        assert ok is False

    def test_ok_readonly_tools_verified(self, tmp_path):
        for tool in ("list_dir", "search_files", "run_tests", "open_app"):
            step = PlanStep(tool, {}, "")
            ok, _ = verify_step(step, ToolResult(ok=True), _ctx(tmp_path))
            assert ok is True, tool

    def test_never_raises_on_bad_input(self, tmp_path):
        step = PlanStep("write_file", {"path": "../../escape", "content": "x"}, "")
        ok, _ = verify_step(step, ToolResult(ok=True), _ctx(tmp_path))
        assert ok is False  # PathEscape yakalandı, raise yok
