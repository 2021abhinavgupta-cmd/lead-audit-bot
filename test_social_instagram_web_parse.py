"""Parsers for the Instagram web reader (scrapers/social/instagram_web.py).

The public-page fixture mirrors what a logged-out visitor's HTML really
contained for instagram.com/mmga.agency on 2026-11-03: og/meta description
with counts, a Relay prefetch <script type="application/json"> holding
xig_user_by_username (follower_count, following_count, biography, bio_links,
is_private, all_media_count null) and a second one holding the 12 most recent
posts (polaris_ordered_timeline_connection: type, caption, alt text with the
date; no like/comment counts, no timestamps). No live network here.
"""

import json
from datetime import datetime, timezone

from scrapers.social import instagram_web as igw

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
BIO = ("A home for creatives who dare to think, create, and make it Great (Again) \U0001F609\n"
       "HQ\U0001F4CDMumbai\nWanna work with us? \nMail to noorish@mmga.agency (all the best)")


def _relay_script(query, user):
    payload = {"require": [["ScheduledServerJS", "handle", None, [{"__bbox": {"require": [[
        "RelayPrefetchedStreamCache", "next", [],
        [f"adp_{query}RelayPreloader_x", {"__bbox": {"complete": True, "result": {"data": {
            "xig_user_by_username": user}}}}],
    ]]}}]]]}
    return f'<script type="application/json" data-sjs="">{json.dumps(payload)}</script>'


def _node(typename, media_type, product_type, alt, caption="caption text"):
    return {"node": {
        "__typename": typename, "pk": "1", "code": "x",
        "accessibility_caption": alt,
        "caption": {"text": caption} if caption is not None else None,
        "media_type": media_type, "product_type": product_type,
        "user": {"username": "mmga.agency"},
    }}


def _video(alt, caption="reel caption"):
    return _node("XIGPolarisVideoMedia", 2, "clips", alt, caption)


def _carousel(alt, caption="carousel caption"):
    return _node("XIGPolarisCarouselMedia", 8, "carousel_container", alt, caption)


_MMGA_EDGES = [
    _video("Video by MMGA on October 27, 2026. May be an image of poster and text.", "MMGA in its softie era?"),
    _video("Video by MMGA on October 15, 2026. May be an image of text."),
    _video("Video by MMGA on October 05, 2026. May be an image of text."),
    _video("Video by Sydenham Placement cell on September 04, 2026."),
    _carousel("Photo by MMGA on August 31, 2026. May be an image of text."),
    _carousel("Photo by MMGA on August 13, 2026."),
    _video("Video by MMGA on August 10, 2026."),
    _carousel("Photo by MMGA on July 13, 2026."),
    _video("Video by MMGA on June 27, 2026."),
    _video("Video by MMGA on June 20, 2026."),
    _carousel("Photo by MMGA on June 17, 2026."),
    _carousel("Photo by MMGA on March 02, 2026."),
]


def public_html(*, followers=559, following=42, og="560 Followers, 44 Following, 86 Posts",
                is_private=False, edges=None, bio=BIO, bio_links=True, has_next_page=True,
                with_profile_json=True):
    user = {
        "pk": "60888964012", "username": "mmga.agency", "is_private": is_private,
        "biography": bio, "full_name": "MMGA",
        "bio_links": ([{"link_type": "external", "title": "More of MMGA",
                        "url": "https://linktr.ee/mmga_agency",
                        "lynx_url": "https://l.instagram.com/?u=https%3A%2F%2Flinktr.ee%2Fmmga_agency"}]
                      if bio_links else []),
        "follower_count": followers, "following_count": following, "all_media_count": None,
        "id": "17841460840381120",
    }
    timeline_user = {"pk": "60888964012", "id": "17841460840381120", "polaris_ordered_timeline_connection": {
        "edges": _MMGA_EDGES if edges is None else edges,
        "page_info": {"end_cursor": "c", "has_next_page": has_next_page}}}
    metas = ""
    if og:
        metas = (f'<meta property="og:title" content="MMGA (@mmga.agency) &#x2022; Instagram photos and videos">'
                 f'<meta property="og:description" content="{og} - See Instagram photos and videos from MMGA (@mmga.agency)">'
                 f'<meta content="{og} - MMGA (@mmga.agency) on Instagram: &quot;A home for creatives&quot;" name="description">')
    scripts = ""
    if with_profile_json:
        scripts += _relay_script("PolarisLoggedOutDesktopWWWProfileRootContentQuery", user)
        scripts += _relay_script("PolarisLoggedOutDesktopWWWProfilePostsTabContentQuery", timeline_user)
    return f"<html><head><title>MMGA (@mmga.agency)</title>{metas}</head><body>{scripts}<header>x</header></body></html>"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def test_to_int_handles_suffixes_and_commas():
    assert igw._to_int("559") == 559
    assert igw._to_int("1,234") == 1234
    assert igw._to_int("1.2K") == 1200
    assert igw._to_int("2.3M") == 2_300_000
    assert igw._to_int("12.5k") == 12_500
    assert igw._to_int("") == 0
    assert igw._to_int("abc") == 0


