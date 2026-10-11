from __future__ import annotations

import json

import pytest

from gildranews.codex_bridge import (
    BridgeError,
    _codex_command,
    build_completion_request,
    encode_agui_response,
)


def test_build_completion_request_preserves_roles_and_forces_luna_6() -> None:
    payload = {
        "messages": [
            {"role": "system", "content": "Return verified JSON."},
            {"role": "user", "content": "Article body"},
        ],
        "forwardedProps": {"openbotAgentModel": "untrusted-model"},
    }

    request = build_completion_request(payload)

    assert request.model == "gpt-6-luna"
    assert "<system_instructions>\nReturn verified JSON.\n</system_instructions>" in request.prompt
    assert "<user_data>\nArticle body\n</user_data>" in request.prompt


@pytest.mark.parametrize("payload", [{}, {"messages": []}, {"messages": "bad"}])
def test_build_completion_request_rejects_missing_messages(payload) -> None:
    with pytest.raises(BridgeError):
        build_completion_request(payload)


def test_encode_agui_response_can_be_parsed_by_bot_client() -> None:
    encoded = encode_agui_response('{"ok":true}', "message-1").decode()
    events = [
        json.loads(line.removeprefix("data: "))
        for line in encoded.splitlines()
        if line.startswith("data: ")
    ]

    assert [event["type"] for event in events] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
    ]
    assert events[1]["delta"] == '{"ok":true}'


def test_codex_command_disables_shell_tools(tmp_path) -> None:
    command = _codex_command(MODEL := "gpt-6-luna", tmp_path, tmp_path / "out")

    assert MODEL in command
    assert [command[i + 1] for i, value in enumerate(command[:-1]) if value == "--disable"] == [
        "shell_tool", "unified_exec",
    ]
    assert all(
        not command[i + 1].startswith("-")
        for i, value in enumerate(command[:-1])
        if value == "-c"
    )
    assert "permissions.gildranews.network.enabled=false" in command
    assert "--ephemeral" in command
