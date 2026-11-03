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
    # nothing about activity the reader never saw, and no raw numbers
    for absent in ("Inactive", "slowed", "engagement", "short video", "3000", "86"):
        assert absent not in prompt
