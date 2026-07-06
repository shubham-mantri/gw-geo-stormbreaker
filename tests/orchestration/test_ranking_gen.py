"""Tests for the ranking-generation orchestrator (M5, orchestration/ranking_gen.py).

Hermetic (TRD §12): in-memory / file SQLite with FK enforcement ON, a dict-backed `FakeFetcher`
(no live HTTP), and either the real keyless `LocalHashEmbedder` (offline!) or a fake -- never a live
embedding call -- plus the deterministic `FakeBackend` from `tests.ranking.test_model` (no
scikit-learn import anywhere). The full sourcing -> feature-extraction -> `run_ranking` chain is
therefore exercised end-to-end with zero network/model dependencies.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.config import Settings
from gw_geo.common.db import Base, Brand, Citation, FeatureModel, Prompt, Tenant
from gw_geo.common.models import RankingReport
from gw_geo.orchestration import ranking_gen
from gw_geo.orchestration.ranking_gen import (
    LocalHashEmbedder,
    _build_embedder,
    _embedding_key_configured,
    build_ranking_runtime,
    generate_ranking_reports,
    run_ranking_refresh_job,
)
from gw_geo.ranking.features import cosine
from gw_geo.ranking.fetch import FetchedPage, HttpxPageFetcher
from tests.ranking.test_model import FakeBackend
from tests.ranking.test_sourcing import FakeEmbedder, FakeFetcher

TENANT = "t1"
BRAND = "b1"


def _seed(session: Session) -> None:
    session.add(Tenant(id=TENANT, name="t", sampling_budget_daily=100.0))
    session.add(Brand(id=BRAND, tenant_id=TENANT, name="Acme", domain="acme.com"))
    session.add(Prompt(id="p1", tenant_id=TENANT, brand_id=BRAND, text="best crm software"))
    session.commit()


def _cite(cid: str, url: str, engine: str, *, domain: str = "src.com") -> Citation:
    return Citation(id=cid, tenant_id=TENANT, brand_id=BRAND, url=url, domain=domain,
                    source_type="other", engine=engine, prompt_id="p1")


def _pages() -> dict[str, FetchedPage]:
    return {
        "https://a.com/x": FetchedPage(text="Acme is the best CRM with 40% faster onboarding."),
        "https://b.com/y": FetchedPage(text="A competitor review comparing 3 CRMs."),
        "https://acme.com": FetchedPage(text="Acme homepage: the best CRM software for SaaS."),
    }


# --- LocalHashEmbedder (the keyless, deterministic, offline fallback) -------------------------


def test_local_hash_embedder_is_deterministic_and_fixed_dim() -> None:
    emb = LocalHashEmbedder()
    v1 = emb.embed("best crm software")
    v2 = emb.embed("best crm software")
    assert v1 == v2  # deterministic (sha256, not salted hash())
    assert len(v1) == len(emb.embed("something else"))  # fixed dimensionality
    assert emb.embed("totally unrelated tokens") != v1


def test_local_hash_embedder_reflects_token_overlap() -> None:
    emb = LocalHashEmbedder()
    crm = emb.embed("best crm software for teams")
    similar = emb.embed("best crm software tools")
    unrelated = emb.embed("banana bicycle mountain")
    # Shared tokens -> higher cosine than disjoint text (a usable offline similarity proxy).
    assert cosine(crm, similar) > cosine(crm, unrelated)


# --- embedder selection: keyless -> local, keyed -> gateway -----------------------------------


def test_embedding_key_detection() -> None:
    assert _embedding_key_configured(Settings(llm_gateway="direct", openai_api_key="sk-x"))
    assert _embedding_key_configured(Settings(llm_gateway="portkey", portkey_api_key="pk-x"))
    assert not _embedding_key_configured(
        Settings(llm_gateway="portkey", portkey_api_key="", openai_api_key="")
    )


def test_build_embedder_prefers_local_when_keyless() -> None:
    embedder = _build_embedder(Settings(llm_gateway="direct", openai_api_key=""))
    assert isinstance(embedder, LocalHashEmbedder)


def test_build_embedder_uses_gateway_when_keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    monkeypatch.setattr(ranking_gen, "build_embedder", lambda settings: sentinel)
    embedder = _build_embedder(Settings(llm_gateway="direct", openai_api_key="sk-real"))
    assert embedder is sentinel  # the real gateway embedder, imported by name for patchability


def test_build_ranking_runtime_wires_httpx_and_local_embedder_when_keyless() -> None:
    fetcher, embedder, backend_factory = build_ranking_runtime(
        Settings(llm_gateway="direct", openai_api_key="")
    )
    assert isinstance(fetcher, HttpxPageFetcher)
    assert isinstance(embedder, LocalHashEmbedder)
    assert callable(backend_factory)


# --- generate_ranking_reports: the offline sourcing -> run_ranking chain ----------------------


def test_generate_ranking_reports_offline_multi_engine() -> None:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    _seed(s)
    # >=2 engines: perplexity cited A, openai cited B -> each engine gets a cross-engine negative.
    s.add(_cite("c1", "https://a.com/x", "perplexity"))
    s.add(_cite("c2", "https://b.com/y", "openai"))
    s.commit()

    reports = generate_ranking_reports(
        session=s,
        tenant_id=TENANT,
        brand_id=BRAND,
        engines=["perplexity", "openai"],
        fetcher=FakeFetcher(_pages()),
        embedder=LocalHashEmbedder(),  # real, keyless, offline
        backend_factory=lambda: FakeBackend(),
        now="2026-07-06",
    )

    assert set(reports) == {"perplexity", "openai"}
    assert isinstance(reports["perplexity"], RankingReport)
    assert reports["perplexity"].factors  # model importances surfaced

    models = {m.engine: m for m in s.query(FeatureModel).all()}
    assert set(models) == {"perplexity", "openai"}
    # Cross-engine labeling: pool = {A, B}; perplexity cited only A -> 1 positive + 1 negative (B).
    assert models["perplexity"].metrics["n_examples"] == 2
    assert models["perplexity"].metrics["n_cited"] == 1
    assert "auc" in models["perplexity"].metrics  # both labels present -> AUC defined
    assert models["openai"].metrics["n_cited"] == 1  # openai cited only B


def test_generate_ranking_reports_single_engine_is_all_positive() -> None:
    # The documented >=2-engine requirement: with one engine, every candidate it cited is a
    # positive, so there are no cross-engine negatives and AUC is undefined (can't learn a boundary).
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    _seed(s)
    s.add(_cite("c1", "https://a.com/x", "perplexity"))
    s.add(_cite("c2", "https://b.com/y", "perplexity"))
    s.commit()

    generate_ranking_reports(
        session=s,
        tenant_id=TENANT,
        brand_id=BRAND,
        engines=["perplexity"],
        fetcher=FakeFetcher(_pages()),
        embedder=FakeEmbedder(),
        backend_factory=lambda: FakeBackend(),
        now="2026-07-06",
    )

    model = s.query(FeatureModel).filter_by(engine="perplexity").one()
    assert model.metrics["n_examples"] == 2
    assert model.metrics["n_cited"] == 2  # all positive
    assert "auc" not in model.metrics  # single-label -> undefined


def test_generate_ranking_reports_missing_brand_is_noop() -> None:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    _seed(s)
    s.add(_cite("c1", "https://a.com/x", "perplexity"))
    s.commit()

    reports = generate_ranking_reports(
        session=s,
        tenant_id="other-tenant",  # cross-tenant: never rank b1
        brand_id=BRAND,
        engines=["perplexity"],
        fetcher=FakeFetcher(_pages()),
        embedder=FakeEmbedder(),
        backend_factory=lambda: FakeBackend(),
    )
    assert reports == {}
    assert s.query(FeatureModel).count() == 0  # no artifact written for a foreign brand


# --- run_ranking_refresh_job: opens its own session + wires the real runtime ------------------


def test_run_ranking_refresh_job_opens_session_and_wires_runtime(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "rank.db"
    url = f"sqlite:///{db_path}"
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        _seed(s)

    # Keyless test settings -> the offline LocalHashEmbedder is wired (fully offline).
    monkeypatch.setattr(
        ranking_gen, "get_settings", lambda: Settings(database_url=url, openai_api_key="")
    )
    captured: dict = {}

    def _spy(**kwargs):
        captured.update(kwargs)
        return {"perplexity": RankingReport(engine="perplexity")}

    monkeypatch.setattr(ranking_gen, "generate_ranking_reports", _spy)

    reports = run_ranking_refresh_job(
        tenant_id=TENANT, brand_id=BRAND, engines=["perplexity", "openai"]
    )

    assert set(reports) == {"perplexity"}
    assert isinstance(captured["session"], Session)  # the job opened its own session
    assert isinstance(captured["fetcher"], HttpxPageFetcher)
    assert isinstance(captured["embedder"], LocalHashEmbedder)  # keyless -> offline embedder
    assert callable(captured["backend_factory"])
    assert captured["tenant_id"] == TENANT and captured["brand_id"] == BRAND
    assert captured["engines"] == ["perplexity", "openai"]
