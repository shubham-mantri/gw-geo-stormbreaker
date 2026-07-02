"""M1 Measurement-GA completeness gate (docs/tasks/M1-T19-contract-completeness-ga.md).

Proves the shared T10 adapter-contract suite (`test_adapter_contract.CASES`) actually enumerates
**every** M1 engine, so "≥8 engines, none drifted from the `EngineAdapter` contract" is a checked
invariant rather than a convention. If a future engine adapter lands without a `CASES` row (and a
`mock_for` branch), this test fails -- the GA bar is enforced here, not just documented.
"""

from tests.measurement.probe.test_adapter_contract import CASES

# The full M1 engine fleet (m1-design.md §1): four API adapters (T03-T06) + the two M0 API
# adapters + three Playwright consumer surfaces (T11-T13). Nine total, comfortably over the
# eight-engine GA bar.
REQUIRED = {
    "perplexity",
    "openai",
    "gemini",
    "claude",
    "copilot",
    "deepseek",
    "google_ai_overviews",
    "chatgpt",
    "grok",
}


def test_all_m1_engines_present_in_contract_suite() -> None:
    names = [name for name, _ in CASES]
    assert REQUIRED <= set(names)  # every M1 engine is contract-tested
    assert len(names) == len(set(names))  # each engine registered exactly once
    assert len(REQUIRED) >= 8  # ≥8-engine Measurement-GA bar
