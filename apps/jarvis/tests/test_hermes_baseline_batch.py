import sys

from scripts.run_hermes_baseline_batch import run_batch


def test_batch_creates_one_evidence_package_per_fixture_and_repeat(tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "one.txt").write_text("one", encoding="utf-8")
    (fixtures / "two.txt").write_text("two", encoding="utf-8")
    evidence = tmp_path / "evidence"

    results = run_batch(
        fixtures,
        evidence,
        command=[sys.executable, "-c", "print('ok')"],
        repeats=2,
        timeout_seconds=5,
    )

    assert len(results) == 4
    assert len(list((evidence / "runs").glob("*.json"))) == 4
    assert len(list((evidence / "workspaces").iterdir())) == 4
    assert (evidence / "summary.json").exists()
    assert all(item["exit_status"] == 0 for item in results)
