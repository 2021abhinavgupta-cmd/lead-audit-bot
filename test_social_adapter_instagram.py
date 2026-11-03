from scrapers.instagram import InstagramData
from scrapers.social.instagram import fetch_instagram


class _FakeScraper:
    def __init__(self, data):
        self._data = data

    def get_instagram_data(self, handle):
        return self._data


def _ig_data(**over):
    base = dict(
        username="acme", followers=4000, following=300, posts_count=88,
        bio="Handmade candles. Link below.", posts_last_30_days=10,
        avg_likes=120.0, avg_comments=8.0, engagement_rate=3.2,
        uses_reels=True, has_link_in_bio=True, posting_frequency="2-3x per week",
        sample_captions=["new drop", "restock soon"],
        content_types={"reels": 4, "carousel": 3, "image": 3},
    )
    base.update(over)
    return InstagramData(**base)


def test_maps_instagram_data_to_socialprofile():
    p = fetch_instagram("acme", scraper=_FakeScraper(_ig_data()))
    assert p.platform == "instagram"
    assert p.handle == "acme"
    assert p.url == "https://instagram.com/acme"
    assert p.followers == 4000
    assert p.following == 300
    assert p.posts_count == 88
    assert p.posts_last_30_days == 10
    assert p.posting_frequency == "2-3x per week"
    assert p.avg_engagement_rate == 3.2
    assert p.uses_video is True
    assert p.has_link_in_bio is True
    assert p.sample_captions == ["new drop", "restock soon"]
    assert p.analyzed is True


def test_none_when_profile_not_found():
    assert fetch_instagram("ghost", scraper=_FakeScraper(None)) is None


def test_strips_leading_at_sign():
    p = fetch_instagram("@acme", scraper=_FakeScraper(_ig_data()))
    assert p.handle == "acme"
    assert p.url == "https://instagram.com/acme"


# ---------------------------------------------------------------------------
# web reader first, legacy instagrapi scraper only when the web reader crashes
# ---------------------------------------------------------------------------

from scrapers.social import instagram as ig_adapter
from scrapers.social.base import SocialProfile, unanalyzed


class _CountingScraper(_FakeScraper):
    def __init__(self, data):
        super().__init__(data)
        self.calls = 0

    def get_instagram_data(self, handle):
        self.calls += 1
        return self._data


def test_web_reader_result_is_used_and_legacy_not_called():
    web = SocialProfile(platform="instagram", handle="acme", url="https://www.instagram.com/acme/",
                        followers=559, activity_known=False, note="Limited view")
    legacy = _CountingScraper(_ig_data())
    p = fetch_instagram("acme", scraper=legacy, web_reader=lambda h: web)
    assert p is web
    assert legacy.calls == 0


def test_web_reader_unanalyzed_is_returned_not_retried_with_legacy():
    note = unanalyzed("instagram", "acme", "u", "This Instagram account is private")
    legacy = _CountingScraper(_ig_data())
    p = fetch_instagram("acme", scraper=legacy, web_reader=lambda h: note)
    assert p.analyzed is False and "private" in p.note
    assert legacy.calls == 0


def test_web_reader_crash_falls_back_to_legacy():
    def boom(h):
        raise RuntimeError("unexpected")
    legacy = _CountingScraper(_ig_data())
    p = fetch_instagram("acme", scraper=legacy, web_reader=boom)
    assert legacy.calls == 1
    assert p.followers == 4000


def test_web_reader_crash_and_legacy_none_is_none():
    def boom(h):
        raise RuntimeError("unexpected")
    assert fetch_instagram("ghost", scraper=_FakeScraper(None), web_reader=boom) is None


def test_default_path_uses_read_instagram(monkeypatch):
    seen = []

    def fake_read(handle):
        seen.append(handle)
        return SocialProfile(platform="instagram", handle=handle, url="u")

    monkeypatch.setattr(ig_adapter, "read_instagram", fake_read)
    p = fetch_instagram("@acme")
    assert seen == ["acme"]
    assert p.handle == "acme"


def test_web_reader_receives_handle_without_at():
    seen = []
    fetch_instagram("@acme", web_reader=lambda h: seen.append(h) or SocialProfile("instagram", h, "u"))
    assert seen == ["acme"]
