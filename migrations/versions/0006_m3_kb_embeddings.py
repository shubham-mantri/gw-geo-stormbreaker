"""m3: kb_fact_embedding table for pgvector-backed KB grounding.

The M3 knowledge base (`content/kb.py`'s `PgVectorStore`) needs a per-brand fact-embedding table to
store/search grounding facts; M3's `0004` never created it (the store was a stub). This adds it:
`id` (fact id) PK, `brand_id` (indexed — the tenant/brand isolation key: every KB query filters on
it), `embedding` (pgvector `vector`, unspecified dim so it's embedding-model-agnostic), `meta`
(jsonb — the full `Fact`). Raw SQL for the `vector` column since SQLAlchemy has no native type for it
without the `pgvector` python package (which we deliberately avoid — `PgVectorStore` uses raw SQL).

pgvector is Postgres-only, so on any non-Postgres backend (e.g. the SQLite used by
`Base.metadata.create_all` in the hermetic suite, or a scratch-SQLite `alembic upgrade head` check)
this migration is a no-op — keeping the full 0001->0006 chain runnable on SQLite too.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # kb_fact_embedding is pgvector-only; nothing to provision on other backends.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS kb_fact_embedding (
            id        TEXT  PRIMARY KEY,
            brand_id  TEXT  NOT NULL,
            embedding vector NOT NULL,
            meta      JSONB NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_kb_fact_embedding_brand_id "
        "ON kb_fact_embedding (brand_id)"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TABLE IF EXISTS kb_fact_embedding")
