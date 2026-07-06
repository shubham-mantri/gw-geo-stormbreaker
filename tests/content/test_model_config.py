"""Tests for the DB-stored, operator-selectable content-chat model (M5 model-selection).

Covers ``content.gateway.resolve_chat_model`` (DB hit + the per-gateway fallback to today's
constants) and that the four content-chat factories thread an explicit ``model`` through while
leaving ``model=None`` behavior (existing callers) unchanged. Hermetic SQLite; no live LLM call.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.config import Settings
from gw_geo.common.db import Base, LlmModelConfig
from gw_geo.content.gateway import (
    DEFAULT_CHAT_MODEL,
    build_claim_extractor,
    build_llm_client,
    build_local_claude_client,
    build_voice_scorer,
    resolve_chat_model,
)
from gw_geo.content.generate import PortkeyLLMClient
from gw_geo.content.llm_local import LocalClaudeCliClient


def _session() -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return Session(eng)


# ---- resolve_chat_model -----------------------------------------------------------------------


def test_resolve_reads_db_row_for_active_gateway() -> None:
    s = _session()
    s.add(LlmModelConfig(gateway="portkey", chat_model="claude-sonnet-4-5"))
    s.add(LlmModelConfig(gateway="local_claude", chat_model="opus"))
    s.commit()
    settings = Settings(llm_gateway="portkey")
    assert resolve_chat_model(s, gateway="portkey", settings=settings) == "claude-sonnet-4-5"
    assert resolve_chat_model(s, gateway="local_claude", settings=settings) == "opus"


def test_resolve_falls_back_to_settings_claude_cli_model_for_local() -> None:
    # No row -> local_claude falls back to settings.claude_cli_model (today's constant default).
    s = _session()
    settings = Settings(llm_gateway="local_claude", claude_cli_model="sonnet")
    assert resolve_chat_model(s, gateway="local_claude", settings=settings) == "sonnet"


def test_resolve_falls_back_to_default_chat_model_for_portkey_and_direct() -> None:
    s = _session()
    settings = Settings()
    assert resolve_chat_model(s, gateway="portkey", settings=settings) == DEFAULT_CHAT_MODEL
    assert resolve_chat_model(s, gateway="direct", settings=settings) == DEFAULT_CHAT_MODEL


# ---- factories honor an explicit model --------------------------------------------------------


def test_build_local_claude_client_honors_explicit_model() -> None:
    client = build_local_claude_client(Settings(), model="opus[1m]")
    assert client._model == "opus[1m]"


def test_build_local_claude_client_default_unchanged_when_model_none() -> None:
    # model=None keeps today's default (settings.claude_cli_model) -> existing callers unchanged.
    client = build_local_claude_client(Settings(claude_cli_model="sonnet"))
    assert client._model == "sonnet"


def test_build_llm_client_threads_model_local_and_portkey() -> None:
    local = build_llm_client(Settings(llm_gateway="local_claude"), model="opus")
    assert isinstance(local, LocalClaudeCliClient) and local._model == "opus"

    portkey = build_llm_client(
        Settings(llm_gateway="portkey", portkey_api_key="pk"), model="claude-opus-4-8"
    )
    assert isinstance(portkey, PortkeyLLMClient) and portkey._model == "claude-opus-4-8"


def test_build_llm_client_portkey_default_preserved_when_model_none() -> None:
    portkey = build_llm_client(Settings(llm_gateway="portkey", portkey_api_key="pk"))
    assert isinstance(portkey, PortkeyLLMClient) and portkey._model == DEFAULT_CHAT_MODEL


def test_build_claim_extractor_and_voice_scorer_thread_model() -> None:
    s = Settings(llm_gateway="portkey", portkey_api_key="pk")
    assert build_claim_extractor(s, model="claude-opus-4-8")._model == "claude-opus-4-8"
    assert build_voice_scorer(s, model="claude-opus-4-8")._model == "claude-opus-4-8"


def test_local_claim_extractor_and_voice_scorer_thread_model_to_cli_client() -> None:
    s = Settings(llm_gateway="local_claude")
    extractor = build_claim_extractor(s, model="haiku")
    scorer = build_voice_scorer(s, model="haiku")
    assert isinstance(extractor._llm, LocalClaudeCliClient) and extractor._llm._model == "haiku"
    assert isinstance(scorer._llm, LocalClaudeCliClient) and scorer._llm._model == "haiku"
