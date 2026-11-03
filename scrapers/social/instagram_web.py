"""
Instagram web reader: replaces the instagrapi login path, which crashes
inside Client.login before credentials are even sent (see CLAUDE.md §5.14).

Two layers, both read only (never like / follow / DM / post):

1. PUBLIC reader, no login: a short-lived headless Playwright browser opens
   https://www.instagram.com/<handle>/ as a logged-out visitor. Verified
   live against instagram.com/mmga.agency on 2026-11-03, that HTML carries:
     * a Relay prefetch <script type="application/json"> holding
       xig_user_by_username: follower_count, following_count, full_name,
       biography, bio_links (url), is_private. all_media_count is null.
       These are the live numbers the visible header renders.
     * og:description "560 Followers, 44 Following, 86 Posts - ...": the only
       place the post count appears logged out. Its follower/following
       numbers can lag the live ones (560/44 vs 559/42 on the same load),
       so they are a fallback only.
     * a second prefetch with the 12 most recent posts
       (polaris_ordered_timeline_connection): media type, caption, and the
       auto-generated alt text "Video by MMGA on October 27, 2026." It has NO
       like/comment counts and NO timestamps.
   So engagement is never known logged out. Posting activity is taken from
   the alt-text dates only when EVERY visible post carries one (an owner can
   replace alt text, and one undated post could be the newest); otherwise the
   profile is header only (activity_known=False) and the audit skips every
   activity check.

2. SESSION reader (spare account): a persistent Playwright context in
   IG_SESSION_DIR (logged into once by hand with scripts/ig_login.py). When
   it has a sessionid cookie, web_profile_info is fetched through that same
   context (same cookies, same browser fingerprint) and gives real
   timestamps and like/comment counts. An expired / checkpointed session
   falls back to the public reader with a note asking for a re-login.

Politeness lives in InstagramThrottle (module level): 20 s between profile
loads, at most 20 per rolling hour, and a 60 minute cooldown after a 429 /
challenge / login wall, during which every read returns analyzed=False with
a note instead of touching Instagram.
"""

import json
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import unescape

import config
from scrapers.social.base import SocialProfile, classify_frequency, unanalyzed

IG_APP_ID = "936619743392459"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Use the alt-text dates of the 12 posts a logged-out visitor sees to judge
# posting activity. Flip to False to make every public read header only.
PUBLIC_ACTIVITY_FROM_ALT_TEXT = True

NOTE_PUBLIC_ONLY = "Limited view: public profile data only (post dates and engagement not available)"
NOTE_PUBLIC_WITH_DATES = ("Limited view: public profile data only (engagement not available; "
                          "posting activity read from the {n} most recent posts)")
NOTE_RELOGIN = "The spare Instagram session needs re-login (run python scripts/ig_login.py)."
NOTE_PRIVATE = "This Instagram account is private, so its profile cannot be read."
NOTE_NOT_FOUND = "Instagram profile not found (the username does not exist or was changed)."
NOTE_LOGIN_WALL = ("Instagram showed a login wall instead of the profile, so it could not be read. "
                   "Instagram reads are paused for 60 minutes.")
NOTE_UNREADABLE = "Instagram served a page this reader does not recognise, so the profile could not be read."
NOTE_INVALID = "Not a valid Instagram username."

_HANDLE_RE = re.compile(r"^[a-z0-9._]{1,30}$")
_OG_COUNTS = re.compile(
    r"([\d.,]+\s*[KkMm]?)\s+Followers?,\s*([\d.,]+\s*[KkMm]?)\s+Following,\s*([\d.,]+\s*[KkMm]?)\s+Posts?",
    re.I,
)
_ALT_DATE = re.compile(r"\bon ([A-Z][a-z]+ \d{1,2}, \d{4})")
_JSON_SCRIPT = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.S)
_PRIVATE_MARKERS = ("this account is private",)
_NOT_FOUND_MARKERS = ("sorry, this page isn't available", "page not found")


# ---------------------------------------------------------------------------
# politeness
# ---------------------------------------------------------------------------

