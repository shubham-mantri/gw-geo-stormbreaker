"""Wire the concrete `PublishConnector`s from `Settings` (PRD §6.4, m3-design §3.5).

`register_default_connectors` is the publish-side counterpart to
`gw_geo.common.wiring.build_runtime`: it registers each connector into the shared
`publish.base` registry, keyed by whether its required config is set, so the T22 content
pipeline can call `publish.base.get_connector(name)` without caring which targets a given tenant
has actually configured. Connector modules stay import-side-effect-free (none call
`publish.base.register()` themselves); this module is the one place that does, at wiring time.
"""

from gw_geo.common.config import Settings
from gw_geo.content.publish import base
from gw_geo.content.publish.base import PublishConnector
from gw_geo.content.publish.framer import FramerConnector
from gw_geo.content.publish.headless import HeadlessConnector
from gw_geo.content.publish.hosted import HostedSubdomainConnector
from gw_geo.content.publish.webflow import WebflowConnector
from gw_geo.content.publish.wordpress import WordPressConnector


def register_default_connectors(settings: Settings) -> list[str]:
    """Register every publish connector whose required config is set on `settings`.

    Registers into the shared connector registry, keyed by config presence:

    * `wordpress` -- when both `wordpress_base_url` and `wordpress_token` are set;
    * `webflow` -- when both `webflow_token` and `webflow_site_id` are set;
    * `framer` -- when `framer_token` is set;
    * `headless` -- when `headless_publish_url` is set;
    * `hosted` -- when `hosted_subdomain_base` is set (defaults to `"kb.example.com"`, so this
      product-owned fallback target is registered out of the box, unlike the third-party CMSes
      above which all require a tenant to supply real credentials first).

    A connector whose required config is unset is silently skipped -- the same graceful-
    degradation posture `build_runtime` uses for a measurement engine with no API key.

    The shared connector registry is process-global, so a warm process calling this twice would
    otherwise hit `base.register`'s duplicate-name `ValueError`. Clearing the registry first makes
    this idempotent: it always rebuilds from the current settings, mirroring `build_runtime`.

    Returns the list of connector names actually registered.
    """
    base.clear_registry()
    registered: list[str] = []

    def _register(connector: PublishConnector) -> None:
        base.register(connector)
        registered.append(connector.name)

    if settings.wordpress_base_url and settings.wordpress_token:
        _register(
            WordPressConnector(
                base_url=settings.wordpress_base_url, token=settings.wordpress_token
            )
        )
    if settings.webflow_token and settings.webflow_site_id:
        _register(
            WebflowConnector(token=settings.webflow_token, site_id=settings.webflow_site_id)
        )
    if settings.framer_token:
        _register(FramerConnector(token=settings.framer_token))
    if settings.headless_publish_url:
        _register(HeadlessConnector(publish_url=settings.headless_publish_url))
    if settings.hosted_subdomain_base:
        _register(HostedSubdomainConnector(subdomain_base=settings.hosted_subdomain_base))

    return registered
