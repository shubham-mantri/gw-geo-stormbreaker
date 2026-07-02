"""Engine adapter contract + registry (TRD §5.2) — keystone interface.

Every AI-search engine (Perplexity, OpenAI, ...) implements `EngineAdapter`. Adding a new
engine means writing one adapter and calling `register()`; zero changes to core code.
"""

from typing import Protocol, runtime_checkable

from gw_geo.common.models import ProbeResult


@runtime_checkable
class EngineAdapter(Protocol):
    name: str
    supports_citations: bool

    async def probe(
        self, prompt: str, *, geo: str = "us", persona: str | None = None
    ) -> ProbeResult: ...


_REGISTRY: dict[str, EngineAdapter] = {}


def register(adapter: EngineAdapter) -> None:
    """Register `adapter` keyed by its `name`.

    Raises:
        ValueError: an adapter with the same `name` is already registered.
    """
    if adapter.name in _REGISTRY:
        raise ValueError(f"adapter already registered: {adapter.name!r}")
    _REGISTRY[adapter.name] = adapter


def get_adapter(name: str) -> EngineAdapter:
    """Look up a registered adapter by name.

    Raises:
        KeyError: no adapter is registered under `name`.
    """
    return _REGISTRY[name]


def all_adapters() -> list[EngineAdapter]:
    """Return all currently registered adapters."""
    return list(_REGISTRY.values())


def clear_registry() -> None:
    """Test helper: reset the registry to empty."""
    _REGISTRY.clear()
