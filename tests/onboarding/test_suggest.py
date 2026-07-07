"""Tests for the grounded, self-critiquing domain-first onboarding auto-fill
(`gw_geo.onboarding.suggest`).

Hermetic: the page fetcher + the two LLM clients are injected Protocols, so **no live HTTP or LLM
call is ever made** (mirrors the injected-seam convention in `tests/ranking/test_fetch.py` /
`tests/content/test_generate.py`). The `ScriptedLLM` fake dispatches on the stage's structured-output
schema (``schema is _PROFILE_SCHEMA`` / ``_DRAFT_SCHEMA`` / ``_CRITIQUE_SCHEMA``) and hands back a
canned dict (or raises a scripted error), so a test scripts each of the three stages
(profile -> web-search-grounded draft -> critique/refine) independently. The failure-mode tests
assert the "never raise -- degrade to the best available result" guarantee onboarding depends on,
and the headline test exercises the real regression: a draft carrying a duplicate acquired entity +
a scale mismatch + a missing product category, refined by the critique into a clean set.
"""

from __future__ import annotations

from typing import Any

from gw_geo.onboarding.suggest import (
    _CRITIQUE_SCHEMA,
    _DRAFT_SCHEMA,
    _PROFILE_SCHEMA,
    BrandSuggestion,
    normalize_url,
    suggest_brand_details,
)
from gw_geo.ranking.fetch import FetchedPage


class FakeFetcher:
    """A `PageFetcher` that returns a canned `FetchedPage` (or `None`) and records the fetched URL."""

    def __init__(self, page: FetchedPage | None) -> None:
        self._page = page
        self.fetched_url: str | None = None

    def fetch(self, url: str) -> FetchedPage | None:
        self.fetched_url = url
        return self._page


class RaisingFetcher:
    """A `PageFetcher` whose `fetch` raises -- exercises the "never raise on fetch failure" path."""

    def fetch(self, url: str) -> FetchedPage | None:
        raise RuntimeError("network exploded")


def _stage(schema: Any) -> str:
    """Which pipeline stage a `complete()` call is, keyed on the (identity of the) schema passed."""
    if schema is _PROFILE_SCHEMA:
        return "profile"
    if schema is _DRAFT_SCHEMA:
        return "draft"
    if schema is _CRITIQUE_SCHEMA:
        return "critique"
    raise AssertionError(f"unexpected schema: {schema!r}")


class ScriptedLLM:
    """An `LLMClient` that returns a canned dict per pipeline stage and records prompts/calls.

    Construct with ``profile=``/``draft=``/``critique=`` responses; a value that is an ``Exception``
    is raised (to simulate a failed stage), a dict is returned, and an un-scripted stage raises
    ``RuntimeError`` (so misrouted stages are catchable via ``.calls``).
    """

    def __init__(self, **stages: Any) -> None:
        self._stages = stages
        self.calls: list[str] = []
        self.prompts: dict[str, str] = {}

    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        stage = _stage(schema)
        self.calls.append(stage)
        self.prompts[stage] = prompt
        if stage not in self._stages:
            raise RuntimeError(f"no scripted {stage} response")
        resp = self._stages[stage]
        if isinstance(resp, BaseException):
            raise resp
        assert isinstance(resp, dict)
        return resp


class RaisingLLM:
    """An `LLMClient` whose `complete` always raises -- exercises total LLM-unavailability degrade."""

    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        raise RuntimeError("llm exploded")


# --- normalize_url ---------------------------------------------------------------------------


def test_normalize_url_adds_https_scheme() -> None:
    assert normalize_url("acme.com") == "https://acme.com"
    assert normalize_url("  acme.com  ") == "https://acme.com"


def test_normalize_url_keeps_existing_scheme() -> None:
    assert normalize_url("http://acme.com") == "http://acme.com"
    assert normalize_url("https://acme.com/x") == "https://acme.com/x"


# --- pipeline wiring: profile -> draft -> critique, each grounded on the prior --------------------


