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

3. POST EMBED reader, no login: https://www.instagram.com/p/<code>/embed/captioned/
   is Instagram's public "embed this post" page. Verified with ONE live
   logged-out load (mmga.agency reel De_6hPjswg-, 2026-11-03), it carries a
   ServerJS script whose PolarisEmbedSimple init holds "contextJSON" with
   gql_data.shortcode_media: edge_liked_by.count (44), edge_media_to_comment
   .count (2), video_view_count (278), caption, __typename / is_video /
   product_type, owner {username, is_verified, edge_followed_by}. The page
   also shows "44 likes" in <div class="SocialProof">. It has NO timestamp
   and NO comment text ("View all comments" only). After a public profile
   read, read_instagram loads the embeds of up to PUBLIC_EMBED_POSTS recent
   posts to fill their likes / comments; engagement_known and the
   engagement rate are set only when at least EMBED_MIN_POSTS were read.

Politeness lives in InstagramThrottle (module level): 20 s between loads
(profile or embed), at most 20 per rolling hour, and a 60 minute cooldown
after a 429 / challenge / login wall, during which every read returns
analyzed=False with a note instead of touching Instagram. A 429 or login
wall on an EMBED pauses only the embeds (its own 60 minute window), never
the profile read.
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
# After a public read, load the public embed page of this many recent posts
# (each one is a throttled load) to read their likes and comments. 0 = off.
PUBLIC_EMBED_POSTS = 6
# Engagement (engagement_known, avg_engagement_rate) only from this many
# embed-read posts or more.
EMBED_MIN_POSTS = 4

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

NOTE_EMBED_READ = "likes and comments read from {n} recent posts"
NOTE_EMBED_FAILED = "Likes and comments could not be read from the post previews this time."

