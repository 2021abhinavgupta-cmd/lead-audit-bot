"""Instagram post embed reader (scrapers/social/instagram_web.py).

The fixture mirrors what ONE live logged-out load of
https://www.instagram.com/p/De_6hPjswg-/embed/captioned/ returned on
2026-11-03 (mmga.agency's October 27 reel): a ServerJS <script> whose
PolarisEmbedSimple init carries "contextJSON" (an escaped JSON string with
gql_data.shortcode_media: __typename, is_video, video_view_count,
edge_media_to_caption, edge_media_to_comment.count, edge_liked_by.count,
product_type, owner {username, is_verified, edge_followed_by}), plus the
visible <div class="SocialProof"> "44 likes" link. No timestamp and no
comment text. Image / CDN URLs are trimmed. No live network here.
"""

import copy
import json
import os
from datetime import datetime, timezone

import pytest
from playwright.sync_api import Error as PlaywrightError

from scrapers.social import instagram_web as igw
from scrapers.social.instagram_web import InstagramThrottle, PublicPage, SessionResult
from test_social_instagram_web_parse import _MMGA_EDGES, public_html, wpi

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
SAVED_EMBED = (r"D:\temp\claude\d--office-work-projects-leads-for-business"
               r"\c8c3d136-7c01-4940-bda4-9c4f0099765a\scratchpad\ig_embed.html")

REAL_CONTEXT = {
    "context": {"type": "GraphVideo", "shortcode": "De_6hPjswg-", "copyright_blocked": False},
    "gql_data": {"shortcode_media": {
        "__typename": "GraphVideo", "id": "4017186763783211070", "shortcode": "De_6hPjswg-",
        "is_video": True, "dimensions": {"height": 480, "width": 640},
        "video_duration": 12.701, "video_view_count": 278, "accessibility_caption": None,
        "edge_media_to_caption": {"edges": [{"node": {"text": (
            "MMGA in its softie era? \U0001f914\U0001f92d\n\nPresenting our latest branding project for "
            "Rajni's Cakes \u2014 filled with mad fun and ton of sweetness (literally) \U0001f5e3\ufe0f\n\n"
            "#marketing #branding #agency #explore #creative")}}]},
        "edge_media_to_comment": {"count": 2},
        "edge_liked_by": {"count": 44},
        "product_type": "clips",
        "clips_music_attribution_info": {"artist_name": "mmga.agency", "song_name": "Original audio",
                                         "uses_original_audio": True, "should_mute_audio": False},
        "owner": {"id": "60888964012", "username": "mmga.agency", "is_verified": False,
                  "profile_pic_url": "https://scontent.cdninstagram.com/p.jpg",
                  "edge_followed_by": {"count": 560}},
    }},
}


def embed_html(context=REAL_CONTEXT, social_proof="44 likes", code="De_6hPjswg-"):
    parts = ["<html><head></head><body>"]
    if social_proof is not None:
        parts.append(
            f'<div class="SocialProof"><a href="https://www.instagram.com/reel/{code}/?utm_source=ig_embed" '
            f'data-log-event="likeCountClick" target="_blank">{social_proof}</a></div>')
    parts.append('<div class="Caption"><a class="CaptionUsername" href="#">mmga.agency</a> caption</div>')
    if context is not None:
        init = {"isRichEmbed": True, "isSidecar": False, "isGuideEmbed": False, "isProfileEmbed": False,
                "contextJSON": json.dumps(context)}
        payload = {"define": [], "require": [["PolarisEmbedRich.react"], ["PolarisEmbedSimple", "init", [], [init]]]}
        parts.append('<script nonce="">requireLazy(["TimeSliceImpl","ServerJS"],function(TimeSlice,ServerJS)'
                     '{var s=(new ServerJS());s.handle(' + json.dumps(payload) + ');});</script>')
    parts.append("</body></html>")
    return "".join(parts)


