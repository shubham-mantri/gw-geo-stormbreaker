"""Wiring tests (TRD §11): `build_runtime` idempotency (C1) + the S3-backed archive (I6).

Hermetic (TRD §12): no live AWS/API calls. `build_runtime` constructs a boto3 S3 client but
issues no request; the `S3RawArchive` round-trip runs entirely under moto's `mock_aws`. The
local-filesystem archive (W2 live wiring) writes only under a `tmp_path`.
"""

import json
from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from gw_geo.capture.base import CapturePage
from gw_geo.common.config import Settings
from gw_geo.common.wiring import (
    LocalFileArchive,
    S3RawArchive,
    build_runtime,
    configured_engine_names,
)
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
        # Keep the archive-backend selection deterministic too, so the default-S3 assertion below
        # can't be flipped by a host that exports GEO_RAW_ARCHIVE_BACKEND=local.
        "GEO_RAW_ARCHIVE_BACKEND",
        "GEO_RAW_ARCHIVE_DIR",
        # Keep the capture-backend selection deterministic: a host exporting
        # GEO_CAPTURE_BACKEND=local must not make a default `Settings()` build a local browser.
        "GEO_CAPTURE_BACKEND",
        "GEO_LOCAL_BROWSER_PROFILE_DIR",
        "GEO_LOCAL_BROWSER_CHANNEL",
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


# --- M5 local browser capture: capture-backend selection -------------------------------------


def test_default_capture_backend_none_registers_no_playwright_engines(clean_registry):
    """`capture_backend="none"` (default) builds no capturer -> the 3 surfaces stay unregistered."""
    rt = build_runtime(Settings())  # default backend, no injected capture, no fleet refs
    assert not ({"google_ai_overviews", "chatgpt", "grok"} & set(rt["engines"]))


def test_build_runtime_selects_local_capture_when_backend_local(clean_registry, monkeypatch):
    """`capture_backend="local"` wires the 3 Playwright surfaces to a `LocalCaptureClient`.

    `_build_local_capture` is patched to a fake so no real browser/profile is touched -- this pins
    only the selection wiring (the LocalCaptureClient unit is tested in tests/capture/test_local.py).
    """
    built: list[Settings] = []

    def fake_build_local(settings):
        built.append(settings)
        return _capture()

    monkeypatch.setattr("gw_geo.common.wiring._build_local_capture", fake_build_local)
    rt = build_runtime(Settings(capture_backend="local", local_browser_profile_dir="/tmp/p"))

    assert len(built) == 1  # the local builder was consulted
    assert {"google_ai_overviews", "chatgpt", "grok"} <= set(rt["engines"])


def test_injected_capture_wins_over_local_backend(clean_registry, monkeypatch):
    """An injected `capture=` always overrides `capture_backend="local"` (the test seam wins)."""

    def boom(settings):
        raise AssertionError("_build_local_capture must not be called when capture is injected")

    monkeypatch.setattr("gw_geo.common.wiring._build_local_capture", boom)
    rt = build_runtime(Settings(capture_backend="local"), capture=_capture())
    assert {"google_ai_overviews", "chatgpt", "grok"} <= set(rt["engines"])


def test_build_local_capture_builds_local_client_without_a_browser(clean_registry):
    """`_build_local_capture` returns a `LocalCaptureClient` from settings; nothing launches."""
    from gw_geo.capture.local import LocalCaptureClient
    from gw_geo.common.wiring import _build_local_capture

    client = _build_local_capture(
        Settings(
            capture_backend="local",
            local_browser_profile_dir="/tmp/p",
            local_browser_channel="chrome",
            playwright_headless=True,
        )
    )
    assert isinstance(client, LocalCaptureClient)


# --- W2 live wiring: local-filesystem archive + archive-backend selection --------------------


def test_local_file_archive_put_round_trip(tmp_path):
    """`LocalFileArchive.put` writes the payload as JSON under base_dir and returns the key ref.

    The local counterpart to the S3 `I6` round-trip above -- the fix that lets a local-only run
    persist a raw snapshot (and thus flow visibility data to the dashboard) without any S3 bucket.
    """
    archive = LocalFileArchive(str(tmp_path))
    key = "probe/t1/b1/perplexity/abc123.json"
    payload = {"answer": "Foo is best", "citations": ["https://foo.com"]}

    ref = archive.put(key, payload)

    assert ref == key
    written = tmp_path / key
    assert written.exists()  # parent dirs were created
    assert json.loads(written.read_text(encoding="utf-8")) == payload


def test_build_runtime_selects_local_archive_when_configured(clean_registry, tmp_path):
    """`raw_archive_backend="local"` -> a `LocalFileArchive` (no boto3 S3 client built at all)."""
    settings = Settings(raw_archive_backend="local", raw_archive_dir=str(tmp_path))
    rt = build_runtime(settings)
    assert isinstance(rt["archive"], LocalFileArchive)


def test_build_runtime_defaults_to_s3_archive(clean_registry):
    """Default backend is unchanged: `build_runtime` still builds an `S3RawArchive`."""
    settings = Settings(s3_bucket="gw-geo-test")
    rt = build_runtime(settings)
    assert isinstance(rt["archive"], S3RawArchive)


def test_configured_engine_names_mirrors_build_runtime_api_engines():
    """`configured_engine_names` returns exactly the API-keyed engines `build_runtime` registers."""
    s = Settings(
        perplexity_api_key="p",
        openai_api_key="o",
        anthropic_api_key="a",
        deepseek_api_key="d",  # no deepseek_enabled -> excluded, like build_runtime
    )
    assert configured_engine_names(s) == ["perplexity", "openai", "claude"]
    assert configured_engine_names(Settings()) == []
