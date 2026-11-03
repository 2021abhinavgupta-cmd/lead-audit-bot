"""Phase A public-page checks in analyzer/social_audit.py (bio call to action,
bio says what/where, bio link health, captions, formats, posting rhythm,
posts vs followers). Every check: fires on the bad case, stays quiet on the
good case, is skipped when its data was not read, and its wording has no
dashes. No network here: the bio link verdict is passed in."""

from datetime import datetime, timedelta, timezone

from analyzer.social_audit import audit_profile
from scrapers.social.base import SocialProfile

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
GOOD_BIO = "Handmade candles from our studio in Pune. Order on WhatsApp or tap the link below."
_DASHES = ("-", "‐", "‑", "‒", "–", "—", "―", "−")


def _ts(days_ago):
    return int((NOW - timedelta(days=days_ago)).timestamp())


def _posts(days_ago, type_="video", caption="New batch is out, order yours via the link in bio #candles"):
    return [{"type": type_, "caption": caption, "likes": None, "comments": None, "ts": _ts(d)}
            for d in days_ago]


EVEN = [2, 5, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35]


def _p(**over):
    base = dict(
        platform="instagram", handle="acme", url="u",
        bio=GOOD_BIO, followers=5000, following=800, posts_count=120,
        posts_last_30_days=10, posting_frequency="2-3x per week",
        uses_video=True, has_link_in_bio=True, link_url="https://acmecandles.in/",
        last_post_age_days=2, activity_known=True,
        recent_posts=_posts(EVEN), captions_known=True, formats_known=True,
    )
    base.update(over)
    return SocialProfile(**base)


def _labels(issues):
    return [i.label for i in issues]


def _no_dashes(issues):
    for i in issues:
        for d in _DASHES:
            assert d not in i.detail, (i.label, i.detail)
            assert d not in i.label, i.label


def test_baseline_profile_is_clean():
    assert audit_profile(_p(), website="https://acmecandles.in", link_check={"verdict": "ok"}) == []


# ---------------------------------------------------------------------------
# 1. bio call to action
# ---------------------------------------------------------------------------

def test_bio_without_call_to_action_is_flagged_medium():
    issues = audit_profile(_p(bio="Handmade soy candles from our little studio in Pune."))
    hit = [i for i in issues if i.label == "No call to action in the bio"]
    assert len(hit) == 1 and hit[0].severity == "medium"
    _no_dashes(issues)


def test_bio_contact_routes_count_as_call_to_action():
    for bio in (
        "Handmade soy candles from our studio in Pune. DM to order",
        "Handmade soy candles from our studio in Pune. hello@acme.in",
        "Handmade soy candles from our studio in Pune. +91 98765 43210",
        "Handmade soy candles from our studio in Pune \U0001F447",
        "Handmade soy candles from our studio in Pune. Bookings open",
        "Handmade soy candles from our studio in Pune. WhatsApp us",
        "Wanna work with us? Mail to noorish@mmga.agency",
    ):
        assert "No call to action in the bio" not in _labels(audit_profile(_p(bio=bio))), bio


def test_bio_call_to_action_word_inside_another_word_does_not_count():
    # "recall" holds "call", "bookish" holds "book": not a call to action
    issues = audit_profile(_p(bio="Bookish candles that recall slow Sunday mornings in Pune."))
    assert "No call to action in the bio" in _labels(issues)


# ---------------------------------------------------------------------------
# 2. bio says what / where (merged with the thin bio check)
# ---------------------------------------------------------------------------

def test_bio_without_what_or_where_is_flagged_low():
    issues = audit_profile(_p(bio="Living the dream, one day at a time ✨ DM for more"))
    hit = [i for i in issues if i.label == "Bio does not say what or where"]
    assert len(hit) == 1 and hit[0].severity == "low"
    _no_dashes(issues)


