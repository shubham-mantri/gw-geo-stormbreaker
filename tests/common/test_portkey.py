"""Hermetic tests for the raw Portkey HTTP client (`gw_geo.common.portkey.PortkeyClient`).

Every request is served by a mocked `httpx` transport (`respx`) -- there is never a live call to
`https://api.portkey.ai`. Provider routing / virtual keys live in the dashboard Config
(`pc-portke-0dd3de`), so these tests only assert the request contract the gateway itself owns:
the Portkey headers, the OpenAI-shaped request body, and response parsing.
"""

import json

import httpx
import pytest
import respx

from gw_geo.common.portkey import PortkeyClient

BASE = "https://api.portkey.ai/v1"


def _client() -> PortkeyClient:
    return PortkeyClient(api_key="pk-test", config="pc-portke-0dd3de")


def test_empty_api_key_raises_runtimeerror() -> None:
    with pytest.raises(RuntimeError):
        PortkeyClient(api_key="", config="pc-portke-0dd3de")


@respx.mock
def test_chat_completion_sends_portkey_headers_and_openai_body_and_parses() -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": '{"ok": true}'}}]}
        )
    )
    client = _client()
    result = client.chat_completion(
        model="claude-haiku-4-5-20251001",
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "x", "schema": {"type": "object"}, "strict": True},
        },
        max_tokens=512,
    )
    assert route.called
    req = route.calls.last.request
    # Portkey headers (case-insensitive lookup via httpx.Headers).
    assert req.headers["content-type"] == "application/json"
    assert req.headers["x-portkey-api-key"] == "pk-test"
    assert req.headers["x-portkey-config"] == "pc-portke-0dd3de"
    assert req.headers["x-portkey-strict-open-ai-compliance"] == "false"
    # OpenAI-shaped request body.
    body = json.loads(req.content)
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert body["max_tokens"] == 512
    assert body["response_format"]["type"] == "json_schema"
    # Response returned parsed and verbatim.
    assert result == {"choices": [{"message": {"content": '{"ok": true}'}}]}


@respx.mock
def test_chat_completion_omits_response_format_when_none() -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    )
    _client().chat_completion(model="m", messages=[{"role": "user", "content": "x"}])
    body = json.loads(route.calls.last.request.content)
    assert "response_format" not in body
    assert body["max_tokens"] == 4096  # documented default


@respx.mock
def test_chat_completion_sends_tools_and_tool_choice_when_given() -> None:
    # Structured output is now requested via OpenAI-style function calling (tools + forced
    # tool_choice), which Portkey maps to Anthropic tool-use -- lenient about free-form object
    # params -- instead of response_format (Anthropic strict structured-output, which 400s on a
    # free-form object schema).
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    )
    tools = [
        {
            "type": "function",
            "function": {"name": "record_x", "description": "d", "parameters": {"type": "object"}},
        }
    ]
    tool_choice = {"type": "function", "function": {"name": "record_x"}}
    _client().chat_completion(
        model="m",
        messages=[{"role": "user", "content": "x"}],
        tools=tools,
        tool_choice=tool_choice,
    )
    body = json.loads(route.calls.last.request.content)
    assert body["tools"] == tools
    assert body["tool_choice"] == tool_choice
    assert "response_format" not in body


@respx.mock
def test_chat_completion_omits_tools_and_tool_choice_when_none() -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    )
    _client().chat_completion(model="m", messages=[{"role": "user", "content": "x"}])
    body = json.loads(route.calls.last.request.content)
    assert "tools" not in body
    assert "tool_choice" not in body


@respx.mock
def test_chat_completion_sets_metadata_header_when_given() -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    )
    _client().chat_completion(
        model="m", messages=[{"role": "user", "content": "x"}], metadata={"tenant": "t1"}
    )
    assert json.loads(route.calls.last.request.headers["x-portkey-metadata"]) == {"tenant": "t1"}


@respx.mock
def test_chat_completion_omits_metadata_header_by_default() -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    )
    _client().chat_completion(model="m", messages=[{"role": "user", "content": "x"}])
    assert "x-portkey-metadata" not in route.calls.last.request.headers


@respx.mock
def test_chat_completion_raises_on_http_error() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        _client().chat_completion(model="m", messages=[{"role": "user", "content": "x"}])


@respx.mock
def test_embedding_posts_input_and_returns_vector() -> None:
    route = respx.post(f"{BASE}/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})
    )
    vec = _client().embedding(model="text-embedding-3-large", text="hello world")
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body == {"model": "text-embedding-3-large", "input": "hello world"}
    assert vec == [0.1, 0.2, 0.3]
    assert route.calls.last.request.headers["x-portkey-api-key"] == "pk-test"


@respx.mock
def test_custom_base_url_trailing_slash_is_normalized() -> None:
    route = respx.post("https://gw.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    )
    PortkeyClient(
        api_key="k", config="c", base_url="https://gw.example.com/v1/"
    ).chat_completion(model="m", messages=[{"role": "user", "content": "x"}])
    assert route.called
