"""
Instagram adapter.

Primary path: scrapers/social/instagram_web.py's read_instagram (spare
Playwright session when one is saved, else the public logged-out page). It
always returns a SocialProfile, analyzed=False with a note when Instagram
could not be read.

Legacy path: the instagrapi InstagramScraper in scrapers/instagram.py, kept
only as a fallback for when the web reader itself crashes unexpectedly
(instagrapi's Client.login currently crashes before sending credentials, so
in practice this path returns None).

Test seam: passing `scraper=` without `web_reader=` runs the legacy path
alone (the original behaviour, no browser); pass `web_reader=` to inject a
fake web reader.
"""

from scrapers.instagram import InstagramScraper
from scrapers.social.base import SocialProfile
from scrapers.social.instagram_web import read_instagram

_scraper = None
_UNSET = object()


def _legacy_scraper():
    global _scraper
    if _scraper is None:
        _scraper = InstagramScraper()
    return _scraper


def _legacy(handle: str, sc) -> SocialProfile | None:
    data = sc.get_instagram_data(handle)
    if data is None:
        return None
    return SocialProfile(
        platform="instagram",
        handle=handle,
        url=f"https://instagram.com/{handle}",
        display_name=data.username,
        bio=data.bio,
        followers=data.followers,
        following=data.following,
        posts_count=data.posts_count,
        posts_last_30_days=data.posts_last_30_days,
        posting_frequency=data.posting_frequency,
        avg_engagement_rate=data.engagement_rate,
        uses_video=data.uses_reels,
        has_link_in_bio=data.has_link_in_bio,
        sample_captions=list(data.sample_captions),
    )


def fetch_instagram(handle: str, scraper=None, web_reader=_UNSET) -> SocialProfile | None:
    """Return a SocialProfile for *handle* (analyzed=False with a note when
    Instagram could not be read), or None only when both the web reader
    crashed and the legacy scraper found nothing."""
    handle = handle.lstrip("@").strip()
    if web_reader is _UNSET:
        web_reader = None if scraper is not None else (lambda h: read_instagram(h))

    if web_reader is not None:
        try:
            return web_reader(handle)
        except Exception as e:  # noqa: BLE001  (unexpected bug: fall back, never crash the audit)
            print(f"[Instagram] web reader crashed, trying the legacy scraper: {e!r}")

    try:
        return _legacy(handle, scraper or _legacy_scraper())
    except Exception as e:  # noqa: BLE001
        print(f"[Instagram] legacy scraper failed too: {e!r}")
        return None
