"""Geo-aware proxy pool for the capture fleet (m1-design.md S3.1 / docs/tasks/M1-T09-proxy-pool.md).

`ProxyPool` hands a healthy, currently-unused proxy for a target geo to `LiveCaptureClient`
(M1-T16), rotating round-robin/LRU across calls so load spreads evenly across the fleet, and
backs an unhealthy proxy off (excludes it from `acquire`) for `backoff_seconds` -- measured via
an injectable `now` clock, so tests never sleep on a wall clock. Fully in-memory: no live
proxy/network, so the default test suite stays hermetic.
"""

import time
from collections import deque
from collections.abc import Callable

from pydantic import BaseModel


class Proxy(BaseModel):
    id: str
    url: str  # e.g. "http://user:pass@host:port"
    geo: str
    healthy: bool = True


class NoProxyAvailable(Exception):
    """Raised when no configured proxy for a geo is currently healthy, free, and off backoff."""


class ProxyPool:
    """In-memory pool of `Proxy` handed out per-geo with round-robin rotation and health backoff.

    Usage::

        pool = ProxyPool(proxies)
        proxy = pool.acquire("us")
        try:
            ...  # use proxy.url
        except SomeTransientCaptureError:
            pool.mark_unhealthy(proxy)
        else:
            pool.release(proxy)
    """

    def __init__(
        self,
        proxies: list[Proxy],
        *,
        backoff_seconds: float = 60.0,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._proxies: dict[str, Proxy] = {proxy.id: proxy for proxy in proxies}
        self._backoff_seconds = backoff_seconds
        self._now: Callable[[], float] = now if now is not None else time.monotonic

        self._in_use: set[str] = set()
        self._backoff_until: dict[str, float] = {}

        self._rotation: dict[str, deque[str]] = {}
        for proxy in proxies:
            self._rotation.setdefault(proxy.geo, deque()).append(proxy.id)

    def acquire(self, geo: str) -> Proxy:
        """Return a healthy, unused, non-backed-off proxy for `geo`, rotating across calls.

        Raises `NoProxyAvailable` if no proxy is configured for `geo`, or every proxy for
        `geo` is currently in use, marked unhealthy, or still backed off.
        """
        rotation = self._rotation.get(geo)
        if not rotation:
            raise NoProxyAvailable(f"no proxy configured for geo={geo!r}")

        for _ in range(len(rotation)):
            proxy_id = rotation[0]
            rotation.rotate(-1)  # move the examined candidate to the back either way (round-robin)
            if self._is_eligible(proxy_id):
                self._in_use.add(proxy_id)
                return self._proxies[proxy_id]

        raise NoProxyAvailable(f"no healthy, available proxy for geo={geo!r}")

    def release(self, proxy: Proxy) -> None:
        """Return `proxy` to the pool, making it eligible for `acquire` again."""
        self._require_known(proxy)
        self._in_use.discard(proxy.id)

    def mark_unhealthy(self, proxy: Proxy) -> None:
        """Release `proxy` and exclude it from `acquire` until `backoff_seconds` elapse."""
        self._require_known(proxy)
        self._in_use.discard(proxy.id)
        self._backoff_until[proxy.id] = self._now() + self._backoff_seconds

    def stats(self) -> dict[str, int]:
        """Return `{"total": N, "healthy": N, "in_use": N}` across all geos."""
        now = self._now()
        healthy = sum(1 for proxy_id in self._proxies if self._is_off_backoff(proxy_id, now))
        return {
            "total": len(self._proxies),
            "healthy": healthy,
            "in_use": len(self._in_use),
        }

    def _is_eligible(self, proxy_id: str) -> bool:
        if proxy_id in self._in_use:
            return False
        return self._is_off_backoff(proxy_id, self._now())

    def _is_off_backoff(self, proxy_id: str, now: float) -> bool:
        if not self._proxies[proxy_id].healthy:
            return False
        backoff_until = self._backoff_until.get(proxy_id)
        return backoff_until is None or now >= backoff_until

    def _require_known(self, proxy: Proxy) -> None:
        if proxy.id not in self._proxies:
            raise KeyError(f"unknown proxy id={proxy.id!r}")
