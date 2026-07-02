"""Content feature extraction: interpretable heuristics → `FeatureVector` (PRD §6.3, TRD §8).

Every feature is either a pure-Python, deterministic heuristic over the raw content string
(`structure_score`, `info_density`, `freshness_days`, and the schema/FAQ/table format signals) or
a pass-through of a value the caller already computed elsewhere (`domain_authority`,
`corroboration_count` come from the measurement/attribution subsystems, not from this module).

The one feature that needs a real model — `embedding_similarity` — is computed via an
**injected** `EmbeddingClient`, exactly like `Extractor` in `measurement/parse.py`: hermetic
tests supply a deterministic stub, and the real embedding-model-backed client is wired in only at
runtime. This module never makes a live call.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Protocol

from gw_geo.common.models import FeatureVector


class EmbeddingClient(Protocol):
    """Anything that can turn text into an embedding vector.

    The real implementation calls the configured embedding model; tests inject a deterministic
    stub so `extract_features` never makes a live call.
    """

    def embed(self, text: str) -> list[float]: ...


# --------------------------------------------------------------------------------------------
# Cosine similarity
# --------------------------------------------------------------------------------------------


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Returns 0.0 (not an error) if either vector is all-zero."""
    norm_a = math.hypot(*a)
    norm_b = math.hypot(*b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (norm_a * norm_b)


# --------------------------------------------------------------------------------------------
# Info density: numeric/stat tokens per 100 words
# --------------------------------------------------------------------------------------------

_STAT_TOKEN_RE = re.compile(r"\d[\d,.]*%?")


def info_density(text: str) -> float:
    """Numeric/stat-like tokens (counts, percentages, decimals) per 100 words.

    e.g. "cut costs by 40% and saved 12 hours" contains 2 stat tokens. Deliberately simple: any
    digit run, optionally with thousands separators/decimals and a trailing `%`.
    """
    words = text.split()
    if not words:
        return 0.0
    stat_tokens = len(_STAT_TOKEN_RE.findall(text))
    return (stat_tokens / len(words)) * 100.0


# --------------------------------------------------------------------------------------------
# Structure score: heading + list + table + definition-first opening
# --------------------------------------------------------------------------------------------

_HEADING_LINE_RE = re.compile(r"^#{1,6}\s+\S|^<h[1-6][ >]", re.IGNORECASE)
_LIST_LINE_RE = re.compile(r"^[-*+]\s+\S|^<li[ >]", re.IGNORECASE)
_MD_TABLE_SEP_LINE_RE = re.compile(r"^[\s|:-]*-[\s|:-]*$")
_HTML_TABLE_RE = re.compile(r"<table[ >]", re.IGNORECASE)
_DEFINITION_LEAD_RE = re.compile(
    r"^[\w][\w'’-]*(?:\s+[\w'’-]+){0,5}\s+(is|are|refers to|means)\s+",
    re.IGNORECASE,
)


def _count_tables(content: str) -> int:
    """Markdown tables (by header-separator row, e.g. `|--|--|`) plus `<table>` tags."""
    md_tables = sum(
        1 for line in content.splitlines() if _MD_TABLE_SEP_LINE_RE.match(line.strip())
    )
    html_tables = len(_HTML_TABLE_RE.findall(content))
    return md_tables + html_tables


def _is_definition_first(lines: list[str]) -> bool:
    """True if the first non-heading, non-blank line opens "<Term> is/are/means/refers to ..."."""
    for line in lines:
        stripped = line.strip()
        if not stripped or _HEADING_LINE_RE.match(stripped):
            continue
        return bool(_DEFINITION_LEAD_RE.match(stripped))
    return False


def structure_score(content: str) -> float:
    """0..1 structural-richness score: heading, list, table, definition-first opening.

    Each of the four binary signals contributes an equal share (0.25). This is an explainable
    additive score, not a probability — it exists to be a legible input to the per-engine ranking
    model (TRD §8), not to be independently calibrated.
    """
    lines = content.splitlines()
    signals = [
        any(_HEADING_LINE_RE.match(line.strip()) for line in lines),
        any(_LIST_LINE_RE.match(line.strip()) for line in lines),
        _count_tables(content) > 0,
        _is_definition_first(lines),
    ]
    return sum(signals) / len(signals)


# --------------------------------------------------------------------------------------------
# Freshness
# --------------------------------------------------------------------------------------------


def _parse_date(value: str) -> date:
    """Parse a date-only (`2026-06-22`) or full ISO-8601 (optionally `Z`-suffixed) string."""
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    return datetime.fromisoformat(text).date()


def freshness_days(published_at: str | None, now: str) -> float | None:
    """Days between `published_at` and `now`; `None` if the publish date is unknown."""
    if published_at is None:
        return None
    return float((_parse_date(now) - _parse_date(published_at)).days)


# --------------------------------------------------------------------------------------------
# Format signals: JSON-LD schema / FAQ
# --------------------------------------------------------------------------------------------

_FAQ_HEADING_RE = re.compile(r"faq|q\s*&\s*a|frequently asked questions", re.IGNORECASE)


def _has_schema(content: str) -> bool:
    """JSON-LD structured data: content contains an `application/ld+json` block."""
    return "application/ld+json" in content.lower()


def _has_faq(content: str) -> bool:
    """An FAQ/Q&A heading, or a schema.org `FAQPage` type, anywhere in the content."""
    if "faqpage" in content.lower():
        return True
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and _HEADING_LINE_RE.match(stripped) and _FAQ_HEADING_RE.search(stripped):
            return True
    return False


# --------------------------------------------------------------------------------------------
# extract_features
# --------------------------------------------------------------------------------------------


def extract_features(
    *,
    content: str,
    prompt_text: str,
    domain_authority: float,
    corroboration_count: int,
    published_at: str | None,
    embedder: EmbeddingClient,
    now: str,
) -> FeatureVector:
    """Build the full `FeatureVector` for one piece of content against one prompt.

    `domain_authority` and `corroboration_count` are passed straight through — they are computed
    upstream (source-domain reputation, cross-domain corroboration count) and simply carried into
    the vector here. Every other field is derived from `content`/`prompt_text`.
    """
    similarity = cosine(embedder.embed(content), embedder.embed(prompt_text))
    return FeatureVector(
        structure_score=structure_score(content),
        info_density=info_density(content),
        freshness_days=freshness_days(published_at, now),
        domain_authority=domain_authority,
        corroboration_count=corroboration_count,
        embedding_similarity=similarity,
        has_schema=_has_schema(content),
        has_faq=_has_faq(content),
        table_count=_count_tables(content),
    )
