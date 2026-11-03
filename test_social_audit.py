from scrapers.social.base import SocialProfile
from analyzer.social_audit import audit_profile, SocialIssue


def _healthy(**over):
    base = dict(
        platform="instagram", handle="acme", url="u",
        bio="We make artisan candles for calm homes. Shop link below.",
        followers=5000, following=800, posts_count=120,
        posts_last_30_days=12, posting_frequency="2-3x per week",
        avg_engagement_rate=2.1, uses_video=True, has_link_in_bio=True,
        last_post_age_days=3, engagement_known=True,
    )
    base.update(over)
    return SocialProfile(**base)


def test_healthy_profile_has_no_issues():
    assert audit_profile(_healthy()) == []


def test_unanalyzed_profile_yields_nothing():
    p = _healthy(analyzed=False, note="login wall")
    assert audit_profile(p) == []


def test_inactive_account_is_high_severity():
    issues = audit_profile(_healthy(posts_last_30_days=0, last_post_age_days=90))
    assert any(i.severity == "high" and "inactiv" in i.label.lower() for i in issues)


def test_low_engagement_flagged_for_instagram_not_youtube():
    ig = audit_profile(_healthy(avg_engagement_rate=0.2, followers=3000))
    assert any("engage" in i.label.lower() for i in ig)
    yt = audit_profile(_healthy(platform="youtube", avg_engagement_rate=0.2, followers=3000))
    assert not any("engage" in i.label.lower() for i in yt)


def test_no_video_flagged():
    issues = audit_profile(_healthy(uses_video=False))
    assert any("video" in i.label.lower() or "reel" in i.label.lower() for i in issues)


def test_no_link_in_bio_instagram_only():
    ig = audit_profile(_healthy(has_link_in_bio=False))
    assert any("link" in i.label.lower() for i in ig)
    li = audit_profile(_healthy(platform="linkedin", has_link_in_bio=False))
    assert not any("link" in i.label.lower() for i in li)


def test_weak_bio_flagged():
    issues = audit_profile(_healthy(bio="candles"))
    assert any("bio" in i.label.lower() for i in issues)


def test_follower_following_imbalance_instagram_small_accounts():
    issues = audit_profile(_healthy(followers=900, following=1500))
    assert any("follow" in i.label.lower() for i in issues)


def test_thin_catalogue_flagged():
    issues = audit_profile(_healthy(posts_count=4))
    assert any("thin" in i.label.lower() or "few post" in i.detail.lower() for i in issues)


def test_ranked_high_before_low():
    issues = audit_profile(_healthy(
        posts_last_30_days=0, last_post_age_days=90,  # high
        bio="candles",                                # low
    ))
    sevs = [i.severity for i in issues]
    assert sevs == sorted(sevs, key=lambda s: {"high": 0, "medium": 1, "low": 2}[s])


def test_detail_has_no_dashes():
    issues = audit_profile(_healthy(uses_video=False, has_link_in_bio=False, bio="x"))
    for i in issues:
        assert "-" not in i.detail and "—" not in i.detail and "–" not in i.detail


# ---------------------------------------------------------------------------
# activity_known=False: a public-only Instagram read has no post dates and no
# engagement, so posts_last_30_days=0 / avg_engagement_rate=0 / uses_video=False
# are UNKNOWN, not facts. None of the checks built on them may fire.
# ---------------------------------------------------------------------------

def _public_only(**over):
    base = dict(
        platform="instagram", handle="acme", url="u",
        bio="We make artisan candles for calm homes. Shop link below.",
        followers=5000, following=800, posts_count=120,
        has_link_in_bio=True, activity_known=False,
        note="Limited view: public profile data only (post dates and engagement not available)",
    )
    base.update(over)
    return SocialProfile(**base)


_ACTIVITY_LABELS = ("Inactive account", "Posting has slowed", "Low engagement", "Not using short video")


def test_public_only_profile_gets_no_activity_issues():
    issues = audit_profile(_public_only())
    assert issues == []
    labels = {i.label for i in issues}
    for bad in _ACTIVITY_LABELS:
        assert bad not in labels


def test_public_only_even_with_zero_defaults_never_says_inactive_or_low_engagement():
    # All the activity fields at their zero defaults, a profile big enough to
    # trip the engagement and cadence checks if they ran.
    issues = audit_profile(_public_only(
        followers=3000, posts_last_30_days=0, last_post_age_days=None,
        posting_frequency="", avg_engagement_rate=0.1, uses_video=False,
    ))
    labels = {i.label for i in issues}
    for bad in _ACTIVITY_LABELS:
        assert bad not in labels


def test_public_only_keeps_the_checks_that_rest_on_known_data():
    issues = audit_profile(_public_only(
        has_link_in_bio=False, bio="hi", followers=300, following=900, posts_count=4,
    ))
    labels = {i.label for i in issues}
    assert "No link in the bio" in labels
    assert "Thin or empty bio" in labels
    assert "Following more than followers" in labels
    assert "Thin content history" in labels
    for bad in _ACTIVITY_LABELS:
        assert bad not in labels


def test_activity_known_true_still_flags_inactive():
    issues = audit_profile(_healthy(posts_last_30_days=0, last_post_age_days=90, activity_known=True))
    assert any(i.label == "Inactive account" for i in issues)


def test_public_only_unread_post_count_is_not_a_thin_catalogue():
    # posts_count=0 on a public-only read means the count could not be read,
    # not that the account has no posts.
    issues = audit_profile(_public_only(posts_count=0))
    assert not any(i.label == "Thin content history" for i in issues)