def test_pipeline_runs_three_stages_and_grounds_each_on_the_prior() -> None:
    fetcher = FakeFetcher(FetchedPage(text="<head><title>Acme | The best CRM</title></head>"))
    llm = ScriptedLLM(
        profile={"name": "Acme Robotics", "categories": ["CRM", "sales analytics"]},
        draft={"competitors": [{"name": "Beta", "category": "CRM", "tier": "direct"}]},
        critique={"competitors": ["Beta", "Gamma"]},
    )
    out = suggest_brand_details(domain="acme.com", fetcher=fetcher, llm=llm)

    assert out.name == "Acme Robotics"  # name from the profile stage (LLM), not the <title>
    assert out.competitors == ["Beta", "Gamma"]  # final list = the critique's refined output
    assert llm.calls == ["profile", "draft", "critique"]  # all three stages ran, in order
    assert fetcher.fetched_url == "https://acme.com"  # normalized URL fetched for grounding
    # The page name-hint grounds the profile prompt...
    assert "Acme" in llm.prompts["profile"]
    # ...the profile's category set grounds the draft prompt...
    assert "CRM" in llm.prompts["draft"] and "sales analytics" in llm.prompts["draft"]
    # ...and the draft's candidates ground the critique prompt.
    assert "Beta" in llm.prompts["critique"]


# --- headline regression: dedupe (acquired) + demote (scale) + cover (missing category) ----------


def test_critique_dedupes_acquired_demotes_scale_and_covers_categories() -> None:
    # The respectmanufacturing.com failure modes, reproduced in one draft: a duplicate acquired
    # entity (Nutricap Labs == NutraScience Labs, acquired 2015), a scale mismatch (enterprise-scale
    # Sirio Pharma vs an 11-50-employee SMB shop), and only the supplements category covered.
    llm = ScriptedLLM(
        profile={
            "name": "Respect Manufacturing",
            "categories": ["supplements", "cosmetics", "skincare", "OTC topicals"],
            "size_segment": "SMB contract manufacturer, 11-50 employees, serves small brands",
        },
        draft={
            "competitors": [
                {"name": "NutraScience Labs", "category": "supplements", "tier": "direct"},
                {"name": "Nutricap Labs", "category": "supplements", "tier": "direct"},  # acquired
                {"name": "Sirio Pharma", "category": "supplements", "tier": "aspirational",
                 "segment": "enterprise"},  # scale mismatch
                # (only supplements covered -- cosmetics/skincare/OTC missing)
            ]
        },
        # The critique refines: drops the acquired dup, demotes the enterprise player, and adds
        # coverage for the missing cosmetics/skincare category.
        critique={
            "competitors": ["NutraScience Labs", "Cosmetic Solutions Corp"],
            "coverage_notes": "Dropped Nutricap (acquired by NutraScience); demoted Sirio "
            "(enterprise); added Cosmetic Solutions for cosmetics/skincare.",
        },
    )
    out = suggest_brand_details(domain="respectmanufacturing.com", fetcher=FakeFetcher(None), llm=llm)

    names = [c.lower() for c in out.competitors]
    assert "nutricap labs" not in names  # the acquired/renamed duplicate is gone
    assert "sirio pharma" not in names  # the scale/segment mismatch is dropped
    assert "nutrascience labs" in names  # the surviving entity is kept
    assert "cosmetic solutions corp" in names  # the missing category is now covered
    assert out.competitors == ["NutraScience Labs", "Cosmetic Solutions Corp"]
    # The critique was actually shown the raw draft -- including the entities it must reconcile.
    critique_prompt = llm.prompts["critique"]
    for shown in ("NutraScience Labs", "Nutricap Labs", "Sirio Pharma"):
        assert shown in critique_prompt
    # ...grounded against the profile's full category set (so it can check coverage).
    assert "cosmetics" in critique_prompt and "OTC topicals" in critique_prompt


def test_deterministic_backstop_dedupes_and_excludes_self_on_critique_output() -> None:
    # Even if the critique leaks a case dupe or the brand itself, the deterministic clean-up catches
    # it (parent-company dedupe is the LLM's job; this is the exact/case-insensitive backstop).
    llm = ScriptedLLM(
        profile={"name": "Acme", "categories": ["CRM"]},
        draft={"competitors": [{"name": "Beta"}]},
        critique={"competitors": ["Beta", "beta", "Acme", "Gamma", "GAMMA"]},
    )
    out = suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(None), llm=llm)
    assert out.competitors == ["Beta", "Gamma"]  # case dupes collapsed, brand self-excluded


