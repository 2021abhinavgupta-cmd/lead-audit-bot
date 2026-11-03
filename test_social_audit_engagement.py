"""Phase B session-only checks: engagement against a size band, comments per
post, and the image vs video / carousel comparison inside the account. They
need likes and comments, which only the logged-in session reader sees
(engagement_known=True); a public read must never produce any of them."""

from datetime import datetime, timedelta, timezone

import pytest

from analyzer.social_audit import audit_profile, engagement_band
from scrapers.social.base import SocialProfile

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
_DASHES = ("-", "‐", "‑", "‒", "–", "—", "―", "−")


def _post(type_, likes, comments, days_ago=3):
    return {"type": type_, "caption": "Order via the link in bio #candles", "likes": likes,
            "comments": comments, "ts": int((NOW - timedelta(days=days_ago)).timestamp())}


def _p(**over):
    base = dict(
        platform="instagram", handle="acme", url="u",
        bio="Handmade candles from our studio in Pune. Order on WhatsApp or tap the link below.",
        followers=5000, following=800, posts_count=120, posts_last_30_days=10,
        posting_frequency="2-3x per week", uses_video=True, has_link_in_bio=True,
        last_post_age_days=2, activity_known=True, avg_engagement_rate=2.0,
        engagement_known=True,
        recent_posts=[_post("video", 90, 10, d) for d in (2, 5, 8, 11, 14, 17)],
        captions_known=True, formats_known=True,
    )
    base.update(over)
    return SocialProfile(**base)


def _labels(p):
    return [i.label for i in audit_profile(p)]


def _no_dashes(issues):
    for i in issues:
        assert not any(d in i.detail or d in i.label for d in _DASHES), i


def test_healthy_session_profile_is_clean():
    assert audit_profile(_p()) == []


# ---------------------------------------------------------------------------
# engagement vs size band
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("followers,band", [
    (600, "under 1k"), (999, "under 1k"), (1000, "1k to 10k"), (9999, "1k to 10k"),
    (10_000, "10k to 50k"), (49_999, "10k to 50k"), (50_000, "50k plus"), (2_000_000, "50k plus"),
])
def test_engagement_band(followers, band):
    assert engagement_band(followers)[0] == band


@pytest.mark.parametrize("followers,rate,flag", [
    (800, 0.9, True), (800, 1.2, False),            # small accounts: typical is high
    (5000, 0.4, True), (5000, 0.6, False),
    (20_000, 0.25, True), (20_000, 0.4, False),
    (100_000, 0.1, True), (100_000, 0.2, False),    # big accounts: typical is low
])
def test_low_engagement_uses_size_bands(followers, rate, flag):
    p = _p(followers=followers, avg_engagement_rate=rate,
           recent_posts=[_post("video", 50, 5, d) for d in (2, 5, 8, 11, 14, 17)])
    assert ("Low engagement" in _labels(p)) is flag


def test_low_engagement_wording_makes_no_reach_or_number_claim():
    issues = audit_profile(_p(avg_engagement_rate=0.1))
    hit = [i for i in issues if i.label == "Low engagement"][0]
    assert hit.severity == "medium"
    assert "typical" in hit.detail
    assert "seen" not in hit.detail and "reach" not in hit.detail
    assert not any(ch.isdigit() for ch in hit.detail)
    _no_dashes(issues)


def test_low_engagement_skipped_for_small_or_unknown():
    assert "Low engagement" not in _labels(_p(followers=400, avg_engagement_rate=0.1))
    assert "Low engagement" not in _labels(_p(engagement_known=False, avg_engagement_rate=0.1))
    assert "Low engagement" not in _labels(_p(platform="youtube", avg_engagement_rate=0.1))


# ---------------------------------------------------------------------------
# comments per post
# ---------------------------------------------------------------------------

def test_very_few_comments_flagged():
    posts = [_post("video", 200, 0, d) for d in (2, 5, 8, 11, 14, 17)]
    posts[0]["comments"] = 2   # avg 0.33 comments
    issues = audit_profile(_p(followers=5000, avg_engagement_rate=4.0, recent_posts=posts))
    hit = [i for i in issues if i.label == "Very few comments"]
    assert len(hit) == 1 and hit[0].severity == "low"
    _no_dashes(issues)


def test_comments_check_boundaries_and_skips():
    posts = [_post("video", 200, 1, d) for d in (2, 5, 8, 11, 14, 17)]   # avg exactly 1
    assert "Very few comments" not in _labels(_p(recent_posts=posts))
    few = [_post("video", 200, 0, d) for d in (2, 5, 8, 11, 14, 17)]
    assert "Very few comments" not in _labels(_p(followers=900, recent_posts=few))
    assert "Very few comments" not in _labels(_p(engagement_known=False, recent_posts=few))
    # not stacked on Low engagement
    labels = _labels(_p(avg_engagement_rate=0.1, recent_posts=few))
    assert "Low engagement" in labels and "Very few comments" not in labels


# ---------------------------------------------------------------------------
# format comparison inside the account
# ---------------------------------------------------------------------------

def _mixed(image=(10, 1), video=(40, 4), carousel=None, n=3):
    posts = []
    day = 1
    for type_, val in (("image", image), ("video", video), ("carousel", carousel)):
        if val is None:
            continue
        for _ in range(n):
            posts.append(_post(type_, val[0], val[1], day))
            day += 3
    return posts


def test_image_posts_underperform_video():
    issues = audit_profile(_p(recent_posts=_mixed()))
    hit = [i for i in issues if "underperform" in i.label]
    assert len(hit) == 1
    assert hit[0].label == "Image posts underperform video posts on this account"
    _no_dashes(issues)


def test_image_posts_underperform_carousels_and_both():
    p = _p(recent_posts=_mixed(video=None, carousel=(30, 3)))
    assert "Image posts underperform carousel posts on this account" in _labels(p)
    p = _p(recent_posts=_mixed(carousel=(30, 3)))
    assert "Image posts underperform video and carousel posts on this account" in _labels(p)


def test_format_comparison_needs_a_clear_gap_and_three_posts_each():
    assert not any("underperform" in lbl for lbl in _labels(_p(recent_posts=_mixed(video=(14, 1)))))
    assert not any("underperform" in lbl for lbl in _labels(_p(recent_posts=_mixed(n=2))))
    posts = _mixed()
    assert not any("underperform" in lbl for lbl in _labels(_p(recent_posts=posts, engagement_known=False)))
    posts = _mixed()
    posts[0]["type"] = ""
    assert not any("underperform" in lbl for lbl in _labels(_p(recent_posts=posts, formats_known=False)))


def test_public_read_never_gets_engagement_checks_even_with_counts_lying_around():
    p = _p(engagement_known=False, avg_engagement_rate=0.0,
           recent_posts=[dict(x, likes=None, comments=None) for x in _mixed()])
    labels = _labels(p)
    assert "Low engagement" not in labels and "Very few comments" not in labels
    assert not any("underperform" in lbl for lbl in labels)