def _ctx(likes=44, comments=2, owner="mmga.agency", hidden=False, drop=()):
    c = copy.deepcopy(REAL_CONTEXT)
    m = c["gql_data"]["shortcode_media"]
    m["edge_liked_by"] = {"count": likes}
    m["edge_media_to_comment"] = {"count": comments}
    m["owner"]["username"] = owner
    if hidden:
        m["like_and_view_counts_disabled"] = True
    for k in drop:
        m.pop(k, None)
    return c


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def test_parse_embed_reads_likes_comments_and_more():
    kind, d = igw.parse_post_embed(embed_html())
    assert kind == "ok"
    assert d["likes"] == 44 and d["comments"] == 2
    assert d["views"] == 278
    assert d["type"] == "video"
    assert d["owner"] == "mmga.agency"
    assert d["owner_followers"] == 560
    assert d["caption"].startswith("MMGA in its softie era?")
    assert d["shortcode"] == "De_6hPjswg-"


def test_parse_embed_falls_back_to_visible_like_count():
    kind, d = igw.parse_post_embed(embed_html(context=None, social_proof="1,234 likes"))
    assert kind == "ok"
    assert d["likes"] == 1234
    assert d["comments"] is None   # the visible block only says "View all comments"


def test_parse_embed_one_like_and_k_suffix():
    assert igw.parse_post_embed(embed_html(context=None, social_proof="1 like"))[1]["likes"] == 1
    assert igw.parse_post_embed(embed_html(context=None, social_proof="12.5K likes"))[1]["likes"] == 12_500


def test_parse_embed_hidden_like_count_is_none_not_zero():
    _, d = igw.parse_post_embed(embed_html(context=_ctx(hidden=True), social_proof=None))
    assert d["likes"] is None
    assert d["comments"] == 2
    _, d = igw.parse_post_embed(embed_html(context=_ctx(likes=-1), social_proof=None))
    assert d["likes"] is None
    _, d = igw.parse_post_embed(embed_html(context=_ctx(drop=("edge_liked_by",)), social_proof=None))
    assert d["likes"] is None


def test_parse_embed_unrecognised_page_and_login_wall():
    assert igw.parse_post_embed("<html><body>This post is unavailable</body></html>")[0] == "unreadable"
    assert igw.parse_post_embed("", final_url="https://www.instagram.com/accounts/login/?next=x")[0] == "login_wall"
    assert igw.parse_post_embed("<html><form id='loginForm'><input name=\"password\"></form></html>")[0] == "login_wall"


def test_parse_embed_garbage_context_json_falls_back():
    html = embed_html(context=None).replace("</body>", '<script>{"contextJSON":"{not json"}</script></body>')
    kind, d = igw.parse_post_embed(html)
    assert kind == "ok" and d["likes"] == 44


@pytest.mark.skipif(not os.path.isfile(SAVED_EMBED), reason="saved live embed page not on this machine")
def test_parse_embed_on_the_saved_live_page():
    with open(SAVED_EMBED, encoding="utf-8") as f:
        kind, d = igw.parse_post_embed(f.read(), final_url="https://www.instagram.com/p/De_6hPjswg-/embed/captioned/")
    assert kind == "ok"
    assert (d["likes"], d["comments"], d["views"], d["owner"]) == (44, 2, 278, "mmga.agency")


def test_valid_shortcode():
    assert igw.valid_shortcode("De_6hPjswg-") is True
    assert igw.valid_shortcode("../x") is False
    assert igw.valid_shortcode("") is False
    assert igw.valid_shortcode("a/b") is False


def test_public_page_posts_carry_their_shortcode():
    _, p = igw.parse_public_page(public_html(edges=_coded_edges()), "mmga.agency", NOW)
    assert p.recent_posts[0]["code"] == "c0"
    assert p.recent_posts[11]["code"] == "c11"


# ---------------------------------------------------------------------------
# read_post_embed + read_instagram with fakes
# ---------------------------------------------------------------------------

