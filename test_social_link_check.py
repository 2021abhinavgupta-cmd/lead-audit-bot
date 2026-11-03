"""analyzer/social_link.py: is the bio link broken? No network here: the
getter, the per hop safety check, the "is our own network up" probe and the
sleep are all injected."""

import httpx
import pytest

from analyzer import social_link as sl


class FakeGet:
    """Plays back a script of responses: an int status, a (status, location)
    redirect, or an exception instance to raise."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, tuple):
            status, location = step
            return status, {"location": location}, b""
        return step, {}, b"<html>ok</html>"


def _check(url, get, *, net=True, hop=None, cache=None):
    return sl.check_bio_link(
        url, get=get, network_ok=lambda: net, hop_check=hop or (lambda u: "ok"),
        cache={} if cache is None else cache, sleep=lambda s: None,
    )


def test_200_is_ok():
    r = _check("https://acme.in/", FakeGet(200))
    assert r["verdict"] == "ok"
    assert r["status"] == 200
    assert r["final_url"] == "https://acme.in/"


def test_missing_scheme_gets_https():
    g = FakeGet(200)
    _check("acme.in/shop", g)
    assert g.calls == ["https://acme.in/shop"]


def test_non_web_scheme_is_unknown_and_not_fetched():
    g = FakeGet()
    for url in ("javascript:alert(1)", "mailto:a@b.c", "ftp://acme.in/x", ""):
        assert _check(url, g)["verdict"] == "unknown"
    assert g.calls == []


def test_redirects_are_followed_and_each_hop_checked():
    hops = []
    g = FakeGet((301, "https://www.acme.in/"), (302, "/home"), 200)
    r = _check("https://acme.in", g, hop=lambda u: hops.append(u) or "ok")
    assert r["verdict"] == "ok"
    assert r["final_url"] == "https://www.acme.in/home"
    assert hops == ["https://acme.in", "https://www.acme.in/", "https://www.acme.in/home"]


def test_more_than_three_redirects_is_unknown_not_broken():
    g = FakeGet(*[(302, f"https://acme.in/{i}") for i in range(10)])
    assert _check("https://acme.in/", g)["verdict"] == "unknown"


def test_redirect_to_a_private_address_is_unknown_and_not_followed():
    g = FakeGet((302, "http://127.0.0.1/admin"))
    r = _check("https://acme.in/", g, hop=lambda u: "unsafe" if "127.0.0.1" in u else "ok")
    assert r["verdict"] == "unknown"
    assert g.calls == ["https://acme.in/"]


def test_404_twice_is_broken():
    r = _check("https://acme.in/", FakeGet(404, 404))
    assert r["verdict"] == "broken" and r["status"] == 404


def test_404_then_200_is_ok():
    assert _check("https://acme.in/", FakeGet(404, 200))["verdict"] == "ok"


def test_server_error_twice_is_broken():
    assert _check("https://acme.in/", FakeGet(500, 502))["verdict"] == "broken"


@pytest.mark.parametrize("status", [401, 403, 429, 503, 999, 418])
def test_bot_walls_and_odd_statuses_are_unknown(status):
    # a 403 / 429 / 503 is very often a firewall refusing our client, not a
    # dead page for a person on a phone
    assert _check("https://acme.in/", FakeGet(status, status))["verdict"] == "unknown"


def test_unreachable_twice_with_our_network_up_is_broken():
    g = FakeGet(httpx.ConnectError("refused"), httpx.ConnectTimeout("slow"))
    r = _check("https://acme.in/", g, net=True)
    assert r["verdict"] == "broken"
    assert r["status"] is None
    assert len(g.calls) == 2


def test_unreachable_with_our_network_down_is_unknown():
    g = FakeGet(httpx.ConnectError("refused"), httpx.ConnectError("refused"))
    assert _check("https://acme.in/", g, net=False)["verdict"] == "unknown"


def test_unreachable_once_then_ok_is_ok():
    g = FakeGet(httpx.ReadTimeout("slow"), 200)
    assert _check("https://acme.in/", g)["verdict"] == "ok"


def test_dns_failure_twice_is_broken_only_when_our_network_is_up():
    g = FakeGet()
    assert _check("https://dead.example/", g, net=True, hop=lambda u: "dns")["verdict"] == "broken"
    assert _check("https://dead.example/", g, net=False, hop=lambda u: "dns")["verdict"] == "unknown"
    assert g.calls == []


def test_unexpected_error_is_unknown():
    g = FakeGet(ValueError("bug"), ValueError("bug"))
    assert _check("https://acme.in/", g)["verdict"] == "unknown"


def test_results_are_cached_per_url_but_unknown_is_not():
    cache = {}
    g = FakeGet(200)
    _check("https://acme.in/", g, cache=cache)
    assert _check("https://acme.in/", g, cache=cache)["verdict"] == "ok"
    assert len(g.calls) == 1

    g2 = FakeGet(403, 200)
    assert _check("https://other.in/", g2, cache=cache)["verdict"] == "unknown"
    assert _check("https://other.in/", g2, cache=cache)["verdict"] == "ok"


def test_hop_safety_classifies_literals_without_dns():
    assert sl._hop_safety("http://127.0.0.1/") == "unsafe"
    assert sl._hop_safety("http://localhost/") == "unsafe"
    assert sl._hop_safety("http://10.0.0.5/") == "unsafe"


def test_registered_domain():
    rd = sl.registered_domain
    assert rd("https://www.acme.in/x") == "acme.in"
    assert rd("shop.acme.co.uk") == "acme.co.uk"
    assert rd("https://acme.com.au") == "acme.com.au"
    assert rd("http://WWW.Acme.COM:8080/") == "acme.com"
    assert rd("") == ""


def test_link_kind_helpers():
    assert sl.is_link_aggregator("https://linktr.ee/acme") is True
    assert sl.is_link_aggregator("https://acme.in/") is False
    assert sl.is_url_shortener("https://bit.ly/x") is True
    assert sl.is_whatsapp_link("https://wa.me/9198") is True
    assert sl.is_whatsapp_link("https://api.whatsapp.com/send?phone=1") is True


def test_default_fetchers_are_disabled_in_tests():
    # conftest makes the real getter raise and the network probe say "down",
    # so a test that forgets to inject never touches the network and never
    # claims a link is broken
    r = sl.check_bio_link("https://acme.in/", cache={}, sleep=lambda s: None)
    assert r["verdict"] == "unknown"
