"""Combined PDF: the website long design audit followed by the long (client
facing) Instagram audit, merged into one file. No browser and no network: the
renderer and the website report pipeline are faked with tiny generated PDFs."""

import io
import os
import uuid

import pytest
from pypdf import PdfReader, PdfWriter

from reporting import combine


def tiny_pdf(pages: int, width: float = 595, height: float = 842, title: str = "") -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=width, height=height)
    if title:
        w.add_metadata({"/Title": title})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- merge

def test_merge_keeps_every_page_in_order_with_sizes_and_title():
    website = tiny_pdf(3, 595, 842, title="Design and conversion review")
    social = tiny_pdf(2, 612, 792, title="Instagram audit")
    merged = combine.merge_pdfs([website, social], title="Acme website and Instagram audit")
    r = PdfReader(io.BytesIO(merged))
    assert len(r.pages) == 5
    sizes = [(round(float(p.mediabox.width)), round(float(p.mediabox.height))) for p in r.pages]
    assert sizes == [(595, 842)] * 3 + [(612, 792)] * 2
    assert r.metadata.title == "Acme website and Instagram audit"


def test_merge_refuses_a_broken_part():
    with pytest.raises(combine.CombineError):
        combine.merge_pdfs([tiny_pdf(1), b"not a pdf at all"], title="x")
    with pytest.raises(combine.CombineError):
        combine.merge_pdfs([tiny_pdf(1), tiny_pdf(0)], title="x")


# --------------------------------------------------------------------------- route

import time

from fastapi.testclient import TestClient

import app as app_module
from security_utils import UnsafeURLError
from test_social_pdf import assert_no_internal_material, body as social_body