def test_parse_og_counts():
    assert igw.parse_og_counts("560 Followers, 44 Following, 86 Posts - See Instagram") == {
        "followers": 560, "following": 44, "posts": 86}
    assert igw.parse_og_counts("1.2M Followers, 1,010 Following, 3,402 Posts - x") == {
        "followers": 1_200_000, "following": 1010, "posts": 3402}
    assert igw.parse_og_counts("1 Follower, 0 Following, 1 Post - x") == {
        "followers": 1, "following": 0, "posts": 1}
    assert igw.parse_og_counts("See Instagram photos and videos") is None


def test_normalize_handle():
    assert igw.normalize_handle("@MMGA.agency") == "mmga.agency"
    assert igw.normalize_handle("https://www.instagram.com/mmga.agency/?igsh=abc") == "mmga.agency"
    assert igw.normalize_handle("mmga.agency?hl=en") == "mmga.agency"
    assert igw.normalize_handle("instagram.com/acme_co") == "acme_co"
    assert igw.normalize_handle("") is None
    assert igw.normalize_handle("../etc/passwd") is None
    assert igw.normalize_handle("a b") is None
    assert igw.normalize_handle("x" * 31) is None


def test_has_session_cookie():
    assert igw.has_session_cookie([{"name": "sessionid", "value": "abc"}]) is True
    assert igw.has_session_cookie([{"name": "sessionid", "value": ""}]) is False
    assert igw.has_session_cookie([{"name": "csrftoken", "value": "x"}]) is False
    assert igw.has_session_cookie([]) is False


# ---------------------------------------------------------------------------
# public page
# ---------------------------------------------------------------------------

def test_public_page_reads_header_numbers_from_embedded_json_over_stale_og():
    kind, p = igw.parse_public_page(public_html(), "mmga.agency", NOW,
                                    final_url="https://www.instagram.com/mmga.agency/")
    assert kind == "ok"
    assert p.analyzed is True
    assert p.platform == "instagram"
    assert p.handle == "mmga.agency"
    assert p.url == "https://www.instagram.com/mmga.agency/"
    # live JSON (what the visible header renders) wins over the cached og numbers
    assert p.followers == 559
    assert p.following == 42
    # posts only come from og:description (all_media_count is null logged out)
    assert p.posts_count == 86
    assert p.display_name == "MMGA"
    assert p.bio.startswith("A home for creatives")
    assert "Mumbai" in p.bio
    assert p.has_link_in_bio is True
    assert p.link_url == "https://linktr.ee/mmga_agency"
    assert p.avg_engagement_rate == 0.0


def test_public_page_activity_from_alt_text_dates_when_every_post_is_dated():
    _, p = igw.parse_public_page(public_html(), "mmga.agency", NOW)
    assert p.activity_known is True
    # newest post October 27, 2026 -> 7 days before NOW
    assert p.last_post_age_days == 7
    # October 27 and October 15 are within 30 days of Nov 3; October 05 is 29 days -> also in
    assert p.posts_last_30_days == 3
    assert p.posting_frequency == "irregular"
    assert p.uses_video is True
    assert p.sample_captions[0] == "MMGA in its softie era?"
    assert len(p.sample_captions) == 5
    assert "engagement not available" in p.note
    assert "12 most recent posts" in p.note


