"""Effort allocation service: persistence + slot allocation for the seeding-effort bandit
(m4-design §3.2, TRD §6.3/§8).

`orchestration/bandit.py` (T07) holds the pure arm math and ranking policies (`Arm`,
`BanditPolicy`, `UCB1Policy`, `ThompsonPolicy`) -- no persistence, no session, no I/O. This
module is the caller: `record_reward` upserts one arm's accumulated pull/reward stats into the
`bandit_arm_effort` table (`db.EffortBanditArm`), `load_arms` reads a tenant/brand's persisted
arms back out as pydantic `Arm`s, and `allocate_effort` turns a `BanditPolicy.rank(...)` ordering
into a concrete slot allocation.

`db.EffortBanditArm`/`bandit_arm_effort` is a **distinct** table from M3's `db.BanditArm`
(`bandit_arm`) -- the Thompson content-variant bandit over (content_variant, channel)
Beta-posterior (alpha/beta) rewards. This module's arms are the UCB1/Thompson seeding-effort
bandit, keyed by a single `arm_key` (`f"{channel}:{variant}"`) with raw pull/reward-sum/
reward-sq-sum statistics (see `db.EffortBanditArm`'s docstring). Never read or write
`db.BanditArm` from here.

Reward is fed in by the caller/scheduler from the measurement/attribution signal, which arrives
14-21 days after a placement (PRD §10) -- this module has no opinion on where a reward number
comes from, it only persists it and ranks by it.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from gw_geo.common import db
from gw_geo.orchestration.bandit import Arm, BanditPolicy


def _to_arm(row: db.EffortBanditArm) -> Arm:
    """Convert a persisted `bandit_arm_effort` row into its `Arm` read model."""
    return Arm(
        key=row.arm_key,
        pulls=row.pulls,
        reward_sum=row.reward_sum,
        reward_sq_sum=row.reward_sq_sum,
    )


def _get_row(
    session: Session, *, tenant_id: str, brand_id: str, arm_key: str
) -> db.EffortBanditArm | None:
    return (
        session.query(db.EffortBanditArm)
        .filter(
            db.EffortBanditArm.tenant_id == tenant_id,
            db.EffortBanditArm.brand_id == brand_id,
            db.EffortBanditArm.arm_key == arm_key,
        )
        .first()
    )


def record_reward(
    session: Session, *, tenant_id: str, brand_id: str, arm_key: str, reward: float
) -> Arm:
    """Upsert one observed `reward` onto `(tenant_id, brand_id, arm_key)`'s `bandit_arm_effort`
    row: `pulls += 1`, `reward_sum += reward`, `reward_sq_sum += reward ** 2`. Creates the row
    (all stats starting at zero, then incremented) the first time an arm is pulled. Commits
    before returning so the fresh totals are durable for a subsequent read -- each call is one
    complete, independent reward observation (rewards arrive on their own schedule, delayed
    14-21 days per PRD §10), not a batch a caller stages and commits itself.

    Returns the updated arm as a pydantic `Arm` (T07), not the ORM row.
    """
    row = _get_row(session, tenant_id=tenant_id, brand_id=brand_id, arm_key=arm_key)
    if row is None:
        row = db.EffortBanditArm(
            id=uuid4().hex,
            tenant_id=tenant_id,
            brand_id=brand_id,
            arm_key=arm_key,
            pulls=0,
            reward_sum=0.0,
            reward_sq_sum=0.0,
        )
        session.add(row)

    row.pulls += 1
    row.reward_sum += reward
    row.reward_sq_sum += reward**2
    session.commit()
    return _to_arm(row)


def load_arms(session: Session, *, tenant_id: str, brand_id: str) -> list[Arm]:
    """Load every persisted `bandit_arm_effort` arm for `(tenant_id, brand_id)` as `Arm`s."""
    rows = (
        session.query(db.EffortBanditArm)
        .filter(
            db.EffortBanditArm.tenant_id == tenant_id,
            db.EffortBanditArm.brand_id == brand_id,
        )
        .all()
    )
    return [_to_arm(row) for row in rows]


def _apportion_by_rank(ranked_keys: list[str], amount: int) -> dict[str, int]:
    """Largest-remainder apportionment of `amount` slots across `ranked_keys` (best-first),
    weighted `n - i` for rank `i` (rank 0 gets weight `n`, the last rank gets weight `1`).

    The strictly-decreasing weight on better-ranked arms is what makes the distribution
    "top-weighted" (m4-design §3.2); the largest-remainder step (Hamilton's apportionment
    method) guarantees the per-arm shares sum to exactly `amount` regardless of rounding, with
    ties in the fractional remainder broken in rank order so the better-ranked arm wins the
    extra slot.
    """
    n = len(ranked_keys)
    if amount <= 0 or n == 0:
        return {}

    weights = [n - i for i in range(n)]
    total_weight = sum(weights)
    raw_shares = [amount * weight / total_weight for weight in weights]
    shares = [int(raw) for raw in raw_shares]
    remainder = amount - sum(shares)

    by_fraction_desc = sorted(range(n), key=lambda i: raw_shares[i] - shares[i], reverse=True)
    for i in by_fraction_desc[:remainder]:
        shares[i] += 1

    return {ranked_keys[i]: shares[i] for i in range(n)}


def _distribute(ranked_keys: list[str], *, budget: int, floor: int) -> dict[str, int]:
    """Distribute `budget` slots across `ranked_keys` (best-first), top-weighted, guaranteeing
    each ranked arm at least `floor` slots when the budget allows (m4-design §3.2).

    Phase 1 walks `ranked_keys` in order handing out `floor` slots per arm until the budget is
    exhausted -- so when the budget can't cover every arm at the floor, only the top-ranked ones
    are funded, and the arm at the cutoff may receive a partial (sub-floor) share of whatever's
    left. Phase 2 only runs once every arm already has its floor: any surplus is apportioned on
    top via `_apportion_by_rank`, so the exact `budget` is always preserved to the slot.

    Only arms that end up with at least one slot are included in the returned mapping.
    """
    counts = dict.fromkeys(ranked_keys, 0)
    n = len(ranked_keys)

    if floor > 0:
        fully_funded = min(n, budget // floor)
        for key in ranked_keys[:fully_funded]:
            counts[key] = floor
        surplus = budget - fully_funded * floor
        if fully_funded < n:
            # Budget can't floor every arm -- whatever's left (less than one floor's worth)
            # goes to the next-best-ranked, not-yet-funded arm; nothing left over to distribute.
            if surplus > 0:
                counts[ranked_keys[fully_funded]] += surplus
            return {key: count for key, count in counts.items() if count > 0}
    else:
        surplus = budget

    for key, bonus in _apportion_by_rank(ranked_keys, surplus).items():
        counts[key] += bonus
    return {key: count for key, count in counts.items() if count > 0}


def allocate_effort(
    session: Session,
    *,
    tenant_id: str,
    brand_id: str,
    budget: int,
    policy: BanditPolicy,
    candidate_arms: list[str] | None = None,
    explore_floor: int = 1,
) -> dict[str, int]:
    """Distribute a `budget` of placement slots across arms via `policy`, returning
    `arm_key -> slot count` summing exactly to `budget` (m4-design §3.2).

    Loads this tenant/brand's persisted arms (`load_arms`), then materializes a zero-stat `Arm`
    (`pulls=0`) in memory -- not persisted -- for any `candidate_arms` not yet seen, so a brand
    new channel is ranked (and, per `_distribute`'s exploration floor, funded) alongside
    already-pulled arms rather than being silently excluded. The combined arm set is ranked via
    `policy.rank`, then `_distribute` turns that ranking into slot counts.

    Raises `ValueError` for a negative `budget`/`explore_floor`, or for a positive `budget` with
    no arms (persisted or candidate) to allocate it across -- there is no way to satisfy the
    "sums to exactly `budget`" contract with nothing to allocate to.
    """
    if budget < 0:
        raise ValueError(f"budget must be >= 0, got {budget!r}")
    if explore_floor < 0:
        raise ValueError(f"explore_floor must be >= 0, got {explore_floor!r}")

    persisted = load_arms(session, tenant_id=tenant_id, brand_id=brand_id)
    arms_by_key: dict[str, Arm] = {arm.key: arm for arm in persisted}
    for arm_key in candidate_arms or []:
        arms_by_key.setdefault(arm_key, Arm(key=arm_key))

    if not arms_by_key:
        if budget == 0:
            return {}
        raise ValueError("no arms available to allocate effort across")
    if budget == 0:
        return {}

    ranked_keys = policy.rank(list(arms_by_key.values()))
    return _distribute(ranked_keys, budget=budget, floor=explore_floor)
