"""Wire real dependencies (adapters, S3 archive, Claude extractor) from `Settings` (TRD §11).

`cli.py` and `handlers/run_measurement.py` both call `build_runtime()` so the CLI and the
deployed Lambda share one source of truth for how M0's real (non-test) dependencies are built.
Adapters register themselves into the shared `measurement.probe.base` registry here, at wiring
time -- the adapter modules themselves are import side-effect-free (see their docstrings), and
`RawArchive`'s S3 client is constructed lazily inside `build_runtime`, never at import time.
"""

from __future__ import annotations

import json
from typing import Any

# boto3 ships no py.typed marker / stubs, so mypy can't analyze it -- see build_runtime's
# docstring re: constructing its client lazily rather than at import time.
import boto3  # type: ignore[import-untyped]

from gw_geo.common.config import Settings
from gw_geo.measurement.parse import ClaudeExtractor, Extractor
from gw_geo.measurement.probe import base
from gw_geo.measurement.probe.openai_chatgpt import OpenAIAdapter
from gw_geo.measurement.probe.perplexity import PerplexityAdapter
from gw_geo.measurement.runner import RawArchive


class S3RawArchive:
    """`RawArchive` (TRD §5.5) backed by S3 `put_object`.

    `client` is injectable for tests; `build_runtime` leaves it unset so a real boto3 S3 client
    is constructed here, lazily, the first (and only) time this archive is built.
    """

    def __init__(self, bucket: str, client: Any | None = None) -> None:
        self._bucket = bucket
        self._client: Any = client if client is not None else boto3.client("s3")

    def put(self, key: str, payload: dict[str, Any]) -> str:
        """Store `payload` as JSON under `key` in this archive's S3 bucket; return `key`."""
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=json.dumps(payload).encode("utf-8"),
            ContentType="application/json",
        )
        return key


def build_runtime(settings: Settings) -> dict[str, Any]:
    """Build M0's real dependencies from `settings`.

    Registers `PerplexityAdapter`/`OpenAIAdapter` into the shared adapter registry for each
    engine whose API key is configured (an engine with no key is silently skipped here; TRD §7's
    graceful-degradation posture -- `run_measurement` already logs+skips any requested engine
    that has no registered adapter). Returns:

        {"extractor": Extractor, "archive": RawArchive, "engines": list[str]}

    where `"engines"` is the list of engine names this call actually registered (informational --
    callers decide independently which engines to *request* from `run_measurement`).

    The shared adapter registry is process-global, so a warm Lambda (or any second CLI invocation
    in one process) would otherwise hit `base.register`'s duplicate-name `ValueError`. Clearing the
    registry first makes `build_runtime` idempotent: it always rebuilds from the current settings.
    """
    base.clear_registry()
    engines: list[str] = []

    if settings.perplexity_api_key:
        perplexity_adapter = PerplexityAdapter(api_key=settings.perplexity_api_key)
        base.register(perplexity_adapter)
        engines.append(perplexity_adapter.name)

    if settings.openai_api_key:
        openai_adapter = OpenAIAdapter(api_key=settings.openai_api_key)
        base.register(openai_adapter)
        engines.append(openai_adapter.name)

    archive: RawArchive = S3RawArchive(bucket=settings.s3_bucket)
    extractor: Extractor = ClaudeExtractor(api_key=settings.anthropic_api_key)

    return {"extractor": extractor, "archive": archive, "engines": engines}