_HANDLE_RE = re.compile(r"^[a-z0-9._]{1,30}$")
_SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SOCIAL_PROOF = re.compile(r'class="SocialProof"[^>]*>\s*<a\b[^>]*>\s*([\d.,]+\s*[KkMm]?)\s+likes?\s*</a>', re.I)
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
        self._embed_locked_until = 0.0

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

    def embed_blocked_note(self) -> str | None:
        """Why post embeds may not load right now (their own cooldown, or
        anything that blocks every load). Consumes no slot."""
        now = self._clock()
        if now < self._embed_locked_until:
            mins = max(1, int((self._embed_locked_until - now + 59) // 60))
            return f"Instagram post previews are paused for about {mins} more minutes after Instagram pushed back."
        return self.blocked_note()

    def trip_embed(self) -> None:
        """A 429 / login wall on a post embed: pause embeds only. The profile
        page is a different path and keeps working."""
        self._embed_locked_until = self._clock() + self.cooldown
        print(f"[InstagramWeb] Instagram pushed back on a post embed, "
              f"pausing post previews for {self.cooldown // 60} minutes")


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


def valid_shortcode(code) -> bool:
    """A post shortcode safe to put in an embed URL."""
    return isinstance(code, str) and bool(_SHORTCODE_RE.match(code))


def has_session_cookie(cookies) -> bool:
    return any(c.get("name") == "sessionid" and c.get("value") for c in (cookies or []))


STATE_FILE = "storage_state.json"


def instagram_cookies_from_state(state) -> list:
    """The instagram.com cookies out of a Playwright storage_state dict."""
    cookies = (state or {}).get("cookies") if isinstance(state, dict) else None
    if not isinstance(cookies, list):
        return []
    return [c for c in cookies if isinstance(c, dict) and "instagram.com" in str(c.get("domain", ""))]


def wait_for_session_cookie(get_cookies, *, timeout=600, poll=2, clock=None, sleep=None) -> bool:
    """Poll get_cookies() until a sessionid cookie appears (the hand login,
    including any checkpoint, is finished) or timeout seconds pass."""
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    deadline = clock() + timeout
    while True:
        if has_session_cookie(get_cookies()):
            return True
        if clock() >= deadline:
            return False
        sleep(poll)


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


def _public_caption(node: dict) -> str | None:
    """A public post's caption: "" when the post has none ("caption": null),
    None when the page did not carry the field at all (unknown)."""
    if "caption" not in node:
        return None
    cap = node.get("caption")
    if cap is None:
        return ""
    if isinstance(cap, dict):
        return (cap.get("text") or "").strip()
    return None


def _post_type(node: dict) -> str:
    """video / carousel / image, or "" when the node does not say."""
    if _is_video_node(node):
        return "video"
    tn = str(node.get("__typename") or "")
    mt = node.get("media_type")
    if ("Carousel" in tn or "Sidecar" in tn or mt == 8
            or node.get("product_type") == "carousel_container" or "edge_sidecar_to_children" in node):
        return "carousel"
    if "Image" in tn or "Photo" in tn or mt == 1:
        return "image"
    return ""


def _day_ts(d: date | None) -> int | None:
    if d is None:
        return None
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def _count_or_none(d, key) -> int | None:
    """Like _count, but None (not 0) when the count is missing or negative
    (Instagram uses -1 for a hidden count)."""
    v = (d or {}).get(key)
    if isinstance(v, dict):
        v = v.get("count")
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _public_highlights(user: dict) -> int | None:
    """Story highlights on a logged-out page: the full list is embedded when
    page_info says there is no next page; otherwise the count is unknown."""
    conn = user.get("lox_highlights_connection")
    if not isinstance(conn, dict) or not isinstance(conn.get("edges"), list):
        return None
    if (conn.get("page_info") or {}).get("has_next_page") is not False:
        return None
    return len(conn["edges"])


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
    p.highlights_count = _public_highlights(user)

    dates = [_alt_date(n) for n in nodes]
    # Logged out: type and caption are on the page, likes and comments never.
    p.recent_posts = [
        {"type": _post_type(n), "caption": _public_caption(n), "likes": None, "comments": None,
         "ts": _day_ts(d), "code": n.get("code") if valid_shortcode(n.get("code")) else None}
        for n, d in zip(nodes, dates)
    ]
    p.captions_known = bool(nodes) and all(x["caption"] is not None for x in p.recent_posts)
    p.formats_known = bool(nodes) and all(x["type"] for x in p.recent_posts)

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

    hl = user.get("highlight_reel_count")
    if isinstance(hl, int) and not isinstance(hl, bool) and hl >= 0:
        p.highlights_count = hl

    cutoff = now - timedelta(days=30)
    total_likes = total_comments = 0
    taken = []
    captions = []
    posts = []
    for n in nodes:
        total_likes += _count(n, "edge_liked_by") or _count(n, "edge_media_preview_like")
        total_comments += _count(n, "edge_media_to_comment")
        ts = n.get("taken_at_timestamp")
        post_ts = None
        if isinstance(ts, (int, float)) and ts > 0:
            taken.append(datetime.fromtimestamp(ts, tz=timezone.utc))
            post_ts = int(ts)
        cap_field = n.get("edge_media_to_caption")
        cap_edges = (cap_field.get("edges") or []) if isinstance(cap_field, dict) else []
        text = ((cap_edges[0] or {}).get("node") or {}).get("text", "") if cap_edges else ""
        if text and text.strip() and len(captions) < 5:
            captions.append(text.strip()[:200])
        if n.get("like_and_view_counts_disabled"):
            likes = None
        else:
            likes = _count_or_none(n, "edge_liked_by")
            if likes is None:
                likes = _count_or_none(n, "edge_media_preview_like")
        posts.append({
            "type": _post_type(n),
            "caption": (text or "").strip() if isinstance(cap_field, dict) else None,
            "likes": likes,
            "comments": _count_or_none(n, "edge_media_to_comment"),
            "ts": post_ts,
        })

    p.uses_video = any(_is_video_node(n) for n in nodes)
    p.sample_captions = captions
    p.recent_posts = posts
    p.captions_known = bool(posts) and all(x["caption"] is not None for x in posts)
    p.formats_known = bool(posts) and all(x["type"] for x in posts)
    p.engagement_known = (bool(posts) and followers > 0
                          and all(x["likes"] is not None and x["comments"] is not None for x in posts))
    if nodes and followers > 0:
        # likes and comments do not depend on the dates, so this holds even
        # when the dates below are incomplete
        count = len(nodes)
        p.avg_engagement_rate = round((total_likes / count + total_comments / count) / followers * 100, 2)
    if (nodes and len(taken) != len(nodes)) or (not nodes and p.posts_count > 0):
        # posts exist but their dates were not all returned: do not guess
        p.activity_known = False
        p.note = "Posting dates could not be read for every recent post, so posting activity is not judged."
        return "ok", p

    p.posts_last_30_days = sum(1 for t in taken if t >= cutoff)
    p.last_post_age_days = max(0, (now - max(taken)).days) if taken else None
    p.posting_frequency = classify_frequency(p.posts_last_30_days)
    return "ok", p


# ---------------------------------------------------------------------------
# post embed page
# ---------------------------------------------------------------------------

def embed_url(code: str) -> str:
    return f"https://www.instagram.com/p/{code}/embed/captioned/"


def _embed_context_media(html: str) -> dict:
    """gql_data.shortcode_media out of the embed's contextJSON, {} when absent."""
    key = '"contextJSON":'
    dec = json.JSONDecoder()
    start = 0
    while True:
        i = html.find(key, start)
        if i == -1:
            return {}
        start = i + len(key)
        while start < len(html) and html[start].isspace():
            start += 1
        try:
            raw, _ = dec.raw_decode(html, start)
            data = json.loads(raw) if isinstance(raw, str) else None
        except ValueError:
            continue
        media = ((data or {}).get("gql_data") or {}).get("shortcode_media") if isinstance(data, dict) else None
        if isinstance(media, dict):
            return media


def parse_post_embed(html: str, final_url: str = "") -> tuple[str, dict | None]:
    """Read one post's public embed page. kind is ok / login_wall /
    unreadable. On ok the dict is
      {"shortcode", "likes", "comments", "views", "type", "caption", "owner",
       "owner_followers"}
    with None for anything the page did not show (a hidden like count is
    None, never 0). The embed never shows a timestamp or comment text."""
    html = html or ""
    low_url = (final_url or "").lower()
    if "/accounts/login" in low_url or "/challenge" in low_url:
        return "login_wall", None
    media = _embed_context_media(html)
    proof = _SOCIAL_PROOF.search(html)
    if not media and not proof:
        low = html.lower()
        if 'name="password"' in low or "loginform" in low:
            return "login_wall", None
        return "unreadable", None

    hidden = bool(media.get("like_and_view_counts_disabled"))
    likes = None if hidden else _count_or_none(media, "edge_liked_by")
    if likes is None and not hidden:
        likes = _count_or_none(media, "edge_media_preview_like")
    if likes is None and proof and not hidden:
        likes = _to_int(proof.group(1))
    comments = _count_or_none(media, "edge_media_to_comment")
    if comments is None:
        comments = _count_or_none(media, "edge_media_preview_comment")
    views = media.get("video_view_count")
    owner = media.get("owner") if isinstance(media.get("owner"), dict) else {}
    cap_edges = ((media.get("edge_media_to_caption") or {}).get("edges") or []) \
        if isinstance(media.get("edge_media_to_caption"), dict) else []
    caption = ((cap_edges[0] or {}).get("node") or {}).get("text") if cap_edges else None
    return "ok", {
        "shortcode": media.get("shortcode"),
        "likes": likes,
        "comments": comments,
        "views": views if isinstance(views, int) and not isinstance(views, bool) and views >= 0 else None,
        "type": _post_type(media) if media else "",
        "caption": caption.strip() if isinstance(caption, str) else None,
        "owner": str(owner.get("username") or "").lower() or None,
        "owner_followers": _count_or_none(owner, "edge_followed_by"),
    }


# ---------------------------------------------------------------------------
# fetch layer (Playwright). Not exercised by tests; read_instagram takes the
# fetchers as parameters so tests pass fakes.
# ---------------------------------------------------------------------------

@dataclass
class PublicPage:
    status: int
    url: str     # final URL after redirects (a login wall shows up here)
    html: str


@dataclass
class SessionResult:
    kind: str            # "no_session" | "blocked" | "response"
    status: int = 0
    payload: object = None
    note: str = ""


_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]
_VIEWPORT = {"width": 1280, "height": 900}


def browser_identity(playwright) -> dict:
    """Context options shared by the login script, the session reader and
    the public reader, so the logged-in session is always replayed from the
    same browser fingerprint. The user agent is Playwright's own "Desktop
    Chrome" descriptor, which matches the bundled Chromium's real version
    (a hand-written UA would disagree with the sec-ch-ua headers)."""
    return {
        "user_agent": playwright.devices["Desktop Chrome"]["user_agent"],
        "viewport": _VIEWPORT,
        "locale": "en-US",
        "timezone_id": "Asia/Kolkata",
    }


def session_dir() -> str:
    d = getattr(config, "IG_SESSION_DIR", "") or os.path.join("data", "ig_session")
    return d if os.path.isabs(d) else os.path.join(BASE_DIR, d)


def _block_heavy(route):
    if route.request.resource_type in ("image", "media", "font"):
        return route.abort()
    return route.continue_()


def _playwright_public_fetch(handle: str) -> PublicPage:
    """One logged-out load of the profile in a short-lived browser. Images,
    video and fonts are blocked (the data is all in the HTML)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        try:
            ctx = browser.new_context(service_workers="block", **browser_identity(p))
            ctx.route("**/*", _block_heavy)
            page = ctx.new_page()
            resp = page.goto(profile_url(handle), wait_until="domcontentloaded", timeout=30_000)
            try:
                page.wait_for_selector('meta[property="og:description"]', state="attached", timeout=5_000)
            except Exception:  # noqa: BLE001  (a missing meta is classified by the parser)
                pass
            return PublicPage(resp.status if resp else 0, page.url, page.content())
        finally:
            browser.close()


def _playwright_embed_fetch(code: str) -> PublicPage:
    """One logged-out load of a post's public embed page, same short-lived
    browser and fingerprint as the public profile reader."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        try:
            ctx = browser.new_context(service_workers="block", **browser_identity(p))
            ctx.route("**/*", _block_heavy)
            page = ctx.new_page()
            resp = page.goto(embed_url(code), wait_until="domcontentloaded", timeout=30_000)
            try:
                page.wait_for_selector(".SocialProof, .EmbedFrame, .Embed", state="attached", timeout=5_000)
            except Exception:  # noqa: BLE001  (an empty page is classified by the parser)
                pass
            return PublicPage(resp.status if resp else 0, page.url, page.content())
        finally:
            browser.close()


def _load_state_cookies(ctx, user_data_dir: str) -> None:
    path = os.path.join(user_data_dir, STATE_FILE)
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            cookies = instagram_cookies_from_state(json.load(f))
        if cookies:
            ctx.add_cookies(cookies)
    except Exception as e:  # noqa: BLE001  (a bad state file just means "no session")
        print(f"[InstagramWeb] could not load {STATE_FILE}: {e}")


def _make_session_fetch(user_data_dir: str):
    def _playwright_session_fetch(handle: str, gate) -> SessionResult:
        """web_profile_info through the persistent logged-in context (same
        cookies and fingerprint as the hand login). `gate` is the throttle,
        called right before the one network request."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir, headless=True, args=_LAUNCH_ARGS, **browser_identity(p))
            try:
                if not has_session_cookie(ctx.cookies("https://www.instagram.com")):
                    # The profile's own cookie store is encrypted per OS: a
                    # login made by scripts/ig_login.py on the Windows host is
                    # unreadable to Linux Chromium in the Docker container.
                    # The login script also saves a plain storage_state.json
                    # in the same (gitignored) folder; load its cookies.
                    _load_state_cookies(ctx, user_data_dir)
                if not has_session_cookie(ctx.cookies("https://www.instagram.com")):
                    return SessionResult("no_session")
                note = gate()
                if note:
                    return SessionResult("blocked", note=note)
                resp = ctx.request.get(
                    f"https://www.instagram.com/api/v1/users/web_profile_info/?username={handle}",
                    headers={
                        "x-ig-app-id": IG_APP_ID,
                        "x-requested-with": "XMLHttpRequest",
                        "referer": profile_url(handle),
                        "accept": "*/*",
                    },
                    timeout=20_000,
                    max_redirects=0,
                )
                text = resp.text()
                try:
                    payload = json.loads(text)
                except ValueError:
                    payload = None
                status = resp.status
                if 300 <= status < 400:
                    # a redirect to /accounts/login or /challenge means a dead session
                    status = 401
                return SessionResult("response", status=status, payload=payload)
            finally:
                ctx.close()

    return _playwright_session_fetch


def _default_session_fetch(user_data_dir: str):
    """The real session fetcher, or None when no login was ever saved."""
    try:
        if os.path.isdir(user_data_dir) and os.listdir(user_data_dir):
            return _make_session_fetch(user_data_dir)
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

_DEFAULT = object()
_READ_LOCK = threading.Lock()   # one Instagram browser at a time


def _join(*notes) -> str:
    return " ".join(n for n in notes if n)


def _read_post_embed(code: str, handle: str, fetch, th) -> dict:
    """One throttled embed load (caller holds _READ_LOCK). Returns
    {"status": ok | invalid | blocked | error | rate_limited | login_wall |
    unreadable | other_owner, "note": str, ...parse_post_embed fields on ok}."""
    from playwright.sync_api import Error as PlaywrightError

    if not valid_shortcode(code):
        return {"status": "invalid", "note": ""}
    note = th.embed_blocked_note() or th.acquire()
    if note:
        return {"status": "blocked", "note": note}
    try:
        page = fetch(code)
    except PlaywrightError as e:
        print(f"[InstagramWeb] embed load failed: {e}")
        return {"status": "error", "note": ""}
    if page.status == 429:
        th.trip_embed()
        return {"status": "rate_limited", "note": ""}
    kind, data = parse_post_embed(page.html, page.url)
    if kind == "login_wall":
        th.trip_embed()
        return {"status": "login_wall", "note": ""}
    if kind != "ok":
        return {"status": "unreadable", "note": ""}
    if handle and data.get("owner") and data["owner"] != handle:
        # a collab post owned by another account: its likes are not this audience's
        return {"status": "other_owner", "note": "", **data}
    return {"status": "ok", "note": "", **data}


def read_post_embed(shortcode: str, *, handle: str = "", fetch=None, throttle=None) -> dict:
    """Read one post's likes / comments from its public embed page, through
    the shared throttle. See _read_post_embed for the result shape."""
    with _READ_LOCK:
        return _read_post_embed(shortcode, (handle or "").lower(), fetch or _playwright_embed_fetch,
                                throttle or _THROTTLE)


_STOP_STATUSES = ("blocked", "error", "rate_limited", "login_wall")


def _engagement_note(note: str, n: int) -> str:
    read = NOTE_EMBED_READ.format(n=n)
    if "post dates and engagement not available" in note:
        return note.replace("post dates and engagement not available", f"post dates not available; {read}")
    if "engagement not available" in note:
        return note.replace("engagement not available", read)
    return _join(note, read[0].upper() + read[1:] + ".")


def _fill_from_embeds(p: SocialProfile, handle: str, fetch, th) -> None:
    """Fill likes / comments of up to PUBLIC_EMBED_POSTS recent posts from
    their embed pages. Only posts actually read get counts; engagement is
    computed only from EMBED_MIN_POSTS or more read posts."""
    if PUBLIC_EMBED_POSTS <= 0 or fetch is None:
        return
    targets = [x for x in p.recent_posts if x.get("code") and x.get("likes") is None][:PUBLIC_EMBED_POSTS]
    read = []
    stopped = False
    for post in targets:
        r = _read_post_embed(post["code"], handle, fetch, th)
        if r["status"] in _STOP_STATUSES:
            stopped = True
            break
        if r["status"] != "ok":
            continue
        if r.get("likes") is not None:
            post["likes"] = r["likes"]
        if r.get("comments") is not None:
            post["comments"] = r["comments"]
        if post["likes"] is not None and post["comments"] is not None:
            read.append(post)
    if len(read) >= EMBED_MIN_POSTS and p.followers > 0:
        n = len(read)
        likes = sum(x["likes"] for x in read)
        comments = sum(x["comments"] for x in read)
        p.engagement_known = True
        p.avg_engagement_rate = round((likes / n + comments / n) / p.followers * 100, 2)
        p.note = _engagement_note(p.note, n)
    elif targets and (stopped or not read):
        p.note = _join(p.note, NOTE_EMBED_FAILED)


def read_instagram(handle: str, *, public_fetch=None, session_fetch=_DEFAULT, embed_fetch=_DEFAULT,
                   throttle=None, now: datetime | None = None) -> SocialProfile:
    """Read one Instagram profile: spare session first when one is saved,
    else (or when it is dead) the public logged-out page, whose recent posts
    then get likes / comments from their public embed pages (embed_fetch,
    None = skip).

    Returns a SocialProfile, analyzed=False with a note for every expected
    failure (invalid name, private, not found, login wall, 429, cooldown,
    hourly cap, browser timeout). Only a genuine bug raises, so the adapter
    can fall back to the legacy scraper."""
    from playwright.sync_api import Error as PlaywrightError

    h = normalize_handle(handle)
    if not h:
        raw = (handle or "").strip()
        return unanalyzed("instagram", raw, "https://www.instagram.com/", NOTE_INVALID)
    url = profile_url(h)
    th = throttle or _THROTTLE
    now = now or datetime.now(timezone.utc)
    public_fetch = public_fetch or _playwright_public_fetch
    if session_fetch is _DEFAULT:
        session_fetch = _default_session_fetch(session_dir())
    if embed_fetch is _DEFAULT:
        embed_fetch = _playwright_embed_fetch

    with _READ_LOCK:
        note = th.blocked_note()
        if note:
            return unanalyzed("instagram", h, url, note)

        session_note = ""
        if session_fetch is not None:
            if not th.session_usable():
                session_note = NOTE_RELOGIN
            else:
                res = None
                try:
                    res = session_fetch(h, th.acquire)
                except PlaywrightError as e:
                    print(f"[InstagramWeb] session browser failed: {e}")
                    session_note = ("The spare Instagram session could not be opened, "
                                    "showing the public view instead.")
                if res is not None:
                    if res.kind == "blocked":
                        return unanalyzed("instagram", h, url, res.note)
                    if res.kind == "no_session":
                        session_note = NOTE_RELOGIN
                    elif res.kind == "response":
                        verdict = classify_session_response(res.status, res.payload)
                        if verdict == "ok":
                            return parse_web_profile_info(res.payload, h, now)[1]
                        if verdict == "not_found":
                            return unanalyzed("instagram", h, url, NOTE_NOT_FOUND)
                        if verdict == "rate_limited":
                            # The session API is rate limited; the public page is a
                            # different path, so pause only the session and fall back.
                            th.mark_session_dead()
                            session_note = ("Instagram rate limited the spare session, "
                                            "showing the public view instead.")
                        elif verdict == "session_dead":
                            th.mark_session_dead()
                            session_note = NOTE_RELOGIN
                        else:
                            session_note = (f"The spare Instagram session could not read this profile "
                                            f"(HTTP {res.status}), showing the public view instead.")

        note = th.acquire()
        if note:
            return unanalyzed("instagram", h, url, _join(note, session_note))
        try:
            page = public_fetch(h)
        except PlaywrightError as e:
            print(f"[InstagramWeb] public load failed: {e}")
            return unanalyzed("instagram", h, url, _join(
                "Instagram did not load (timeout or browser error), so the profile could not be read.",
                session_note))

        if page.status == 429:
            th.trip()
            return unanalyzed("instagram", h, url, _join(th.blocked_note(), session_note))
        kind, profile = parse_public_page(page.html, h, now, final_url=page.url)
        if kind == "login_wall":
            th.trip()
        if kind == "ok" and profile.analyzed:
            _fill_from_embeds(profile, h, embed_fetch, th)
        profile.note = _join(profile.note, session_note)
        return profile
