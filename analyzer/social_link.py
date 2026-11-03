"""
Is a social profile's bio link broken? Plus the small URL helpers the social
audit uses to judge where the link goes (link list page, shortener,
WhatsApp, the business's own domain).

The bio link is someone else's input, so every hop is checked the way
importer/homepage.py checks a CSV domain: http(s) only, no localhost or
private IP literal, and security_utils.validate_public_url (DNS must resolve
to public addresses) before each request. Redirects are followed by hand, at
most three. Each request uses importer.homepage._http_get: 8 s, 1 MB body cap,
Accept-Encoding identity, no automatic redirects.

Verdicts, load-bearing for the audit (code owns the findings, so a verdict of
"broken" must be one a person tapping the link would also hit):
  ok       a 2xx at the end of the redirect chain
  broken   404 / 410 / a 5xx other than 503 on two tries, or unreachable
           (DNS name does not resolve, connection refused, timeout) on two
           tries while our own network is demonstrably up
  unknown  everything else: 401 / 403 / 429 / 503 and other statuses that
           are usually a firewall refusing our client, an unsafe hop, too
           many redirects, a non web link, an unexpected error, or our own
           network being down. The audit never draws a conclusion from it.

Results are cached per URL for the process (ok and broken only, for a few
hours), so auditing the same profile again does not refetch.
"""

import socket
import threading
import time
from urllib.parse import urljoin, urlparse

import httpx

from importer.homepage import MAX_REDIRECTS, _http_get
from security_utils import UnsafeURLError, browser_url_allowed, validate_public_url

_REDIRECT_STATUSES = (301, 302, 303, 307, 308)
_CACHE_TTL = 6 * 3600
_CACHE_MAX = 512
_RETRY_PAUSE = 1.5

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()

# Link list / "link in bio" services: the link opens a page of more links.
LINK_AGGREGATORS = (
    "linktr.ee", "beacons.ai", "bio.link", "linkin.bio", "lnk.bio", "taplink.cc", "tap.bio",
    "campsite.bio", "solo.to", "allmylinks.com", "linkpop.com", "hoo.be", "msha.ke",
    "linkr.bio", "snipfeed.co", "milkshake.app", "bio.site", "linkbio.co", "later.com",
    "koji.to", "withkoji.com", "stan.store", "direct.me", "linkfly.to", "flow.page",
    "bento.me", "contactinbio.com", "lynx.bio",
)
URL_SHORTENERS = (
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "rb.gy", "cutt.ly", "shorturl.at", "is.gd",
    "ow.ly", "buff.ly", "rebrand.ly", "tiny.cc", "s.id", "l.instagram.com", "lnkd.in",
    "fb.me", "linkin.page",
)
_WHATSAPP_HOSTS = ("wa.me", "whatsapp.com", "wa.link")