def test_final_list_capped_at_eight() -> None:
    many = [f"C{i}" for i in range(12)]
    llm = ScriptedLLM(
        profile={"name": "Acme", "categories": ["CRM"]},
        draft={"competitors": [{"name": "seed"}]},
        critique={"competitors": many},
    )
    out = suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(None), llm=llm)
    assert out.competitors == many[:8]  # ordered, capped at ~8


# --- graceful degrade: each stage failing falls back to the best available result ----------------


def test_critique_failure_falls_back_to_draft_direct_names_excluding_aspirational() -> None:
    # Critique down -> use the draft's DIRECT-tier names; the aspirational (larger) player is still
    # excluded, so a scale mismatch never sneaks in even without the refine pass.
    llm = ScriptedLLM(
        profile={"name": "Acme", "categories": ["CRM"]},
        draft={
            "competitors": [
                {"name": "Beta", "tier": "direct"},
                {"name": "Gamma", "tier": "direct"},
                {"name": "BigCorp", "tier": "aspirational"},
            ]
        },
        critique=RuntimeError("critique exploded"),
    )
    out = suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(None), llm=llm)
    assert out.competitors == ["Beta", "Gamma"]  # direct-tier only; BigCorp (aspirational) dropped
    assert llm.calls == ["profile", "draft", "critique"]  # critique was attempted, then failed


def test_draft_failure_yields_name_but_no_competitors_and_skips_critique() -> None:
    llm = ScriptedLLM(
        profile={"name": "Acme Robotics", "categories": ["CRM"]},
        draft=RuntimeError("draft exploded"),
    )
    out = suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(None), llm=llm)
    assert out.name == "Acme Robotics"  # profile name still stands
    assert out.competitors == []  # no draft -> nothing to refine
    assert "critique" not in llm.calls  # critique is skipped when the draft is empty


def test_profile_failure_still_drafts_and_names_from_domain() -> None:
    # Profile down -> the draft/critique still run (profile=None), and the name falls to the domain.
    llm = ScriptedLLM(
        profile=RuntimeError("profile exploded"),
        draft={"competitors": [{"name": "Beta"}]},
        critique={"competitors": ["Beta"]},
    )
    out = suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(None), llm=llm)
    assert out.name == "Acme"  # domain heuristic, since the profile gave no name
    assert out.competitors == ["Beta"]
    assert "No profile available" in llm.prompts["draft"]  # draft told there's no profile


def test_fetch_failure_never_raises_and_drops_grounding() -> None:
    llm = ScriptedLLM(
        profile={"name": "Acme", "categories": ["CRM"]},
        draft={"competitors": [{"name": "Beta"}]},
        critique={"competitors": ["Beta"]},
    )
    out = suggest_brand_details(domain="acme.com", fetcher=RaisingFetcher(), llm=llm)
    assert out.name == "Acme" and out.competitors == ["Beta"]
    assert "Name hint" not in llm.prompts["profile"]  # no page -> no hint line
    assert "Visible page text" not in llm.prompts["profile"]


def test_all_llm_stages_failing_degrades_to_domain_name_and_empty() -> None:
    # The total-failure floor == today's behavior: domain-heuristic name, no competitors, never 5xx.
    out = suggest_brand_details(domain="globex.com", fetcher=FakeFetcher(None), llm=RaisingLLM())
    assert out.name == "Globex"
    assert out.competitors == []


# --- the two-client seam: research (llm) vs critic ---------------------------------------------


def test_critic_defaults_to_llm_when_omitted() -> None:
    llm = ScriptedLLM(
        profile={"name": "Acme", "categories": ["CRM"]},
        draft={"competitors": [{"name": "Beta"}]},
        critique={"competitors": ["Beta", "Gamma"]},
    )
    out = suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(None), llm=llm)  # no critic
    assert out.competitors == ["Beta", "Gamma"]
    assert llm.calls == ["profile", "draft", "critique"]  # llm handled the critique too


