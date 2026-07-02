"""Shared engine-adapter contract suite (docs/tasks/M0-T10-adapter-contract-tests.md, TRD §12).

Every `EngineAdapter` must pass this one parametrized suite so adapters can't drift from the
contract in `measurement/probe/base.py`. To add a new adapter: add one `CASES` row here and one
`mock_for` branch in `fixtures.py` -- no other changes needed.

Reformatted from the task spec's comma-joined `import pytest, httpx, respx` (ruff E401) into
ruff-clean, one-import-per-line form; every assertion below is identical to the spec.
"""

import httpx
import pytest
import respx

from gw_geo.common.models import ProbeResult
from gw_geo.measurement.probe.base import EngineAdapter
from gw_geo.measurement.probe.openai_chatgpt import OpenAIAdapter
from gw_geo.measurement.probe.perplexity import PerplexityAdapter
from tests.measurement.probe.fixtures import mock_for

CASES = [
    ("perplexity", lambda: PerplexityAdapter(api_key="k", client=httpx.AsyncClient())),
    ("openai", lambda: OpenAIAdapter(api_key="k", client=httpx.AsyncClient())),
]


@pytest.mark.parametrize("name,factory", CASES)
@respx.mock
async def test_adapter_contract(name, factory):
    mock_for(name)
    a = factory()
    assert isinstance(a, EngineAdapter)
    r = await a.probe("best crm for smb?")
    assert isinstance(r, ProbeResult)
    assert r.engine == a.name and r.answer_text
    assert all(u.startswith("http") for u in r.cited_urls)
    assert r.latency_ms >= 0 and r.cost_usd >= 0
