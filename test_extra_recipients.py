"""
Extra To recipients: other addresses a lead publishes on its contact/about
pages ride on the same email's To line.

Everything is offline: the page fetches are faked, the database is a scratch
SQLite file, and the transports are recording stand-ins.
"""

import smtplib
import uuid
from datetime import datetime

import pytest

import config
from enrichment import decision_maker as dm
from enrichment.decision_maker import DecisionMaker
from emailer.gmail_sender import GmailSender
from emailer.ses_sender import SESSender
from storage import db


def _use_temp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.sqlite"))


class _Resp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


class _FakeClient:
    """Serves fixed HTML per URL path; anything else is a 404."""

    def __init__(self, pages):
        self.pages = pages

    def get(self, url, headers=None, follow_redirects=True):
        path = "/" + url.split("://", 1)[-1].split("/", 1)[-1] if "/" in url.split("://", 1)[-1] else "/"
        path = path.rstrip("/") or "/"
        for known, html in self.pages.items():
            if (known.rstrip("/") or "/") == path:
                return _Resp(html)
        return _Resp("", 404)


def _maker(monkeypatch, tmp_path, pages):
    _use_temp_db(monkeypatch, tmp_path)
    m = DecisionMaker()
    m.client = _FakeClient(pages)
    monkeypatch.setattr(config, "EXTRA_RECIPIENTS_MAX", 2)
    return m


def _mail(*addresses):
    return "".join(f'<a href="mailto:{a}">{a}</a> ' for a in addresses)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_other_addresses_on_the_contact_page_are_added(monkeypatch, tmp_path):
    m = _maker(monkeypatch, tmp_path, {"/contact": _mail("info@acme.co", "priya@acme.co", "sales@acme.co")})

    extras = m.find_extra_recipients("https://acme.co", "info@acme.co")

    assert "info@acme.co" not in extras                    # the primary is never repeated
    assert set(extras) == {"priya@acme.co", "sales@acme.co"}


def test_best_addresses_come_first_and_the_cap_is_respected(monkeypatch, tmp_path):
    m = _maker(monkeypatch, tmp_path, {
        "/contact": _mail("zed@acme.co", "ceo@acme.co", "marketing@acme.co", "info@acme.co"),
    })

    extras = m.find_extra_recipients("https://acme.co", "hello@acme.co")

    assert extras == ["ceo@acme.co", "marketing@acme.co"]  # cap of 2, ranked


def test_a_cap_of_zero_turns_the_feature_off(monkeypatch, tmp_path):
    m = _maker(monkeypatch, tmp_path, {"/contact": _mail("priya@acme.co")})
    monkeypatch.setattr(config, "EXTRA_RECIPIENTS_MAX", 0)

    assert m.find_extra_recipients("https://acme.co", "info@acme.co") == []


def test_addresses_on_other_domains_are_ignored(monkeypatch, tmp_path):
    """A web agency's credit or a hosting provider's address is not the lead."""
    m = _maker(monkeypatch, tmp_path, {"/contact": _mail("support@somehost.com", "team@webagency.io", "priya@acme.co")})

    assert m.find_extra_recipients("https://acme.co", "info@acme.co") == ["priya@acme.co"]


def test_subdomain_addresses_count_as_the_lead(monkeypatch, tmp_path):
    m = _maker(monkeypatch, tmp_path, {"/contact": _mail("sales@mail.acme.co")})

    assert m.find_extra_recipients("https://www.acme.co", "info@acme.co") == ["sales@mail.acme.co"]


def test_a_gmail_address_counts_only_when_it_sits_on_a_contact_page(monkeypatch, tmp_path):
    m = _maker(monkeypatch, tmp_path, {
        "/": _mail("webdev.rahul@gmail.com"),              # homepage footer: probably the developer
        "/contact": _mail("owner.priya@gmail.com"),        # contact page: the business's own
    })

    extras = m.find_extra_recipients(
        "https://acme.co", "info@acme.co", html_content=_mail("webdev.rahul@gmail.com"),
    )

    assert extras == ["owner.priya@gmail.com"]


