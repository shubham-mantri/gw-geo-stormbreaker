"""Static seeding-channel catalog + compliance-rule seed (`docs/m4-design.md` S2.2/S2.7).

`ChannelCatalog.default()` is the versioned, in-code source of truth for the 8 off-site channels
gw-geo seeds content into (design table S2.2): each channel maps to a `SourceType` (the same
enum `measurement/parse.py` classifies citations into), a ToS ruleset reference, and the
disclosure/UGC placement metadata that the compliance engine (`compliance.py`, T03), per-channel
briefs (`briefs.py`, T06), and the seeding workflow (`workflow.py`, T10) all key off of by
channel `name`.

`seed_channels`/`seed_compliance_rules` persist that in-code catalog -- plus
`ComplianceEngine.default_ruleset()` (T03) -- into the `seeding_channel`/`compliance_rule`
system-level reference tables (T02). The two tables differ in whether their persisted state is
read back: for **channels**, `load_catalog` reads `seeding_channel`, so an ops-side `active` flip
there *is* honored at runtime without a code change. For **compliance rules**, the seeded
`compliance_rule` table is currently only a mirror for future ops-tooling -- nothing reads it back
to build the gate, so the authoritative runtime ruleset is the code-defined
`ComplianceEngine.default_ruleset()` and editing a rule row does not (yet) change gate behavior
(see `compliance.py`'s module docstring). Both seeders are **idempotent upserts** keyed on each
table's natural key -- `name` for channels, `(channel, code)` for rules -- so re-running a
seed/migration step never duplicates rows; neither function commits, mirroring the rest of this
codebase's upsert helpers (e.g. `attribution/linkage.py::_upsert_link`), so the caller controls
the transaction boundary.

`load_catalog` is the read-side counterpart: it reconstructs a `ChannelCatalog` from the
*persisted*, active `seeding_channel` rows, which is what production callers (discovery, briefs,
workflow) load from rather than importing `default()` directly -- so an ops-side deactivation of
a channel (flipping `active` to `False` in the database) is honored without a code change.
"""

from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from gw_geo.common.db import ComplianceRule as ComplianceRuleRow
from gw_geo.common.db import SeedingChannel
from gw_geo.common.models import SourceType
from gw_geo.seeding.compliance import ComplianceEngine


class Channel(BaseModel):
    """One supported off-site seeding channel (design table S2.2)."""

    name: str
    source_type: SourceType
    tos_ruleset_ref: str
    requires_disclosure: bool
    allows_ugc: bool
    active: bool = True


# The 8 channels of m4-design.md S2.2, in the order documented there. `tos_ruleset_ref` is a
# versioned per-channel ToS ruleset id (bumped whenever a platform's terms materially change) --
# every channel starts at "_v1"; `requires_disclosure` is True for every channel except
# `pr_wire` (wire distribution of factual, non-misleading content has no affiliation to disclose).
_DEFAULT_CHANNELS: tuple[Channel, ...] = (
    Channel(
        name="reddit",
        source_type=SourceType.REDDIT,
        tos_ruleset_ref="reddit_tos_v1",
        requires_disclosure=True,
        allows_ugc=True,
    ),
    Channel(
        name="quora",
        source_type=SourceType.FORUM_QA,
        tos_ruleset_ref="quora_tos_v1",
        requires_disclosure=True,
        allows_ugc=True,
    ),
    Channel(
        name="g2",
        source_type=SourceType.REVIEW_SITE,
        tos_ruleset_ref="g2_tos_v1",
        requires_disclosure=True,
        allows_ugc=True,
    ),
    Channel(
        name="capterra",
        source_type=SourceType.REVIEW_SITE,
        tos_ruleset_ref="capterra_tos_v1",
        requires_disclosure=True,
        allows_ugc=True,
    ),
    Channel(
        name="listicle",
        source_type=SourceType.LISTICLE,
        tos_ruleset_ref="listicle_tos_v1",
        requires_disclosure=True,
        allows_ugc=False,
    ),
    Channel(
        name="wikipedia",
        source_type=SourceType.WIKIPEDIA,
        tos_ruleset_ref="wikipedia_tos_v1",
        requires_disclosure=True,
        allows_ugc=True,
    ),
    Channel(
        name="pr_wire",
        source_type=SourceType.NEWS_PR,
        tos_ruleset_ref="pr_wire_tos_v1",
        requires_disclosure=False,
        allows_ugc=False,
    ),
    Channel(
        name="expert_byline",
        source_type=SourceType.NEWS_PR,
        tos_ruleset_ref="expert_byline_tos_v1",
        requires_disclosure=True,
        allows_ugc=False,
    ),
)


