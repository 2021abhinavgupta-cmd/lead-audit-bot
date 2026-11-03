"""Social tab PDFs: POST /api/social-pdf (small internal summary, long client
facing audit). The renderer is faked, so no browser runs; the HTML handed to
it is what is checked."""

import ast
import os
import re

import pytest
from fastapi.testclient import TestClient

import app as app_module
from reporting import social_pdf

_H = {"X-API-Key": app_module.config.API_KEY or "dp"}
ROOT = os.path.dirname(os.path.abspath(__file__))

ISSUES = [
    {"severity": "low", "label": "Thin or empty bio",
     "detail": "The bio barely says what the business does or why someone should follow."},
    {"severity": "high", "label": "Link in bio is broken",
     "detail": "The link in the bio does not open, so anyone who taps it lands on an error instead."},
    {"severity": "medium", "label": "No call to action in the bio",
     "detail": "The bio never tells a visitor what to do next."},
    {"severity": "low", "label": "Image posts underperform video posts on this account",
     "detail": "On this account the video posts get clearly more likes and comments."},
    {"severity": "low", "label": "Something new nobody mapped yet", "detail": "A brand new kind of finding."},
]

INTERNAL_SIGNAL = "One recent post got far more likes than all the others while the rest stay flat."


def body(mode="small", **over):
    b = {
        "mode": mode, "company": "Acme Co", "platform": "instagram", "handle": "acme.cakes",
        "profile_url": "https://www.instagram.com/acme.cakes/", "website": "https://acme.example",
        "profile": {"followers": 558, "following": 120, "posts_count": 86, "bio": "Cakes in Pune",
                    "note": "Limited view: public profile data only", "engagement_rate": 7.53,
                    "median_likes": 40, "median_comments": 2, "posts_read": 6, "link_in_bio": True},
        "issues": ISSUES,
        "email": {"subject": "Your bio link is broken", "body": "Hi team,\nThe link in your bio fails.\nKshitij"},
        "lead_score": {"score": 75, "label": "strong",
                       "reasons": ["Posted within the last month, so someone is still running the account."]},
        "audience_quality": {"label": "mixed", "score": 70, "signals": [{"key": "engagement_spike", "detail": INTERNAL_SIGNAL}]},
        "timestamp": "3 November 2026",
    }
    b.update(over)
    return b


@pytest.fixture
def seen(monkeypatch):
    got = {}

    async def fake_render(html, margin_mm=0):
        got["html"] = html
        got["margin_mm"] = margin_mm
        return b"%PDF-1.4 fake"

    monkeypatch.setattr(app_module.reporting_report, "render_pdf", fake_render)
    # this file posts far more than the route's 10 a minute; the limit itself
    # is checked in test_rate_limited below
    app_module.app.dependency_overrides[app_module.SOCIAL_PDF_RATE_LIMIT] = lambda: None
    yield got
    app_module.app.dependency_overrides.pop(app_module.SOCIAL_PDF_RATE_LIMIT, None)


def _post(b):
    return TestClient(app_module.app, raise_server_exceptions=False).post("/api/social-pdf", json=b, headers=_H)


def _visible(html):
    """The text a reader of the PDF sees: no <style>, no tags, entities decoded."""
    import html as h
    text = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    text = re.sub(r"<title>.*?</title>", " ", text, flags=re.S)
    return h.unescape(re.sub(r"<[^>]+>", " ", text))


# --------------------------------------------------------------------------- routing

def test_nothing_to_render_is_400(seen):
    assert _post({"mode": "small"}).status_code == 400
    # the long PDF never shows the email, so an email alone is still nothing
    empty = body("long", issues=[], profile={})
    assert _post(empty).status_code == 400
    assert "html" not in seen


def test_bad_mode_is_rejected(seen):
    assert _post(body("medium")).status_code == 422


def test_requires_the_api_key(seen, monkeypatch):
    monkeypatch.setattr(app_module.config, "API_KEY", "secret")
    r = TestClient(app_module.app).post("/api/social-pdf", json=body(), headers={"X-API-Key": "wrong"})
    assert r.status_code in (401, 403)


