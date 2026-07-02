"""Alembic environment for gw-geo-stormbreaker.

Wires `gw_geo.common.db.Base.metadata` (TRD §4 tables) as the autogenerate target, and resolves
the DB URL from the app's typed settings (`gw_geo.common.config.Settings.database_url`, i.e. the
`GEO_DATABASE_URL` env var) rather than duplicating connection config in `alembic.ini`.

This repo has no editable install of `gw_geo` (parallel-worktree M0 setup), so we defensively
insert `src/` onto `sys.path` here -- this makes `alembic` invocations robust even if the caller
forgot to set `PYTHONPATH=src` first.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from gw_geo.common.config import get_settings  # noqa: E402
from gw_geo.common.db import Base  # noqa: E402

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# autogenerate target: `Base.metadata` is the single source of truth for the TRD §4 schema.
target_metadata = Base.metadata

# Resolve the DB URL. Priority: `-x db_url=...` (handy for pointing autogenerate at a scratch
# DB) > the app's typed settings (`GEO_DATABASE_URL`) > whatever is already in alembic.ini.
_x_args = context.get_x_argument(as_dictionary=True)
_db_url = _x_args.get("db_url") or get_settings().database_url
config.set_main_option("sqlalchemy.url", _db_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