def test_public_page_one_undated_post_makes_activity_unknown():
    edges = list(_MMGA_EDGES)
    edges[0] = _video("A custom alt text written by the owner")
    _, p = igw.parse_public_page(public_html(edges=edges), "mmga.agency", NOW)
    assert p.activity_known is False
    assert p.posts_last_30_days == 0
    assert p.last_post_age_days is None
    assert p.posting_frequency == ""
    assert p.note == igw.NOTE_PUBLIC_ONLY
    # header data is still real
    assert p.followers == 559 and p.posts_count == 86


def test_public_page_without_timeline_is_header_only():
    _, p = igw.parse_public_page(public_html(edges=[]), "mmga.agency", NOW)
    assert p.analyzed is True
    assert p.activity_known is False
    assert p.note == "Limited view: public profile data only (post dates and engagement not available)"


def test_public_page_alt_text_switch_off_means_header_only(monkeypatch):
    monkeypatch.setattr(igw, "PUBLIC_ACTIVITY_FROM_ALT_TEXT", False)
    _, p = igw.parse_public_page(public_html(), "mmga.agency", NOW)
    assert p.activity_known is False
    assert p.note == igw.NOTE_PUBLIC_ONLY


def test_public_page_og_only_fallback():
    kind, p = igw.parse_public_page(public_html(with_profile_json=False, og="1.2K Followers, 300 Following, 45 Posts"),
                                    "acme", NOW)
    assert kind == "ok"
    assert (p.followers, p.following, p.posts_count) == (1200, 300, 45)
    assert p.display_name == "MMGA"
    assert p.activity_known is False


def test_public_page_no_link():
    _, p = igw.parse_public_page(public_html(bio_links=False), "mmga.agency", NOW)
    assert p.has_link_in_bio is False
    assert p.link_url == ""


def test_public_page_private_account_is_unanalyzed():
    kind, p = igw.parse_public_page(public_html(is_private=True), "mmga.agency", NOW)
    assert kind == "private"
    assert p.analyzed is False
    assert "private" in p.note.lower()


def test_public_page_private_marker_text():
    html = "<html><head></head><body><h2>This account is private</h2></body></html>"
    kind, p = igw.parse_public_page(html, "secret", NOW)
    assert kind == "private" and p.analyzed is False


def test_public_page_not_found():
    html = ("<html><head><title>Page not found &#x2022; Instagram</title></head><body>"
            "<span>Sorry, this page isn't available.</span></body></html>")
    kind, p = igw.parse_public_page(html, "ghost", NOW)
    assert kind == "not_found"
    assert p.analyzed is False
    assert "not found" in p.note.lower() or "does not exist" in p.note.lower()


def test_public_page_login_redirect_is_login_wall():
    kind, p = igw.parse_public_page("<html></html>", "acme", NOW,
                                    final_url="https://www.instagram.com/accounts/login/?next=%2Facme%2F")
    assert kind == "login_wall"
    assert p.analyzed is False
    assert p.note


def test_public_page_challenge_redirect_is_login_wall():
    kind, _ = igw.parse_public_page("<html></html>", "acme", NOW,
                                    final_url="https://www.instagram.com/challenge/?next=/acme/")
    assert kind == "login_wall"


def test_public_page_unreadable_markup_is_unanalyzed_not_zeroes():
    kind, p = igw.parse_public_page("<html><body>something else entirely</body></html>", "acme", NOW)
    assert kind == "unreadable"
    assert p.analyzed is False
    assert p.followers == 0


# ---------------------------------------------------------------------------
# session (web_profile_info) payload
# ---------------------------------------------------------------------------

def _ts(y, m, d):
    return int(datetime(y, m, d, 10, 0, tzinfo=timezone.utc).timestamp())