@pytest.mark.parametrize("local", ["careers", "jobs", "hr", "billing", "accounts", "legal", "privacy", "press", "noreply"])
def test_mailboxes_that_are_not_buyers_are_dropped(monkeypatch, tmp_path, local):
    m = _maker(monkeypatch, tmp_path, {"/contact": _mail(f"{local}@acme.co")})

    assert m.find_extra_recipients("https://acme.co", "info@acme.co") == []


def test_an_unsubscribed_address_is_never_added(monkeypatch, tmp_path):
    m = _maker(monkeypatch, tmp_path, {"/contact": _mail("priya@acme.co", "ravi@acme.co")})
    db.add_suppression("priya@acme.co")

    assert m.find_extra_recipients("https://acme.co", "info@acme.co") == ["ravi@acme.co"]


def test_image_file_names_that_look_like_addresses_are_ignored(monkeypatch, tmp_path):
    m = _maker(monkeypatch, tmp_path, {"/contact": "<img src='logo@2x.png'> " + _mail("priya@acme.co")})

    assert m.find_extra_recipients("https://acme.co", "info@acme.co") == ["priya@acme.co"]


def test_unreachable_pages_add_nothing_and_never_raise(monkeypatch, tmp_path):
    m = _maker(monkeypatch, tmp_path, {})

    class Boom:
        def get(self, *a, **k):
            raise RuntimeError("network down")

    m.client = Boom()
    assert m.find_extra_recipients("https://acme.co", "info@acme.co") == []


def test_a_scraped_primary_brings_its_extras_through_find_decision_maker(monkeypatch, tmp_path):
    m = _maker(monkeypatch, tmp_path, {"/contact": _mail("info@acme.co", "priya@acme.co")})
    monkeypatch.setattr(dm.DecisionMaker, "domain_accepts_mail", staticmethod(lambda email: True))

    result = m.find_decision_maker("Acme", "https://acme.co", html_content=_mail("info@acme.co"))

    assert result["email"] == "info@acme.co"
    assert result["is_guess"] is False
    assert result["extra_emails"] == ["priya@acme.co"]


def test_a_guessed_primary_never_gets_extras(monkeypatch, tmp_path):
    """Only a published address proves the site wants to be written to."""
    m = _maker(monkeypatch, tmp_path, {"/contact": _mail("priya@acme.co")})
    monkeypatch.setattr(dm.DecisionMaker, "domain_accepts_mail", staticmethod(lambda email: True))
    monkeypatch.setattr(m, "_scrape_website_for_email", lambda *a, **k: "")
    monkeypatch.setattr(m, "_find_ceo_name", lambda company: "")
    monkeypatch.setattr(m, "_find_email_via_osint", lambda *a, **k: "")
    monkeypatch.setattr(m, "_find_via_claude_web_fetch", lambda *a, **k: {})

    result = m.find_decision_maker("Acme", "https://acme.co")

    assert result["is_guess"] is True
    assert result["extra_emails"] == []