class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def th(clock):
    return InstagramThrottle(clock=clock, sleep=clock.sleep)


def _coded_edges():
    edges = copy.deepcopy(_MMGA_EDGES)
    for i, e in enumerate(edges):
        e["node"]["code"] = f"c{i}"
    return edges


class Public:
    def __init__(self, html=None):
        self.html = html or public_html(edges=_coded_edges())
        self.calls = []

    def __call__(self, handle):
        self.calls.append(handle)
        return PublicPage(200, f"https://www.instagram.com/{handle}/", self.html)


class Embeds:
    """Fake embed fetcher: per shortcode a PublicPage, or an exception."""

    def __init__(self, pages=None, default=None, clock=None):
        self.pages = pages or {}
        self.default = default
        self.calls = []
        self.times = []
        self.clock = clock

    def __call__(self, code):
        self.calls.append(code)
        if self.clock:
            self.times.append(self.clock())
        page = self.pages.get(code, self.default)
        if isinstance(page, Exception):
            raise page
        if page is None:
            likes = 40 + int(code[1:])
            page = embed_html(context=_ctx(likes=likes, comments=3), code=code)
        if isinstance(page, str):
            page = PublicPage(200, f"https://www.instagram.com/p/{code}/embed/captioned/", page)
        return page


def _read(handle="mmga.agency", **kw):
    kw.setdefault("now", NOW)
    kw.setdefault("session_fetch", None)
    return igw.read_instagram(handle, **kw)


def test_read_post_embed_ok(th):
    r = igw.read_post_embed("De_6hPjswg-", fetch=Embeds(default=embed_html()), throttle=th)
    assert r["status"] == "ok" and r["likes"] == 44 and r["comments"] == 2


def test_read_post_embed_rejects_bad_shortcode_without_loading(th):
    emb = Embeds()
    assert igw.read_post_embed("../../x", fetch=emb, throttle=th)["status"] == "invalid"
    assert emb.calls == []


def test_embed_429_pauses_only_embeds_not_the_profile_read(th):
    r = igw.read_post_embed("c0", fetch=Embeds(default=PublicPage(429, "u", "")), throttle=th)
    assert r["status"] == "rate_limited"
    assert th.blocked_note() is None            # profile reads keep working
    assert th.embed_blocked_note() is not None  # embeds are paused
    emb = Embeds()
    assert igw.read_post_embed("c1", fetch=emb, throttle=th)["status"] == "blocked"
    assert emb.calls == []


def test_embed_login_wall_pauses_only_embeds(th):
    page = PublicPage(200, "https://www.instagram.com/accounts/login/?next=/p/c0/", "<html></html>")
    r = igw.read_post_embed("c0", fetch=Embeds(default=page), throttle=th)
    assert r["status"] == "login_wall"
    assert th.blocked_note() is None and th.embed_blocked_note() is not None


def test_public_read_fills_six_posts_from_embeds(th, clock):
    emb = Embeds(clock=clock)
    p = _read(public_fetch=Public(), embed_fetch=emb, throttle=th)
    assert emb.calls == ["c0", "c1", "c2", "c3", "c4", "c5"]
    assert [x["likes"] for x in p.recent_posts[:6]] == [40, 41, 42, 43, 44, 45]
    assert all(x["comments"] == 3 for x in p.recent_posts[:6])
    # the posts not read stay unknown, never 0
    assert all(x["likes"] is None and x["comments"] is None for x in p.recent_posts[6:])
    assert p.engagement_known is True
    # (avg likes 42.5 + avg comments 3) / 559 followers
    assert p.avg_engagement_rate == round((42.5 + 3) / 559 * 100, 2)
    assert "likes and comments read from 6 recent posts" in p.note
    assert "engagement not available" not in p.note
    # every embed load waited out the 20 s spacing after the one before
    assert all(b - a >= 20 for a, b in zip(emb.times, emb.times[1:]))


