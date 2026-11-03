"""read_instagram orchestration with fake fetchers (no live network)."""

from datetime import datetime, timezone

import pytest
from playwright.sync_api import Error as PlaywrightError

from scrapers.social import instagram_web as igw
from scrapers.social.instagram_web import InstagramThrottle, PublicPage, SessionResult
from test_social_instagram_web_parse import public_html, wpi

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def th():
    c = FakeClock()
    return InstagramThrottle(clock=c, sleep=c.sleep)


class Public:
    def __init__(self, page=None, exc=None):
        self.page = page or PublicPage(200, "https://www.instagram.com/mmga.agency/", public_html())
        self.exc = exc
        self.calls = []

    def __call__(self, handle):
        self.calls.append(handle)
        if self.exc:
            raise self.exc
        return self.page


class Session:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def __call__(self, handle, gate):
        self.calls.append(handle)
        if self.exc:
            raise self.exc
        if self.result.kind == "response":
            note = gate()
            if note:
                return SessionResult("blocked", note=note)
        return self.result


def _read(handle="mmga.agency", **kw):
    kw.setdefault("now", NOW)
    return igw.read_instagram(handle, **kw)


def test_invalid_handle_is_unanalyzed_without_any_load(th):
    pub = Public()
    p = _read("../x/y", public_fetch=pub, session_fetch=None, throttle=th)
    assert p.analyzed is False and p.note == igw.NOTE_INVALID
    assert pub.calls == []


def test_public_reader_when_no_session(th):
    pub = Public()
    p = _read("@MMGA.agency", public_fetch=pub, session_fetch=None, throttle=th)
    assert pub.calls == ["mmga.agency"]
    assert p.analyzed is True
    assert (p.followers, p.following, p.posts_count) == (559, 42, 86)
    assert "Limited view" in p.note
    assert "re-login" not in p.note


def test_session_reader_used_when_session_works(th):
    pub = Public()
    ses = Session(SessionResult("response", status=200, payload=wpi()))
    p = _read(public_fetch=pub, session_fetch=ses, throttle=th)
    assert ses.calls == ["mmga.agency"]
    assert pub.calls == []
    assert p.activity_known is True
    assert p.avg_engagement_rate > 0
    assert p.note == ""


def test_dead_session_falls_back_to_public_with_relogin_note(th):
    pub = Public()
    ses = Session(SessionResult("response", status=401, payload={"message": "login_required"}))
    p = _read(public_fetch=pub, session_fetch=ses, throttle=th)
    assert pub.calls == ["mmga.agency"]
    assert p.analyzed is True and p.followers == 559
    assert igw.NOTE_RELOGIN in p.note
    assert "Limited view" in p.note
    assert th.session_usable() is False
    # next read skips the dead session but still says it needs re-login
    p2 = _read(public_fetch=pub, session_fetch=ses, throttle=th)
    assert ses.calls == ["mmga.agency"]
    assert igw.NOTE_RELOGIN in p2.note


def test_checkpointed_session_falls_back_to_public(th):
    pub = Public()
    ses = Session(SessionResult("response", status=400, payload={"message": "checkpoint_required"}))
    p = _read(public_fetch=pub, session_fetch=ses, throttle=th)
    assert p.analyzed is True and igw.NOTE_RELOGIN in p.note


def test_session_dir_without_login_cookie_falls_back_with_note(th):
    pub = Public()
    p = _read(public_fetch=pub, session_fetch=Session(SessionResult("no_session")), throttle=th)
    assert pub.calls == ["mmga.agency"]
    assert igw.NOTE_RELOGIN in p.note


def test_session_browser_error_falls_back_to_public(th):
    pub = Public()
    p = _read(public_fetch=pub, session_fetch=Session(exc=PlaywrightError("profile dir locked")), throttle=th)
    assert pub.calls == ["mmga.agency"]
    assert p.analyzed is True
    assert "spare Instagram session" in p.note


def test_session_other_http_error_falls_back_to_public(th):
    pub = Public()
    p = _read(public_fetch=pub, session_fetch=Session(SessionResult("response", status=500, payload=None)),
              throttle=th)
    assert pub.calls == ["mmga.agency"]
    assert "spare Instagram session" in p.note


