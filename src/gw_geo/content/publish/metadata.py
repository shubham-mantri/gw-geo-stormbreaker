"""Schema.org JSON-LD + freshness metadata for published content (PRD §6.4, TRD §9).

`build_jsonld` renders an `Article` or `FAQPage` JSON-LD object for a `ContentDraft`; `freshness_meta`
renders the `datePublished`/`dateModified` pair. Every `PublishConnector` (T18) attaches both to its
publish payload and uses them to trigger sitemap resubmission.
"""

import re
from typing import Any

from gw_geo.common.models import ContentDraft

_SCHEMA_CONTEXT = "https://schema.org"

# A markdown heading reads as a "question" heading when it is shaped like `Q`, `Q1:`, `Q.`,
# `Question: ...`, or ends in `?`. Mirrors the `has_faq` signal used by `ranking/features.py`.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_QUESTION_HEADING_RE = re.compile(r"^(?:q\d*\b[:.]?|question\b[:.]?)", re.IGNORECASE)
_FAQ_KEYWORD_RE = re.compile(r"\bfaq\b|\bq\s*&\s*a\b|frequently asked questions", re.IGNORECASE)


def _is_question_heading(text: str) -> bool:
    return text.endswith("?") or bool(_QUESTION_HEADING_RE.match(text))


def _looks_like_faq(draft: ContentDraft) -> bool:
    """Heuristic: does the draft's body/schema indicate Q&A content?

    True when the draft already carries an explicit `FAQPage` schema hint, its body mentions
    "FAQ"/"Q&A"/"frequently asked questions", or it has at least one question-shaped heading
    (`## Q`, `## Q1:`, `## Question: ...`, or a heading ending in `?`).
    """
    if draft.schema_jsonld.get("@type") == "FAQPage":
        return True
    if _FAQ_KEYWORD_RE.search(draft.body_markdown):
        return True
    for line in draft.body_markdown.splitlines():
        heading = _HEADING_RE.match(line)
        if heading and _is_question_heading(heading.group(2)):
            return True
    return False


def _extract_qa_pairs(body_markdown: str) -> list[dict[str, Any]]:
    """Best-effort extraction of (question heading -> following text) pairs for `mainEntity`."""
    pairs: list[dict[str, Any]] = []
    question: str | None = None
    answer_lines: list[str] = []

    def flush() -> None:
        if question is None:
            return
        answer = " ".join(part.strip() for part in answer_lines if part.strip())
        if answer:
            pairs.append(
                {
                    "@type": "Question",
                    "name": question,
                    "acceptedAnswer": {"@type": "Answer", "text": answer},
                }
            )

    for line in body_markdown.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            text = heading.group(2)
            question = text if _is_question_heading(text) else None
            answer_lines = []
        elif question is not None:
            answer_lines.append(line)
    flush()
    return pairs


def freshness_meta(published: str, modified: str) -> dict[str, Any]:
    """Freshness fields (PRD §6.4) every connector attaches to its publish payload."""
    return {"datePublished": published, "dateModified": modified}


def build_jsonld(draft: ContentDraft, *, published: str, modified: str) -> dict[str, Any]:
    """Render a schema.org JSON-LD object (`Article` or `FAQPage`) describing `draft`.

    `@type` is `FAQPage` when the draft's body/schema indicates Q&A content (`_looks_like_faq`),
    else `Article`. For `FAQPage`, `mainEntity` is populated from question-shaped headings when any
    are found.
    """
    is_faq = _looks_like_faq(draft)
    jsonld: dict[str, Any] = {
        "@context": _SCHEMA_CONTEXT,
        "@type": "FAQPage" if is_faq else "Article",
        "headline": draft.title,
        **freshness_meta(published, modified),
    }
    if is_faq:
        qa_pairs = _extract_qa_pairs(draft.body_markdown)
        if qa_pairs:
            jsonld["mainEntity"] = qa_pairs
    return jsonld
