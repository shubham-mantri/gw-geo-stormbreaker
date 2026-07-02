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

_FIXTURES_DIR = pathlib.Path("tests/fixtures/answers")


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
    else:
        raise ValueError(f"no mock_for branch registered for adapter {name!r}")