def test_rate_limited(monkeypatch):
    async def fake_render(html, margin_mm=0):
        return b"%PDF-1.4 fake"

    monkeypatch.setattr(app_module.reporting_report, "render_pdf", fake_render)
    codes = [_post(body()).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_over_long_strings_are_rejected(seen):
    assert _post(body(company="x" * 5000)).status_code == 422


def test_filename_and_pdf_bytes(seen):
    r = _post(body("small"))
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert 'filename="Acme-Co-social-small.pdf"' in r.headers["content-disposition"]
    assert seen["margin_mm"] == 14
    r = _post(body("long"))
    assert 'filename="Acme-Co-social-long.pdf"' in r.headers["content-disposition"]


# --------------------------------------------------------------------------- small

def test_small_has_email_scores_issues_and_internal_block(seen):
    assert _post(body("small")).status_code == 200
    text = _visible(seen["html"])
    assert "SOCIAL DRAFT" in text
    assert "Your bio link is broken" in text and "The link in your bio fails." in text
    assert "75" in text and "strong" in text.lower()
    assert "Posted within the last month" in text
    for issue in ISSUES:
        assert issue["label"] in text
    assert "Internal" in text and INTERNAL_SIGNAL in text and "mixed" in text
    assert "558" in text and "@acme.cakes" in text and "3 November 2026" in text
    # the typical (median) numbers are in the snapshot
    assert "40 likes" in text and "2 comments" in text


def test_small_email_keeps_its_line_breaks(seen):
    _post(body("small"))
    assert "Hi team,\nThe link in your bio fails.\nKshitij" in seen["html"]


# --------------------------------------------------------------------------- long

def test_long_has_a_card_with_fix_text_for_every_issue(seen):
    assert _post(body("long")).status_code == 200
    text = _visible(seen["html"])
    assert "Instagram audit for Acme Co" in text
    assert text.count("What we found") == len(ISSUES)
    assert text.count("Why it matters") == len(ISSUES)
    assert text.count("What we would do") == len(ISSUES)
    for issue in ISSUES:
        assert issue["detail"] in text
        assert social_pdf.fix_for(issue["label"]) in text
        assert social_pdf.why_for(issue["label"]) in text
    # the unknown label falls back to the generic text
    assert social_pdf.fix_for("Something new nobody mapped yet") == social_pdf.GENERIC_FIX
    assert "Next step" in text and "one page plan" in text


def test_long_action_plan_runs_high_to_low(seen):
    _post(body("long"))
    text = _visible(seen["html"])
    plan = text[text.index("Action plan"):]
    order = [plan.index(lbl) for lbl in ("Link in bio is broken", "No call to action in the bio", "Thin or empty bio")]
    assert order == sorted(order)


# Internal material from body(): none of it may reach a client facing PDF.
LONG_FORBIDDEN = ("lead score", "audience quality", "internal", "Posted within the last month",
                  INTERNAL_SIGNAL, "Your bio link is broken", "The link in your bio fails",
                  "Limited view", "Cakes in Pune", "strong")


def assert_no_internal_material(html):
    """Shared with test_combined_pdf.py for the Instagram half of the combined PDF."""
    low = _visible(html).lower()
    for absent in LONG_FORBIDDEN:
        assert absent.lower() not in low, absent
    assert not re.search(r"\b(fake|fakes|bot|bots|bought|pod|pods)\b", html, re.I)


def test_long_never_carries_internal_material(seen):
    _post(body("long"))
    assert_no_internal_material(seen["html"])


def test_long_engagement_only_when_known_and_never_a_benchmark(seen):
    _post(body("long"))
    text = _visible(seen["html"])
    assert "typical post" in text.lower() and "40 likes" in text
    assert not re.search(r"\b(average|benchmark|industry|than most|compared)\b", text, re.I)
    prof = dict(body("long")["profile"], median_likes=None, median_comments=None, posts_read=None, engagement_rate=7.5)
    _post(body("long", profile=prof))
    text = _visible(seen["html"])
    assert "typical" not in text.lower() and "7.5" not in text


def test_long_generated_copy_has_no_dashes(seen, monkeypatch):
    # fixture values carry no dashes, so any dash seen is from our own copy
    monkeypatch.setattr(app_module.config, "REPLY_TO_EMAIL", "hello@mmga.agency")
    monkeypatch.setenv("YOUR_NAME", "Kshitij Gupta")
    b = body("long", website="", profile_url="")
    _post(b)
    text = _visible(seen["html"])
    for ch in ("-", "–", "—"):
        assert ch not in text, f"dash {ch!r} in the long PDF"
    for d in (social_pdf.FIX_TEXT, social_pdf.WHY_TEXT):
        for v in d.values():
            assert not re.search("[-–—]", v)
    for v in (social_pdf.GENERIC_FIX, social_pdf.GENERIC_WHY):
        assert not re.search("[-–—]", v)


def test_long_next_step_uses_the_sender_from_config(seen, monkeypatch):
    monkeypatch.setattr(app_module.config, "REPLY_TO_EMAIL", "hello@mmga.example")
    monkeypatch.setenv("YOUR_NAME", "Kshitij Gupta")
    _post(body("long"))
    text = _visible(seen["html"])
    assert "hello@mmga.example" in text and "Kshitij Gupta" in text
    monkeypatch.setattr(app_module.config, "REPLY_TO_EMAIL", None)
    monkeypatch.setattr(app_module.config, "FROM_EMAIL", None)
    _post(body("long"))
    text = _visible(seen["html"])
    assert "reply to this email" in text.lower() and "hello@mmga.example" not in text


def test_fix_and_why_copy_makes_no_unverifiable_claims():
    for v in [*social_pdf.FIX_TEXT.values(), *social_pdf.WHY_TEXT.values(), social_pdf.GENERIC_FIX,
              social_pdf.GENERIC_WHY]:
        assert not re.search(r"\d", v), v                      # no invented numbers
        assert not re.search(r"\b(we have|our clients|we helped|years|proven|guarantee)\b", v, re.I), v


# --------------------------------------------------------------------------- escaping

@pytest.mark.parametrize("mode", ["small", "long"])
def test_hostile_strings_are_escaped(seen, mode):
    evil = '<script>alert("x")</script>'
    attr = '"><img src=x onerror=alert(1)>'
    b = body(mode, company=evil, handle=attr, website=attr, timestamp=evil,
             issues=[{"severity": "high", "label": evil, "detail": attr}],
             email={"subject": evil, "body": attr},
             lead_score={"score": 1, "label": evil, "reasons": [attr]},
             audience_quality={"label": evil, "signals": [{"detail": attr}]})
    assert _post(b).status_code == 200
    html = seen["html"]
    assert "<script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html


# --------------------------------------------------------------------------- label registry

def _emitted_labels():
    """Every issue label analyzer/social_audit.py (and the audience quality
    outreach line) can emit, read from the source so a new label fails here."""
    labels = []
    for rel in ("analyzer/social_audit.py",):
        tree = ast.parse(open(os.path.join(ROOT, rel), encoding="utf-8").read())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "SocialIssue"
                    and len(node.args) >= 2):
                arg = node.args[1]
                if isinstance(arg, ast.Constant):
                    labels.append(arg.value)
                elif isinstance(arg, ast.JoinedStr):
                    for fill in ("video", "carousel", "video and carousel"):
                        labels.append("".join(v.value if isinstance(v, ast.Constant) else fill
                                              for v in arg.values))
                else:
                    raise AssertionError(f"label at line {node.lineno} is not a literal; add it to this test")
    from analyzer.audience_quality import OUTREACH_LABEL
    labels.append(OUTREACH_LABEL)
    return labels


def test_every_emitted_issue_label_has_fix_and_why_text():
    labels = _emitted_labels()
    assert len(set(labels)) >= 22
    for label in labels:
        assert social_pdf.fix_for(label) != social_pdf.GENERIC_FIX, f"no fix text for {label!r}"
        assert social_pdf.why_for(label) != social_pdf.GENERIC_WHY, f"no why text for {label!r}"


def test_no_stale_entries_in_the_fix_table():
    labels = set(_emitted_labels())
    for key in social_pdf.FIX_TEXT:
        assert key in labels or any(lbl.startswith(key) for lbl in labels), f"stale fix entry {key!r}"