class ChannelCatalog:
    """An in-memory, name-keyed view over a set of `Channel`s."""

    def __init__(self, channels: list[Channel]) -> None:
        self._by_name: dict[str, Channel] = {channel.name: channel for channel in channels}

    def get(self, name: str) -> Channel:
        """Return the channel named `name`.

        Raises:
            KeyError: `name` is not in this catalog.
        """
        try:
            return self._by_name[name]
        except KeyError:
            raise KeyError(f"unknown seeding channel: {name!r}") from None

    def active(self) -> list[Channel]:
        """Every channel in this catalog with `active=True`, in catalog order."""
        return [channel for channel in self._by_name.values() if channel.active]

    @staticmethod
    def default() -> ChannelCatalog:
        """The 8 documented channels of m4-design.md S2.2."""
        return ChannelCatalog(list(_DEFAULT_CHANNELS))


def seed_channels(session: Session) -> int:
    """Idempotently upsert `ChannelCatalog.default()` into `seeding_channel`, keyed on `name`.

    An existing row (matched by `name`) has its mutable fields refreshed in place rather than
    being duplicated, so re-running this (e.g. on every deploy/migration) is safe. Does not
    commit -- the caller controls the transaction boundary.

    Returns:
        The number of channels in the default catalog (8), regardless of how many were newly
        inserted vs. already present.
    """
    for channel in _DEFAULT_CHANNELS:
        row = session.scalar(select(SeedingChannel).where(SeedingChannel.name == channel.name))
        if row is None:
            row = SeedingChannel(id=uuid4().hex, name=channel.name)
            session.add(row)
        row.source_type = channel.source_type.value
        row.tos_ruleset_ref = channel.tos_ruleset_ref
        row.requires_disclosure = channel.requires_disclosure
        row.allows_ugc = channel.allows_ugc
        row.active = channel.active
    session.flush()
    return len(_DEFAULT_CHANNELS)


def seed_compliance_rules(session: Session) -> int:
    """Idempotently upsert `ComplianceEngine.default_ruleset()` into `compliance_rule`, keyed on
    the `(channel, code)` pair.

    An existing row (matched by `(channel, code)`) has its mutable fields refreshed in place
    rather than being duplicated, so re-running this is safe. Does not commit -- the caller
    controls the transaction boundary.

    NOTE: this table is a mirror for future ops-tooling; it is not read back to build the
    compliance engine. The authoritative runtime ruleset is `ComplianceEngine.default_ruleset()`
    in code, so seeding or editing this table does not change gate behavior (see `compliance.py`).

    Returns:
        The number of rules in the default ruleset, regardless of how many were newly inserted
        vs. already present.
    """
    rules = ComplianceEngine.default_ruleset()
    for rule in rules:
        row = session.scalar(
            select(ComplianceRuleRow).where(
                ComplianceRuleRow.channel == rule.channel,
                ComplianceRuleRow.code == rule.code,
            )
        )
        if row is None:
            row = ComplianceRuleRow(id=uuid4().hex, channel=rule.channel, code=rule.code)
            session.add(row)
        row.description = rule.description
        row.severity = rule.severity
        row.check_key = rule.check
        row.active = True
    session.flush()
    return len(rules)


def load_catalog(session: Session) -> ChannelCatalog:
    """Reconstruct a `ChannelCatalog` from the active rows of `seeding_channel`.

    Production callers (discovery, briefs, the seeding workflow) load the catalog this way
    rather than importing `ChannelCatalog.default()` directly, so an ops-side deactivation of a
    channel is honored without a code change.
    """
    rows = session.scalars(
        select(SeedingChannel).where(SeedingChannel.active.is_(True))
    ).all()
    channels = [
        Channel(
            name=row.name,
            source_type=SourceType(row.source_type),
            tos_ruleset_ref=row.tos_ruleset_ref,
            requires_disclosure=row.requires_disclosure,
            allows_ugc=row.allows_ugc,
            active=row.active,
        )
        for row in rows
    ]
    return ChannelCatalog(channels)
