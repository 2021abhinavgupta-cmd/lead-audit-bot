from fastapi.testclient import TestClient
import app as app_module
from scrapers.social.base import SocialProfile


def test_audit_runs_instagram_and_returns_issues_and_outreach(monkeypatch):
    monkeypatch.setattr(app_module.config, "API_KEY", None)

    fake_profile = SocialProfile(
        platform="instagram", handle="acme", url="https://instagram.com/acme",
        followers=3000, bio="candles", posts_last_30_days=0, last_post_age_days=90,
        uses_video=False, has_link_in_bio=False,
    )
    monkeypatch.setitem(app_module._SOCIAL_ADAPTERS, "instagram", lambda handle: fake_profile)

    def fake_outreach(company, profile, issues, channel, your_name="Kshitij", ai_call=None):
        return {"subject": "" if channel != "email" else "S", "body": f"{channel} body", "cost": 0.0}
    monkeypatch.setattr(app_module, "generate_social_outreach", fake_outreach)

    with TestClient(app_module.app, raise_server_exceptions=False) as client:
        r = client.post("/api/social/audit", json={
            "company": "Acme Candles",
            "handles": {"instagram": "https://instagram.com/acme"},
        })
        assert r.status_code == 200, r.text
        res = r.json()["results"]["instagram"]
        assert res["profile"]["followers"] == 3000
        assert any(i["severity"] == "high" for i in res["issues"])
        assert res["outreach"]["dm"]["body"] == "dm body"
        assert res["outreach"]["email"]["subject"] == "S"


def test_audit_checks_the_bio_link_and_passes_the_website(monkeypatch):
    monkeypatch.setattr(app_module.config, "API_KEY", None)
    profile = SocialProfile(
        platform="instagram", handle="acme", url="https://instagram.com/acme",
        followers=3000, bio="Handmade candles from our Pune studio. DM to order.",
        has_link_in_bio=True, link_url="https://acme.example/shop",
    )
    monkeypatch.setitem(app_module._SOCIAL_ADAPTERS, "instagram", lambda handle: profile)
    checked = []

    def fake_check(url):
        checked.append(url)
        return {"verdict": "broken", "status": 404, "final_url": url}
    monkeypatch.setattr(app_module, "check_bio_link", fake_check)
    monkeypatch.setattr(app_module, "generate_social_outreach",
                        lambda *a, **k: {"subject": "", "body": "b", "cost": 0.0})

    with TestClient(app_module.app, raise_server_exceptions=False) as client:
        r = client.post("/api/social/audit", json={
            "company": "Acme", "website": "https://acme.example",
            "handles": {"instagram": "https://instagram.com/acme"},
        })
        assert r.status_code == 200, r.text
        res = r.json()["results"]["instagram"]
    assert checked == ["https://acme.example/shop"]
    assert res["issues"][0]["label"] == "Link in bio is broken"
    assert res["link_check"]["verdict"] == "broken"
    assert res["lead_score"]["label"] in ("strong", "maybe", "weak")
    assert res["lead_score"]["reasons"]


def test_unanalyzed_and_missing_profiles_get_a_weak_lead_score(monkeypatch):
    from scrapers.social.base import unanalyzed
    monkeypatch.setattr(app_module.config, "API_KEY", None)
    monkeypatch.setitem(app_module._SOCIAL_ADAPTERS, "instagram",
                        lambda handle: unanalyzed("instagram", "x", "u", "This Instagram account is private."))
    monkeypatch.setitem(app_module._SOCIAL_ADAPTERS, "facebook", lambda handle: None)
    with TestClient(app_module.app, raise_server_exceptions=False) as client:
        r = client.post("/api/social/audit", json={
            "company": "Acme", "handles": {"instagram": "x", "facebook": "https://facebook.com/x"}})
        assert r.status_code == 200, r.text
        results = r.json()["results"]
    assert results["instagram"]["lead_score"]["label"] == "weak"
    assert results["facebook"]["lead_score"]["label"] == "weak"


def test_audit_without_a_bio_link_fetches_nothing(monkeypatch):
    monkeypatch.setattr(app_module.config, "API_KEY", None)
    profile = SocialProfile(platform="instagram", handle="acme", url="u", followers=10, bio="candles")
    monkeypatch.setitem(app_module._SOCIAL_ADAPTERS, "instagram", lambda handle: profile)
    monkeypatch.setattr(app_module, "check_bio_link", lambda url: (_ for _ in ()).throw(AssertionError("fetched")))
    monkeypatch.setattr(app_module, "generate_social_outreach",
                        lambda *a, **k: {"subject": "", "body": "b", "cost": 0.0})
    with TestClient(app_module.app, raise_server_exceptions=False) as client:
        r = client.post("/api/social/audit", json={"company": "Acme", "handles": {"instagram": "acme"}})
        assert r.status_code == 200, r.text


def test_adapter_returning_none_is_a_note_not_a_crash(monkeypatch):
    monkeypatch.setattr(app_module.config, "API_KEY", None)
    monkeypatch.setitem(app_module._SOCIAL_ADAPTERS, "facebook", lambda handle: None)
    with TestClient(app_module.app, raise_server_exceptions=False) as client:
        r = client.post("/api/social/audit", json={
            "company": "Acme", "handles": {"facebook": "https://facebook.com/acme"},
        })
        assert r.status_code == 200
        res = r.json()["results"]["facebook"]
        assert res["profile"] is None
        assert res["issues"] == []
        assert "note" in res


def test_routes_gated():
    import inspect
    for fn in (app_module.social_audit, app_module.social_audit_progress, app_module.social_audit_result):
        src = inspect.getsource(fn)
        assert "require_api_key" in src and "rate_limit" in src