def test_bio_with_service_or_location_is_not_flagged():
    for bio in (
        "Handmade candles, DM to order",            # what
        "Dreaming big ✨ \U0001F4CDMumbai. DM us",   # where
        "Good vibes only, based in Goa. DM us",      # where
    ):
        assert "Bio does not say what or where" not in _labels(audit_profile(_p(bio=bio))), bio


def test_short_bio_gets_only_the_thin_bio_issue_not_both():
    labels = _labels(audit_profile(_p(bio="hi there")))
    assert "Thin or empty bio" in labels
    assert "Bio does not say what or where" not in labels


# ---------------------------------------------------------------------------
# 3. bio link
# ---------------------------------------------------------------------------

def test_broken_link_is_high_and_ranked_first():
    issues = audit_profile(_p(), link_check={"verdict": "broken", "status": 404})
    assert issues[0].label == "Link in bio is broken"
    assert issues[0].severity == "high"
    _no_dashes(issues)


def test_unknown_link_verdict_never_claims_broken():
    for lc in (None, {"verdict": "unknown"}, {}):
        assert "Link in bio is broken" not in _labels(audit_profile(_p(), link_check=lc))


def test_link_aggregator_is_low_and_not_also_a_wrong_site():
    issues = audit_profile(_p(link_url="https://linktr.ee/acme"), website="https://acmecandles.in",
                           link_check={"verdict": "ok", "final_url": "https://linktr.ee/acme"})
    labels = _labels(issues)
    assert "Link in bio is a link list page" in labels
    assert "Link in bio skips the website" not in labels
    assert [i.severity for i in issues if i.label == "Link in bio is a link list page"] == ["low"]
    _no_dashes(issues)


def test_aggregator_detected_on_subdomains_and_other_services():
    for url in ("https://beacons.ai/acme", "https://bio.link/acme", "https://www.linktr.ee/acme",
                "https://taplink.cc/acme", "https://lnk.bio/acme"):
        assert "Link in bio is a link list page" in _labels(audit_profile(_p(link_url=url))), url


def test_link_to_another_site_when_website_known_is_low():
    issues = audit_profile(_p(link_url="https://shop.otherplatform.com/acme"), website="https://acmecandles.in")
    hit = [i for i in issues if i.label == "Link in bio skips the website"]
    assert len(hit) == 1 and hit[0].severity == "low"
    _no_dashes(issues)


def test_link_to_own_site_matches_on_registered_domain():
    for link in ("https://www.acmecandles.in/shop", "http://acmecandles.in", "https://shop.acmecandles.in/x"):
        issues = audit_profile(_p(link_url=link), website="acmecandles.in")
        assert "Link in bio skips the website" not in _labels(issues), link
    # two level public suffixes compare on the name, not on "co.uk"
    issues = audit_profile(_p(link_url="https://other.co.uk/"), website="https://acme.co.uk")
    assert "Link in bio skips the website" in _labels(issues)


def test_own_site_check_follows_the_redirect_target():
    issues = audit_profile(_p(link_url="https://bit.ly/x"), website="https://acmecandles.in",
                           link_check={"verdict": "ok", "final_url": "https://acmecandles.in/home"})
    assert "Link in bio skips the website" not in _labels(issues)


def test_shortened_link_without_a_known_target_is_not_called_a_wrong_site():
    for lc in (None, {"verdict": "unknown"}):
        issues = audit_profile(_p(link_url="https://bit.ly/x"), website="https://acmecandles.in", link_check=lc)
        assert "Link in bio skips the website" not in _labels(issues)


def test_own_site_check_skipped_without_a_known_website_or_for_whatsapp():
    assert "Link in bio skips the website" not in _labels(audit_profile(_p(link_url="https://other.com")))
    issues = audit_profile(_p(link_url="https://wa.me/919876543210"), website="https://acmecandles.in")
    assert "Link in bio skips the website" not in _labels(issues)


