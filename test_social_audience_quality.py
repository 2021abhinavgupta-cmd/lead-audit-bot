"""analyzer/audience_quality.py: deterministic audience quality from known data only."""

import re

import pytest

from analyzer import audience_quality as aqm
from analyzer.audience_quality import audience_quality, outreach_issue
from scrapers.social.base import SocialProfile

ACCUSATION = re.compile(r"\b(fake|fakes|bot|bots|bought|purchased|pod|pods|scam|scams)\b", re.I)
DASHES = ("-", "‐", "‑", "‒", "–", "—", "―", "−")


def _post(likes, comments, **extra):
    return {"type": "video", "caption": "x", "likes": likes, "comments": comments, "ts": None, **extra}


def _p(**kw):
    base = dict(platform="instagram", handle="acme", url="https://www.instagram.com/acme/",
                followers=3000, following=300, posts_count=80, engagement_known=True,
                followers_exact=True,
                recent_posts=[_post(l, c) for l, c in ((90, 4), (110, 6), (80, 3), (100, 5), (95, 2), (120, 7))])
    base.update(kw)
    return SocialProfile(**base)


def _keys(r):
    return [s["key"] for s in r["signals"]]


# ---------------------------------------------------------------------------
# healthy / unknown
# ---------------------------------------------------------------------------

def test_healthy_account_has_no_signals():
    r = audience_quality(_p())
    assert r["label"] == "healthy"
    assert r["score"] == 100
    assert r["signals"] == []


def test_unknown_when_profile_missing_or_unanalyzed():
    assert audience_quality(None)["label"] == "unknown"
    r = audience_quality(SocialProfile(platform="instagram", handle="x", url="u", analyzed=False))
    assert r == {"score": None, "label": "unknown", "signals": []}


def test_unknown_without_likes_and_comments():
    # a plain public read: counts are None, never treated as 0
    posts = [_post(None, None) for _ in range(12)]
    r = audience_quality(_p(engagement_known=False, recent_posts=posts))
    assert r["label"] == "unknown" and r["score"] is None
    assert r["signals"] == []


def test_unknown_with_fewer_than_four_posts_read():
    posts = [_post(1, 0), _post(2, 0), _post(1, 0)] + [_post(None, None)] * 9
    r = audience_quality(_p(followers=20_000, recent_posts=posts))
    assert r["label"] == "unknown"
    assert "likes_low_for_audience" not in _keys(r)


def test_posts_with_unknown_counts_are_ignored_not_zeroed():
    posts = [_post(100, 5)] * 4 + [_post(None, None)] * 8
    r = audience_quality(_p(recent_posts=posts))
    assert r["label"] == "healthy"


# ---------------------------------------------------------------------------
# likes low for the audience size
# ---------------------------------------------------------------------------

def test_likes_low_for_audience_size():
    # 20k followers (band 10k to 50k, floor 0.3%): about 10 likes a post is far below
    posts = [_post(l, 1) for l in (10, 12, 9, 11, 10, 8)]
    r = audience_quality(_p(followers=20_000, recent_posts=posts))
    assert "likes_low_for_audience" in _keys(r)
    assert r["label"] in ("mixed", "suspect")
    assert r["score"] < 100


def test_likes_fine_for_small_account():
    posts = [_post(l, 2) for l in (40, 44, 38, 50)]
    assert "likes_low_for_audience" not in _keys(audience_quality(_p(followers=560, recent_posts=posts)))


def test_likes_low_skipped_for_tiny_accounts():
    posts = [_post(1, 0) for _ in range(6)]
    assert "likes_low_for_audience" not in _keys(audience_quality(_p(followers=400, recent_posts=posts)))


def test_likes_low_uses_the_median_so_one_hit_does_not_hide_it():
    posts = [_post(l, 1) for l in (900, 10, 12, 9, 11, 10)]
    assert "likes_low_for_audience" in _keys(audience_quality(_p(followers=20_000, recent_posts=posts)))


# ---------------------------------------------------------------------------
# likes but no comments
# ---------------------------------------------------------------------------

def test_likes_without_comments_on_nearly_every_post():
    posts = [_post(l, 0) for l in (300, 280, 310, 290, 305)] + [_post(295, 1)]
    r = audience_quality(_p(recent_posts=posts))
    assert "likes_no_comments" in _keys(r)
    # not stacked with the extremely low ratio signal
    assert "comment_ratio_low" not in _keys(r)


