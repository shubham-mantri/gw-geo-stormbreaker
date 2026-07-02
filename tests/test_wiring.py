"""Wiring tests (TRD §11): `build_runtime` idempotency (C1) + the S3-backed archive (I6).

Hermetic (TRD §12): no live AWS/API calls. `build_runtime` constructs a boto3 S3 client but
issues no request; the `S3RawArchive` round-trip runs entirely under moto's `mock_aws`.
"""

import json
from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from gw_geo.common.config import Settings
from gw_geo.common.wiring import S3RawArchive, build_runtime
from gw_geo.measurement.probe import base


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

    first = build_runtime(settings)
    assert first["engines"] == ["perplexity", "openai"]
    assert {adapter.name for adapter in base.all_adapters()} == {"perplexity", "openai"}

    # Second call in the same process previously raised ValueError (duplicate adapter name).
    second = build_runtime(settings)
    assert second["engines"] == ["perplexity", "openai"]
    assert {adapter.name for adapter in base.all_adapters()} == {"perplexity", "openai"}


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