WEBSITE_PAGES, SOCIAL_PAGES = 3, 2


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Fake website report pipeline and renderer. env['social_html'] collects
    every HTML page printed for the Instagram half."""
    got = {"social_html": [], "collected": []}
    monkeypatch.setattr(app_module.config, "API_KEY", None)
    monkeypatch.setattr(app_module, "REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "validate_public_url", lambda url: None)
    app_module._report_jobs.clear()

    async def collect(website):
        got["collected"].append(website)
        if got.get("fail"):
            raise RuntimeError(got["fail"])
        return {"final_url": website, "design_probe": None}

    async def render_pdf(html, margin_mm=0):
        if margin_mm == 14:        # the social long audit
            got["social_html"].append(html)
            return tiny_pdf(SOCIAL_PAGES, 612, 792, title="Instagram audit")
        return tiny_pdf(WEBSITE_PAGES, 595, 842, title="Design review")

    monkeypatch.setattr(app_module.reporting_collect, "collect", collect)
    monkeypatch.setattr(app_module.reporting_report, "render_pdf", render_pdf)
    app_module.app.dependency_overrides[app_module.COMBINED_PDF_RATE_LIMIT] = lambda: None
    got["client"] = TestClient(app_module.app, raise_server_exceptions=False,
                               headers={"X-API-Key": uuid.uuid4().hex})
    yield got
    app_module.app.dependency_overrides.pop(app_module.COMBINED_PDF_RATE_LIMIT, None)
    app_module._report_jobs.clear()


def _wait(client, job_id, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"/api/report/{job_id}").json()
        if st["status"] != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("report job never finished")


def _report_job(env, website="acme.example"):
    job = env["client"].post("/api/report", json={"website": website, "company": "Acme Co"}).json()
    _wait(env["client"], job["job_id"])
    return job["job_id"]


def combined_body(job_id, **over):
    b = social_body("long")
    b["report_job_id"] = job_id
    b.update(over)
    return b


def _seed_job(env, **job):
    job_id = uuid.uuid4().hex
    app_module._report_jobs[job_id] = {"created": time.time(), "website": "https://acme.example", **job}
    return job_id


def test_combined_pdf_is_the_website_report_then_the_instagram_audit(env):
    c = env["client"]
    with c:
        job_id = _report_job(env)
        r = c.post("/api/combined-pdf", json=combined_body(job_id))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert 'filename="Acme-Co-website-and-instagram-audit.pdf"' in r.headers["content-disposition"]
    pdf = PdfReader(io.BytesIO(r.content))
    assert len(pdf.pages) == WEBSITE_PAGES + SOCIAL_PAGES
    widths = [round(float(p.mediabox.width)) for p in pdf.pages]
    assert widths == [595] * WEBSITE_PAGES + [612] * SOCIAL_PAGES     # website first
    assert pdf.metadata.title == "Acme Co website and Instagram audit"
    # the Instagram half is the long client facing audit, with nothing internal
    assert len(env["social_html"]) == 1
    html = env["social_html"][0]
    assert "Instagram audit for Acme Co" in html
    assert_no_internal_material(html)


def test_requires_the_api_key(env, monkeypatch):
    monkeypatch.setattr(app_module.config, "API_KEY", "secret")
    r = TestClient(app_module.app).post("/api/combined-pdf", json=combined_body("0" * 32),
                                        headers={"X-API-Key": "wrong"})
    assert r.status_code == 401 and not env["social_html"]


@pytest.mark.parametrize("bad", ["", "abc", "../../etc/passwd", "0" * 31, "G" * 32, "0" * 32 + "/x"])
def test_an_invalid_job_id_is_422_and_nothing_is_read(env, bad):
    r = env["client"].post("/api/combined-pdf", json=combined_body(bad))
    assert r.status_code == 422 and not env["social_html"]


def test_other_bad_payloads_are_422(env):
    c = env["client"]
    job_id = _seed_job(env, status="done", path="x")
    assert c.post("/api/combined-pdf", json=combined_body(job_id, company="x" * 5000)).status_code == 422
    assert c.post("/api/combined-pdf", json=combined_body(job_id, mode="small")).status_code == 422
    assert c.post("/api/combined-pdf", json=social_body("long")).status_code == 422   # no job id
    # the combined file is the Instagram audit only
    assert c.post("/api/combined-pdf", json=combined_body(job_id, platform="facebook")).status_code == 422
    assert not env["social_html"]


def test_nothing_to_render_or_no_website_is_400(env):
    c = env["client"]
    job_id = _seed_job(env, status="done", path="x")
    assert c.post("/api/combined-pdf", json=combined_body(job_id, issues=[], profile={})).status_code == 400
    assert c.post("/api/combined-pdf", json=combined_body(job_id, website="")).status_code == 400
    assert not env["social_html"]


def test_an_unsafe_website_is_refused(env, monkeypatch):
    def refuse(url):
        raise UnsafeURLError("private address")

    monkeypatch.setattr(app_module, "validate_public_url", refuse)
    job_id = _seed_job(env, status="done", path="x", website="http://169.254.169.254")
    r = env["client"].post("/api/combined-pdf", json=combined_body(job_id, website="http://169.254.169.254"))
    assert r.status_code == 400 and "Refusing" in r.json()["detail"] and not env["social_html"]


def test_unknown_running_and_mismatched_jobs_are_refused(env, tmp_path):
    c = env["client"]
    assert c.post("/api/combined-pdf", json=combined_body("0" * 32)).status_code == 404
    running = _seed_job(env, status="running")
    assert c.post("/api/combined-pdf", json=combined_body(running)).status_code == 409
    path = tmp_path / "other.pdf"
    path.write_bytes(tiny_pdf(1))
    other_site = _seed_job(env, status="done", path=str(path), website="https://someone-else.example")
    r = c.post("/api/combined-pdf", json=combined_body(other_site))
    assert r.status_code == 409 and "different website" in r.json()["detail"]
    assert not env["social_html"]


def test_only_a_file_the_job_store_wrote_in_the_reports_folder_is_read(env, tmp_path):
    outside = tmp_path.parent / f"outside-{uuid.uuid4().hex}.pdf"
    outside.write_bytes(tiny_pdf(1))
    try:
        job_id = _seed_job(env, status="done", path=str(outside))
        r = env["client"].post("/api/combined-pdf", json=combined_body(job_id))
        assert r.status_code == 404 and not env["social_html"]
    finally:
        outside.unlink()


def test_a_failed_website_report_is_502_with_its_reason_and_nothing_partial(env):
    env["fail"] = "the website refused to load"
    c = env["client"]
    with c:
        job_id = _report_job(env)
        r = c.post("/api/combined-pdf", json=combined_body(job_id))
    assert r.status_code == 502
    assert "refused to load" in r.json()["detail"]
    assert not r.content.startswith(b"%PDF") and not env["social_html"]


def test_a_broken_website_pdf_is_502_and_nothing_partial(env, tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"this is not a pdf")
    job_id = _seed_job(env, status="done", path=str(path))
    r = env["client"].post("/api/combined-pdf", json=combined_body(job_id))
    assert r.status_code == 502 and not r.content.startswith(b"%PDF")


def test_a_bare_domain_matches_the_job_made_for_it(env):
    c = env["client"]
    with c:
        job_id = _report_job(env, website="acme.example")
        r = c.post("/api/combined-pdf", json=combined_body(job_id, website="acme.example/"))
    assert r.status_code == 200, r.text
    assert env["collected"] == ["https://acme.example"]


def test_the_route_is_rate_limited_and_keyed():
    import inspect
    src = inspect.getsource(app_module.combined_pdf)
    assert "require_api_key" in src and "COMBINED_PDF_RATE_LIMIT" in src
