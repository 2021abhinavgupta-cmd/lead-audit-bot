"""InstagramThrottle: 20 s between loads, 20 per rolling hour, 60 min cooldown."""

from scrapers.social.instagram_web import InstagramThrottle


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def _throttle(clock, **kw):
    return InstagramThrottle(clock=clock, sleep=clock.sleep, **kw)


def test_first_load_does_not_wait():
    c = FakeClock()
    th = _throttle(c)
    assert th.acquire() is None
    assert c.slept == []


def test_second_load_waits_out_the_minimum_interval():
    c = FakeClock()
    th = _throttle(c)
    th.acquire()
    c.t += 5
    assert th.acquire() is None
    assert c.slept == [15.0]


def test_no_wait_when_interval_already_passed():
    c = FakeClock()
    th = _throttle(c)
    th.acquire()
    c.t += 25
    assert th.acquire() is None
    assert c.slept == []


def test_hourly_cap_refuses_with_a_note_and_never_sleeps_an_hour():
    c = FakeClock()
    th = _throttle(c, max_per_hour=3)
    for _ in range(3):
        assert th.acquire() is None
    slept_before = list(c.slept)
    note = th.acquire()
    assert note and "per hour" in note
    assert c.slept == slept_before
    # an hour after the first read, one slot frees up
    c.t += 3600
    assert th.acquire() is None


def test_cooldown_blocks_until_it_expires():
    c = FakeClock()
    th = _throttle(c)
    th.trip()
    note = th.blocked_note()
    assert note and "60" in note
    assert th.acquire() == note
    c.t += 3599
    assert th.acquire() is not None
    c.t += 2
    assert th.blocked_note() is None
    assert th.acquire() is None


def test_blocked_note_does_not_consume_a_slot():
    c = FakeClock()
    th = _throttle(c, max_per_hour=1)
    assert th.blocked_note() is None
    assert th.blocked_note() is None
    assert th.acquire() is None
    assert th.blocked_note() is not None


def test_session_dead_window():
    c = FakeClock()
    th = _throttle(c)
    assert th.session_usable() is True
    th.mark_session_dead()
    assert th.session_usable() is False
    c.t += 3601
    assert th.session_usable() is True


def test_defaults_match_the_brief():
    th = InstagramThrottle()
    assert th.min_interval == 20
    assert th.max_per_hour == 20
    assert th.cooldown == 3600
