"""Hermetic tests for `BedrockLLMClient` -- no real AWS calls."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from gw_geo.content.llm_bedrock import BedrockLLMClient


def _fake_converse_response(tool_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "test-id",
                            "name": "record_generated_content",
                            "input": tool_input,
                        }
                    }
                ],
            }
        },
        "stopReason": "tool_use",
    }


class TestBedrockLLMClient:
    def test_complete_returns_structured_output(self) -> None:
        expected = {"title": "Test", "body_markdown": "# Hello", "schema_jsonld": {}}
        mock_client = MagicMock()
        mock_client.converse.return_value = _fake_converse_response(expected)

        client = BedrockLLMClient(model_id="us.anthropic.claude-sonnet-4-20250514", client=mock_client)
        result = client.complete(system="You are helpful.", prompt="Generate content.")

        assert result == expected
        mock_client.converse.assert_called_once()
        call_kwargs = mock_client.converse.call_args[1]
        assert call_kwargs["modelId"] == "us.anthropic.claude-sonnet-4-20250514"
        assert call_kwargs["system"] == [{"text": "You are helpful."}]
        assert call_kwargs["messages"] == [{"role": "user", "content": [{"text": "Generate content."}]}]

    def test_complete_with_custom_schema(self) -> None:
        custom_schema = {
            "type": "object",
            "properties": {"claims": {"type": "array", "items": {"type": "string"}}},
            "required": ["claims"],
        }
        expected = {"claims": ["claim 1", "claim 2"]}
        mock_client = MagicMock()
        mock_client.converse.return_value = _fake_converse_response(expected)

        client = BedrockLLMClient(model_id="us.anthropic.claude-sonnet-4-20250514", client=mock_client)
        result = client.complete(system="Extract claims.", prompt="Some text.", schema=custom_schema)

        assert result == expected
        call_kwargs = mock_client.converse.call_args[1]
        tool_spec = call_kwargs["toolConfig"]["tools"][0]["toolSpec"]
        assert tool_spec["inputSchema"]["json"] == custom_schema

    def test_complete_forces_tool_use(self) -> None:
        mock_client = MagicMock()
        mock_client.converse.return_value = _fake_converse_response({"title": "x", "body_markdown": "y", "schema_jsonld": {}})

        client = BedrockLLMClient(model_id="test-model", client=mock_client)
        client.complete(system="sys", prompt="p")

        call_kwargs = mock_client.converse.call_args[1]
        assert call_kwargs["toolConfig"]["toolChoice"] == {"tool": {"name": "record_generated_content"}}

    def test_complete_raises_on_missing_tool_use(self) -> None:
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"role": "assistant", "content": [{"text": "some text"}]}},
            "stopReason": "end_turn",
        }

        client = BedrockLLMClient(model_id="test-model", client=mock_client)
        with pytest.raises(ValueError, match="toolUse content block"):
            client.complete(system="sys", prompt="p")

    def test_lazy_client_construction(self) -> None:
        client = BedrockLLMClient(model_id="test-model", region="us-west-2")
        assert client._client is None

    def test_region_passed_to_client(self) -> None:
        mock_client = MagicMock()
        mock_client.converse.return_value = _fake_converse_response({"title": "t", "body_markdown": "b", "schema_jsonld": {}})

        client = BedrockLLMClient(model_id="test-model", region="eu-west-1", client=mock_client)
        client.complete(system="s", prompt="p")
        assert client._region == "eu-west-1"