# Second level labels under which the registered name is one label deeper
# (acme.co.uk, acme.com.au, acme.org.in). Not the full public suffix list,
# just the common ones for the markets this tool audits.
_TWO_LEVEL_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "me.uk",
    "co.in", "net.in", "org.in", "firm.in", "gen.in", "ind.in", "ac.in", "edu.in", "gov.in", "res.in",
    "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "co.nz", "org.nz", "net.nz",
    "com.sg", "com.my", "com.br", "com.mx", "com.ar", "com.tr", "com.cn", "com.hk", "com.tw",
    "co.za", "co.jp", "co.kr", "co.id", "co.th", "com.ph", "com.pk", "com.bd", "com.np",
    "com.lk", "com.ng", "co.ke", "com.eg", "com.sa", "ae.org", "co.ae",
}


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _host(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    try:
        host = urlparse(u).hostname or ""
    except ValueError:
        return ""
    return host.rstrip(".").lower()


def registered_domain(url: str) -> str:
    """'https://www.shop.acme.co.uk/x' -> 'acme.co.uk' (approximate: common
    two level suffixes only)."""
    host = _host(url)
    if not host:
        return ""
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_LEVEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _host_in(url: str, domains) -> bool:
    host = _host(url)
    return any(host == d or host.endswith("." + d) for d in domains)


def is_link_aggregator(url: str) -> bool:
    return _host_in(url, LINK_AGGREGATORS)


def is_url_shortener(url: str) -> bool:
    return _host_in(url, URL_SHORTENERS)


def is_whatsapp_link(url: str) -> bool:
    return _host_in(url, _WHATSAPP_HOSTS)


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def _hop_safety(url: str) -> str:
    """'ok' to fetch, 'dns' when the name does not resolve (could be a dead
    domain or our own DNS), 'unsafe' for anything we must not fetch."""
    if not browser_url_allowed(url, navigation=False):
        return "unsafe"
    try:
        validate_public_url(url)
    except UnsafeURLError as e:
        return "dns" if "Could not resolve host" in str(e) else "unsafe"
    except Exception:  # noqa: BLE001  (fail closed)
        return "unsafe"
    return "ok"


def _network_up() -> bool:
    """Is OUR network working? Two well known names must not both fail to
    resolve. Used only to tell "their site is unreachable" from "we are
    offline"."""
    for host in ("www.google.com", "one.one.one.one"):
        try:
            if socket.getaddrinfo(host, 443):
                return True
        except OSError:
            continue
    return False


# Module level so conftest.py can disable the real network for every test.
_default_get = _http_get
_default_network_ok = _network_up
_default_hop_check = _hop_safety


def _is_broken_status(status: int) -> bool:
    return status in (404, 410) or (500 <= status <= 599 and status != 503)


def _attempt(url: str, get, hop_check) -> tuple[str, int | None, str]:
    """One try along the redirect chain: (kind, status, final_url) where kind
    is ok / broken / unreachable / unknown."""
    for _ in range(MAX_REDIRECTS + 1):
        safety = hop_check(url)
        if safety == "dns":
            return "unreachable", None, url
        if safety != "ok":
            return "unknown", None, url
        try:
            status, headers, _body = get(url)
        except (httpx.TransportError, OSError):
            return "unreachable", None, url
        except Exception as e:  # noqa: BLE001
            print(f"[SocialLink] unexpected error fetching {url}: {e!r}")
            return "unknown", None, url
        if status in _REDIRECT_STATUSES:
            location = (headers or {}).get("location") or (headers or {}).get("Location")
            if not location:
                return "unknown", status, url
            url = urljoin(url, location)
            continue
        if 200 <= status < 300:
            return "ok", status, url
        if _is_broken_status(status):
            return "broken", status, url
        return "unknown", status, url
    return "unknown", None, url


def _cache_get(cache, url, now):
    with _CACHE_LOCK:
        hit = cache.get(url)
        if hit and hit[0] > now:
            return dict(hit[1])
        if hit:
            cache.pop(url, None)
    return None


def _cache_put(cache, url, result, now):
    with _CACHE_LOCK:
        if len(cache) >= _CACHE_MAX:
            cache.clear()
        cache[url] = (now + _CACHE_TTL, dict(result))


def check_bio_link(url: str, *, get=None, network_ok=None, hop_check=None, cache=None,
                   sleep=None, clock=None) -> dict:
    """{"verdict": "ok" | "broken" | "unknown", "status": int | None,
    "final_url": str}. Never raises."""
    get = get or _default_get
    network_ok = network_ok or _default_network_ok
    hop_check = hop_check or _default_hop_check
    cache = _CACHE if cache is None else cache
    sleep = sleep or time.sleep
    clock = clock or time.time

    raw = (url or "").strip()
    if raw and "://" not in raw and ":" not in raw.split("/", 1)[0]:
        raw = "https://" + raw
    if not raw or urlparse(raw).scheme.lower() not in ("http", "https"):
        return {"verdict": "unknown", "status": None, "final_url": raw}

    cached = _cache_get(cache, raw, clock())
    if cached is not None:
        return cached

    kind, status, final = _attempt(raw, get, hop_check)
    if kind in ("broken", "unreachable"):
        # one retry: a single failure can be a blip on either side
        sleep(_RETRY_PAUSE)
        kind2, status2, final2 = _attempt(raw, get, hop_check)
        if kind2 == "ok":
            kind, status, final = kind2, status2, final2
        elif kind2 == "broken" or (kind2 == "unreachable" and kind == "broken"):
            # a dead status on one try and no better answer on the other
            kind, status, final = "broken", (status2 if status2 is not None else status), final2
        elif kind2 == "unreachable":
            # unreachable twice: only their fault if OUR network is fine
            try:
                ours_ok = bool(network_ok())
            except Exception:  # noqa: BLE001
                ours_ok = False
            kind = "broken" if ours_ok else "unknown"
            status, final = None, final2
        else:
            kind, status, final = "unknown", status2, final2

    result = {"verdict": kind if kind in ("ok", "broken") else "unknown", "status": status, "final_url": final}
    if result["verdict"] in ("ok", "broken"):
        _cache_put(cache, raw, result, clock())
    return result
