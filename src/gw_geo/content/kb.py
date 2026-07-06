"""Per-brand knowledge base: the anti-hallucination substrate for grounded content generation.

The knowledge base (PRD §6.4) is the per-brand source of truth -- approved facts, USPs, products,
pricing, certifications, and claims -- indexed in a vector store for semantic retrieval.
`KnowledgeBase.ground(query)` returns the `Fact`s a generated claim can be checked against, which
is what makes generation checkable rather than hallucinated; `ground_scored(query)` is the same
lookup paired with each `Fact`'s similarity score, so a claim can be thresholded on how strongly it
is supported (consumed by the T15 claim-verification guardrail).

Both the embedding model (`EmbeddingClient`) and the vector index (`VectorStore`) are injected
`Protocol`s, so the hermetic test suite (`tests/content/test_kb.py`) never makes a live embedding
or vector-DB call -- it exercises `KnowledgeBase` against an in-memory fake of each. Real
`VectorStore` backends (`PineconeVectorStore`, `PgVectorStore`) live at the bottom of this module,
config-selected via `build_vector_store()` per `Settings.vector_store` (TRD OT4); neither is
exercised by tests.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

import httpx

from gw_geo.common.config import Settings
from gw_geo.common.models import Fact
from gw_geo.common.portkey import PortkeyClient


class EmbeddingClient(Protocol):
    """Turns text into a dense embedding vector. Injected so tests never call a live model."""

    def embed(self, text: str) -> list[float]: ...


class VectorStore(Protocol):
    """A similarity-searchable key/vector/metadata index. Injected so tests never hit a live
    vector database.
    """

    def upsert(self, id: str, vector: list[float], meta: dict[str, Any]) -> None: ...

    def query(self, vector: list[float], top_k: int) -> list[tuple[str, float, dict[str, Any]]]:
        """Return up to `top_k` nearest neighbors of `vector` as `(id, score, meta)`, sorted by
        `score` descending (most similar first).
        """
        ...


class KnowledgeBase:
    """The per-brand source of truth for grounded generation (PRD §6.4).

    `add_fact` embeds and indexes one approved `Fact`; `ground` runs semantic search and
    reconstructs the top-k supporting `Fact`s from what was indexed -- no separate database
    round-trip is needed, since the full `Fact` was stored as the vector's metadata at `add_fact`
    time. `ground_scored` is the same lookup but also returns each `Fact`'s similarity score, for
    callers (the T15 claim-verification guardrail) that must threshold on how strongly a `Fact`
    supports a claim rather than just retrieving the top-k matches.
    """

    def __init__(self, *, brand_id: str, store: VectorStore, embedder: EmbeddingClient) -> None:
        self._brand_id = brand_id
        self._store = store
        self._embedder = embedder

    @property
    def brand_id(self) -> str:
        return self._brand_id

    def add_fact(self, fact: Fact) -> None:
        """Embed `fact.text` and upsert it into the store, keyed by `fact.id`, with the full
        `Fact` recorded as metadata so `ground()` can reconstruct it directly.
        """
        if fact.brand_id != self._brand_id:
            raise ValueError(
                f"fact {fact.id!r} has brand_id={fact.brand_id!r}, but this knowledge base is "
                f"scoped to brand_id={self._brand_id!r}"
            )
        vector = self._embedder.embed(fact.text)
        self._store.upsert(fact.id, vector, fact.model_dump())

    def ground(self, query: str, *, top_k: int = 5) -> list[Fact]:
        """Return the `top_k` `Fact`s most semantically relevant to `query`, most relevant first."""
        return [fact for fact, _ in self.ground_scored(query, top_k=top_k)]

    def ground_scored(self, query: str, *, top_k: int = 5) -> list[tuple[Fact, float]]:
        """Like `ground`, but pairs each `Fact` with its similarity score, most relevant first.

        Added for the T15 claim-verification guardrail (back-compatible with T06: `ground`'s
        signature and behavior are unchanged, and are now implemented in terms of this method).
        """
        vector = self._embedder.embed(query)
        matches = self._store.query(vector, top_k)
        return [(Fact(**meta), score) for _, score, meta in matches]


# --------------------------------------------------------------------------------------------
# Real (non-test) `EmbeddingClient` backends: `OpenAIEmbeddingClient` (direct) and
# `PortkeyEmbeddingClient` (via the gateway), config-selected by `content.gateway.build_embedder`
# per `Settings.llm_gateway`. Neither is exercised by the hermetic suite (`tests/content/test_kb.py`
# injects a fake `EmbeddingClient`); `tests/content/test_gateway.py` exercises both against a mocked
# transport (`respx`). Both use `httpx` directly, mirroring `generate.AnthropicLLMClient`.
# --------------------------------------------------------------------------------------------


class OpenAIEmbeddingClient:
    """`EmbeddingClient` that calls OpenAI's embeddings API directly (the `"direct"` gateway path).

    Uses `httpx` directly (no `openai` SDK dependency). Requires an OpenAI API key at call time --
    mirroring `AnthropicLLMClient`, which validates its key inside `complete` rather than `__init__`.
    """

    _API_URL = "https://api.openai.com/v1/embeddings"
    _DEFAULT_MODEL = "text-embedding-3-large"

    def __init__(self, *, api_key: str, model: str | None = None, timeout: float = 60.0) -> None:
        self._api_key = api_key
        self._model = model or self._DEFAULT_MODEL
        self._timeout = timeout

    def embed(self, text: str) -> list[float]:
        if not self._api_key:
            raise RuntimeError(
                "OpenAIEmbeddingClient requires an OpenAI API key "
                "(pass api_key= or set GEO_OPENAI_API_KEY)."
            )
        response = httpx.post(
            self._API_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={"model": self._model, "input": text},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        vector: list[float] = payload["data"][0]["embedding"]
        return vector


class PortkeyEmbeddingClient:
    """`EmbeddingClient` backed by the Portkey gateway's `/embeddings` endpoint (the `"portkey"`
    gateway path). Delegates to an injected `PortkeyClient`; the embedding model is addressed by its
    native slug (default `text-embedding-3-large`), with provider routing held in the Portkey Config.
    """

    _DEFAULT_MODEL = "text-embedding-3-large"

    def __init__(self, client: PortkeyClient, *, model: str | None = None) -> None:
        self._client = client
        self._model = model or self._DEFAULT_MODEL

    def embed(self, text: str) -> list[float]:
        return self._client.embedding(model=self._model, text=text)


# --------------------------------------------------------------------------------------------
# Real (non-test) `VectorStore` backends, config-selected via `Settings.vector_store` (TRD OT4).
# Neither is exercised by the hermetic test suite (`tests/content/test_kb.py` injects a fake
# `VectorStore`); both build their real client/connection lazily, on first use, never at
# `__init__` or module-import time.
# --------------------------------------------------------------------------------------------


class PineconeVectorStore:
    """`VectorStore` backed by Pinecone (TRD OT4's default embeddings store).

    `pinecone` is imported lazily, inside `_index()`, rather than at module import time -- so
    importing `gw_geo.content.kb` (and running the hermetic test suite) never requires the
    `pinecone` package to be installed or reachable. Mirrors `wiring.S3RawArchive`'s lazily
    constructed `boto3` client and `capture.browser.BrowserSession`'s lazy `playwright` import.

    `pinecone-client` is a declared project dependency (see `pyproject.toml`) that is not
    installed in every environment that merely imports this module, hence `import-not-found`
    below rather than `import-untyped` (the code `boto3`/`playwright` use, since those *are*
    always installed here) -- update the ignored code to `import-untyped` if this environment
    ever ships the package (it has no `py.typed` marker, so mypy would still need an ignore).
    """

    def __init__(self, *, api_key: str, index_name: str, namespace: str | None = None) -> None:
        self._api_key = api_key
        self._index_name = index_name
        self._namespace = namespace
        self._index_client: Any | None = None

    def _index(self) -> Any:
        if self._index_client is None:
            import pinecone  # type: ignore[import-not-found]

            self._index_client = pinecone.Pinecone(api_key=self._api_key).Index(self._index_name)
        return self._index_client

    def upsert(self, id: str, vector: list[float], meta: dict[str, Any]) -> None:
        # Pinecone metadata values must be str/int/float/bool/list[str] -- None isn't valid, so
        # unset optional `Fact` fields (e.g. `source`) are dropped rather than sent.
        metadata = {k: v for k, v in meta.items() if v is not None}
        self._index().upsert(vectors=[(id, vector, metadata)], namespace=self._namespace)

    def query(self, vector: list[float], top_k: int) -> list[tuple[str, float, dict[str, Any]]]:
        response = self._index().query(
            vector=vector, top_k=top_k, include_metadata=True, namespace=self._namespace
        )
        return [(m.id, m.score, dict(m.metadata or {})) for m in response.matches]


class PgVectorStore:
    """`VectorStore` backed by Postgres + the `pgvector` extension (TRD OT4's alternative to
    Pinecone).

    Talks to Postgres over the already-required `psycopg` driver via raw SQL, so no separate
    `pgvector` Python package is needed -- vectors are passed as pgvector's text literal format
    (`'[v1,v2,...]'::vector`) and ranked by the `<=>` cosine-distance operator. Requires the
    `kb_fact_embedding` table + `vector` extension provisioned by migration `0006`.

    **Brand-scoped:** one instance is bound to a single `brand_id`; every `query` filters
    `WHERE brand_id = <this brand>` and every `upsert` stamps it, so the shared `kb_fact_embedding`
    table can never leak one brand's facts into another brand's grounding (the tenant/brand
    isolation guarantee). `build_vector_store` constructs one per brand, mirroring Pinecone's
    per-brand `namespace`. Never exercised by the hermetic suite (`tests/content/test_kb.py` injects
    a fake `VectorStore`).
    """

    # `table` is interpolated into SQL via an f-string (below), so it must be a trusted SQL
    # identifier, never attacker-controlled. It has a constant default and no caller ever sets it
    # from user input today; this guard is defense-in-depth so it can't silently become an
    # injection vector if a future caller does pass it. A bare table identifier only:
    # a leading letter/underscore then letters/digits/underscores (no quotes, dots, or whitespace).
    _SAFE_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

    def __init__(
        self, *, database_url: str, brand_id: str, table: str = "kb_fact_embedding"
    ) -> None:
        if not self._SAFE_TABLE_RE.match(table):
            raise ValueError(
                f"PgVectorStore table name {table!r} is not a safe SQL identifier; it must remain a "
                f"trusted constant (matching {self._SAFE_TABLE_RE.pattern!r}), never user input."
            )
        self._database_url = database_url
        self._brand_id = brand_id
        self._table = table

    def _connect(self) -> Any:
        import psycopg

        # `psycopg.connect` wants a libpq URL (`postgresql://...`); strip SQLAlchemy's `+driver`
        # (e.g. `postgresql+psycopg://`), which libpq does not understand.
        libpq_url = re.sub(
            r"^(postgresql|postgres)\+[a-z0-9]+://", r"\1://", self._database_url
        )
        return psycopg.connect(libpq_url)

    @staticmethod
    def _literal(vector: list[float]) -> str:
        return "[" + ",".join(repr(float(v)) for v in vector) + "]"

    def upsert(self, id: str, vector: list[float], meta: dict[str, Any]) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self._table} (id, brand_id, embedding, meta) "
                "VALUES (%s, %s, %s::vector, %s) ON CONFLICT (id) DO UPDATE "
                "SET brand_id = EXCLUDED.brand_id, embedding = EXCLUDED.embedding, "
                "meta = EXCLUDED.meta",
                (id, self._brand_id, self._literal(vector), json.dumps(meta)),
            )
            conn.commit()

    def query(self, vector: list[float], top_k: int) -> list[tuple[str, float, dict[str, Any]]]:
        literal = self._literal(vector)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT id, 1 - (embedding <=> %s::vector) AS score, meta FROM {self._table} "
                "WHERE brand_id = %s ORDER BY embedding <=> %s::vector LIMIT %s",
                (literal, self._brand_id, literal, top_k),
            )
            rows = cur.fetchall()
        return [(row[0], float(row[1]), dict(row[2])) for row in rows]


def build_vector_store(settings: Settings, *, brand_id: str) -> VectorStore:
    """Build the real, **brand-scoped** `VectorStore` selected by `settings.vector_store` (TRD OT4:
    `"pinecone"` | `"pgvector"`). `brand_id` scopes the store to a single brand -- pgvector filters
    every query on it, Pinecone uses it as the `namespace` -- so KB grounding can never cross a
    brand boundary. Not used by the hermetic test suite, which injects a fake `VectorStore` directly
    into `KnowledgeBase`.
    """
    if settings.vector_store == "pgvector":
        return PgVectorStore(database_url=settings.database_url, brand_id=brand_id)
    if settings.vector_store == "pinecone":
        return PineconeVectorStore(
            api_key=settings.pinecone_api_key,
            index_name=settings.pinecone_index,
            namespace=brand_id,
        )
    raise ValueError(f"unknown vector_store setting: {settings.vector_store!r}")
