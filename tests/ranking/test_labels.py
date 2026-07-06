"""Tests for ranking labels (M3-T05, TRD §8) -- tenant/brand/engine-scoped `Citation` read.

Mirrors the M0/M1/M2/M3 `test_db*.py` style: hermetic in-memory SQLite (TRD §12),
`Base.metadata.create_all`. `test_cited_urls_excludes_other_tenant_and_brand` goes beyond the
task-spec case (which only varies `engine`) by also seeding a second tenant and a second brand,
mirroring `tests/measurement/test_feed.py`'s convention of seeding a second tenant so cross-
tenant/cross-brand leakage would fail loudly rather than silently.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.db import Base, Brand, Citation, Prompt, Tenant
from gw_geo.ranking.labels import cited_urls_for


def _session() -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    # FK parents for the Citations these tests seed (citation -> tenant, brand, prompt). Extra
    # rows with no citations don't affect cited_urls_for, which filters by the citation columns.
    s.add(Tenant(id="t1", name="t", sampling_budget_daily=100.0))
    s.add(Tenant(id="t2", name="t", sampling_budget_daily=100.0))
    s.add(Brand(id="b1", tenant_id="t1", name="b", domain="b.com"))
    s.add(Brand(id="b2", tenant_id="t1", name="b", domain="b.com"))
    s.add(Prompt(id="p1", tenant_id="t1", brand_id="b1", text="q"))
    s.commit()
    return s


def test_cited_urls_scoped_to_tenant_brand_engine() -> None:
    s = _session()
    s.add(
        Citation(
            id="1",
            tenant_id="t1",
            brand_id="b1",
            url="https://a.com/x",
            domain="a.com",
            source_type="own_site",
            engine="perplexity",
            prompt_id="p1",
        )
    )
    s.add(
        Citation(
            id="2",
            tenant_id="t1",
            brand_id="b1",
            url="https://z.com/q",
            domain="z.com",
            source_type="reddit",
            engine="openai",
            prompt_id="p1",
        )
    )
    s.commit()
    urls = cited_urls_for(s, tenant_id="t1", brand_id="b1", engine="perplexity")
    assert urls == {"https://a.com/x"}


def test_cited_urls_excludes_other_tenant_and_brand() -> None:
    s = _session()
    s.add(
        Citation(
            id="1",
            tenant_id="t1",
            brand_id="b1",
            url="https://a.com/x",
            domain="a.com",
            source_type="own_site",
            engine="perplexity",
            prompt_id="p1",
        )
    )
    s.add(
        Citation(
            id="2",
            tenant_id="t2",
            brand_id="b1",
            url="https://other-tenant.com/x",
            domain="other-tenant.com",
            source_type="own_site",
            engine="perplexity",
            prompt_id="p1",
        )
    )
    s.add(
        Citation(
            id="3",
            tenant_id="t1",
            brand_id="b2",
            url="https://other-brand.com/x",
            domain="other-brand.com",
            source_type="own_site",
            engine="perplexity",
            prompt_id="p1",
        )
    )
    s.commit()
    urls = cited_urls_for(s, tenant_id="t1", brand_id="b1", engine="perplexity")
    assert urls == {"https://a.com/x"}


def test_cited_urls_empty_when_no_matching_rows() -> None:
    urls = cited_urls_for(_session(), tenant_id="t1", brand_id="b1", engine="perplexity")
    assert urls == set()