class InstagramThrottle:
    """Module-level politeness for every Instagram profile load.

    * at least min_interval seconds between loads (waits it out, it is short)
    * at most max_per_hour loads per rolling hour (refuses with a note; it
      never sleeps for an hour inside a request)
    * a cooldown after a 429 / challenge / login wall, like the legacy
      scrapers/instagram.py breaker: retrying right after Instagram pushed
      back is how a soft flag becomes a block
    * a separate window during which the spare session is known dead, so a
      checkpointed account is not hit again on every read
    """

    def __init__(self, min_interval=20, max_per_hour=20, cooldown=3600, clock=None, sleep=None):
        self.min_interval = min_interval
        self.max_per_hour = max_per_hour
        self.cooldown = cooldown
        self._clock = clock or time.time
        self._sleep = sleep or time.sleep
        self._loads: deque = deque()
        self._last = None
        self._locked_until = 0.0
        self._session_dead_until = 0.0

    def _prune(self, now):
        while self._loads and now - self._loads[0] >= 3600:
            self._loads.popleft()

    def blocked_note(self) -> str | None:
        """Why a load may not happen right now, without consuming a slot."""
        now = self._clock()
        if now < self._locked_until:
            mins = max(1, int((self._locked_until - now + 59) // 60))
            return (f"Instagram asked us to slow down, so Instagram reads are paused for about "
                    f"{mins} more minutes (60 minute cooldown). Try again later.")
        self._prune(now)
        if len(self._loads) >= self.max_per_hour:
            mins = max(1, int((self._loads[0] + 3600 - now + 59) // 60))
            return (f"Read limit reached ({self.max_per_hour} Instagram profiles per hour, to stay polite "
                    f"to Instagram). Try again in about {mins} minutes.")
        return None

    def acquire(self) -> str | None:
        """Take one load slot (waiting out the minimum interval). Returns
        None when the load may go ahead, else the note to show instead."""
        note = self.blocked_note()
        if note:
            return note
        if self._last is not None:
            wait = self._last + self.min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        now = self._clock()
        self._last = now
        self._loads.append(now)
        return None

    def trip(self) -> None:
        self._locked_until = self._clock() + self.cooldown
        print(f"[InstagramWeb] Instagram pushed back (429 / challenge / login wall), "
              f"pausing all Instagram reads for {self.cooldown // 60} minutes")

    def session_usable(self) -> bool:
        return self._clock() >= self._session_dead_until

    def mark_session_dead(self) -> None:
        self._session_dead_until = self._clock() + self.cooldown


_THROTTLE = InstagramThrottle()


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _to_int(raw) -> int:
    """'559' / '1,234' / '1.2K' / '2.3M' -> int (0 when unparseable)."""
    s = str(raw or "").replace(",", "").strip()
    m = re.match(r"^([\d.]+)\s*([KkMm])?", s)
    if not m:
        return 0
    try:
        n = float(m.group(1))
    except ValueError:
        return 0
    mult = {"k": 1_000, "m": 1_000_000}.get((m.group(2) or "").lower(), 1)
    return int(round(n * mult))


def parse_og_counts(text: str) -> dict | None:
    m = _OG_COUNTS.search(text or "")
    if not m:
        return None
    return {"followers": _to_int(m.group(1)), "following": _to_int(m.group(2)), "posts": _to_int(m.group(3))}


def normalize_handle(raw: str) -> str | None:
    """A bare lowercase username from '@x', 'x?igsh=..', or a profile URL;
    None when it is not a plausible Instagram username."""
    s = (raw or "").strip()
    is_url = "instagram.com/" in s
    if is_url:
        s = s.split("instagram.com/", 1)[1]
    s = s.split("?", 1)[0].split("#", 1)[0].strip("/")
    if is_url:
        s = s.split("/")[0]
    elif "/" in s:
        return None
    s = s.lstrip("@").lower()
    if ".." in s or s.strip(".") == "":
        return None
    return s if _HANDLE_RE.match(s) else None


def has_session_cookie(cookies) -> bool:
    return any(c.get("name") == "sessionid" and c.get("value") for c in (cookies or []))


def profile_url(handle: str) -> str:
    return f"https://www.instagram.com/{handle}/"


def _walk(obj):
    """Yield every dict inside a JSON structure."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def _meta(html: str, attr: str, name: str) -> str:
    for tag in re.findall(r"<meta\b[^>]*>", html, re.I):
        if re.search(rf'{attr}="{re.escape(name)}"', tag, re.I):
            m = re.search(r'content="([^"]*)"', tag, re.S)
            if m:
                return unescape(m.group(1))
    return ""


# ---------------------------------------------------------------------------
# public page
# ---------------------------------------------------------------------------

def _embedded_users(html: str) -> tuple[dict, dict]:
    """(profile user dict, timeline connection dict) from the Relay prefetch
    scripts. Either can be {} when absent."""
    profile, timeline = {}, {}
    for raw in _JSON_SCRIPT.findall(html):
        if "xig_user_by_username" not in raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for d in _walk(data):
            user = d.get("xig_user_by_username")
            if not isinstance(user, dict):
                continue
            if "follower_count" in user and not profile:
                profile = user
            conn = user.get("polaris_ordered_timeline_connection")
            if isinstance(conn, dict) and not timeline:
                timeline = conn
    return profile, timeline


def _bio_link(user: dict) -> str:
    for link in user.get("bio_links") or []:
        if isinstance(link, dict) and link.get("url"):
            return str(link["url"])
    return str(user.get("external_url") or "")


def _alt_date(node: dict) -> date | None:
    ts = node.get("taken_at") or node.get("taken_at_timestamp")
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    m = _ALT_DATE.search(node.get("accessibility_caption") or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%B %d, %Y").date()
    except ValueError:
        return None


def _is_video_node(node: dict) -> bool:
    return ("Video" in str(node.get("__typename") or "") or node.get("media_type") == 2
            or node.get("product_type") == "clips" or bool(node.get("is_video")))


def _caption(node: dict) -> str:
    cap = node.get("caption")
    if isinstance(cap, dict):
        return (cap.get("text") or "").strip()
    return ""


def parse_public_page(html: str, handle: str, now: datetime, final_url: str = "") -> tuple[str, SocialProfile]:
    """Classify a logged-out profile page and read what it shows.
    kind is one of ok / private / not_found / login_wall / unreadable."""
    url = profile_url(handle)
    html = html or ""
    low_url = (final_url or "").lower()
    if "/accounts/login" in low_url or "/challenge" in low_url:
        return "login_wall", unanalyzed("instagram", handle, url, NOTE_LOGIN_WALL)

    user, timeline = _embedded_users(html)
    og = parse_og_counts(_meta(html, "property", "og:description")) or \
        parse_og_counts(_meta(html, "name", "description"))
    low = html.lower()

    if user.get("is_private") is True or (not user and any(m in low for m in _PRIVATE_MARKERS)):
        return "private", unanalyzed("instagram", handle, url, NOTE_PRIVATE)
    if not user and not og:
        if any(m in low for m in _NOT_FOUND_MARKERS):
            return "not_found", unanalyzed("instagram", handle, url, NOTE_NOT_FOUND)
        if 'name="password"' in low or "loginform" in low:
            return "login_wall", unanalyzed("instagram", handle, url, NOTE_LOGIN_WALL)
        return "unreadable", unanalyzed("instagram", handle, url, NOTE_UNREADABLE)

    og = og or {}
    followers = user.get("follower_count")
    following = user.get("following_count")
    posts = user.get("media_count") or user.get("all_media_count")
    edges = [e.get("node") for e in (timeline.get("edges") or []) if isinstance(e, dict)]
    nodes = [n for n in edges if isinstance(n, dict)]
    if not isinstance(posts, int):
        posts = og.get("posts")
    if not isinstance(posts, int) and nodes and (timeline.get("page_info") or {}).get("has_next_page") is False:
        posts = len(nodes)  # the whole grid is on the page

    name = user.get("full_name") or ""
    if not name:
        name = _meta(html, "property", "og:title").split("(@")[0].strip()
    link = _bio_link(user)

    p = SocialProfile(
        platform="instagram", handle=handle, url=url,
        display_name=name,
        bio=(user.get("biography") or "").strip(),
        followers=followers if isinstance(followers, int) else og.get("followers", 0),
        following=following if isinstance(following, int) else og.get("following", 0),
        posts_count=posts if isinstance(posts, int) else 0,
        has_link_in_bio=bool(link),
        link_url=link,
        activity_known=False,
        note=NOTE_PUBLIC_ONLY,
    )
    p.uses_video = any(_is_video_node(n) for n in nodes)
    p.sample_captions = [c[:200] for c in (_caption(n) for n in nodes) if c][:5]

    dates = [_alt_date(n) for n in nodes]
    if PUBLIC_ACTIVITY_FROM_ALT_TEXT and nodes and all(dates):
        today = now.date()
        ages = [max(0, (today - d).days) for d in dates]
        p.activity_known = True
        p.last_post_age_days = min(ages)
        p.posts_last_30_days = sum(1 for a in ages if a <= 30)
        p.posting_frequency = classify_frequency(p.posts_last_30_days)
        p.note = NOTE_PUBLIC_WITH_DATES.format(n=len(nodes))
    return "ok", p


# ---------------------------------------------------------------------------
# session reader payload (web_profile_info)
# ---------------------------------------------------------------------------

def classify_session_response(status: int, payload) -> str:
    """ok / not_found / rate_limited / session_dead / error."""
    msg = ""
    if isinstance(payload, dict):
        msg = str(payload.get("message") or "").lower()
    if status == 429 or "wait a few minutes" in msg:
        return "rate_limited"
    if (msg in ("login_required", "checkpoint_required", "challenge_required")
            or (isinstance(payload, dict) and payload.get("require_login"))
            or status in (401, 403)):
        return "session_dead"
    if status == 404:
        return "not_found"
    if status == 200 and isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return "ok" if isinstance(payload["data"].get("user"), dict) else "not_found"
    return "error"


def _count(d, key) -> int:
    v = (d or {}).get(key)
    if isinstance(v, dict):
        v = v.get("count")
    return max(0, v) if isinstance(v, int) else 0


def parse_web_profile_info(payload: dict, handle: str, now: datetime) -> tuple[str, SocialProfile]:
    url = profile_url(handle)
    user = ((payload or {}).get("data") or {}).get("user")
    if not isinstance(user, dict):
        return "not_found", unanalyzed("instagram", handle, url, NOTE_NOT_FOUND)

    media = user.get("edge_owner_to_timeline_media") or {}
    nodes = [e.get("node") for e in (media.get("edges") or []) if isinstance(e, dict)]
    nodes = [n for n in nodes if isinstance(n, dict)]
    if user.get("is_private") and not nodes:
        return "private", unanalyzed("instagram", handle, url, NOTE_PRIVATE)

    followers = _count(user, "edge_followed_by")
    link = _bio_link(user)
    p = SocialProfile(
        platform="instagram", handle=handle, url=url,
        display_name=user.get("full_name") or "",
        bio=(user.get("biography") or "").strip(),
        followers=followers,
        following=_count(user, "edge_follow"),
        posts_count=_count(user, "edge_owner_to_timeline_media"),
        has_link_in_bio=bool(link),
        link_url=link,
    )

    cutoff = now - timedelta(days=30)
    total_likes = total_comments = 0
    taken = []
    captions = []
    for n in nodes:
        total_likes += _count(n, "edge_liked_by") or _count(n, "edge_media_preview_like")
        total_comments += _count(n, "edge_media_to_comment")
        ts = n.get("taken_at_timestamp")
        if isinstance(ts, (int, float)) and ts > 0:
            taken.append(datetime.fromtimestamp(ts, tz=timezone.utc))
        cap_edges = (n.get("edge_media_to_caption") or {}).get("edges") or []
        text = ((cap_edges[0] or {}).get("node") or {}).get("text", "") if cap_edges else ""
        if text and text.strip() and len(captions) < 5:
            captions.append(text.strip()[:200])

    p.uses_video = any(_is_video_node(n) for n in nodes)
    p.sample_captions = captions
    if nodes and len(taken) != len(nodes):
        # some posts without a timestamp: do not guess the cadence
        p.activity_known = False
        p.note = "Posting dates could not be read for every recent post, so posting activity is not judged."
        return "ok", p

    p.posts_last_30_days = sum(1 for t in taken if t >= cutoff)
    p.last_post_age_days = max(0, (now - max(taken)).days) if taken else None
    p.posting_frequency = classify_frequency(p.posts_last_30_days)
    if nodes and followers > 0:
        count = len(nodes)
        p.avg_engagement_rate = round((total_likes / count + total_comments / count) / followers * 100, 2)
    return "ok", p
