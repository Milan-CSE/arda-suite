from arda.hermes_evaluation import HERMES_PROVIDER_FIXTURES, evaluate_tool_trace


def fixture(fixture_id):
    return next(item for item in HERMES_PROVIDER_FIXTURES if item.fixture_id == fixture_id)


def test_fixture_set_has_read_abstention_and_boundary_cases():
    ids = {item.fixture_id for item in HERMES_PROVIDER_FIXTURES}
    assert ids == {
        "episode_status_read_v1",
        "insufficient_evidence_abstention_v1",
        "mutation_boundary_refusal_v1",
    }


def test_valid_read_trace_passes():
    result = evaluate_tool_trace(fixture("episode_status_read_v1"), ["arda_episode_status"])
    assert result.passed is True
    assert result.violations == ()


def test_missing_evidence_requires_abstention():
    result = evaluate_tool_trace(
        fixture("insufficient_evidence_abstention_v1"),
        ["arda_verification_status"],
        abstained=True,
    )
    assert result.passed is True


def test_boundary_fixture_rejects_mutation_or_shell_attempts():
    result = evaluate_tool_trace(
        fixture("mutation_boundary_refusal_v1"),
        ["shell", "arda_request_approval"],
    )
    assert result.passed is False
    assert "forbidden_tool_attempt:shell" in result.violations
    assert "tool_not_allowed:arda_request_approval" in result.violations

