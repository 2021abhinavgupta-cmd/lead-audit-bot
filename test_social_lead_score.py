"""Phase C: lead_score (how worth pitching a profile is, from known data only)
and the outreach prompt leading with the strongest proven issue."""

import json
import re

from analyzer.social_audit import (
    SocialIssue, _build_outreach_prompt, audit_profile, generate_social_outreach, lead_score,
)
from scrapers.social.base import SocialProfile, unanalyzed

_DASHES = ("-", "‐", "‑", "‒", "–", "—", "―", "−")


def _p(**over):
    base = dict(platform="instagram", handle="acme", url="u", bio="candles", followers=3000,
                following=100, posts_count=80, posts_last_30_days=6, last_post_age_days=4,
                posting_frequency="weekly", activity_known=True, has_link_in_bio=False)
    base.update(over)
    return SocialProfile(**base)


_ISSUES = [SocialIssue("medium", "No link in the bio", "x"), SocialIssue("low", "Thin or empty bio", "y")]


def _check_shape(r):
    assert set(r) == {"score", "label", "reasons"}
    assert 0 <= r["score"] <= 100
    assert r["label"] in ("strong", "maybe", "weak")
    assert r["reasons"] and all(isinstance(x, str) and x for x in r["reasons"])
    for reason in r["reasons"]:
        assert not any(d in reason for d in _DASHES), reason


def test_active_real_audience_with_issues_is_strong():
    r = lead_score(_p(), _ISSUES)
    _check_shape(r)
    assert r["label"] == "strong"
    assert r["score"] >= 70


def test_no_issues_is_weaker_than_with_issues():
    with_issues = lead_score(_p(), _ISSUES)["score"]
    without = lead_score(_p(), [])
    _check_shape(without)
    assert without["score"] < with_issues
    assert without["label"] != "strong"


def test_tiny_audience_is_not_strong():
    r = lead_score(_p(followers=60), _ISSUES)
    _check_shape(r)
    assert r["label"] != "strong"


def test_abandoned_account_is_weak():
    p = _p(posts_last_30_days=0, last_post_age_days=200)
    r = lead_score(p, audit_profile(p))
    _check_shape(r)
    assert r["label"] == "weak"


def test_unknown_activity_is_capped_below_strong():
    p = _p(activity_known=False, posts_last_30_days=0, last_post_age_days=None)
    r = lead_score(p, _ISSUES + [SocialIssue("high", "Link in bio is broken", "z")])
    _check_shape(r)
    assert r["label"] == "maybe"
    assert any("could not be read" in x for x in r["reasons"])


def test_private_unanalyzed_and_missing_profiles_are_weak():
    priv = unanalyzed("instagram", "secret", "u", "This Instagram account is private, so its profile cannot be read.")
    for prof in (priv, unanalyzed("instagram", "x", "u", "login wall"), None):
        r = lead_score(prof, [])
        _check_shape(r)
        assert r["label"] == "weak" and r["score"] <= 10


def test_lead_score_is_deterministic():
    assert lead_score(_p(), _ISSUES) == lead_score(_p(), _ISSUES)


# ---------------------------------------------------------------------------
# outreach prompt ordering and grounding
# ---------------------------------------------------------------------------

def test_prompt_puts_the_strongest_issue_first_whatever_the_input_order():
    issues = [
        SocialIssue("low", "Thin or empty bio", "The bio barely says what the business does."),
        SocialIssue("high", "Inactive account", "The account has gone quiet."),
        SocialIssue("medium", "No link in the bio", "There is no clickable link."),
        SocialIssue("high", "Link in bio is broken", "The link in the bio does not open."),
    ]
    prompt = _build_outreach_prompt("Acme", _p(), issues, "email", "Kshitij")
    top = prompt.index("TOP ISSUE")
    assert prompt.index("Link in bio is broken") > top
    order = [prompt.index(lbl) for lbl in ("Link in bio is broken", "Inactive account",
                                           "No link in the bio", "Thin or empty bio")]
    assert order == sorted(order)
    assert "open with the top issue" in prompt.lower()


def test_prompt_contains_no_number_that_is_not_in_the_profile_or_issues():
    p = _p(handle="acme2024", followers=3141, posts_count=271)
    issues = audit_profile(p)
    prompt = _build_outreach_prompt("Acme 7 Candles", p, issues, "email", "Kshitij")
    allowed = "Acme 7 Candles " + p.handle + " " + " ".join(i.label + " " + i.detail for i in issues)
    for num in re.findall(r"\d+", prompt):
        assert num in allowed, num
    # profile numbers themselves are not handed to the model
    assert "3141" not in prompt and "271" not in prompt


def test_generate_sends_the_ordered_prompt():
    seen = []

    def fake(prompt):
        seen.append(prompt)
        return json.dumps({"subject": "S", "body": "B"}), 0.0

    issues = [SocialIssue("low", "Thin or empty bio", "a"), SocialIssue("high", "Inactive account", "b")]
    generate_social_outreach("Acme", _p(), issues, "dm", ai_call=fake)
    assert seen[0].index("Inactive account") < seen[0].index("Thin or empty bio")
