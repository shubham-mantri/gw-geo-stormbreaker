import pytest

from gw_geo.content.publish import base


class FakeConnector:
    name = "fake"

    async def publish(self, draft, *, freshness):
        return base.PublishResult(published_url="https://x/1", external_id="1", connector="fake")


def test_register_get_and_protocol():
    base.clear_registry(); c = FakeConnector(); base.register(c)
    assert base.get_connector("fake") is c
    assert isinstance(c, base.PublishConnector)


def test_duplicate_and_unknown():
    base.clear_registry(); base.register(FakeConnector())
    with pytest.raises(ValueError): base.register(FakeConnector())
    with pytest.raises(KeyError): base.get_connector("nope")