def test_likes_without_comments_needs_five_posts():
    posts = [_post(300, 0) for _ in range(4)]
    assert "likes_no_comments" not in _keys(audience_quality(_p(recent_posts=posts)))


def test_likes_without_comments_needs_likes_clearly_present():
    posts = [_post(3, 0) for _ in range(6)]
    assert "likes_no_comments" not in _keys(audience_quality(_p(followers=300, recent_posts=posts)))


# ---------------------------------------------------------------------------
# comment to like ratio
# ---------------------------------------------------------------------------

def test_comment_ratio_extremely_high():
    posts = [_post(l, c) for l, c in ((40, 35), (50, 40), (45, 30), (38, 33), (42, 36))]
    r = audience_quality(_p(followers=2000, recent_posts=posts))
    assert "comment_ratio_high" in _keys(r)


def test_comment_ratio_extremely_low():
    # comments on most posts, but a tiny share of likes
    posts = [_post(5000, c) for c in (1, 2, 1, 0)]
    r = audience_quality(_p(followers=40_000, recent_posts=posts))
    assert "comment_ratio_low" in _keys(r)


def test_comment_ratio_normal():
    keys = _keys(audience_quality(_p()))
    assert "comment_ratio_low" not in keys and "comment_ratio_high" not in keys


# ---------------------------------------------------------------------------
# engagement spike
# ---------------------------------------------------------------------------

def test_spike_one_post_far_above_flat_others():
    posts = [_post(l, 3) for l in (900, 100, 110, 95, 105, 100)]
    assert "engagement_spike" in _keys(audience_quality(_p(recent_posts=posts)))


def test_no_spike_when_the_rest_are_not_flat():
    posts = [_post(l, 3) for l in (900, 30, 300, 60, 250, 100)]
    assert "engagement_spike" not in _keys(audience_quality(_p(recent_posts=posts)))


def test_no_spike_on_tiny_numbers_or_few_posts():
    assert "engagement_spike" not in _keys(audience_quality(
        _p(followers=300, recent_posts=[_post(l, 1) for l in (10, 2, 2, 2, 2)])))
    assert "engagement_spike" not in _keys(audience_quality(
        _p(recent_posts=[_post(l, 3) for l in (900, 100, 110, 95)])))


# ---------------------------------------------------------------------------
# public data signals
# ---------------------------------------------------------------------------

def test_follow_for_follow_works_on_public_data():
    posts = [_post(None, None) for _ in range(12)]
    r = audience_quality(_p(followers=400, following=2500, engagement_known=False, recent_posts=posts))
    assert "follow_for_follow" in _keys(r)
    # no engagement data: never "suspect" or "healthy" from public data alone
    assert r["label"] == "mixed"
    assert isinstance(r["score"], int)


def test_follow_for_follow_needs_a_large_margin_and_a_modest_audience():
    assert "follow_for_follow" not in _keys(audience_quality(_p(followers=900, following=1200)))
    assert "follow_for_follow" not in _keys(audience_quality(_p(followers=20_000, following=60_000)))
    assert "follow_for_follow" not in _keys(audience_quality(_p(followers=100, following=400)))


def test_round_follower_count_is_weak():
    r = audience_quality(_p(followers=12_000))
    assert "round_follower_count" in _keys(r)
    sig = next(s for s in r["signals"] if s["key"] == "round_follower_count")
    assert sig["severity"] == "low"
    assert r["label"] == "healthy"   # weak on its own


def test_round_follower_count_only_on_exact_counts_above_5000():
    assert "round_follower_count" not in _keys(audience_quality(_p(followers=5000)))
    assert "round_follower_count" not in _keys(audience_quality(_p(followers=12_001)))
    # an "12K" style rounded display count is not exact
    assert "round_follower_count" not in _keys(audience_quality(_p(followers=12_000, followers_exact=False)))


def test_round_count_alone_without_engagement_stays_unknown():
    posts = [_post(None, None) for _ in range(12)]
    r = audience_quality(_p(followers=12_000, engagement_known=False, recent_posts=posts))
    assert r["label"] == "unknown"
    assert "round_follower_count" in _keys(r)


def test_readers_mark_exact_follower_counts():
    from datetime import datetime, timezone

    from scrapers.social import instagram_web as igw
    from test_social_instagram_web_parse import public_html, wpi
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert igw.parse_public_page(public_html(), "mmga.agency", now)[1].followers_exact is True
    og_only = public_html(with_profile_json=False, og="12K Followers, 300 Following, 45 Posts")
    assert igw.parse_public_page(og_only, "acme", now)[1].followers_exact is False
    assert igw.parse_web_profile_info(wpi(), "mmga.agency", now)[1].followers_exact is True
    assert SocialProfile(platform="youtube", handle="x", url="u").followers_exact is False