def test_a_failing_extras_lookup_does_not_lose_the_lead(monkeypatch, tmp_path):
    m = _maker(monkeypatch, tmp_path, {})
    monkeypatch.setattr(dm.DecisionMaker, "domain_accepts_mail", staticmethod(lambda email: True))
    monkeypatch.setattr(m, "find_extra_recipients", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    result = m.find_decision_maker("Acme", "https://acme.co", html_content=_mail("info@acme.co"))

    assert result["email"] == "info@acme.co"
    assert result["extra_emails"] == []


# ---------------------------------------------------------------------------
# Sending: one message, everyone on the To line, everyone in the envelope
# ---------------------------------------------------------------------------

class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.sent.append((msg, from_addr, to_addrs))


@pytest.fixture
def gmail(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(config, "GMAIL_USER", "outreach@example.com")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.setattr(config, "BCC_EMAIL", "")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr("emailer.base_sender.db.is_suppressed", lambda email: False)
    return GmailSender()


def test_the_to_header_and_the_envelope_carry_every_recipient(gmail):
    gmail.send_email("info@acme.co", "S", "B", extra_to=["priya@acme.co", "ravi@acme.co"])

    msg, _, envelope = FakeSMTP.instances[-1].sent[0]
    assert msg["To"] == "info@acme.co, priya@acme.co, ravi@acme.co"
    assert envelope == ["info@acme.co", "priya@acme.co", "ravi@acme.co"]


def test_the_bcc_copy_is_still_envelope_only(gmail, monkeypatch):
    monkeypatch.setattr(config, "BCC_EMAIL", "marketing@mmga.agency")

    gmail.send_email("info@acme.co", "S", "B", extra_to=["priya@acme.co"])

    msg, _, envelope = FakeSMTP.instances[-1].sent[0]
    assert envelope == ["info@acme.co", "priya@acme.co", "marketing@mmga.agency"]
    assert "marketing@mmga.agency" not in msg["To"]


def test_a_send_without_extras_is_unchanged(gmail):
    gmail.send_email("info@acme.co", "S", "B")

    msg, _, envelope = FakeSMTP.instances[-1].sent[0]
    assert msg["To"] == "info@acme.co"
    assert envelope == ["info@acme.co"]


def test_an_extra_that_unsubscribed_meanwhile_is_dropped_at_send_time(gmail, monkeypatch):
    monkeypatch.setattr("emailer.base_sender.db.is_suppressed", lambda email: email == "priya@acme.co")

    gmail.send_email("info@acme.co", "S", "B", extra_to=["priya@acme.co", "ravi@acme.co"])

    msg, _, envelope = FakeSMTP.instances[-1].sent[0]
    assert envelope == ["info@acme.co", "ravi@acme.co"]
    assert "priya@acme.co" not in msg["To"]


def test_duplicates_and_the_primary_are_not_repeated(gmail):
    gmail.send_email("info@acme.co", "S", "B", extra_to=["INFO@acme.co", "ravi@acme.co", "ravi@acme.co", ""])

    assert FakeSMTP.instances[-1].sent[0][0]["To"] == "info@acme.co, ravi@acme.co"


def test_a_newline_in_a_recipient_is_refused(gmail):
    with pytest.raises(ValueError):
        gmail.send_email("info@acme.co", "S", "B", extra_to=["x@acme.co\r\nBcc: evil@bad.com"])


def test_ses_delivers_to_every_address_on_the_to_line(monkeypatch):
    sent = {}

    class FakeSES:
        def send_raw_email(self, **kwargs):
            sent.update(kwargs)

    monkeypatch.setattr(config, "BCC_EMAIL", "")
    monkeypatch.setattr("emailer.base_sender.db.is_suppressed", lambda email: False)
    sender = SESSender()
    sender.client = FakeSES()

    sender.send_email("info@acme.co", "S", "B", extra_to=["priya@acme.co"])

    assert sent["Destinations"] == ["info@acme.co", "priya@acme.co"]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def test_schema_is_at_least_v17(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    db.init_db()
    import sqlite3
    conn = sqlite3.connect(db.DB_PATH)
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 17
    conn.close()


def test_a_draft_round_trips_its_extra_emails(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    db.log_draft("Acme", "acme.co", "info@acme.co", "S", "B", extra_emails=["priya@acme.co"])

    assert db.get_draft_by_website("acme.co")["extra_emails"] == ["priya@acme.co"]
    assert db.get_drafts()[0]["extra_emails"] == ["priya@acme.co"]


def test_a_draft_without_extras_reads_back_an_empty_list(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    db.log_draft("Acme", "acme.co", "info@acme.co", "S", "B")

    assert db.get_draft_by_website("acme.co")["extra_emails"] == []


def test_history_records_who_else_was_on_the_to_line(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    db.log_email("Acme", "acme.co", "info@acme.co", "me@x.com", "S", "B", message_id="<a@x>",
                 extra_emails=["priya@acme.co"])
    db.log_email("Beta", "beta.co", "hi@beta.co", "me@x.com", "S", "B", message_id="<b@x>")

    rows = {r["company"]: r for r in db.get_email_history()}
    assert rows["Acme"]["extra_emails"] == ["priya@acme.co"]
    assert rows["Beta"]["extra_emails"] == []


def test_co_recipients_are_found_from_either_side(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    db.log_email("Acme", "acme.co", "info@acme.co", "me@x.com", "S", "B",
                 extra_emails=["priya@acme.co", "ravi@acme.co"])

    assert db.get_co_recipients("priya@acme.co") == ["info@acme.co", "ravi@acme.co"]
    assert db.get_co_recipients("info@acme.co") == ["priya@acme.co", "ravi@acme.co"]
    assert db.get_co_recipients("stranger@elsewhere.com") == []


# ---------------------------------------------------------------------------
# /api/send and /unsubscribe
# ---------------------------------------------------------------------------

def _fresh():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _send_client(monkeypatch, draft):
    from fastapi.testclient import TestClient
    import app as app_module

    monkeypatch.setattr(app_module.config, "API_KEY", None)
    monkeypatch.setattr(app_module.config, "EXTRA_RECIPIENTS_MAX", 2)
    monkeypatch.setattr(app_module.db, "count_emails_sent_today", lambda: 0)
    monkeypatch.setattr(app_module.db, "get_draft_by_website", lambda website: draft)
    monkeypatch.setattr(app_module.db, "is_suppressed", lambda email: email == "gone@acme.co")
    monkeypatch.setattr(app_module.db, "log_cost", lambda *a, **k: None)
    monkeypatch.setattr(app_module.db, "delete_draft_by_website", lambda *a, **k: None)
    monkeypatch.setattr(app_module.sheets, "find_row_by_website", lambda *a, **k: None)

    seen = {"send": None, "log": None}

    def fake_send(*a, **k):
        seen["send"] = k
        return "<msg@example.com>"

    monkeypatch.setattr(app_module.ses, "send_email", fake_send)
    monkeypatch.setattr(app_module.db, "log_email", lambda *a, **k: seen.update(log=k))

    client = TestClient(app_module.app, raise_server_exceptions=False, headers={"X-API-Key": uuid.uuid4().hex})
    return client, seen


_PAYLOAD = {
    "email": "info@acme.co", "subject": "Quick note", "body": "Hi there",
    "company": "Acme", "website": "https://acme.co",
}


def test_send_uses_the_drafts_extras_when_the_client_sends_none(monkeypatch):
    client, seen = _send_client(monkeypatch, {"timestamp": _fresh(), "review_warnings": [], "extra_emails": ["priya@acme.co"]})

    res = client.post("/api/send", json=_PAYLOAD)

    assert res.status_code == 200, res.text
    assert seen["send"]["extra_to"] == ["priya@acme.co"]
    assert seen["log"]["extra_emails"] == ["priya@acme.co"]


def test_send_honours_the_reviewers_removals(monkeypatch):
    """An empty list from the client means the reviewer removed every extra."""
    client, seen = _send_client(monkeypatch, {"timestamp": _fresh(), "review_warnings": [], "extra_emails": ["priya@acme.co"]})

    res = client.post("/api/send", json={**_PAYLOAD, "extra_emails": []})

    assert res.status_code == 200, res.text
    assert "extra_to" not in seen["send"]
    assert seen["log"]["extra_emails"] == []


def test_send_filters_invalid_suppressed_duplicate_and_excess_extras(monkeypatch):
    client, seen = _send_client(monkeypatch, {"timestamp": _fresh(), "review_warnings": []})

    res = client.post("/api/send", json={**_PAYLOAD, "extra_emails": [
        "not-an-email", "gone@acme.co", "info@acme.co", "a@acme.co", "a@acme.co", "b@acme.co", "c@acme.co",
    ]})

    assert res.status_code == 200, res.text
    assert seen["send"]["extra_to"] == ["a@acme.co", "b@acme.co"]    # capped at 2, nothing invalid


def test_unsubscribing_from_a_shared_email_opts_out_everyone_on_it(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import app as app_module

    _use_temp_db(monkeypatch, tmp_path)
    db.log_email("Acme", "acme.co", "info@acme.co", "me@x.com", "S", "B",
                 extra_emails=["priya@acme.co", "ravi@acme.co"])
    monkeypatch.setattr(app_module.sheets, "find_row_by_email", lambda *a, **k: None)

    res = TestClient(app_module.app).post("/unsubscribe?email=priya@acme.co")

    assert res.status_code == 200
    assert db.is_suppressed("priya@acme.co")
    assert db.is_suppressed("info@acme.co")
    assert db.is_suppressed("ravi@acme.co")
    assert not db.is_suppressed("someone@else.com")
