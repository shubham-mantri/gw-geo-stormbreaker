"""`Retrainer` adapter over the M5 ranking refresh (m4-design §3.1, M5 live wiring).

`orchestration.retrain.RetrainTrigger` (T12) turns a breached+flagged `drift_event` into a retrain
job via an injected `Retrainer` protocol -- `retrain(*, engine) -> {"model_ref", "metrics"}`. The
production adaptation handler shipped an `_UnwiredRetrainer` that raised, because the M3/M5 ranking
trainer was not yet adapted to that contract. `RankingRetrainer` is that adapter.

It wraps the real ranking refresh (`orchestration.ranking_gen.run_ranking_refresh_job`), bound to
one `(tenant_id, brand_id)`: `retrain(engine=...)` runs a single-engine refresh, then reports the
freshly persisted `FeatureModel`'s id (as `model_ref`) and its `metrics` (AUC + counts). The trainer
is injected (`retrain_fn`) so tests pass a fake and no live crawl / embedding / scikit-learn call is
ever made here.

**Honest no-op when there is nothing to train (m4-design decision).** Ranking negatives are sourced
cross-engine (a URL some *other* engine cited but this one didn't), so a brand measured on fewer
than two engines yields all-positive, non-discriminative labels -- an AUC-less "model". And a
missing/cross-tenant brand trains nothing at all. In both cases this retrainer does not fake a
success:

* nothing trained (`engine` absent from the refresh's reports -- brand missing / no data): a clear
  no-op is logged and a synthetic `f"{engine}@{ts}"` `model_ref` with empty metrics is returned.
* trained but degenerate (`<2` engines -> no `auc` in metrics): the real, honest artifact id +
  metrics are returned, and the degenerate/no-op nature is logged so an operator can see it.

Either way `retrain` returns a well-formed dict (never raises for "nothing to train"), so
`RetrainTrigger.on_breach` records a `succeeded` job that honestly reflects what was (or wasn't)
trained, rather than a spurious `failed`.

The FeatureModel lookup uses an injected `session_factory` (a fresh session per lookup, always
closed); its default opens one from `settings.database_url` -- the same database
`run_ranking_refresh_job` persists to. `get_settings` / `run_ranking_refresh_job` are imported by
name so tests can patch them on this module.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.config import get_settings
from gw_geo.common.db import FeatureModel
from gw_geo.common.models import RankingReport
from gw_geo.orchestration.ranking_gen import run_ranking_refresh_job

logger = logging.getLogger(__name__)

RetrainFn = Callable[..., dict[str, RankingReport]]
SessionFactory = Callable[[], Session]


class RankingRetrainer:
    """`Retrainer` (see `orchestration.retrain.Retrainer`) backed by the M5 ranking refresh."""

    def __init__(
        self,
        tenant_id: str,
        brand_id: str,
        *,
        retrain_fn: RetrainFn = run_ranking_refresh_job,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._brand_id = brand_id
        self._retrain_fn = retrain_fn
        self._session_factory = session_factory

    def retrain(self, *, engine: str) -> dict[str, Any]:
        """Refresh `engine`'s ranking model for this tenant/brand; return `{"model_ref","metrics"}`.

        Runs a single-engine refresh via `retrain_fn`, then reports the freshly persisted
        `FeatureModel` (id + metrics). Honestly no-ops (with a clear log) when there is nothing
        discriminative to train -- see the module docstring.
        """
        reports = self._retrain_fn(
            tenant_id=self._tenant_id, brand_id=self._brand_id, engines=[engine]
        )
        found = self._latest_model(engine) if engine in reports else None

        if found is None:
            model_ref = f"{engine}@{datetime.now(timezone.utc).isoformat()}"
            logger.warning(
                "ranking retrain no-op: no trainable model for tenant_id=%s brand_id=%s engine=%s "
                "(brand missing or <2 engines measured -> nothing to train); synthesizing "
                "model_ref=%s",
                self._tenant_id,
                self._brand_id,
                engine,
                model_ref,
            )
            return {"model_ref": model_ref, "metrics": {}}

        model_id, metrics = found
        if "auc" not in metrics:
            logger.warning(
                "ranking retrain no-op/degenerate: model %s for tenant_id=%s brand_id=%s "
                "engine=%s trained on single-label data (measure >=2 engines for a discriminative "
                "boundary); reporting it honestly with metrics=%s",
                model_id,
                self._tenant_id,
                self._brand_id,
                engine,
                metrics,
            )
        return {"model_ref": model_id, "metrics": metrics}

    def _latest_model(self, engine: str) -> tuple[str, dict[str, Any]] | None:
        """The freshest persisted `FeatureModel` for this tenant/brand/engine as `(id, metrics)`."""
        factory = self._session_factory if self._session_factory is not None else self._default_session
        with factory() as session:
            model = (
                session.query(FeatureModel)
                .filter_by(tenant_id=self._tenant_id, brand_id=self._brand_id, engine=engine)
                .order_by(FeatureModel.trained_at.desc())
                .first()
            )
            if model is None:
                return None
            return model.id, dict(model.metrics or {})

    @staticmethod
    def _default_session() -> Session:
        return Session(create_engine(get_settings().database_url))


__all__ = ["RankingRetrainer"]
