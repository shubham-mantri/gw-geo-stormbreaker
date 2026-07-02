from gw_geo.content.publish import base
from gw_geo.content.publish import wordpress, webflow, framer, headless, hosted


def test_all_connectors_are_protocol_conformant():
    for mod, cls, kw in [
        (wordpress, wordpress.WordPressConnector, dict(base_url="https://b", token="t")),
        (webflow, webflow.WebflowConnector, dict(token="t", site_id="s")),
        (framer, framer.FramerConnector, dict(token="t")),
        (headless, headless.HeadlessConnector, dict(publish_url="https://h")),
        (hosted, hosted.HostedSubdomainConnector, dict(subdomain_base="kb.example.com")),
    ]:
        conn = cls(**kw)
        assert isinstance(conn, base.PublishConnector) and isinstance(conn.name, str)
