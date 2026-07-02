"""Fixture-backed respx transports for the engine-adapter contract suite.

To add an adapter: add one `CASES` row in `test_adapter_contract.py` and one `mock_for`
branch here that registers the adapter's HTTP route against its recorded fixture JSON
under `tests/fixtures/answers/`.
"""

import json
import pathlib
from typing import Any

import httpx
import respx

# Anchor on this file (tests/measurement/probe/fixtures.py -> tests/) rather than the cwd, so
# fixtures resolve regardless of the directory pytest is invoked from.
_FIXTURES_DIR = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "answers"


def _load_fixture(filename: str) -> dict[str, Any]:
    """Load a recorded provider payload from `tests/fixtures/answers/<filename>`."""
    return json.loads((_FIXTURES_DIR / filename).read_text())


def mock_for(name: str) -> None:
    """Register the respx route + recorded fixture response for adapter `name`.

    Raises:
        ValueError: no branch is defined for `name`.
    """
    if name == "perplexity":
        respx.post("https://api.perplexity.ai/chat/completions").mock(
            return_value=httpx.Response(200, json=_load_fixture("perplexity_api.json"))
        )
    elif name == "openai":
        respx.post("https://api.openai.com/v1/responses").mock(
            return_value=httpx.Response(200, json=_load_fixture("openai_api.json"))
        )
    elif name == "gemini":
        # Gemini puts the model in the path (`.../models/<model>:generateContent`), so match by
        # regex on the host + `:generateContent` operation rather than a fixed model URL.
        respx.route(
            method="POST",
            url__regex=r"https://generativelanguage\.googleapis\.com/.*:generateContent",
        ).mock(return_value=httpx.Response(200, json=_load_fixture("gemini_api.json")))
    elif name == "claude":
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, json=_load_fixture("claude_api.json"))
        )
    elif name == "copilot":
        # Copilot/Bing bills per call and varies the body by market; route on the host so the
        # fixture backs any Bing Search API-family path this adapter posts to.
        respx.route(method="POST", host="api.bing.microsoft.com").mock(
            return_value=httpx.Response(200, json=_load_fixture("copilot_api.json"))
        )
    elif name == "deepseek":
        respx.post("https://api.deepseek.com/chat/completions").mock(
            return_value=httpx.Response(200, json=_load_fixture("deepseek_api.json"))
        )
    elif name in ("google_ai_overviews", "chatgpt", "grok"):
        # Playwright consumer-surface adapters make no HTTP call: their rendered page is served
        # directly by a FakeCaptureClient in the CASES factory, so respx has nothing to route.
        return
    else:
        raise ValueError(f"no mock_for branch registered for adapter {name!r}")