def test_fewer_than_four_posts_read_sets_counts_but_no_engagement(th):
    bad = "<html><body>This post is unavailable</body></html>"
    emb = Embeds(pages={"c1": bad, "c2": bad, "c4": bad})
    p = _read(public_fetch=Public(), embed_fetch=emb, throttle=th)
    assert len(emb.calls) == 6
    assert [x["likes"] for x in p.recent_posts[:6]] == [40, None, None, 43, None, 45]
    assert p.engagement_known is False
    assert p.avg_engagement_rate == 0.0


def test_embed_rate_limit_stops_embeds_but_keeps_the_profile(th):
    emb = Embeds(pages={"c2": PublicPage(429, "u", "")})
    pub = Public()
    p = _read(public_fetch=pub, embed_fetch=emb, throttle=th)
    assert emb.calls == ["c0", "c1", "c2"]
    assert p.analyzed is True and p.followers == 559
    assert p.engagement_known is False
    assert th.blocked_note() is None
    # the next profile read still loads the profile, but skips the paused embeds
    emb2 = Embeds()
    p2 = _read(public_fetch=pub, embed_fetch=emb2, throttle=th)
    assert p2.analyzed is True and len(pub.calls) == 2
    assert emb2.calls == []


def test_embed_browser_error_stops_embeds(th):
    emb = Embeds(default=PlaywrightError("Timeout"))
    p = _read(public_fetch=Public(), embed_fetch=emb, throttle=th)
    assert emb.calls == ["c0"]
    assert p.analyzed is True and p.engagement_known is False


def test_hourly_cap_stops_embeds(clock):
    th = InstagramThrottle(clock=clock, sleep=clock.sleep, max_per_hour=3)
    emb = Embeds()
    p = _read(public_fetch=Public(), embed_fetch=emb, throttle=th)
    assert emb.calls == ["c0", "c1"]   # profile + two embeds = the three loads allowed
    assert p.analyzed is True and p.engagement_known is False


def test_collab_post_owned_by_another_account_is_not_counted(th):
    emb = Embeds(pages={"c3": embed_html(context=_ctx(owner="sydenham.placement"), code="c3")})
    p = _read(public_fetch=Public(), embed_fetch=emb, throttle=th)
    assert p.recent_posts[3]["likes"] is None
    assert p.engagement_known is True   # five others were read


def test_hidden_like_count_post_is_skipped(th):
    emb = Embeds(pages={"c0": embed_html(context=_ctx(hidden=True), social_proof=None, code="c0")})
    p = _read(public_fetch=Public(), embed_fetch=emb, throttle=th)
    assert p.recent_posts[0]["likes"] is None
    assert p.recent_posts[0]["comments"] == 2


def test_session_read_never_loads_embeds(th):
    emb = Embeds()

    def ses(handle, gate):
        gate()
        return SessionResult("response", status=200, payload=wpi())

    p = _read(public_fetch=Public(), session_fetch=ses, embed_fetch=emb, throttle=th)
    assert p.activity_known is True and emb.calls == []


def test_embed_switch_off(th, monkeypatch):
    monkeypatch.setattr(igw, "PUBLIC_EMBED_POSTS", 0)
    emb = Embeds()
    p = _read(public_fetch=Public(), embed_fetch=emb, throttle=th)
    assert emb.calls == [] and p.engagement_known is False


def test_private_profile_loads_no_embeds(th):
    emb = Embeds()
    p = _read(public_fetch=Public(public_html(is_private=True)), embed_fetch=emb, throttle=th)
    assert p.analyzed is False and emb.calls == []


def test_default_embed_fetch_is_blocked_in_tests(th):
    # conftest replaces the real browser fetcher: a read must not reach Instagram
    p = _read(public_fetch=Public(), throttle=th)
    assert p.analyzed is True and p.engagement_known is False
