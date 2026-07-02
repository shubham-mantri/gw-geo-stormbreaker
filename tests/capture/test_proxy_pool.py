"""Spec tests for the geo-aware proxy pool (docs/tasks/M1-T09-proxy-pool.md).

Reformatted from the task spec's compact semicolon-joined statements into ruff-clean,
multi-line form -- every assertion below is identical to the spec.
"""

import pytest

from gw_geo.capture.proxy_pool import NoProxyAvailable, Proxy, ProxyPool


def _pool(now):
    return ProxyPool(
        [
            Proxy(id="p1", url="http://a", geo="us"),
            Proxy(id="p2", url="http://b", geo="us"),
            Proxy(id="p3", url="http://c", geo="de"),
        ],
        backoff_seconds=60.0,
        now=now,
    )


def test_acquire_matches_geo_and_rotates():
    t = [0.0]
    p = _pool(lambda: t[0])
    a = p.acquire("us")
    b = p.acquire("us")
    assert {a.id, b.id} == {"p1", "p2"}
    assert a.geo == b.geo == "us"
    with pytest.raises(NoProxyAvailable):
        p.acquire("us")  # both in use
    p.release(a)
    assert p.acquire("us").id == a.id


def test_unhealthy_backoff_then_recovers():
    t = [0.0]
    p = _pool(lambda: t[0])
    a = p.acquire("us")
    p.mark_unhealthy(a)
    p.release(p.acquire("us"))  # only p2 usable now
    with pytest.raises(NoProxyAvailable):
        p.acquire("us")
        p.acquire("us")  # p1 still backed off
    t[0] = 61.0  # backoff elapsed
    assert p.acquire("us") is not None


def test_no_proxy_for_unknown_geo():
    p = _pool(lambda: 0.0)
    with pytest.raises(NoProxyAvailable):
        p.acquire("jp")