def test_no_link_checks_without_a_link():
    issues = audit_profile(_p(has_link_in_bio=False, link_url=""), website="https://acmecandles.in",
                           link_check={"verdict": "broken"})
    labels = _labels(issues)
    assert "No link in the bio" in labels
    assert not any(lbl.startswith("Link in bio") for lbl in labels)


# ---------------------------------------------------------------------------
# 4. captions
# ---------------------------------------------------------------------------

def test_many_empty_captions_flagged():
    posts = _posts(EVEN[:6], caption="") + _posts(EVEN[6:])
    issues = audit_profile(_p(recent_posts=posts))
    assert "Many posts have no caption" in _labels(issues)
    _no_dashes(issues)


def test_few_empty_captions_not_flagged():
    posts = _posts(EVEN[:2], caption="") + _posts(EVEN[2:])
    assert "Many posts have no caption" not in _labels(audit_profile(_p(recent_posts=posts)))


def test_captions_without_any_call_to_action_flagged():
    posts = _posts(EVEN, caption="A quiet evening with our lavender candle #candles")
    issues = audit_profile(_p(recent_posts=posts))
    assert "Captions never ask for action" in _labels(issues)
    _no_dashes(issues)


def test_one_caption_with_call_to_action_is_enough():
    posts = _posts(EVEN[:11], caption="A quiet evening #candles") + _posts(EVEN[11:], caption="DM us to order #candles")
    assert "Captions never ask for action" not in _labels(audit_profile(_p(recent_posts=posts)))


def test_no_hashtags_and_too_many_hashtags():
    posts = _posts(EVEN, caption="Order yours today via the link in bio")
    assert "No hashtags on recent posts" in _labels(audit_profile(_p(recent_posts=posts)))
    wall = "Order now " + " ".join(f"#tag{i}" for i in range(20))
    issues = audit_profile(_p(recent_posts=_posts(EVEN, caption=wall)))
    assert "Too many hashtags" in _labels(issues)
    assert "No hashtags on recent posts" not in _labels(issues)
    _no_dashes(issues)


def test_caption_checks_skipped_when_captions_unread():
    posts = _posts(EVEN, caption="")
    issues = audit_profile(_p(recent_posts=posts, captions_known=False))
    labels = _labels(issues)
    for lbl in ("Many posts have no caption", "Captions never ask for action",
                "No hashtags on recent posts", "Too many hashtags"):
        assert lbl not in labels


def test_caption_checks_need_at_least_three_posts():
    posts = _posts([2, 5], caption="quiet evening")
    labels = _labels(audit_profile(_p(recent_posts=posts)))
    assert "Captions never ask for action" not in labels
    assert "No hashtags on recent posts" not in labels


# ---------------------------------------------------------------------------
# 5. formats (one issue, shared with "Not using short video")
# ---------------------------------------------------------------------------

def test_all_static_posts_gives_exactly_one_video_issue():
    posts = _posts(EVEN[:6], type_="image") + _posts(EVEN[6:], type_="carousel")
    issues = audit_profile(_p(recent_posts=posts, uses_video=False))
    video = [i for i in issues if "video" in i.label.lower()]
    assert len(video) == 1 and video[0].label == "Not using short video"
    _no_dashes(issues)


def test_any_video_in_recent_posts_means_no_video_issue():
    posts = _posts(EVEN[:11], type_="image") + _posts(EVEN[11:], type_="video")
    assert "Not using short video" not in _labels(audit_profile(_p(recent_posts=posts)))


def test_formats_known_flags_static_posts_even_when_dates_were_not_read():
    posts = [dict(x, ts=None) for x in _posts(EVEN, type_="image")]
    issues = audit_profile(_p(recent_posts=posts, uses_video=False, activity_known=False,
                              posts_last_30_days=0, last_post_age_days=None))
    assert _labels(issues).count("Not using short video") == 1


