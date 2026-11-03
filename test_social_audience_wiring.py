"""Audience quality wired into lead_score and the /api/social/audit route.
Internal only: signals reach the UI, never the outreach prompt (except the one
soft "Posts are not reaching the audience" issue)."""

import re

from fastapi.testclient import TestClient

import app as app_module
from analyzer import social_audit
from analyzer.social_audit import SocialIssue, lead_score
from scrapers.social.base import SocialProfile, unanalyzed

ACCUSATION = re.compile(r"\b(fake|fakes|bot|bots|bought|purchased|pod|pods|scam|scams)\b", re.I)
_DASHES = ("-", "‐", "‑", "‒", "–", "—", "―", "−")
_ISSUES = [SocialIssue("medium", "No link in the bio", "x"), SocialIssue("low", "Thin or empty bio", "y")]


def _p(**over):
    base = dict(platform="instagram", handle="acme", url="u", bio="candles", followers=3000,
                following=100, posts_count=80, posts_last_30_days=6, last_post_age_days=4,
                posting_frequency="weekly", activity_known=True, has_link_in_bio=False)
    base.update(over)
    return SocialProfile(**base)


def _post(likes, comments):
    return {"type": "video", "caption": "x", "likes": likes, "comments": comments, "ts": None}


# ---------------------------------------------------------------------------
# lead_score
# ---------------------------------------------------------------------------

def test_suspect_audience_lowers_the_lead_score_with_a_plain_reason():
    base = lead_score(_p(), _ISSUES)
    r = lead_score(_p(), _ISSUES, audience={"label": "suspect", "score": 40, "signals": []})
    assert r["score"] < base["score"]
    extra = [x for x in r["reasons"] if x not in base["reasons"]]
    assert len(extra) == 1 and "worth checking" in extra[0]
    assert not any(d in extra[0] for d in _DASHES)
    assert not ACCUSATION.search(extra[0])


def test_healthy_audience_raises_the_lead_score_slightly():
    p = _p(followers=600, last_post_age_days=40)
    base = lead_score(p, _ISSUES[:1])
    r = lead_score(p, _ISSUES[:1], audience={"label": "healthy", "score": 100, "signals": []})
    assert 0 < r["score"] - base["score"] <= 5
    assert any("looks healthy" in x for x in r["reasons"])


def test_unknown_or_mixed_or_missing_audience_changes_nothing():
    base = lead_score(_p(), _ISSUES)
    for aq in (None, {"label": "unknown", "score": None, "signals": []},
               {"label": "mixed", "score": 70, "signals": []}):
        assert lead_score(_p(), _ISSUES, audience=aq) == base


def test_audience_never_lifts_an_unanalyzed_profile():
    r = lead_score(unanalyzed("instagram", "x", "u", "private"), [],
                   audience={"label": "healthy", "score": 100, "signals": []})
    assert r["score"] == 0 and r["label"] == "weak"


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------

def _suspect_profile(**over):
    # likes low for 30k followers and no comments; engagement rate not set,
    # so the audit's own Low engagement issue does not fire
    base = dict(platform="instagram", handle="acme", url="https://www.instagram.com/acme/",
                followers=30_000, following=7000, bio="Handmade candles from our Pune studio. DM to order.",
                has_link_in_bio=True, link_url="", posts_count=200, activity_known=True,
                posts_last_30_days=8, last_post_age_days=2, posting_frequency="2-3x per week",
                followers_exact=True,
                recent_posts=[_post(l, 0) for l in (60, 55, 62, 58, 61, 57)])
    base.update(over)
    return SocialProfile(**base)


def _run(monkeypatch, profile):
    monkeypatch.setattr(app_module.config, "API_KEY", None)
    monkeypatch.setitem(app_module._SOCIAL_ADAPTERS, "instagram", lambda handle: profile)
    prompts = []

    def fake_ai(prompt):
        prompts.append(prompt)
        return '{"subject": "s", "body": "b"}', 0.0

    def outreach(company, profile, issues, channel, your_name="Kshitij", ai_call=None):
        return social_audit.generate_social_outreach(company, profile, issues, channel, your_name, ai_call=fake_ai)

    monkeypatch.setattr(app_module, "generate_social_outreach", outreach)
    with TestClient(app_module.app, raise_server_exceptions=False) as client:
        r = client.post("/api/social/audit", json={"company": "Acme Candles",
                                                   "handles": {"instagram": "https://instagram.com/acme"}})
        assert r.status_code == 200, r.text
        return r.json()["results"]["instagram"], prompts


def test_route_returns_audience_quality_beside_the_lead_score(monkeypatch):
    res, _ = _run(monkeypatch, _suspect_profile())
    aq = res["audience_quality"]
    assert aq["label"] == "suspect"
    assert {s["key"] for s in aq["signals"]} >= {"likes_low_for_audience", "likes_no_comments"}
    assert any("worth checking" in x for x in res["lead_score"]["reasons"])


def test_outreach_prompt_gets_only_the_soft_reach_line(monkeypatch):
    res, prompts = _run(monkeypatch, _suspect_profile())
    assert len(prompts) == 3
    labels = [i["label"] for i in res["issues"]]
    assert "Posts are not reaching the audience" in labels
    for prompt in prompts:
        assert "Posts are not reaching the audience" in prompt
        assert "usually means posts are not reaching the people who follow the account" in prompt
        assert not ACCUSATION.search(prompt), ACCUSATION.search(prompt)
        # no other audience quality wording leaks in
        for s in res["audience_quality"]["signals"]:
            assert s["detail"] not in prompt
        assert "audience quality" not in prompt.lower()
        assert "suspect" not in prompt.lower()


def test_no_reach_line_when_low_engagement_is_already_an_issue(monkeypatch):
    res, _ = _run(monkeypatch, _suspect_profile(engagement_known=True, avg_engagement_rate=0.2))
    labels = [i["label"] for i in res["issues"]]
    assert "Low engagement" in labels
    assert "Posts are not reaching the audience" not in labels


def test_no_reach_line_without_the_low_likes_signal(monkeypatch):
    prof = _suspect_profile(followers=400, following=2500, recent_posts=[])
    res, prompts = _run(monkeypatch, prof)
    assert "follow_for_follow" in {s["key"] for s in res["audience_quality"]["signals"]}
    assert "Posts are not reaching the audience" not in [i["label"] for i in res["issues"]]
    for prompt in prompts:
        assert "follow back" not in prompt
        assert not ACCUSATION.search(prompt)


def test_unanalyzed_and_missing_profiles_get_unknown_audience(monkeypatch):
    monkeypatch.setattr(app_module.config, "API_KEY", None)
    monkeypatch.setitem(app_module._SOCIAL_ADAPTERS, "instagram",
                        lambda handle: unanalyzed("instagram", "x", "u", "This Instagram account is private."))
    monkeypatch.setitem(app_module._SOCIAL_ADAPTERS, "facebook", lambda handle: None)
    with TestClient(app_module.app, raise_server_exceptions=False) as client:
        r = client.post("/api/social/audit", json={
            "company": "Acme", "handles": {"instagram": "x", "facebook": "https://facebook.com/x"}})
        results = r.json()["results"]
    assert results["instagram"]["audience_quality"]["label"] == "unknown"
    assert results["facebook"]["audience_quality"]["label"] == "unknown"
