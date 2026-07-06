"""Round-trip test for migration ``0008`` (llm_model_config), M5 model-selection.

Hermetic: runs the *real* Alembic chain ``0001 -> head`` against a scratch file-backed SQLite DB
(file, not ``:memory:`` -- Alembic opens its own connections, and an in-memory DB is per-connection),
then downgrades ``0008 -> 0007``. Asserts the table + three seeded gateway defaults appear on upgrade
and the table is dropped on downgrade. The DB URL is passed via ``-x db_url=...`` (env.py honors it
over the lru-cached ``get_settings().database_url``, so this never touches the developer's Postgres).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    # env.py reads `-x db_url=...` first (before the lru-cached settings), pinning the scratch DB.
    cfg.cmd_opts = argparse.Namespace(x=[f"db_url={db_url}"])
    return cfg


def test_migration_0008_creates_seeds_and_drops(tmp_path: Path) -> None:
    db_path = tmp_path / "mig0008.db"
    url = f"sqlite:///{db_path}"
    cfg = _alembic_config(url)
    engine = create_engine(url)

    # up: the full chain lands the table + its three seeded gateway defaults.
    command.upgrade(cfg, "head")
    assert "llm_model_config" in inspect(engine).get_table_names()
    with engine.connect() as conn:
        rows = sorted(
            conn.execute(text("SELECT gateway, chat_model FROM llm_model_config")).all()
        )
    assert rows == [
        ("direct", "claude-opus-4-8"),
        ("local_claude", "sonnet"),
        ("portkey", "claude-haiku-4-5-20251001"),
    ]

    # down: 0008 -> 0007 drops the table (chain still intact below it).
    command.downgrade(cfg, "0007")
    assert "llm_model_config" not in inspect(engine).get_table_names()
    engine.dispose()
