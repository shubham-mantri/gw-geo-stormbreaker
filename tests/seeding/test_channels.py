"""Tests for the seeding channel catalog + compliance-rule seed (m4-design.md S2.2,
docs/tasks/M4-T04-channel-catalog.md).

`docs/tasks/M4-T04-channel-catalog.md` step 1 mandates these three tests: the shape of the
in-code `ChannelCatalog.default()` (8 channels, correct source-types/disclosure/UGC flags), a
seed-then-load round trip through a real (SQLite) session covering both `seeding_channel` and
`compliance_rule`, and idempotency of `seed_channels` under repeated invocation.
"""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from gw_geo.common.db import Base, ComplianceRule, SeedingChannel
from gw_geo.common.models import SourceType
from gw_geo.seeding.channels import (
    ChannelCatalog,
    load_catalog,
    seed_channels,
    seed_compliance_rules,
)


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_default_catalog_shape() -> None:
    cat = ChannelCatalog.default()
    assert cat.get("wikipedia").source_type == SourceType.WIKIPEDIA
    assert cat.get("wikipedia").requires_disclosure is True
    assert cat.get("g2").allows_ugc is True
    assert len(cat.active()) == 8


def test_seed_and_load_roundtrip() -> None:
    session = _session()
    n_ch = seed_channels(session)
    n_rules = seed_compliance_rules(session)
    session.commit()
    assert n_ch == 8
    assert n_rules > 0
    reddit_row = session.scalar(select(SeedingChannel).where(SeedingChannel.name == "reddit"))
    assert reddit_row is not None
    astroturf_rule = session.scalar(
        select(ComplianceRule).where(ComplianceRule.code == "no_astroturf")
    )
    assert astroturf_rule is not None
    cat = load_catalog(session)
    assert cat.get("pr_wire").requires_disclosure is False


def test_seed_is_idempotent() -> None:
    session = _session()
    seed_channels(session)
    seed_channels(session)
    session.commit()
    assert session.query(SeedingChannel).count() == 8
