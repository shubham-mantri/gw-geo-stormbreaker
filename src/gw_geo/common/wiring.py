"""Wire real dependencies (adapters, S3 archive, Claude extractor) from `Settings` (TRD §11).

`cli.py` and `handlers/run_measurement.py` both call `build_runtime()` so the CLI and the
deployed Lambda share one source of truth for how M0's real (non-test) dependencies are built.
Adapters register themselves into the shared `measurement.probe.base` registry here, at wiring
time -- the adapter modules themselves are import side-effect-free (see their docstrings), and
`RawArchive`'s S3 client is constructed lazily inside `build_runtime`, never at import time.
"""

from __future__ import annotations

import json
import logging
from typing import Any

# boto3 ships no py.typed marker / stubs, so mypy can't analyze it -- see build_runtime's
# docstring re: constructing its client lazily rather than at import time.
import boto3  # type: ignore[import-untyped]

from gw_geo.capture.base import CaptureClient
from gw_geo.common.config import Settings
from gw_geo.measurement.parse import ClaudeExtractor, Extractor
from gw_geo.measurement.probe import base
from gw_geo.measurement.probe.ai_overviews import AIOverviewsAdapter
from gw_geo.measurement.probe.base import EngineAdapter
from gw_geo.measurement.probe.chatgpt import ChatGPTAdapter
from gw_geo.measurement.probe.claude import ClaudeAdapter
from gw_geo.measurement.probe.copilot import CopilotAdapter
from gw_geo.measurement.probe.deepseek import DeepSeekAdapter
from gw_geo.measurement.probe.gemini import GeminiAdapter
from gw_geo.measurement.probe.grok import GrokAdapter
from gw_geo.measurement.probe.openai_chatgpt import OpenAIAdapter
from gw_geo.measurement.probe.perplexity import PerplexityAdapter
from gw_geo.measurement.runner import RawArchive

logger = logging.getLogger(__name__)


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


def _build_live_capture(settings: Settings) -> CaptureClient | None:
    """Build the deploy-path Playwright capture fleet from the configured pool refs, or None.

    The real backend is `capture.live.LiveCaptureClient` (M1-T16) composed over a `ProxyPool`
    (T09) and an `AccountPool` (T10). Building it means turning `settings.proxy_pool_config_ref` /
    `settings.account_pool_config_ref` -- opaque SSM/secret references -- into concrete `Proxy` /
    `Account` material, which is a `SecretProvider` (SSM/Secrets Manager) responsibility. No
    concrete `SecretProvider` exists in this repo yet, and `ProxyPool` has no `from_secrets`
    counterpart to `AccountPool.from_secrets`, so that ref -> pool resolution has nothing to call.

    Until that fleet-secret layer lands, this logs a warning and returns None; `build_runtime`
    then skips the three Playwright engines -- the same graceful-degradation posture it already
    uses for an API engine whose key is unset (TRD §7) -- rather than crashing an API-only run
    (any `build_runtime` caller, incl. the drift canary) that never needed the browser fleet.
    Hermetic tests never reach this path: they inject a `FakeCaptureClient` via
    `build_runtime(settings, capture=...)`.
    """
    logger.warning(
        "Playwright capture fleet requested (proxy/account pool refs set) but not built: "
        "resolving those refs into ProxyPool/AccountPool needs a SecretProvider (SSM/Secrets "
        "Manager) not yet implemented here, so google_ai_overviews/chatgpt/grok are skipped. "
        "Inject a CaptureClient via build_runtime(settings, capture=...) to drive these surfaces."
    )
    return None


def build_runtime(settings: Settings, *, capture: CaptureClient | None = None) -> dict[str, Any]:
    """Build the real (non-test) dependencies from `settings`, registering every engine by config.

    Registers into the shared adapter registry, keyed by config:

    * `perplexity` / `openai` (M0), `gemini`, `claude` (keyed off `anthropic_api_key`), and
      `copilot` -- each registered when its API key is set;
    * `deepseek` -- additionally gated on `settings.deepseek_enabled` (TRD OT3: off by default);
    * `google_ai_overviews` / `chatgpt` / `grok` -- the Playwright surfaces, wired to a
      `CaptureClient`: the injected `capture` when provided (a `FakeCaptureClient` in tests), else
      the live browser fleet built from the proxy/account pool refs on the deploy path
      (`_build_live_capture`).

    An engine whose key is unset (or whose capture backend is unavailable) is silently skipped --
    TRD §7's graceful-degradation posture; `run_measurement` already logs+skips any *requested*
    engine that has no registered adapter. Returns:

        {"extractor": Extractor, "archive": RawArchive, "engines": list[str]}

    where `"engines"` is the list of engine names this call actually registered (informational --
    callers decide independently which engines to *request* from `run_measurement`).

    The shared adapter registry is process-global, so a warm Lambda (or any second CLI invocation
    in one process) would otherwise hit `base.register`'s duplicate-name `ValueError`. Clearing the
    registry first makes `build_runtime` idempotent: it always rebuilds from the current settings.
    """
    base.clear_registry()
    engines: list[str] = []

    def _register(adapter: EngineAdapter) -> None:
        base.register(adapter)
        engines.append(adapter.name)

    # API engines: registered per configured key (an unset key is a graceful skip, TRD §7).
    if settings.perplexity_api_key:
        _register(PerplexityAdapter(api_key=settings.perplexity_api_key))
    if settings.openai_api_key:
        _register(OpenAIAdapter(api_key=settings.openai_api_key))
    if settings.gemini_api_key:
        _register(GeminiAdapter(api_key=settings.gemini_api_key))
    if settings.anthropic_api_key:
        _register(ClaudeAdapter(api_key=settings.anthropic_api_key))
    if settings.copilot_api_key:
        _register(CopilotAdapter(api_key=settings.copilot_api_key))
    # DeepSeek is additionally gated on an explicit toggle (TRD OT3: off by default).
    if settings.deepseek_api_key and settings.deepseek_enabled:
        _register(DeepSeekAdapter(api_key=settings.deepseek_api_key))

    # Playwright engines: wired to the injected capturer (tests) or the live fleet (deploy). When
    # no capturer is injected and the fleet pool refs are set, build the deploy-path fleet.
    capture_backend = capture
    fleet_refs_set = bool(settings.proxy_pool_config_ref and settings.account_pool_config_ref)
    if capture_backend is None and fleet_refs_set:
        capture_backend = _build_live_capture(settings)
    if capture_backend is not None:
        _register(AIOverviewsAdapter(capture_backend))
        _register(ChatGPTAdapter(capture_backend))
        _register(GrokAdapter(capture_backend))

    archive: RawArchive = S3RawArchive(bucket=settings.s3_bucket)
    extractor: Extractor = ClaudeExtractor(api_key=settings.anthropic_api_key)

    return {"extractor": extractor, "archive": archive, "engines": engines}
