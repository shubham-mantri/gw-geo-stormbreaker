"""ORM tests for the M5 ``llm_model_config`` table (system-level chat-model selection per gateway).

Hermetic SQLite (FK enforcement ON via the suite ``conftest``). Covers the round-trip and the
``gateway`` primary-key uniqueness the upsert endpoint / migration seed rely on.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from gw_geo.common.db import Base, LlmModelConfig


def _session() -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return Session(eng)


def test_llm_model_config_roundtrips() -> None:
    s = _session()
    s.add(LlmModelConfig(gateway="portkey", chat_model="claude-haiku-4-5-20251001"))
    s.commit()
    assert s.get(LlmModelConfig, "portkey").chat_model == "claude-haiku-4-5-20251001"


def test_llm_model_config_gateway_is_unique_pk() -> None:
    # `gateway` is the PK -> a second row for the same gateway is rejected (backs the PUT upsert's
    # get-then-update, and the migration's one-row-per-gateway seed).
    s = _session()
    s.add(LlmModelConfig(gateway="local_claude", chat_model="sonnet"))
    s.commit()
    s.add(LlmModelConfig(gateway="local_claude", chat_model="opus"))
    with pytest.raises(IntegrityError):
        s.commit()


def test_llm_model_config_is_system_level_no_tenant() -> None:
    # Global operator config, not tenant-owned (documented exception to the per-row tenant_id rule).
    assert "tenant_id" not in LlmModelConfig.__table__.columns
