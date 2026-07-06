import sqlite3

import pytest
from sqlalchemy import Engine, event

from gw_geo.common import config as _config

# Hermetic isolation: the suite must never read a developer's local `.env`. `Settings` is configured
# with `env_file=".env"`, so a real `.env` (pgvector selected + live API keys) would otherwise leak
# into every `Settings()` a test builds -- even for fields not passed explicitly -- breaking the
# suite's default/key assumptions (e.g. `vector_store`, or which engines have keys). Disable env-file
# loading for the whole test session; explicit kwargs and `monkeypatch.setenv` still work, only the
# on-disk `.env` is ignored.
_config.Settings.model_config["env_file"] = None


# Enforce foreign-key constraints on every SQLite connection the suite opens. SQLite ships with
# FK enforcement OFF by default, which silently tolerated orphan-child inserts and mis-ordered
# (child-before-parent) flushes that real Postgres rejects -- masking real bugs. `PRAGMA
# foreign_keys=ON` at connect time makes the hermetic SQLite suite faithful to Postgres. It is a
# no-op for non-SQLite backends (e.g. the Postgres cross-check), which enforce FKs natively. The
# pragma must be set outside a transaction, so `connect` (fired once per new DBAPI connection,
# before any transaction begins) is the correct hook.
@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: object, connection_record: object) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
