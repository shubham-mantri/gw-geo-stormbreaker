"""Wiring tests (TRD §11): `build_runtime` idempotency (C1) + the S3-backed archive (I6).

Hermetic (TRD §12): no live AWS/API calls. `build_runtime` constructs a boto3 S3 client but
issues no request; the `S3RawArchive` round-trip runs entirely under moto's `mock_aws`.
"""

import json
from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from gw_geo.capture.base import CapturePage
from gw_geo.common.config import Settings
from gw_geo.common.wiring import S3RawArchive, build_runtime
from gw_geo.measurement.probe import base
from tests.capture.fakes import FakeCaptureClient


@pytest.fixture(autouse=True)
def _hermetic_wiring_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make `build_runtime`'s engine registration depend only on each test's explicit `Settings`.

    Gives boto3 a region so S3-client construction never depends on machine AWS config
    (`build_runtime` always builds an `S3RawArchive`); strips any ambient `GEO_*` engine env vars
    so a host with real keys exported can't leak extra engines into a `Settings(...)` built here;
    and leaves the process-global adapter registry empty for other test modules on the way out.
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    for var in (
        "GEO_PERPLEXITY_API_KEY",
        "GEO_OPENAI_API_KEY",
        "GEO_ANTHROPIC_API_KEY",
        "GEO_GEMINI_API_KEY",
        "GEO_COPILOT_API_KEY",
        "GEO_DEEPSEEK_API_KEY",
        "GEO_DEEPSEEK_ENABLED",
        "GEO_PROXY_POOL_CONFIG_REF",
        "GEO_ACCOUNT_POOL_CONFIG_REF",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
    base.clear_registry()


@pytest.fixture
def clean_registry() -> Iterator[None]:
    """Isolate the process-global adapter registry from other tests."""
    base.clear_registry()
    yield
    base.clear_registry()


def test_build_runtime_is_idempotent_across_calls(clean_registry, monkeypatch):
    """C1: a warm Lambda / second CLI call must rebuild the registry, not raise on duplicates."""
    # Region only (no request is made); guards against a machine with no AWS region configured.
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    settings = Settings(
        perplexity_api_key="pk-dummy",
        openai_api_key="ok-dummy",
        anthropic_api_key="ak-dummy",
        s3_bucket="gw-geo-test",
    )

    # `claude` registers whenever `anthropic_api_key` is set (M1-T04/T18); no capture is injected
    # and no fleet config refs are set, so the three Playwright engines are not registered here.
    expected = ["perplexity", "openai", "claude"]
    first = build_runtime(settings)
    assert first["engines"] == expected
    assert {adapter.name for adapter in base.all_adapters()} == set(expected)

    # Second call in the same process previously raised ValueError (duplicate adapter name).
    second = build_runtime(settings)
    assert second["engines"] == expected
    assert {adapter.name for adapter in base.all_adapters()} == set(expected)


def test_build_runtime_skips_engines_without_keys(clean_registry, monkeypatch):
    """An engine whose API key is unset is not registered (graceful degradation, TRD §7)."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    settings = Settings(perplexity_api_key="pk-dummy", openai_api_key="", s3_bucket="gw-geo-test")

    runtime = build_runtime(settings)

    assert runtime["engines"] == ["perplexity"]
    assert {adapter.name for adapter in base.all_adapters()} == {"perplexity"}


@mock_aws
def test_s3_raw_archive_put_stores_object_and_returns_key():
    """I6: S3RawArchive.put writes the JSON payload to S3 and returns the key ref."""
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="gw-geo-test")

    archive = S3RawArchive(bucket="gw-geo-test", client=client)
    key = "probe/t1/b1/perplexity/abc123.json"
    payload = {"answer": "Foo is best", "citations": ["https://foo.com"]}

    ref = archive.put(key, payload)

    assert ref == key
    stored = client.get_object(Bucket="gw-geo-test", Key=key)
    assert json.loads(stored["Body"].read()) == payload
    assert stored["ContentType"] == "application/json"


def _capture() -> FakeCaptureClient:
    """A hermetic `CaptureClient` serving a recorded page for each Playwright surface (T18 spec)."""
    page = CapturePage(html="<div>x</div>", final_url="https://e.com")
    return FakeCaptureClient({s: page for s in ("google_ai_overviews", "chatgpt", "grok")})


def test_registers_api_engines_by_key():
    """Each API engine registers exactly when its key is set (claude keys off anthropic_api_key)."""
    s = Settings(
        gemini_api_key="g",
        copilot_api_key="c",
        anthropic_api_key="a",
        perplexity_api_key="p",
        openai_api_key="o",
    )  # deepseek key empty
    rt = build_runtime(s, capture=_capture())
    assert {"perplexity", "openai", "gemini", "claude", "copilot"} <= set(rt["engines"])
    assert "deepseek" not in rt["engines"]  # no key


def test_deepseek_gated_on_toggle():
    """DeepSeek needs both its key *and* `deepseek_enabled` (TRD OT3, off by default)."""
    s = Settings(deepseek_api_key="d", deepseek_enabled=False)
    assert "deepseek" not in build_runtime(s, capture=_capture())["engines"]
    s2 = Settings(deepseek_api_key="d", deepseek_enabled=True)
    assert "deepseek" in build_runtime(s2, capture=_capture())["engines"]


def test_registers_playwright_engines_with_capture():
    """The three Playwright surfaces register when a `CaptureClient` is injected."""
    rt = build_runtime(Settings(), capture=_capture())
    assert {"google_ai_overviews", "chatgpt", "grok"} <= set(rt["engines"])
