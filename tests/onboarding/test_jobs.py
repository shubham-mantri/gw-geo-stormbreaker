"""Tests for the async-suggest job store + runner (``gw_geo.onboarding.jobs``).

Hermetic: the runner drives the (already-hermetic) suggest pipeline with the same injected
``FakeFetcher``/``ScriptedLLM`` fakes as ``test_suggest.py`` -- no live HTTP/LLM call. The store is a
plain in-memory, thread-safe map; these tests exercise its state machine (create -> running,
set_stage, finish -> done, fail -> error) and the runner's success/degrade paths directly (no
event loop needed -- ``run_suggest_job`` is a plain sync function).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from gw_geo.onboarding.jobs import SuggestJobStore, run_suggest_job
from gw_geo.onboarding.suggest import BrandSuggestion
from gw_geo.ranking.fetch import FetchedPage


class FakeFetcher:
    """A ``PageFetcher`` returning a canned ``FetchedPage`` (or ``None``) -- no live HTTP."""

    def __init__(self, page: FetchedPage | None) -> None:
        self._page = page

    def fetch(self, url: str) -> FetchedPage | None:
        return self._page


class OneShotLLM:
    """An ``LLMClient`` returning one canned ``{name, competitors, prompts}`` dict for every stage.

    The four suggest stages each read the field they need off the shared dict (profile -> ``name``,
    draft/critique -> ``competitors``, seed-prompts -> ``prompts``), so one fake drives the whole
    flow end-to-end without a live call."""

    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._result


# --- store state machine ------------------------------------------------------------------------


def test_create_seeds_a_running_job_and_returns_a_hex_id() -> None:
    store = SuggestJobStore()
    job_id = store.create()
    assert isinstance(job_id, str) and len(job_id) == 32  # uuid4().hex
    job = store.get(job_id)
    assert job is not None
    assert job.status == "running"
    assert job.result is None and job.error is None


def test_set_stage_updates_stage_and_label_while_running() -> None:
    store = SuggestJobStore()
    job_id = store.create()
    store.set_stage(job_id, "researching", "Researching competitors across the web")
    job = store.get(job_id)
    assert job is not None
    assert job.status == "running"
    assert job.stage == "researching"
    assert job.label == "Researching competitors across the web"


def test_finish_transitions_to_done_with_result() -> None:
    store = SuggestJobStore()
    job_id = store.create()
    result = BrandSuggestion(name="Acme", domain="acme.com", competitors=["Beta"])
    store.finish(job_id, result)
    job = store.get(job_id)
    assert job is not None
    assert job.status == "done"
    assert job.stage == "done"
    assert job.result == result and job.error is None


def test_fail_transitions_to_error_with_message() -> None:
    store = SuggestJobStore()
    job_id = store.create()
    store.fail(job_id, "boom")
    job = store.get(job_id)
    assert job is not None
    assert job.status == "error"
    assert job.error == "boom" and job.result is None


def test_set_stage_is_a_noop_once_terminal() -> None:
    # A late progress update after done/error must not resurrect the job to running.
    store = SuggestJobStore()
    job_id = store.create()
    store.finish(job_id, BrandSuggestion(name="Acme", domain="acme.com", competitors=[]))
    store.set_stage(job_id, "researching", "late update")
    job = store.get(job_id)
    assert job is not None and job.status == "done" and job.stage == "done"


def test_get_unknown_job_returns_none() -> None:
    assert SuggestJobStore().get("nope") is None


# --- runner: drives the pipeline, records the outcome -------------------------------------------


def test_run_suggest_job_streams_stages_and_finishes_done() -> None:
    store = SuggestJobStore()
    job_id = store.create()
    llm = OneShotLLM(
        {
            "name": "Acme",
            "competitors": [{"name": "Beta"}, {"name": "Gamma"}],
            "prompts": ["best CRM for startups", "how do I choose a CRM"],
        }
    )
    run_suggest_job(
        store,
        job_id,
        domain="acme.com",
        fetcher=FakeFetcher(FetchedPage(text="<title>Acme</title>")),
        llm=llm,
        critic=llm,
    )
    job = store.get(job_id)
    assert job is not None
    assert job.status == "done"
    assert job.result is not None
    assert job.result.name == "Acme"
    assert job.result.competitors == ["Beta", "Gamma"]
    # the job result surfaces the recommended seed prompts alongside the competitors
    assert job.result.seed_prompts == ["best CRM for startups", "how do I choose a CRM"]


def test_run_suggest_job_records_error_when_pipeline_raises() -> None:
    # The pipeline is normally total; if it ever raised, the runner must not let it escape the
    # background thread -- it records status="error" instead.
    store = SuggestJobStore()
    job_id = store.create()
    llm = OneShotLLM({"name": "Acme", "competitors": []})
    with patch(
        "gw_geo.onboarding.jobs.suggest_brand_details", side_effect=RuntimeError("kaboom")
    ):
        run_suggest_job(
            store, job_id, domain="acme.com", fetcher=FakeFetcher(None), llm=llm, critic=llm
        )
    job = store.get(job_id)
    assert job is not None
    assert job.status == "error"
    assert job.error is not None and "kaboom" in job.error
