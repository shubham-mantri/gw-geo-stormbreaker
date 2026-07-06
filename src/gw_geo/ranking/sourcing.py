"""Candidate sourcing for the ranking pipeline (M5): the citation pool -> `run_ranking` inputs.

`ranking/runner.py`'s `run_ranking` takes its candidates, current-asset feature vectors, and
per-engine source mix **pre-built** (m3-design §2.6 draws that boundary deliberately). Before M5 an
operator supplied them by hand as JSON (`cli rank --input`). This module sources them instead from
the data we already collect -- the `Citation` pool measurement writes -- so the ranking chain can
run end-to-end with no operator input and, crucially, **no new external API** (PRD NG1: white-hat,
LOCAL-ONLY; not a SERP/search API).

How labels arise without a labeling API (the key idea):

- The candidate pool is **every distinct URL any engine cited for the brand** -- cross-engine
  (`candidate_urls_for_brand`). The *same* pool is used for every engine.
- `run_ranking` then labels that shared pool per engine via `labels.cited_urls_for`: a URL is a
  **positive** for engine E iff E cited it, and a **negative** otherwise -- i.e. a URL some *other*
  engine cited but E didn't. So negatives are sourced cross-engine, for free.
- Corollary: **>=2 engines must have citations** for any engine to see both labels. With a single
  engine measured, every candidate it cited is a positive and there are no negatives -- an
  all-positive dataset a classifier can't learn a boundary from. `generate_ranking_reports` logs
  this; callers should measure >=2 engines before ranking.

Feature inputs use LOCAL proxies (no external authority/corroboration API): `corroboration_fn`
counts the distinct domains that corroborate a URL's prompt, and `domain_authority_fn` blends a
small static high-authority table with a citation-frequency fallback. Content + publish date come
from the injected `PageFetcher` (`ranking/fetch.py`); a URL that can't be fetched is skipped, not
fatal. Every read is tenant/brand-scoped (TRD §7), matching `ranking/labels.py`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from gw_geo.common.db import Brand, Citation, Prompt
from gw_geo.common.models import FeatureVector, SourceType
from gw_geo.measurement.parse import domain_of
from gw_geo.ranking.features import EmbeddingClient, extract_features
from gw_geo.ranking.fetch import PageFetcher

# A small static table of well-known high-authority domains (registrable domain -> [0,1] score),
# matched by exact host or subdomain. Deliberately tiny and conservative -- a LOCAL proxy for a real
# domain-authority service, not a claim to reproduce one. Unknown domains fall back to how often the
# brand's own citations draw on them (capped well below this tier, see `_FALLBACK_AUTHORITY_CEIL`).
_STATIC_DOMAIN_AUTHORITY: dict[str, float] = {
    "wikipedia.org": 0.95,
    "github.com": 0.90,
    "stackoverflow.com": 0.88,
    "nytimes.com": 0.90,
    "forbes.com": 0.85,
    "techcrunch.com": 0.82,
    "youtube.com": 0.85,
    "linkedin.com": 0.80,
    "g2.com": 0.80,
    "capterra.com": 0.78,
    "trustradius.com": 0.75,
    "reddit.com": 0.75,
    "quora.com": 0.62,
    "medium.com": 0.60,
}

# Ceiling for the citation-frequency fallback: an unknown domain, however heavily the brand's own
# citations lean on it, never reads as authoritative as a curated high-authority domain.
_FALLBACK_AUTHORITY_CEIL = 0.5


def candidate_urls_for_brand(session: Session, *, tenant_id: str, brand_id: str) -> list[str]:
    """The candidate URL pool for `(tenant, brand)`: every distinct cited URL, sorted.

    Cross-engine by design (no `engine` filter): the union of every engine's citations is the
    candidate set each per-engine model is scored against, so an engine's non-citations become its
    training negatives (see module docstring). Sorted for a deterministic, stable candidate order.
    """
    stmt = (
        select(Citation.url)
        .where(Citation.tenant_id == tenant_id, Citation.brand_id == brand_id)
        .distinct()
    )
    return sorted(set(session.execute(stmt).scalars()))


def per_engine_source_mix(
    session: Session, *, tenant_id: str, brand_id: str
) -> dict[str, dict[SourceType, float]]:
    """`{engine: {source_type: fraction}}` of citation volume, `seen_count`-weighted, per engine.

    Groups the brand's citations by `(engine, source_type)` and normalizes each engine's mix to
    sum to 1.0 -- the per-engine analogue of `measurement.feed.citation_source_mix` (which pools
    across engines). Feeds `ranking.recommend.channel_recommendations` via `run_ranking`. An engine
    with no citations does not appear.
    """
    stmt = select(Citation.engine, Citation.source_type, Citation.seen_count).where(
        Citation.tenant_id == tenant_id, Citation.brand_id == brand_id
    )
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for engine, source_type, seen_count in session.execute(stmt).all():
        counts[engine][source_type] += seen_count

    mix: dict[str, dict[SourceType, float]] = {}
    for engine, by_type in counts.items():
        total = sum(by_type.values())
        if not total:
            continue
        mix[engine] = {SourceType(st): count / total for st, count in by_type.items()}
    return mix


def make_corroboration_fn(
    session: Session, *, tenant_id: str, brand_id: str
) -> Callable[[str], int]:
    """Build a `url -> corroboration_count` proxy: distinct domains sharing the URL's prompt.

    Corroboration (`FeatureVector.corroboration_count`) is meant to capture "how many independent
    sources back this claim". Locally, we proxy it by the number of *distinct domains* that were
    cited for the same prompt the URL was cited for -- a prompt answered by citations from many
    domains is better corroborated than one leaning on a single source. A URL not in the pool
    corroborates nothing (`0`). Snapshots the citations once so the returned closure does no further
    I/O.
    """
    stmt = select(Citation.url, Citation.prompt_id, Citation.domain).where(
        Citation.tenant_id == tenant_id, Citation.brand_id == brand_id
    )
    url_prompt: dict[str, str] = {}
    prompt_domains: dict[str, set[str]] = defaultdict(set)
    for url, prompt_id, domain in session.execute(stmt).all():
        url_prompt.setdefault(url, prompt_id)  # a representative prompt per URL
        prompt_domains[prompt_id].add(domain)

    def corroboration_fn(url: str) -> int:
        prompt_id = url_prompt.get(url)
        if prompt_id is None:
            return 0
        return len(prompt_domains.get(prompt_id, set()))

    return corroboration_fn


def _static_authority(domain: str) -> float | None:
    """The static-table authority for `domain` (exact host or subdomain match), else `None`."""
    for base, score in _STATIC_DOMAIN_AUTHORITY.items():
        if domain == base or domain.endswith(f".{base}"):
            return score
    return None


def make_domain_authority_fn(
    session: Session, *, tenant_id: str, brand_id: str
) -> Callable[[str], float]:
    """Build a `url -> domain_authority` proxy: static high-authority table + frequency fallback.

    A known high-authority domain (`_STATIC_DOMAIN_AUTHORITY`, subdomain-aware) returns its curated
    score. Otherwise the score falls back to how heavily the brand's own citations lean on that
    domain (`seen_count` share of the most-cited domain), scaled into `[0, _FALLBACK_AUTHORITY_CEIL]`
    so an unknown-but-frequent domain never out-scores a curated authoritative one. A never-cited
    unknown domain scores `0.0`. Snapshots citation frequencies once so the closure does no more I/O.
    """
    stmt = select(Citation.domain, Citation.seen_count).where(
        Citation.tenant_id == tenant_id, Citation.brand_id == brand_id
    )
    frequency: dict[str, int] = defaultdict(int)
    for domain, seen_count in session.execute(stmt).all():
        frequency[domain] += seen_count
    max_frequency = max(frequency.values(), default=0)

    def domain_authority_fn(url: str) -> float:
        domain = domain_of(url)
        static = _static_authority(domain)
        if static is not None:
            return static
        if max_frequency == 0:
            return 0.0
        share = frequency.get(domain, 0) / max_frequency
        return round(share * _FALLBACK_AUTHORITY_CEIL, 6)

    return domain_authority_fn


def _with_scheme(domain: str) -> str:
    """Ensure `domain` is fetchable: prepend `https://` unless it already carries a scheme."""
    if domain.startswith(("http://", "https://")):
        return domain
    return f"https://{domain}"


def _url_to_prompt_text(session: Session, *, tenant_id: str, brand_id: str) -> dict[str, str]:
    """`{url: prompt_text}` -- a representative prompt (the first cited) per candidate URL.

    `embedding_similarity` scores a candidate's content against the query it was cited for; a URL
    cited under several prompts uses the first as its representative query.
    """
    stmt = (
        select(Citation.url, Prompt.text)
        .join(Prompt, Citation.prompt_id == Prompt.id)
        .where(Citation.tenant_id == tenant_id, Citation.brand_id == brand_id)
    )
    result: dict[str, str] = {}
    for url, text in session.execute(stmt).all():
        result.setdefault(url, text)
    return result


def _brand_prompt_corpus(session: Session, *, tenant_id: str, brand_id: str) -> str:
    """The brand's prompt texts joined into one representative "query corpus".

    The current homepage isn't tied to any single prompt, so its `embedding_similarity` is scored
    against the union of the queries the brand is measured on.
    """
    stmt = select(Prompt.text).where(Prompt.tenant_id == tenant_id, Prompt.brand_id == brand_id)
    return " ".join(text for text in session.execute(stmt).scalars())


def _empty_inputs(engines: list[str]) -> dict[str, Any]:
    """The no-op result shape for a missing/cross-tenant brand: every requested engine keyed, empty."""
    return {
        "candidates_by_engine": {engine: [] for engine in engines},
        "current_by_engine": {},
        "source_mix_by_engine": {engine: {} for engine in engines},
    }


def _current_feature_vector(
    session: Session,
    brand: Brand,
    *,
    fetcher: PageFetcher,
    embedder: EmbeddingClient,
    now: str,
    domain_authority_fn: Callable[[str], float],
    corroboration_fn: Callable[[str], int],
) -> FeatureVector:
    """The current asset's `FeatureVector`: features of the brand's homepage (fetched once).

    Falls back to an all-zero vector (publish date unknown) when the homepage can't be fetched, so
    `ranking.recommend.find_gaps` treats an unreachable homepage as trailing every target -- a
    conservative "improve everything" signal rather than a crash.
    """
    homepage_url = _with_scheme(brand.domain)
    page = fetcher.fetch(homepage_url)
    authority = domain_authority_fn(homepage_url)
    if page is None:
        return FeatureVector(
            structure_score=0.0,
            info_density=0.0,
            freshness_days=None,
            domain_authority=authority,
            corroboration_count=0,
            embedding_similarity=0.0,
            has_schema=False,
            has_faq=False,
            table_count=0,
        )
    query = _brand_prompt_corpus(session, tenant_id=brand.tenant_id, brand_id=brand.id) or brand.name
    return extract_features(
        content=page.text,
        prompt_text=query,
        domain_authority=authority,
        corroboration_count=corroboration_fn(homepage_url),
        published_at=page.published_at,
        embedder=embedder,
        now=now,
    )


def build_ranking_inputs_from_db(
    session: Session,
    *,
    tenant_id: str,
    brand_id: str,
    engines: list[str],
    fetcher: PageFetcher,
    embedder: EmbeddingClient,
    now: str,
    domain_authority_fn: Callable[[str], float],
    corroboration_fn: Callable[[str], int],
) -> dict[str, Any]:
    """Assemble the three per-engine `run_ranking` inputs from the citation pool.

    Returns `{"candidates_by_engine", "current_by_engine", "source_mix_by_engine"}`:

    - **candidates_by_engine** -- the *same* candidate list for every requested engine (labels are
      applied per-engine inside `run_ranking`, so candidates are engine-agnostic). Each candidate is
      `{"url", "features": FeatureVector}`; features come from `extract_features` over the fetched
      page, with the URL's representative prompt as the similarity query and the injected local
      `domain_authority_fn`/`corroboration_fn` proxies. A URL the fetcher can't return is skipped.
    - **current_by_engine** -- one homepage `FeatureVector` (fetched once), reused for every engine.
    - **source_mix_by_engine** -- `per_engine_source_mix`, filled with `{}` for any requested engine
      that has no citations, so `run_ranking` finds a key for every engine.

    A missing or cross-tenant brand is a no-op (`_empty_inputs`), mirroring
    `orchestration.opportunity_gen.generate_and_persist_opportunities`.
    """
    brand = session.get(Brand, brand_id)
    if brand is None or brand.tenant_id != tenant_id:
        return _empty_inputs(engines)

    prompt_text_by_url = _url_to_prompt_text(session, tenant_id=tenant_id, brand_id=brand_id)

    candidates: list[dict[str, Any]] = []
    for url in candidate_urls_for_brand(session, tenant_id=tenant_id, brand_id=brand_id):
        page = fetcher.fetch(url)
        if page is None:
            continue
        features = extract_features(
            content=page.text,
            prompt_text=prompt_text_by_url.get(url, ""),
            domain_authority=domain_authority_fn(url),
            corroboration_count=corroboration_fn(url),
            published_at=page.published_at,
            embedder=embedder,
            now=now,
        )
        candidates.append({"url": url, "features": features})

    current = _current_feature_vector(
        session,
        brand,
        fetcher=fetcher,
        embedder=embedder,
        now=now,
        domain_authority_fn=domain_authority_fn,
        corroboration_fn=corroboration_fn,
    )
    mix = per_engine_source_mix(session, tenant_id=tenant_id, brand_id=brand_id)

    return {
        "candidates_by_engine": {engine: candidates for engine in engines},
        "current_by_engine": {engine: current for engine in engines},
        "source_mix_by_engine": {engine: mix.get(engine, {}) for engine in engines},
    }