def test_format_check_skipped_when_formats_unread_and_activity_unknown():
    posts = [dict(x, type="", ts=None) for x in _posts(EVEN)]
    issues = audit_profile(_p(recent_posts=posts, formats_known=False, uses_video=False,
                              activity_known=False, posts_last_30_days=0, last_post_age_days=None))
    assert "Not using short video" not in _labels(issues)


# ---------------------------------------------------------------------------
# 6. posting rhythm
# ---------------------------------------------------------------------------

def test_long_gap_between_posts_flagged_medium():
    days = [2, 5, 8, 11, 14, 50, 53, 56, 59, 62, 65, 68]   # a 36 day gap
    issues = audit_profile(_p(recent_posts=_posts(days)))
    hit = [i for i in issues if i.label == "Uneven posting schedule"]
    assert len(hit) == 1 and hit[0].severity == "medium"
    _no_dashes(issues)


def test_burst_of_posts_in_one_week_flagged():
    days = [1, 2, 3, 4, 5, 6, 7, 20, 26, 33, 40, 44]   # 7 of 12 in one week, span 43 days
    assert "Uneven posting schedule" in _labels(audit_profile(_p(recent_posts=_posts(days))))


def test_daily_posting_is_not_a_burst():
    days = list(range(1, 13))   # 12 posts in 12 days: steady, not a burst
    assert "Uneven posting schedule" not in _labels(audit_profile(_p(recent_posts=_posts(days))))


def test_even_posting_not_flagged():
    assert "Uneven posting schedule" not in _labels(audit_profile(_p()))


def test_old_pinned_post_at_the_top_is_not_a_gap():
    days = [400] + EVEN[:11]   # pinned year old post first in the grid
    assert "Uneven posting schedule" not in _labels(audit_profile(_p(recent_posts=_posts(days))))


def test_rhythm_skipped_when_activity_unknown_or_dates_missing():
    days = [2, 5, 8, 11, 14, 50, 53, 56, 59, 62, 65, 68]
    issues = audit_profile(_p(recent_posts=_posts(days), activity_known=False))
    assert "Uneven posting schedule" not in _labels(issues)
    posts = _posts(days)
    posts[3]["ts"] = None
    assert "Uneven posting schedule" not in _labels(audit_profile(_p(recent_posts=posts)))


def test_rhythm_not_stacked_on_inactive_account():
    days = [60, 63, 66, 69, 72, 110, 113, 116, 119, 122, 125, 128]
    issues = audit_profile(_p(recent_posts=_posts(days), posts_last_30_days=0, last_post_age_days=60))
    labels = _labels(issues)
    assert "Inactive account" in labels
    assert "Uneven posting schedule" not in labels


# ---------------------------------------------------------------------------
# 7. many posts, few followers
# ---------------------------------------------------------------------------

def test_many_posts_few_followers_flagged_low_and_soft():
    issues = audit_profile(_p(posts_count=300, followers=1200))
    hit = [i for i in issues if i.label == "Audience not growing with the posts"]
    assert len(hit) == 1 and hit[0].severity == "low"
    _no_dashes(issues)


def test_posts_vs_followers_boundaries():
    assert "Audience not growing with the posts" not in _labels(audit_profile(_p(posts_count=49, followers=100)))
    assert "Audience not growing with the posts" not in _labels(audit_profile(_p(posts_count=100, followers=800)))
    # header only read where the post count was not read
    assert "Audience not growing with the posts" not in _labels(
        audit_profile(_p(posts_count=0, followers=10, activity_known=False)))


# ---------------------------------------------------------------------------
# public only profile: nothing that needs likes or comments, ever
# ---------------------------------------------------------------------------

def test_public_only_read_never_yields_engagement_issues():
    p = _p(engagement_known=False, avg_engagement_rate=0.0, followers=20000)
    labels = _labels(audit_profile(p))
    for bad in ("Low engagement", "Very few comments"):
        assert bad not in labels
    assert not any("underperform" in lbl for lbl in labels)
