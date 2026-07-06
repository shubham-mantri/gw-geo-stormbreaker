"""m5: llm_model_config table -- DB-stored, operator-selectable content-chat model per gateway.

Backs the M5 model-selection feature: the content-chat model (previously a hardcoded constant --
`content.gateway.DEFAULT_CHAT_MODEL` on the Portkey path, `settings.claude_cli_model` on the local
path) becomes a DB row per `GEO_LLM_GATEWAY` value, editable from the Settings UI via
`PUT /settings/llm-model`. Mirrors the ORM `LlmModelConfig` (`gw_geo.common.db`): `gateway` PK,
`chat_model` NOT NULL, no tenant scope (system-level operator config). The *gateway* stays env-driven
-- only the *model* moves to the DB.

`upgrade` **creates and seeds** the three gateway defaults so a fresh `alembic upgrade head` matches
the code's own fallback constants exactly (`resolve_chat_model` falls back to those same values when
a row is missing) and the migration round-trips cleanly; `downgrade` drops the table.

Portable across Postgres and SQLite with no `batch_alter_table`: `create_table` / `bulk_insert` /
`drop_table` all emit backend-native DDL/DML on both (batch mode is only needed to ALTER an existing
table under SQLite -- see `0007`), so the full 0001->0008 chain stays runnable on SQLite too.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# The three gateway defaults -- must match `content.gateway`'s fallback constants
# (`settings.claude_cli_model` default for local_claude; `DEFAULT_CHAT_MODEL` for portkey; the
# guardrail/direct default for direct) so a migrated DB and an un-migrated (fallback) DB behave
# identically before any operator override.
_SEED = (
    {"gateway": "local_claude", "chat_model": "sonnet"},
    {"gateway": "portkey", "chat_model": "claude-haiku-4-5-20251001"},
    {"gateway": "direct", "chat_model": "claude-opus-4-8"},
)


def upgrade() -> None:
    table = op.create_table(
        "llm_model_config",
        sa.Column("gateway", sa.String(), nullable=False),
        sa.Column("chat_model", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("gateway"),
    )
    # Seed within the migration so a fresh `upgrade head` matches the code's fallback constants and
    # the create/seed/drop round-trips. `op.create_table` returns the table for exactly this.
    op.bulk_insert(table, list(_SEED))


def downgrade() -> None:
    op.drop_table("llm_model_config")
