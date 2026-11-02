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



def test_small_pdf_inlines_the_screenshot_and_ignores_path_tricks(monkeypatch, tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    (shots / "acme_audit.jpg").write_bytes(b"\xff\xd8\xff jpeg")
    (tmp_path / "secret.txt").write_bytes(b"nope")
    monkeypatch.setattr(app_module, "SCREENSHOTS_DIR", str(shots))
    seen = {}

    async def fake_render(html):
        seen["html"] = html
        return b"%PDF-1.4 fake"

    monkeypatch.setattr(app_module.reporting_report, "render_pdf", fake_render)
    body = {"subject": "S", "body": "B", "image_url": "/screenshots/acme_audit.jpg",
            "review_warnings": ["Check the 7 broken links"], "sector": "agriculture", "niche": "Dairy"}
    assert _client().post("/api/draft-pdf", json=body, headers=_H).status_code == 200
    assert "data:image/jpeg;base64," in seen["html"]
    assert "Check the 7 broken links" in seen["html"] and "agriculture / Dairy" in seen["html"]

    body["image_url"] = "/screenshots/../secret.txt"
    _client().post("/api/draft-pdf", json=body, headers=_H)
    assert "base64," not in seen["html"]
