"""Engagement uses the MEDIAN likes and the MEDIAN comments per post, not the
mean: one collab reel with thousands of likes on a small account made the
Social tab show "71.51% engagement" for 558 followers. No live network."""

from scrapers.social import instagram_web as igw
from scrapers.social.base import SocialProfile, median_engagement_rate
from test_social_instagram_embed import Embeds, Public, _ctx, _read, embed_html, th, clock  # noqa: F401
from test_social_instagram_web_parse import NOW, _media, _ts, wpi


def _edges(likes):
    return [_media(_ts(2026, 10, 30 - i), n, 2) for i, n in enumerate(likes)]


def _session_rate(likes):
    payload = wpi(edge_owner_to_timeline_media={"count": len(likes), "edges": _edges(likes)})
    _, p = igw.parse_web_profile_info(payload, "acme", NOW)
    return p.avg_engagement_rate


def test_helper_is_median_of_likes_plus_median_of_comments():
    assert median_engagement_rate([40, 40, 40, 40, 40, 5000], [2, 2, 2, 2, 2, 90], 1000) == 4.2
    assert median_engagement_rate([], [], 1000) == 0.0
    assert median_engagement_rate([10], [1], 0) == 0.0


def test_session_reader_outlier_post_does_not_move_the_rate():
    base = _session_rate([40, 40, 40, 40, 40])
    with_outlier = _session_rate([40, 40, 40, 40, 40, 5000])
    assert base == with_outlier == round((40 + 2) / 559 * 100, 2)


def test_session_reader_varied_posts_outlier_barely_moves_the_rate():
    base = _session_rate([38, 40, 41, 42, 39])
    with_outlier = _session_rate([38, 40, 41, 42, 39, 5000])
    assert abs(with_outlier - base) < 0.2   # the mean would have jumped to about 150%


def test_embed_reader_outlier_post_does_not_move_the_rate(th):
    # c0..c4 get 40..44 likes and 3 comments from the fake; c5 is a 5000 like collab
    outlier = embed_html(context=_ctx(likes=5000, comments=3), code="c5")
    p = _read(public_fetch=Public(), embed_fetch=Embeds(pages={"c5": outlier}), throttle=th)
    assert p.engagement_known is True
    assert p.avg_engagement_rate == round((42.5 + 3) / 559 * 100, 2)


def _profile(likes, comments):
    posts = [{"likes": lk, "comments": c} for lk, c in zip(likes, comments)]
    return SocialProfile(platform="instagram", handle="acme", url="u", followers=558,
                         recent_posts=posts, engagement_known=True,
                         avg_engagement_rate=median_engagement_rate(likes, comments, 558))


def test_measured_facts_quote_the_typical_post_not_the_average():
    from analyzer.social_audit import measured_facts
    facts, nums = measured_facts(_profile([40, 40, 40, 40, 40, 5000], [2, 2, 2, 2, 2, 400]))
    joined = " ".join(facts)
    assert "a typical post gets about 40 likes and 2 comments" in joined
    assert "averages" not in joined and "5000" not in nums and "866" not in nums
    assert "7.5 percent" in joined


def test_engagement_summary_gives_medians_and_posts_read():
    from analyzer.social_audit import engagement_summary
    s = engagement_summary(_profile([40, 40, 40, 40, 40, 5000], [2, 2, 2, 2, 2, 400]))
    assert s == {"median_likes": 40, "median_comments": 2, "posts_read": 6}
    few = _profile([40, 40, 40], [2, 2, 2])
    assert engagement_summary(few) is None
    unknown = _profile([40] * 5, [2] * 5)
    unknown.engagement_known = False
    assert engagement_summary(unknown) is None
