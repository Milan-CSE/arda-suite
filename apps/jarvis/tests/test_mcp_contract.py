import pytest

from arda.mcp_contract import MCPGatewayContract, MCPGatewayContractError, MCPToolAccess


def test_gateway_exposes_only_core_allowlist():
    names = {tool.name for tool in MCPGatewayContract.list_tools()}
    assert names == {
        "arda_artifact_read",
        "arda_episode_status",
        "arda_evaluation_read",
            "arda_request_approval",
            "arda_provider_evaluate",
        "arda_route_proposal",
        "arda_task_intake",
        "arda_verification_status",
    }
    assert "shell" not in names
    assert "arda_provider_invoke" not in names


def test_read_tool_call_is_typed_and_non_mutating():
    descriptor = MCPGatewayContract.descriptor("arda_episode_status")
    call = MCPGatewayContract.validate_call(
        "arda_episode_status",
        {"episode_id": "episode-1"},
        correlation_id="corr-1",
        episode_id="episode-1",
    )
    assert descriptor.access is MCPToolAccess.READ
    assert descriptor.mutates_core_state is False
    assert call.contract_hash == descriptor.canonical_hash
    assert call.canonical_hash


def test_request_tools_are_intents_not_provider_calls():
    descriptor = MCPGatewayContract.descriptor("arda_route_proposal")
    call = MCPGatewayContract.validate_call(
        "arda_route_proposal",
        {
            "episode_id": "episode-1",
            "task_contract_hash": "contract-hash",
            "required_capabilities": ["coding.review"],
        },
        correlation_id="corr-2",
        episode_id="episode-1",
    )
    assert descriptor.access is MCPToolAccess.REQUEST
    assert "provider_invocation" in descriptor.forbidden_effects
    assert call.tool_name == "arda_route_proposal"


@pytest.mark.parametrize("tool_name", ["shell", "filesystem_write", "arda_provider_invoke"])
def test_unallowlisted_or_direct_provider_tools_are_rejected(tool_name):
    with pytest.raises(MCPGatewayContractError, match="not allowlisted"):
        MCPGatewayContract.validate_call(tool_name, {}, correlation_id="corr-3")


def test_missing_argument_is_rejected_before_any_execution_layer():
    with pytest.raises(MCPGatewayContractError, match="missing required arguments"):
        MCPGatewayContract.validate_call(
            "arda_task_intake",
            {"intake_id": "intake-1"},
            correlation_id="corr-4",
        )


def test_null_and_invalid_call_metadata_are_rejected():
    with pytest.raises(MCPGatewayContractError, match="cannot contain null"):
        MCPGatewayContract.validate_call(
            "arda_episode_status",
            {"episode_id": None},
            correlation_id="corr-5",
        )
    with pytest.raises(MCPGatewayContractError, match="correlation_id"):
        MCPGatewayContract.validate_call(
            "arda_episode_status",
            {"episode_id": "episode-1"},
            correlation_id="",
        )
