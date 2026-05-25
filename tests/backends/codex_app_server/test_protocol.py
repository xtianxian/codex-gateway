from __future__ import annotations

from codex_gateway.backends.codex_app_server.protocol import (
    generated_protocol_methods,
    generated_server_request_methods,
)


def test_generated_protocol_methods_are_loaded_from_schema_bundle() -> None:
    methods = generated_protocol_methods()

    assert "thread/start" in methods
    assert "turn/start" in methods
    assert "model/list" in methods
    assert "experimentalFeature/list" in methods


def test_generated_server_request_methods_are_loaded_from_server_request_schema() -> None:
    assert generated_server_request_methods() == frozenset(
        {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "item/tool/requestUserInput",
            "mcpServer/elicitation/request",
            "item/permissions/requestApproval",
            "item/tool/call",
            "account/chatgptAuthTokens/refresh",
            "attestation/generate",
            "applyPatchApproval",
            "execCommandApproval",
        }
    )