# ---------------------------------------------------------------------------
# comment texts
# ---------------------------------------------------------------------------

def _with_comments(texts, **kw):
    posts = [_post(100, 5, comment_texts=texts[i::4]) for i in range(4)]
    return _p(recent_posts=posts, **kw)


def test_generic_comment_texts():
    texts = ["nice", "Great pic!", "\U0001F525\U0001F525", "follow back", "wow", "❤️",
             "amazing", "Nice post", "When do you open on Sunday?"]
    assert "generic_comments" in _keys(audience_quality(_with_comments(texts)))


def test_real_comment_texts():
    texts = ["Do you deliver to Baner?", "Ordered last week, the cake was lovely and moist",
             "What time do you close?", "Can I book for Saturday", "nice", "Is the red one in stock",
             "My daughter loved the party setup", "How much for fifty cupcakes"]
    assert "generic_comments" not in _keys(audience_quality(_with_comments(texts)))


def test_generic_comments_need_eight_seen():
    texts = ["nice", "wow", "\U0001F525", "great pic", "nice", "wow", "cool"]
    assert "generic_comments" not in _keys(audience_quality(_with_comments(texts)))


def test_no_comment_texts_means_check_skipped():
    assert "generic_comments" not in _keys(audience_quality(_p()))


# ---------------------------------------------------------------------------
# labels, wording
# ---------------------------------------------------------------------------

def test_two_strong_signals_make_suspect():
    posts = [_post(l, 0) for l in (60, 55, 62, 58, 61, 57)]
    r = audience_quality(_p(followers=30_000, recent_posts=posts))
    assert {"likes_low_for_audience", "likes_no_comments"} <= set(_keys(r))
    assert r["label"] == "suspect"


def _all_signal_profiles():
    texts = ["nice", "wow", "\U0001F525", "great pic", "follow back", "amazing", "cool", "nice post"]
    return [
        _p(followers=30_000, recent_posts=[_post(l, 0) for l in (60, 55, 62, 58, 61, 57)]),
        _p(followers=2000, recent_posts=[_post(l, c) for l, c in ((40, 35), (50, 40), (45, 30), (38, 33))]),
        _p(followers=40_000, recent_posts=[_post(5000, c) for c in (1, 2, 1, 0)]),
        _p(recent_posts=[_post(l, 3) for l in (900, 100, 110, 95, 105, 100)]),
        _p(followers=400, following=2500),
        _p(followers=12_000),
        _with_comments(texts),
    ]


def test_every_signal_key_is_reachable_and_documented():
    seen = set()
    for prof in _all_signal_profiles():
        seen.update(_keys(audience_quality(prof)))
    assert seen == set(aqm.WEIGHTS)
    for key in aqm.WEIGHTS:
        assert key in aqm.__doc__


@pytest.mark.parametrize("prof", _all_signal_profiles())
def test_wording_has_no_dashes_and_no_accusations(prof):
    r = audience_quality(prof)
    assert r["signals"]
    for s in r["signals"]:
        assert set(s) == {"key", "severity", "detail"}
        assert s["severity"] in ("high", "medium", "low")
        assert not any(d in s["detail"] for d in DASHES), s["detail"]
        assert not ACCUSATION.search(s["detail"]), s["detail"]


def test_score_bounds():
    for prof in _all_signal_profiles():
        r = audience_quality(prof)
        assert r["score"] is None or 0 <= r["score"] <= 100


# ---------------------------------------------------------------------------
# the one outreach sentence
# ---------------------------------------------------------------------------

def test_outreach_issue_only_for_low_likes():
    low = audience_quality(_p(followers=20_000, recent_posts=[_post(l, 1) for l in (10, 12, 9, 11, 10, 8)]))
    issue = outreach_issue(low)
    assert issue.label == "Posts are not reaching the audience"
    assert "usually means posts are not reaching the people who follow the account" in issue.detail
    assert not any(d in issue.detail + issue.label for d in DASHES)
    assert not ACCUSATION.search(issue.detail + issue.label)
    assert outreach_issue(audience_quality(_p(followers=400, following=2500))) is None
    assert outreach_issue(audience_quality(None)) is None
    assert outreach_issue(None) is None
