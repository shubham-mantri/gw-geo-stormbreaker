"""Consumer ChatGPT UI engine adapter (TRD §5.2, m1-design.md §3.2) -- Playwright surface.

Distinct from the M0 `OpenAIAdapter` (`measurement.probe.openai_chatgpt`, `name = "openai"`),
which calls the Responses API directly: this adapter drives the actual chatgpt.com *product*
through the `CaptureClient` seam (docs/tasks/M1-T07-capture-seam.md) and parses the rendered
assistant-turn DOM, so measurement reflects consumer-facing behavior rather than the raw API
(m1-design.md §3). It depends only on the `CaptureClient` Protocol, never on Playwright directly,
so the default test suite stays hermetic (a fake capturer serves a recorded HTML fixture).

Import side-effect-free: this module never calls `measurement.probe.base.register()` itself --
that happens at wiring time (M1-T18), same convention as the other adapters.
"""

from bs4 import BeautifulSoup
from bs4.element import Tag

from gw_geo.capture.base import CaptureClient
from gw_geo.common.models import ProbeResult

# The consumer chatgpt.com DOM is unversioned and can change without notice (m1-design.md §10),
# so every selector below is a best-effort target with a documented fallback -- see
# `_extract_answer` -- rather than a hard structural requirement.
_ASSISTANT_TURN_SELECTOR = '[data-message-author-role="assistant"]'
_MESSAGE_TEXT_SELECTOR = ".markdown"
_LINK_SELECTOR = "a[href]"


def _href(anchor: Tag) -> str | None:
    """Return an anchor's `href` as a plain string, or `None` if it's missing/non-string.

    `Tag.get` is typed to also allow bs4's multi-valued-attribute list form (used for
    attributes like `class`); a list value here would mean malformed/unexpected markup for
    `href`, treated as "no usable link" rather than raising.
    """
    value = anchor.get("href")
    return value if isinstance(value, str) else None


def _normalize_url(href: str | None) -> str | None:
    """Normalize a raw `href` to a comparable absolute URL, or `None` if it isn't usable.

    Strips surrounding whitespace and any `#fragment`, drops a trailing `/`, and rejects
    anything that isn't an absolute `http(s)` link -- relative paths, `mailto:`, and bare
    `#footnote-marker` anchors all normalize away instead of polluting `cited_urls`.
    """
    if href is None:
        return None
    candidate = href.strip().split("#", 1)[0].rstrip("/")
    return candidate if candidate.startswith(("http://", "https://")) else None


def _extract_answer(html: str) -> tuple[str, list[str]]:
    """Parse a chatgpt.com assistant turn into `(answer_text, cited_urls)`.

    Resilient by construction (m1-design.md §10): the assistant-turn and message-text
    selectors each fall back to a wider scope when the narrower node is missing, citation URLs
    are normalized + de-duped (first-seen order), and any unexpected parse failure degrades to
    `("", [])` rather than raising -- a renamed or garbled DOM should never crash a probe run.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")

        turns = soup.select(_ASSISTANT_TURN_SELECTOR)
        turn: Tag = turns[-1] if turns else soup

        text_nodes = turn.select(_MESSAGE_TEXT_SELECTOR)
        text_scope: Tag = text_nodes[-1] if text_nodes else turn
        answer_text = text_scope.get_text(separator=" ", strip=True)

        cited_urls: list[str] = []
        seen: set[str] = set()
        for anchor in turn.select(_LINK_SELECTOR):
            url = _normalize_url(_href(anchor))
            if url is not None and url not in seen:
                seen.add(url)
                cited_urls.append(url)

        return answer_text, cited_urls
    except Exception:
        return "", []


class ChatGPTAdapter:
    """`EngineAdapter` for the consumer chatgpt.com UI (Playwright-captured, DOM-parsed)."""

    name = "chatgpt"
    supports_citations = True

    def __init__(self, capture: CaptureClient) -> None:
        self._capture = capture

    async def probe(
        self, prompt: str, *, geo: str = "us", persona: str | None = None
    ) -> ProbeResult:
        """Fetch a chatgpt.com turn via `capture` and parse it into a `ProbeResult`.

        `geo` selects the capturer's proxy geo and `persona` its authenticated account
        (m1-design.md §3.1) -- both flow straight through to `capture.fetch`.
        """
        page = await self._capture.fetch(prompt, surface=self.name, geo=geo, persona=persona)
        answer_text, cited_urls = _extract_answer(page.html)
        return ProbeResult(
            engine=self.name,
            answer_text=answer_text,
            cited_urls=cited_urls,
            raw={"html": page.html, "final_url": page.final_url},
        )
