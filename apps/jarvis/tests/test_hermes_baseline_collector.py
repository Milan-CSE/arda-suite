import json
import sys

from scripts.capture_hermes_baseline import capture_run, workspace_manifest


def test_workspace_manifest_is_deterministic_and_hashes_files(tmp_path):
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    first = workspace_manifest(tmp_path)
    second = workspace_manifest(tmp_path)
    assert first == second
    assert [entry["path"] for entry in first] == ["a.txt", "b.txt"]
    assert all(entry["sha256"] for entry in first)


def test_capture_run_records_process_and_workspace_change(tmp_path):
    result = capture_run(
        [sys.executable, "-c", "from pathlib import Path; Path('created.txt').write_text('ok')"],
        workspace=tmp_path,
        prompt=b"fixture prompt",
        timeout_seconds=5,
    )
    assert result["exit_status"] == 0
    assert result["timed_out"] is False
    assert result["workspace_before_hash"] != result["workspace_after_hash"]
    assert any(item["path"] == "created.txt" for item in result["workspace_after"])
    assert result["stdout_sha256"]
    assert result["stderr_sha256"]


def test_capture_run_marks_timeout(tmp_path):
    result = capture_run(
        [sys.executable, "-c", "import time; time.sleep(1)"],
        workspace=tmp_path,
        prompt=b"fixture prompt",
        timeout_seconds=0.01,
    )
    assert result["timed_out"] is True
    assert result["exit_status"] is None