def _media(ts, likes, comments, is_video=False, caption="cap", product_type="feed"):
    return {"node": {
        "taken_at_timestamp": ts, "is_video": is_video, "product_type": product_type,
        "edge_liked_by": {"count": likes}, "edge_media_to_comment": {"count": comments},
        "edge_media_to_caption": {"edges": [{"node": {"text": caption}}] if caption else []},
    }}


def wpi(**over):
    user = {
        "username": "mmga.agency", "full_name": "MMGA", "biography": BIO,
        "external_url": "https://linktr.ee/mmga_agency", "bio_links": [],
        "is_private": False,
        "edge_followed_by": {"count": 559}, "edge_follow": {"count": 42},
        "edge_owner_to_timeline_media": {"count": 86, "edges": [
            _media(_ts(2026, 10, 27), 40, 4, is_video=True, caption="softie era", product_type="clips"),
            _media(_ts(2026, 10, 15), 20, 2, caption="office reel"),
            _media(_ts(2026, 8, 31), 30, 0, caption=""),
            _media(_ts(2026, 6, 17), 10, 2, caption="june"),
        ]},
    }
    user.update(over)
    return {"data": {"user": user}, "status": "ok"}


def test_web_profile_info_full_activity():
    kind, p = igw.parse_web_profile_info(wpi(), "mmga.agency", NOW)
    assert kind == "ok"
    assert p.analyzed is True and p.activity_known is True
    assert (p.followers, p.following, p.posts_count) == (559, 42, 86)
    assert p.display_name == "MMGA"
    assert p.has_link_in_bio is True and p.link_url == "https://linktr.ee/mmga_agency"
    assert p.posts_last_30_days == 2
    assert p.last_post_age_days == 7
    assert p.posting_frequency == "irregular"
    assert p.uses_video is True
    # (avg likes 25 + avg comments 2) / 559 * 100 = 4.83, same formula as
    # scrapers/instagram.py _build_instagram_data
    assert p.avg_engagement_rate == round((100 / 4 + 8 / 4) / 559 * 100, 2)
    assert p.sample_captions == ["softie era", "office reel", "june"]
    assert p.note == ""


def test_web_profile_info_bio_links_count_as_link():
    _, p = igw.parse_web_profile_info(
        wpi(external_url=None, bio_links=[{"url": "https://shop.example"}]), "acme", NOW)
    assert p.has_link_in_bio is True and p.link_url == "https://shop.example"


def test_web_profile_info_preview_like_and_negative_counts():
    media = {"node": {"taken_at_timestamp": _ts(2026, 11, 1), "is_video": False,
                      "edge_media_preview_like": {"count": 50},
                      "edge_media_to_comment": {"count": -1}}}
    _, p = igw.parse_web_profile_info(
        wpi(edge_owner_to_timeline_media={"count": 1, "edges": [media]},
            edge_followed_by={"count": 1000}), "acme", NOW)
    assert p.avg_engagement_rate == 5.0
    assert p.uses_video is False


def test_web_profile_info_missing_user_is_not_found():
    kind, p = igw.parse_web_profile_info({"data": {"user": None}, "status": "ok"}, "ghost", NOW)
    assert kind == "not_found" and p.analyzed is False


def test_web_profile_info_private_is_unanalyzed():
    kind, p = igw.parse_web_profile_info(
        wpi(is_private=True, edge_owner_to_timeline_media={"count": 50, "edges": []}), "secret", NOW)
    assert kind == "private" and p.analyzed is False and "private" in p.note.lower()


def test_classify_session_response():
    c = igw.classify_session_response
    assert c(200, wpi()) == "ok"
    assert c(200, {"data": {"user": None}}) == "not_found"
    assert c(404, None) == "not_found"
    assert c(429, None) == "rate_limited"
    assert c(400, {"message": "Please wait a few minutes before you try again.", "status": "fail"}) == "rate_limited"
    assert c(401, {"message": "login_required"}) == "session_dead"
    assert c(400, {"message": "checkpoint_required", "checkpoint_url": "/challenge/x"}) == "session_dead"
    assert c(200, {"require_login": True}) == "session_dead"
    assert c(403, None) == "session_dead"
    assert c(500, None) == "error"
    assert c(200, "not json") == "error"