def test_session_rate_limit_falls_back_to_public_without_a_global_cooldown(th):
    pub = Public()
    ses = Session(SessionResult("response", status=429, payload=None))
    p = _read(public_fetch=pub, session_fetch=ses, throttle=th)
    # the public page still answers, with a note that the session was rate limited
    assert p.analyzed is True and "rate limited the spare session" in p.note
    assert pub.calls == ["mmga.agency"]
    # the session is parked, but the public page keeps working on the next read
    p2 = _read(public_fetch=pub, session_fetch=ses, throttle=th)
    assert p2.analyzed is True
    assert ses.calls == ["mmga.agency"] and len(pub.calls) == 2


def test_session_not_found_is_final(th):
    pub = Public()
    p = _read("ghost", public_fetch=pub,
              session_fetch=Session(SessionResult("response", status=200, payload={"data": {"user": None}})),
              throttle=th)
    assert p.analyzed is False and p.note == igw.NOTE_NOT_FOUND
    assert pub.calls == []


def test_public_429_trips_cooldown(th):
    pub = Public(PublicPage(429, "https://www.instagram.com/acme/", ""))
    p = _read("acme", public_fetch=pub, session_fetch=None, throttle=th)
    assert p.analyzed is False and "slow down" in p.note
    assert th.blocked_note() is not None


def test_public_login_wall_trips_cooldown(th):
    pub = Public(PublicPage(200, "https://www.instagram.com/accounts/login/?next=/acme/", "<html></html>"))
    p = _read("acme", public_fetch=pub, session_fetch=None, throttle=th)
    assert p.analyzed is False and "login wall" in p.note
    assert th.blocked_note() is not None


def test_public_private_and_not_found_do_not_trip(th):
    p = _read(public_fetch=Public(PublicPage(200, "u", public_html(is_private=True))),
              session_fetch=None, throttle=th)
    assert p.analyzed is False and "private" in p.note
    nf = PublicPage(404, "u", "<html><title>Page not found</title>Sorry, this page isn't available.</html>")
    p = _read("ghost", public_fetch=Public(nf), session_fetch=None, throttle=th)
    assert p.analyzed is False and p.note == igw.NOTE_NOT_FOUND
    assert th.blocked_note() is None


def test_public_browser_error_is_a_note_not_a_crash(th):
    p = _read(public_fetch=Public(exc=PlaywrightError("Timeout 30000ms exceeded")), session_fetch=None, throttle=th)
    assert p.analyzed is False
    assert "did not load" in p.note


def test_hourly_cap_returns_note_without_loading():
    c = FakeClock()
    th = InstagramThrottle(clock=c, sleep=c.sleep, max_per_hour=1)
    pub = Public()
    _read(public_fetch=pub, session_fetch=None, throttle=th)
    p = _read(public_fetch=pub, session_fetch=None, throttle=th)
    assert p.analyzed is False and "per hour" in p.note
    assert pub.calls == ["mmga.agency"]


def test_session_then_public_both_count_as_loads_and_are_spaced():
    c = FakeClock()
    th = InstagramThrottle(clock=c, sleep=c.sleep)
    start = c.t
    pub = Public()
    ses = Session(SessionResult("response", status=401, payload=None))
    _read(public_fetch=pub, session_fetch=ses, throttle=th)
    assert c.t - start >= 20  # the fallback load waited out the 20 s spacing


def test_unexpected_bug_propagates_so_the_adapter_can_fall_back(th):
    with pytest.raises(ZeroDivisionError):
        _read(public_fetch=Public(exc=ZeroDivisionError()), session_fetch=None, throttle=th)


def test_default_session_fetch_absent_when_no_session_dir(tmp_path):
    assert igw._default_session_fetch(str(tmp_path / "missing")) is None
    empty = tmp_path / "empty"
    empty.mkdir()
    assert igw._default_session_fetch(str(empty)) is None
    full = tmp_path / "full"
    full.mkdir()
    (full / "Default").mkdir()
    assert igw._default_session_fetch(str(full)) is not None


def test_session_dir_resolves_relative_to_project(monkeypatch):
    import os
    monkeypatch.setattr(igw.config, "IG_SESSION_DIR", "data/ig_session", raising=False)
    assert os.path.normpath(igw.session_dir()) == os.path.normpath(os.path.join(igw.BASE_DIR, "data", "ig_session"))
    assert os.path.isfile(os.path.join(igw.BASE_DIR, "app.py"))
    monkeypatch.setattr(igw.config, "IG_SESSION_DIR", os.path.abspath("/somewhere/else"), raising=False)
    assert igw.session_dir() == os.path.abspath("/somewhere/else")
