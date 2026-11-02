from fastapi.testclient import TestClient

import app as app_module


_H = {"X-API-Key": app_module.config.API_KEY or "dp"}


def _client():
    return TestClient(app_module.app, raise_server_exceptions=False)


def test_empty_draft_is_rejected():
    r = _client().post("/api/draft-pdf", json={"subject": " ", "body": ""}, headers=_H)
    assert r.status_code == 400


def test_draft_renders_to_a_real_pdf_with_escaped_text(monkeypatch):
    seen = {}

    async def fake_render(html):
        seen["html"] = html
        return b"%PDF-1.4 fake"

    monkeypatch.setattr(app_module.reporting_report, "render_pdf", fake_render)
    r = _client().post(
        "/api/draft-pdf",
        json={"subject": "acme.com: Slow", "body": "Hi <script>x</script>\nBye", "company": "Acme Co",
              "target_email": "a@acme.com", "extra_emails": ["b@acme.com"]},
        headers=_H,
    )
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert "Acme-Co-email.pdf" in r.headers["content-disposition"]
    assert "<script>" not in seen["html"] and "&lt;script&gt;" in seen["html"]
    assert "a@acme.com, b@acme.com" in seen["html"]

