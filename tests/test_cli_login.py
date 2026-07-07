"""Tests for the `login` CLI subcommand (M5 local browser capture, one-time sign-in helper).

Hermetic: the real browser-driving opener (`gw_geo.cli.run_login_session`) is patched, so this
exercises only argument parsing, the friendly surface -> start-URL mapping, and how the profile
dir/channel are resolved from `--profile` / settings. No browser is ever launched here (`login`
is the one code path that legitimately drives a real browser, kept out of the default test path).
"""

from unittest.mock import AsyncMock, patch

import pytest

from gw_geo import cli
from gw_geo.common.config import Settings


def test_login_parses_and_invokes_opener() -> None:
    with (
        patch("gw_geo.cli.get_settings", return_value=Settings()),
        patch("gw_geo.cli.run_login_session", new=AsyncMock()) as opener,
    ):
        rc = cli.main(["login", "--surface", "chatgpt", "--profile", "/tmp/p"])
    assert rc == 0
    opener.assert_awaited_once()
    kwargs = opener.await_args.kwargs
    assert kwargs["user_data_dir"] == "/tmp/p"
    assert kwargs["start_url"] == "https://chatgpt.com/"
    assert kwargs["channel"] == "chrome"  # from default Settings.local_browser_channel


@pytest.mark.parametrize(
    "surface,expected_url",
    [
        ("chatgpt", "https://chatgpt.com/"),
        ("grok", "https://grok.com/"),
        ("google", "https://www.google.com/"),
    ],
)
def test_login_maps_friendly_surface_to_start_url(surface: str, expected_url: str) -> None:
    with (
        patch("gw_geo.cli.get_settings", return_value=Settings()),
        patch("gw_geo.cli.run_login_session", new=AsyncMock()) as opener,
    ):
        rc = cli.main(["login", "--surface", surface, "--profile", "/tmp/p"])
    assert rc == 0
    assert opener.await_args.kwargs["start_url"] == expected_url


def test_login_profile_and_channel_default_to_settings() -> None:
    settings = Settings(
        local_browser_profile_dir="/home/dev/.gw-geo-profile", local_browser_channel="msedge"
    )
    with (
        patch("gw_geo.cli.get_settings", return_value=settings),
        patch("gw_geo.cli.run_login_session", new=AsyncMock()) as opener,
    ):
        rc = cli.main(["login", "--surface", "chatgpt"])
    assert rc == 0
    kwargs = opener.await_args.kwargs
    assert kwargs["user_data_dir"] == "/home/dev/.gw-geo-profile"
    assert kwargs["channel"] == "msedge"


def test_login_empty_channel_setting_becomes_none() -> None:
    """An empty channel setting means "bundled Chromium" -> pass channel=None to Playwright."""
    settings = Settings(local_browser_channel="")
    with (
        patch("gw_geo.cli.get_settings", return_value=settings),
        patch("gw_geo.cli.run_login_session", new=AsyncMock()) as opener,
    ):
        rc = cli.main(["login", "--surface", "chatgpt", "--profile", "/tmp/p"])
    assert rc == 0
    assert opener.await_args.kwargs["channel"] is None


def test_login_rejects_unknown_surface() -> None:
    with pytest.raises(SystemExit):  # argparse `choices` rejects it before any opener runs
        cli.main(["login", "--surface", "bing", "--profile", "/tmp/p"])
