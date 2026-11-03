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
