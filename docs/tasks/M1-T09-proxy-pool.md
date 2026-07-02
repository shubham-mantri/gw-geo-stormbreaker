# M1-T09 — ProxyPool (geo-aware acquire/release + health)

**Depends on:** T01 (config) · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Geo-aware proxy pool for the capture fleet: acquire a healthy proxy for a target geo,
release it, rotate, and back off unhealthy proxies. Unit-tested with in-memory fakes — **no live
proxy/network**.

**Files:**
- Create: `src/gw_geo/capture/proxy_pool.py`
- Test: `tests/capture/test_proxy_pool.py`

## Interface

```python
from pydantic import BaseModel

class Proxy(BaseModel):
    id: str
    url: str            # e.g. "http://user:pass@host:port"
    geo: str
    healthy: bool = True

class NoProxyAvailable(Exception): ...

class ProxyPool:
    def __init__(self, proxies: list[Proxy], *, backoff_seconds: float = 60.0,
                 now = None) -> None: ...            # now: injectable clock for tests
    def acquire(self, geo: str) -> Proxy: ...        # a healthy, not-backed-off proxy for geo
    def release(self, proxy: Proxy) -> None: ...
    def mark_unhealthy(self, proxy: Proxy) -> None: ...   # start backoff; excluded until it elapses
    def stats(self) -> dict[str, int]: ...           # {"total","healthy","in_use"}
```

- `acquire(geo)` returns a healthy proxy matching `geo` that is not currently in use and not in
  backoff; rotates (round-robin / least-recently-used) across calls; raises `NoProxyAvailable` if
  none. `release` returns it to the pool. `mark_unhealthy` puts it in backoff for `backoff_seconds`
  (measured via the injected `now` clock); after that it becomes eligible again. Fully in-memory.

## Steps
- [ ] **1. Failing test** `tests/capture/test_proxy_pool.py`:

```python
import pytest
from gw_geo.capture.proxy_pool import Proxy, ProxyPool, NoProxyAvailable

def _pool(now):
    return ProxyPool([Proxy(id="p1", url="http://a", geo="us"),
                      Proxy(id="p2", url="http://b", geo="us"),
                      Proxy(id="p3", url="http://c", geo="de")],
                     backoff_seconds=60.0, now=now)

def test_acquire_matches_geo_and_rotates():
    t = [0.0]; p = _pool(lambda: t[0])
    a = p.acquire("us"); b = p.acquire("us")
    assert {a.id, b.id} == {"p1", "p2"} and a.geo == b.geo == "us"
    with pytest.raises(NoProxyAvailable):
        p.acquire("us")                 # both in use
    p.release(a); assert p.acquire("us").id == a.id

def test_unhealthy_backoff_then_recovers():
    t = [0.0]; p = _pool(lambda: t[0])
    a = p.acquire("us"); p.mark_unhealthy(a)
    p.release(p.acquire("us"))          # only p2 usable now
    with pytest.raises(NoProxyAvailable):
        p.acquire("us"); p.acquire("us")   # p1 still backed off
    t[0] = 61.0                          # backoff elapsed
    assert p.acquire("us") is not None

def test_no_proxy_for_unknown_geo():
    p = _pool(lambda: 0.0)
    with pytest.raises(NoProxyAvailable):
        p.acquire("jp")
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `proxy_pool.py` (in-memory tracking of in-use + backoff-until per proxy;
  injectable `now` clock; round-robin rotation). No live network.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(capture): geo-aware proxy pool with health backoff`

## Acceptance
- Acquire/release/rotate works per-geo; unhealthy proxies back off and recover on the injected
  clock; exhaustion raises `NoProxyAvailable`; fully hermetic (no live proxy).
