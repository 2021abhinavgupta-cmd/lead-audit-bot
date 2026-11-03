"""Shared test setup."""
import pytest

import config


@pytest.fixture(autouse=True)
def _no_bcc_copy_from_the_real_env(monkeypatch):
    # BCC_EMAIL comes from the machine's .env (the laptop sets it). Tests that
    # assert on who a send goes to must not depend on that; test_bcc_copy.py
    # sets it explicitly where it matters.
    monkeypatch.setattr(config, "BCC_EMAIL", "", raising=False)


@pytest.fixture(autouse=True)
def _no_live_instagram(monkeypatch, tmp_path):
    # The Instagram web reader opens a real browser on instagram.com by
    # default. No test may do that: point the spare-session dir at an empty
    # temp path (so no saved login is ever used) and make the public load fail
    # loudly as a browser error (read_instagram turns that into a note).
    from playwright.sync_api import Error as PlaywrightError
    from scrapers.social import instagram_web

    def _blocked(handle):
        raise PlaywrightError("live Instagram is disabled in tests")

    monkeypatch.setattr(config, "IG_SESSION_DIR", str(tmp_path / "no_ig_session"), raising=False)
    monkeypatch.setattr(instagram_web, "_playwright_public_fetch", _blocked)
    # fresh, non-sleeping throttle per test (the module one is process-wide)
    monkeypatch.setattr(instagram_web, "_THROTTLE", instagram_web.InstagramThrottle(min_interval=0))


@pytest.fixture(autouse=True)
def _no_live_bio_link_fetch(monkeypatch):
    # The social audit fetches a profile's bio link. No test may touch the
    # network for it: the default getter fails like a dead connection and the
    # "is our network up" probe says no, so check_bio_link returns "unknown"
    # (never "broken") unless a test injects its own fakes. The cache is fresh
    # per test.
    import httpx
    from analyzer import social_link

    def _blocked(url):
        raise httpx.ConnectError("live bio link fetch is disabled in tests")

    monkeypatch.setattr(social_link, "_default_get", _blocked)
    monkeypatch.setattr(social_link, "_default_network_ok", lambda: False)
    monkeypatch.setattr(social_link, "_default_hop_check", lambda url: "ok")
    monkeypatch.setattr(social_link, "_CACHE", {})
    monkeypatch.setattr(social_link, "_RETRY_PAUSE", 0)
