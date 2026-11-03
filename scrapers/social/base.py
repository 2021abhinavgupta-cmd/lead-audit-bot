"""
Shared shape for every social platform adapter.

Each adapter (instagram.py, youtube.py, facebook.py, linkedin.py) takes a
handle or profile URL and returns a SocialProfile. Fields an adapter cannot
fill are left at their zero value, never guessed. An adapter that reached the
profile but could not extract usable data returns analyzed=False with a note
rather than raising.
"""

from dataclasses import dataclass, field


@dataclass
class SocialProfile:
    platform: str            # "instagram" | "youtube" | "facebook" | "linkedin"
    handle: str
    url: str
    display_name: str = ""
    bio: str = ""
    followers: int = 0       # subscribers for YouTube, page likes for Facebook
    following: int = 0
    posts_count: int = 0
    posts_last_30_days: int = 0
    posting_frequency: str = ""
    avg_engagement_rate: float = 0.0
    uses_video: bool = False
    has_link_in_bio: bool = False
    link_url: str = ""       # the bio link itself, when the adapter could read it
    last_post_age_days: int | None = None
    sample_captions: list[str] = field(default_factory=list)
    analyzed: bool = True
    note: str = ""
    # False when the adapter could read the profile header (followers, bio,
    # link, post count) but NOT the posting activity (post dates, engagement,
    # post formats) — e.g. a logged-out Instagram read. Then
    # posts_last_30_days / avg_engagement_rate / uses_video / posting_frequency
    # are unknown, not zero, and analyzer/social_audit.py must not draw any
    # conclusion from them.
    activity_known: bool = True
    # The recent posts the reader actually saw, in grid order (newest first,
    # except pinned posts). Each is a dict:
    #   {"type": "image" | "video" | "carousel" | "",   "" = not readable
    #    "caption": str | None,                          None = not readable
    #    "likes": int | None, "comments": int | None,    None = not readable
    #    "ts": int | None,                               unix seconds (UTC), None = not readable
    #    "code": str | None}                             post shortcode (public Instagram read only)
    # None always means "not read", never 0. Readers may add optional keys
    # (e.g. "comment_texts": list[str]); consumers must use .get().
    recent_posts: list[dict] = field(default_factory=list)
    # True only when EVERY recent post's caption / type was readable. The
    # caption and format checks in analyzer/social_audit.py run only then.
    captions_known: bool = False
    formats_known: bool = False
    # True when likes AND comments were read: for every recent post (the
    # logged-in session reader), or for at least 4 recent posts through their
    # public embed pages (a public Instagram read; the other posts keep
    # likes/comments None). avg_engagement_rate then covers the posts read
    # only. Per-post checks must still skip posts whose counts are None.
    engagement_known: bool = False
    # Story highlights shown on the profile, None when not read.
    highlights_count: int | None = None
    # True only when `followers` is the exact count (Instagram JSON), not a
    # rounded display value like "12K" or a rounded API figure.
    followers_exact: bool = False


def classify_frequency(posts_last_30_days: int) -> str:
    """Map post count in the last 30 days to a human label (same bands as
    scrapers/instagram.py's InstagramScraper._classify_frequency)."""
    if posts_last_30_days >= 20:
        return "daily"
    if posts_last_30_days >= 8:
        return "2-3x per week"
    if posts_last_30_days >= 4:
        return "weekly"
    if posts_last_30_days >= 1:
        return "irregular"
    return "inactive (no posts in 30 days)"


def unanalyzed(platform: str, handle: str, url: str, note: str) -> SocialProfile:
    """A profile the adapter reached but could not analyze — surfaced in the
    UI as a note, never as an error or a fabricated issue."""
    return SocialProfile(platform=platform, handle=handle, url=url, analyzed=False, note=note)
