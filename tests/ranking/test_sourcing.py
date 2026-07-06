"""Tests for candidate sourcing from the citation pool (M5, ranking/sourcing.py).

Hermetic (TRD §12): in-memory SQLite with FK enforcement ON (see tests/conftest.py), a dict-backed
`FakeFetcher` (no live HTTP), and a deterministic `FakeEmbedder` (no live embedding). All FK parents
(tenant/brand/prompt) are committed before the citations that reference them, mirroring the real
crawler's read-only-of-committed-data contract.

The candidate pool is CROSS-ENGINE by construction: every distinct cited URL is a candidate for
every engine, and per-engine labels (cited vs not) are applied later inside `run_ranking`. That is
what makes an engine's negatives = "URLs OTHER engines cited but this one didn't" -- so >=2 engines
must have citations for any engine to see both labels (exercised in test_ranking_gen.py).
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.db import Base, Brand, Citation, Prompt, Tenant
from gw_geo.common.models import FeatureVector, SourceType
from gw_geo.ranking.fetch import FetchedPage
from gw_geo.ranking.sourcing import (
    build_ranking_inputs_from_db,
    candidate_urls_for_brand,
    make_corroboration_fn,
    make_domain_authority_fn,
    per_engine_source_mix,
)

TENANT = "t1"
BRAND = "b1"


class FakeFetcher:
    """Dict-backed `PageFetcher`: returns a `FetchedPage` for known URLs, `None` otherwise."""

    def __init__(self, pages: dict[str, FetchedPage]) -> None:
        self.pages = pages
        self.fetched: list[str] = []

    def fetch(self, url: str) -> FetchedPage | None:
        self.fetched.append(url)
        return self.pages.get(url)


class FakeEmbedder:
    """Deterministic, offline embedder: a tiny fixed-dim vector derived from the text."""

    def embed(self, text: str) -> list[float]:
        return [float(len(text)), float(len(text.split())), 1.0]


def _session() -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    s.add(Tenant(id=TENANT, name="t", sampling_budget_daily=100.0))
    s.add(Brand(id=BRAND, tenant_id=TENANT, name="Acme", domain="acme.com"))
    s.add(Prompt(id="p1", tenant_id=TENANT, brand_id=BRAND, text="best crm"))
    s.add(Prompt(id="p2", tenant_id=TENANT, brand_id=BRAND, text="crm pricing"))
    # A second tenant/brand whose citations must never leak into t1/b1 reads.
    s.add(Tenant(id="t2", name="t2", sampling_budget_daily=100.0))
    s.add(Brand(id="b2", tenant_id="t2", name="Other", domain="other.com"))
    s.add(Prompt(id="p9", tenant_id="t2", brand_id="b2", text="other"))
    s.commit()
    return s


def _cite(cid: str, url: str, *, domain: str, source_type: str, engine: str,
          prompt_id: str = "p1", seen: int = 1, tenant: str = TENANT, brand: str = BRAND) -> Citation:
    return Citation(id=cid, tenant_id=tenant, brand_id=brand, url=url, domain=domain,
                    source_type=source_type, engine=engine, prompt_id=prompt_id, seen_count=seen)


def test_candidate_urls_distinct_and_tenant_scoped() -> None:
    s = _session()
    # url A cited by BOTH engines (still one candidate); url B by one; a cross-tenant url excluded.
    s.add(_cite("c1", "https://a.com/x", domain="a.com", source_type="other", engine="perplexity"))
    s.add(_cite("c2", "https://a.com/x", domain="a.com", source_type="other", engine="openai"))
    s.add(_cite("c3", "https://b.com/y", domain="b.com", source_type="other", engine="openai"))
    s.add(_cite("c9", "https://leak.com/z", domain="leak.com", source_type="other",
                engine="openai", prompt_id="p9", tenant="t2", brand="b2"))
    s.commit()

    urls = candidate_urls_for_brand(s, tenant_id=TENANT, brand_id=BRAND)
    assert urls == ["https://a.com/x", "https://b.com/y"]  # distinct + sorted, no cross-tenant leak


def test_per_engine_source_mix_seen_count_weighted() -> None:
    s = _session()
    s.add(_cite("c1", "https://reddit.com/r/a", domain="reddit.com", source_type="reddit",
                engine="perplexity", seen=3))
    s.add(_cite("c2", "https://acme.com/a", domain="acme.com", source_type="own_site",
                engine="perplexity", seen=1))
    s.add(_cite("c3", "https://x.com/a", domain="x.com", source_type="other",
                engine="openai", seen=2))
    s.commit()

    mix = per_engine_source_mix(s, tenant_id=TENANT, brand_id=BRAND)
    assert mix["perplexity"][SourceType.REDDIT] == 0.75
    assert mix["perplexity"][SourceType.OWN_SITE] == 0.25
    assert sum(mix["perplexity"].values()) == 1.0
    assert mix["openai"] == {SourceType.OTHER: 1.0}


def test_corroboration_fn_counts_distinct_domains_sharing_prompt() -> None:
    s = _session()
    # prompt p1 is corroborated by two distinct domains (a.com, b.com); c3 repeats a.com.
    s.add(_cite("c1", "https://a.com/x", domain="a.com", source_type="other", engine="perplexity"))
    s.add(_cite("c2", "https://b.com/y", domain="b.com", source_type="other", engine="openai"))
    s.add(_cite("c3", "https://a.com/z", domain="a.com", source_type="other", engine="openai"))
    # prompt p2 has a single corroborating domain.
    s.add(_cite("c4", "https://c.com/q", domain="c.com", source_type="other",
                engine="perplexity", prompt_id="p2"))
    s.commit()

    corroboration = make_corroboration_fn(s, tenant_id=TENANT, brand_id=BRAND)
    assert corroboration("https://a.com/x") == 2  # a.com + b.com share p1
    assert corroboration("https://b.com/y") == 2
    assert corroboration("https://c.com/q") == 1  # only c.com on p2
    assert corroboration("https://unknown.com/nope") == 0  # not in the pool


def test_domain_authority_static_table_and_frequency_fallback() -> None:
    s = _session()
    # acme.com is the most-cited unknown domain (freq 10 == max -> ceiling); foo.com is lighter.
    s.add(_cite("c1", "https://acme.com/a", domain="acme.com", source_type="own_site",
                engine="perplexity", seen=10))
    s.add(_cite("c2", "https://foo.com/a", domain="foo.com", source_type="other",
                engine="openai", seen=2))
    s.commit()

    da = make_domain_authority_fn(s, tenant_id=TENANT, brand_id=BRAND)
    # Known-authoritative domain (incl. a subdomain) comes from the static table.
    assert da("https://en.wikipedia.org/wiki/CRM") >= 0.9
    # Unknown domains fall back to citation frequency, capped below the static tier.
    assert da("https://acme.com/x") <= 0.5
    assert da("https://acme.com/x") > da("https://foo.com/x") > 0.0
    assert da("https://never-cited.com/x") == 0.0


def _fetcher_with_pages() -> FakeFetcher:
    return FakeFetcher(
        {
            "https://a.com/x": FetchedPage(text="Acme is the best CRM. 40% faster.",
                                           published_at="2026-06-01"),
            "https://b.com/y": FetchedPage(text="A rival review with 3 data points 12 99."),
            "https://acme.com": FetchedPage(text="Acme homepage. CRM for SaaS.",
                                            published_at="2026-05-01"),
        }
    )


def _inputs(s: Session, fetcher: FakeFetcher, engines: list[str]) -> dict:
    return build_ranking_inputs_from_db(
        s,
        tenant_id=TENANT,
        brand_id=BRAND,
        engines=engines,
        fetcher=fetcher,
        embedder=FakeEmbedder(),
        now="2026-07-06",
        domain_authority_fn=make_domain_authority_fn(s, tenant_id=TENANT, brand_id=BRAND),
        corroboration_fn=make_corroboration_fn(s, tenant_id=TENANT, brand_id=BRAND),
    )


def test_build_inputs_same_candidate_list_for_every_engine() -> None:
    s = _session()
    s.add(_cite("c1", "https://a.com/x", domain="a.com", source_type="other", engine="perplexity"))
    s.add(_cite("c2", "https://b.com/y", domain="b.com", source_type="other", engine="openai"))
    s.commit()

    inputs = _inputs(s, _fetcher_with_pages(), ["perplexity", "openai"])

    cands = inputs["candidates_by_engine"]
    assert set(cands) == {"perplexity", "openai"}
    # The SAME candidate list is reused for every engine (labels are applied per-engine downstream).
    assert cands["perplexity"] == cands["openai"]
    assert {c["url"] for c in cands["perplexity"]} == {"https://a.com/x", "https://b.com/y"}
    assert all(isinstance(c["features"], FeatureVector) for c in cands["perplexity"])
    # freshness carried through from the fetched page's datePublished.
    a = next(c for c in cands["perplexity"] if c["url"] == "https://a.com/x")
    assert a["features"].freshness_days == 35.0  # 2026-07-06 - 2026-06-01

    # current: one homepage feature vector, reused per engine.
    current = inputs["current_by_engine"]
    assert set(current) == {"perplexity", "openai"}
    assert isinstance(current["perplexity"], FeatureVector)
    assert current["perplexity"] is current["openai"]

    # source mix: one entry per requested engine.
    assert set(inputs["source_mix_by_engine"]) == {"perplexity", "openai"}


def test_build_inputs_skips_unfetchable_urls() -> None:
    s = _session()
    s.add(_cite("c1", "https://a.com/x", domain="a.com", source_type="other", engine="perplexity"))
    s.add(_cite("c2", "https://gone.com/y", domain="gone.com", source_type="other", engine="openai"))
    s.commit()

    fetcher = FakeFetcher({"https://a.com/x": FetchedPage(text="ok"),
                           "https://acme.com": FetchedPage(text="home")})  # gone.com -> None
    inputs = _inputs(s, fetcher, ["perplexity", "openai"])

    urls = {c["url"] for c in inputs["candidates_by_engine"]["perplexity"]}
    assert urls == {"https://a.com/x"}  # the unfetchable URL is dropped, not fatal


def test_build_inputs_missing_or_cross_tenant_brand_is_noop() -> None:
    s = _session()
    s.add(_cite("c1", "https://a.com/x", domain="a.com", source_type="other", engine="perplexity"))
    s.commit()

    # cross-tenant brand: t2 asking for b1
    inputs = build_ranking_inputs_from_db(
        s, tenant_id="t2", brand_id=BRAND, engines=["perplexity"],
        fetcher=_fetcher_with_pages(), embedder=FakeEmbedder(), now="2026-07-06",
        domain_authority_fn=lambda u: 0.0, corroboration_fn=lambda u: 0,
    )
    assert inputs["candidates_by_engine"] == {"perplexity": []}
    assert inputs["current_by_engine"] == {}
    assert inputs["source_mix_by_engine"] == {"perplexity": {}}
