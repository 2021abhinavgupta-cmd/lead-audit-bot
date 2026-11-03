import json
from scrapers.social.base import SocialProfile
from analyzer.social_audit import SocialIssue, generate_social_outreach, _build_outreach_prompt


_PROFILE = SocialProfile(
    platform="instagram", handle="acme", url="https://instagram.com/acme",
    followers=3000, bio="candles",
)
_ISSUES = [
    SocialIssue("high", "Inactive account", "The account has gone quiet."),
    SocialIssue("low", "Thin or empty bio", "The bio barely says what the business does."),
]


def test_prompt_contains_every_issue_and_forbids_new_claims():
    prompt = _build_outreach_prompt("Acme Candles", _PROFILE, _ISSUES, "email", "Kshitij")
    assert "Inactive account" in prompt
    assert "Thin or empty bio" in prompt
    assert "instagram" in prompt.lower()
    assert "only" in prompt.lower() and "issue" in prompt.lower()
    assert "dash" in prompt.lower() or "hyphen" in prompt.lower()


def test_email_channel_returns_subject_and_body():
    def fake_call(prompt):
        return json.dumps({"subject": "Your Instagram has gone quiet", "body": "Hi Acme, one quick thing."}), 0.0003
    out = generate_social_outreach("Acme Candles", _PROFILE, _ISSUES, "email", ai_call=fake_call)
    assert out["subject"] == "Your Instagram has gone quiet"
    assert out["body"] == "Hi Acme, one quick thing."
    assert out["cost"] == 0.0003


def test_dm_and_whatsapp_have_no_subject():
    def fake_call(prompt):
        return json.dumps({"subject": "ignored", "body": "hey, noticed your IG went quiet"}), 0.0
    dm = generate_social_outreach("Acme", _PROFILE, _ISSUES, "dm", ai_call=fake_call)
    wa = generate_social_outreach("Acme", _PROFILE, _ISSUES, "whatsapp", ai_call=fake_call)
    assert dm["subject"] == ""
    assert wa["subject"] == ""
    assert dm["body"]


def test_markdown_fenced_json_is_parsed():
    def fake_call(prompt):
        return "```json\n{\"subject\": \"S\", \"body\": \"B\"}\n```", 0.0
    out = generate_social_outreach("Acme", _PROFILE, _ISSUES, "email", ai_call=fake_call)
    assert out["subject"] == "S" and out["body"] == "B"


def test_unparseable_response_returns_empty_body_not_crash():
    def fake_call(prompt):
        return "the model rambled without json", 0.0
    out = generate_social_outreach("Acme", _PROFILE, _ISSUES, "email", ai_call=fake_call)
    assert out["body"] == ""
    assert out["cost"] == 0.0


def test_public_only_outreach_prompt_is_grounded_only_in_the_issue_list():
    from analyzer.social_audit import audit_profile
    profile = SocialProfile(
        platform="instagram", handle="acme", url="https://instagram.com/acme",
        followers=3000, following=40, posts_count=86, bio="hi", has_link_in_bio=False,
        activity_known=False,
        note="Limited view: public profile data only (post dates and engagement not available)",
    )
    issues = audit_profile(profile)
    prompt = _build_outreach_prompt("Acme", profile, issues, "email", "Kshitij")
    seen = []

    def fake_call(p):
        seen.append(p)
        return json.dumps({"subject": "S", "body": "B"}), 0.0

    generate_social_outreach("Acme", profile, issues, "email", ai_call=fake_call)
    assert seen == [prompt]
    for i in issues:
        assert i.label in prompt
    # nothing about activity the reader never saw, no engagement numbers it could not read
    for absent in ("Inactive", "slowed", "short video", "averages", "engagement rate of", "86"):
        assert absent not in prompt
    # the audience size is a measured fact the email may quote
    assert "3,000 followers" in prompt


def _engaged_profile():
    posts = [{"likes": l, "comments": c, "code": f"c{i}"} for i, (l, c) in enumerate([(40, 2), (50, 3), (30, 1), (60, 2), (44, 2)])]
    return SocialProfile(
        platform="instagram", handle="acme", url="u", bio="candles", followers=5200, following=100,
        posts_count=80, posts_last_30_days=4, last_post_age_days=3, activity_known=True,
        recent_posts=posts, engagement_known=True, avg_engagement_rate=0.9,
    )


def test_email_prompt_carries_measured_engagement_numbers():
    from analyzer.social_audit import measured_facts
    p = _engaged_profile()
    facts, nums = measured_facts(p)
    joined = " ".join(facts)
    assert "5,200 followers" in joined and "last 5 posts" in joined
    assert "45 likes" in joined and "2 comments" in joined and "0.9 percent" in joined
    prompt = _build_outreach_prompt("Acme", p, [], "email", "Kshitij")
    assert "MEASURED FACTS" in prompt and "5,200 followers" in prompt


def test_engagement_facts_need_four_counted_posts():
    from analyzer.social_audit import measured_facts
    p = _engaged_profile()
    p.recent_posts = p.recent_posts[:3]
    facts, _ = measured_facts(p)
    assert not any("likes" in f for f in facts)


def test_email_using_an_unmeasured_number_is_retried_then_rejected():
    p = _engaged_profile()
    calls = []

    def bad_call(prompt):
        calls.append(prompt)
        return json.dumps({"subject": "S", "body": "You lose 73 customers a week."}), 0.001

    out = generate_social_outreach("Acme", p, [], "email", ai_call=bad_call)
    assert len(calls) == 2 and "73" in calls[1]
    assert out["body"] == "" and out["cost"] == 0.002


def test_email_retry_that_fixes_the_numbers_is_accepted():
    p = _engaged_profile()
    replies = iter([
        json.dumps({"subject": "S", "body": "You lose 73 customers a week."}),
        json.dumps({"subject": "Fix it", "body": "5,200 followers but about 45 likes per post."}),
    ])
    out = generate_social_outreach("Acme", p, [], "email", ai_call=lambda _p: (next(replies), 0.001))
    assert out["body"].startswith("5,200 followers") and out["subject"] == "Fix it"


def test_dm_channel_is_not_number_checked_or_given_the_facts_block():
    p = _engaged_profile()
    assert "MEASURED FACTS" not in _build_outreach_prompt("Acme", p, [], "dm", "Kshitij")
