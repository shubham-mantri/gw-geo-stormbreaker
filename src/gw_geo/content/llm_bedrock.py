"""AWS Bedrock `LLMClient` -- routes internal LLM calls through the Bedrock Converse API.

`BedrockLLMClient` implements the same `content.generate.LLMClient` protocol as
`AnthropicLLMClient` / `PortkeyLLMClient` / `LocalClaudeCliClient` -- `complete(*, system, prompt,
schema=None) -> dict[str, Any]` -- so all call sites (generation, extraction, guardrails,
onboarding, seeding) are backend-agnostic. Selected via `Settings.llm_gateway == "bedrock"` in
`content.gateway`.

Uses the **Converse API** (`bedrock-runtime.converse`) with `toolConfig` to force structured output
via tool-use, mirroring `AnthropicLLMClient`'s approach but translated to Bedrock's request shape.
Auth is via standard AWS credentials (env vars `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`,
IAM role, or instance profile) -- no separate API key setting needed, and `boto3` is already a
project dependency.

The `client` constructor param is the injectable seam for hermetic tests (same pattern as
`common.wiring.S3RawArchive`); when `None`, a real `boto3.client("bedrock-runtime")` is built
lazily on first `complete` call.
"""

from __future__ import annotations

from typing import Any

import boto3  # type: ignore[import-untyped]

from gw_geo.content.generate import _RESPONSE_SCHEMA

_TOOL_NAME = "record_generated_content"


class BedrockLLMClient:
    """`LLMClient` backed by AWS Bedrock's Converse API with tool-use for structured output."""

    def __init__(
        self,
        *,
        model_id: str,
        region: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._model_id = model_id
        self._region = region
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            kwargs: dict[str, Any] = {"service_name": "bedrock-runtime"}
            if self._region:
                kwargs["region_name"] = self._region
            self._client = boto3.client(**kwargs)
        return self._client

    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        effective_schema = schema if schema is not None else _RESPONSE_SCHEMA
        client = self._get_client()

        response = client.converse(
            modelId=self._model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            toolConfig={
                "tools": [
                    {
                        "toolSpec": {
                            "name": _TOOL_NAME,
                            "description": "Record the structured output.",
                            "inputSchema": {"json": effective_schema},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": _TOOL_NAME}},
            },
        )

        for block in response.get("output", {}).get("message", {}).get("content", []):
            if block.get("toolUse", {}).get("name") == _TOOL_NAME:
                result: dict[str, Any] = block["toolUse"]["input"]
                return result

        raise ValueError(
            "Bedrock Converse response did not include the expected toolUse content block."
        )


__all__ = ["BedrockLLMClient"]