def test_separate_critic_client_runs_only_the_critique_stage() -> None:
    research = ScriptedLLM(
        profile={"name": "Acme", "categories": ["CRM"]},
        draft={"competitors": [{"name": "Beta"}]},
    )
    critic = ScriptedLLM(critique={"competitors": ["Beta", "Gamma"]})
    out = suggest_brand_details(
        domain="acme.com", fetcher=FakeFetcher(None), llm=research, critic=critic
    )
    assert out.competitors == ["Beta", "Gamma"]
    assert research.calls == ["profile", "draft"]  # web-search client: research only
    assert critic.calls == ["critique"]  # plain client: refine only


# --- name resolution: profile name wins, else the domain heuristic -----------------------------


def test_name_from_profile_when_present() -> None:
    llm = ScriptedLLM(
        profile={"name": "Globex Corporation", "categories": []},
        draft={"competitors": []},
    )
    out = suggest_brand_details(domain="globex.com", fetcher=FakeFetcher(None), llm=llm)
    assert out.name == "Globex Corporation"


def test_name_falls_back_to_domain_when_profile_name_blank() -> None:
    cases = {"acme.com": "Acme", "https://www.foo-bar.io/": "Foo Bar", "WWW.Globex.CO": "Globex"}
    for domain, expected in cases.items():
        llm = ScriptedLLM(profile={"categories": []}, draft={"competitors": []})
        out = suggest_brand_details(domain=domain, fetcher=FakeFetcher(None), llm=llm)
        assert out.name == expected, domain


# --- profile grounding: the fetched page (hint + visible text) reaches the profile prompt --------


def test_jsonld_name_and_page_text_ground_the_profile_prompt() -> None:
    html = """
    <html><head>
      <title>Home | Boilerplate</title>
      <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Organization","name":"Acme Robotics"}
      </script>
    </head><body>We build supplements and cosmetics for small brands.</body></html>
    """
    llm = ScriptedLLM(
        profile={"name": "Acme Robotics", "categories": ["supplements"]},
        draft={"competitors": []},
    )
    suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(FetchedPage(text=html)), llm=llm)
    profile_prompt = llm.prompts["profile"]
    assert "Acme Robotics" in profile_prompt  # JSON-LD name fed as the hint (beats <title>)
    assert "supplements and cosmetics" in profile_prompt  # visible page text excerpt grounds it


def test_visible_text_snippet_grounds_profile_when_no_markup() -> None:
    # The real HttpxPageFetcher returns visible text only -> a bounded snippet becomes the grounding.
    llm = ScriptedLLM(profile={"name": "Acme", "categories": []}, draft={"competitors": []})
    suggest_brand_details(
        domain="acme.com",
        fetcher=FakeFetcher(FetchedPage(text="Welcome to Acme, the CRM built for teams")),
        llm=llm,
    )
    assert "Welcome to Acme" in llm.prompts["profile"]


# --- malformed payloads degrade to the best available result -----------------------------------


def test_malformed_profile_and_draft_degrade_to_domain_and_empty() -> None:
    # A non-dict profile -> None; a draft with no usable competitors -> [] -> critique skipped.
    for draft_payload in ({}, {"competitors": "nope"}, {"competitors": [1, 2, 3]}, {"other": []}):
        llm = ScriptedLLM(profile={"not": "a profile"}, draft=draft_payload)
        out = suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(None), llm=llm)
        assert out.name == "Acme", draft_payload  # domain heuristic
        assert out.competitors == [], draft_payload


# --- BrandSuggestion shape (unchanged response contract) ---------------------------------------


def test_brand_suggestion_shape_unchanged() -> None:
    llm = ScriptedLLM(
        profile={"name": "Acme", "categories": ["CRM"]},
        draft={"competitors": [{"name": "Beta"}]},
        critique={"competitors": ["Beta"]},
    )
    out = suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(None), llm=llm)
    assert isinstance(out, BrandSuggestion)
    assert out.model_dump() == {"name": "Acme", "domain": "acme.com", "competitors": ["Beta"]}
