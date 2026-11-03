"""The PDF report routes: validation, the job lifecycle, download, and no path tricks."""
import asyncio
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import app as app_module


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module.config, "API_KEY", None)
    monkeypatch.setattr(app_module, "REPORTS_DIR", str(tmp_path))
    app_module._report_jobs.clear()
    return TestClient(app_module.app, raise_server_exceptions=False,
                      headers={"X-API-Key": uuid.uuid4().hex})   # own rate-limit bucket


def _fake_pipeline(monkeypatch, *, fail=None):
    async def collect(website, **_):
        if fail:
            raise RuntimeError(fail)
        return {"final_url": website, "design_probe": None}

    async def render_pdf(html):
        return b"%PDF-1.4 fake"

    monkeypatch.setattr(app_module.reporting_collect, "collect", collect)
    monkeypatch.setattr(app_module.reporting_report, "render_pdf", render_pdf)
    monkeypatch.setattr(app_module, "validate_public_url", lambda url: None)


def _wait_done(client, job_id, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        body = client.get(f"/api/report/{job_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("report job never finished")


def test_a_report_job_runs_and_the_pdf_can_be_downloaded(client, monkeypatch):
    _fake_pipeline(monkeypatch)
    with client:
        job = client.post("/api/report", json={"website": "acme.com", "company": "Acme"}).json()
        done = _wait_done(client, job["job_id"])
        assert done["status"] == "done" and done["filename"] == "MMGA-review-acme.pdf"
        pdf = client.get(f"/api/report/{job['job_id']}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["content-type"] == "application/pdf"


def test_a_bare_domain_gets_https(client, monkeypatch):
    _fake_pipeline(monkeypatch)
    seen = []

    async def collect(website, **_):
        seen.append(website)
        return {"final_url": website}

    monkeypatch.setattr(app_module.reporting_collect, "collect", collect)
    with client:
        job = client.post("/api/report", json={"website": "acme.com"}).json()
        _wait_done(client, job["job_id"])
    assert seen == ["https://acme.com"]


def test_a_failed_job_reports_the_reason_and_has_no_pdf(client, monkeypatch):
    _fake_pipeline(monkeypatch, fail="the website refused to load")
    with client:
        job = client.post("/api/report", json={"website": "acme.com"}).json()
        done = _wait_done(client, job["job_id"])
        assert done["status"] == "failed" and "refused to load" in done["error"]
        assert client.get(f"/api/report/{job['job_id']}/pdf").status_code == 404


def test_a_private_address_is_refused_before_any_browser_work(client, monkeypatch):
    called = []
    monkeypatch.setattr(app_module.reporting_collect, "collect", lambda w: called.append(w))
    r = client.post("/api/report", json={"website": "http://169.254.169.254/latest/meta-data"})
    assert r.status_code == 400 and not called


def test_unknown_and_malformed_job_ids_are_404_never_a_file_read(client):
    assert client.get("/api/report/" + "0" * 32).status_code == 404
    assert client.get("/api/report/..%2F..%2Fetc%2Fpasswd/pdf").status_code in (404, 405)
    assert client.get("/api/report/not-a-job/pdf").status_code == 404


def test_a_finished_job_exposes_its_key_findings_by_title(client, monkeypatch):
    # The Full Audit page shows these instead of auditing the site a second time.
    _fake_pipeline(monkeypatch)

    async def collect(website, **_):
        return {"final_url": website, "mobile_overflow": True, "mobile_scroll_width": 520}

    monkeypatch.setattr(app_module.reporting_collect, "collect", collect)
    with client:
        job = client.post("/api/report", json={"website": "acme.com"}).json()
        done = _wait_done(client, job["job_id"])
    assert done["status"] == "done" and done["findings"] == 1
    assert done["key_findings"] == [{"title": "On a phone the page scrolls sideways", "tag": "Next"}]
    assert done["queued"] is False


def test_key_findings_are_capped_at_five(client, monkeypatch):
    _fake_pipeline(monkeypatch)
    many = [{"key": f"k{i}", "title": f"Finding {i}", "saw": "", "advice": "", "impact": "moderate",
             "image": None, "caption": "", "number": i, "tag": "Next"} for i in range(8)]
    monkeypatch.setattr(app_module.reporting_report, "build_report",
                        lambda *a, **k: {"company": "Acme", "findings": many})
    monkeypatch.setattr(app_module.reporting_report, "render_html", lambda built: "<html></html>")
    with client:
        job = client.post("/api/report", json={"website": "acme.com"}).json()
        done = _wait_done(client, job["job_id"])
    assert done["findings"] == 8
    assert [f["title"] for f in done["key_findings"]] == [f"Finding {i}" for i in range(5)]


def test_a_job_waiting_for_the_browser_says_queued_then_moves_on(client, monkeypatch):
    # The single browser slot is busy: the job is waiting, not failing.
    _fake_pipeline(monkeypatch)
    gate = {"open": False}

    async def collect(website, on_stage=None):
        on_stage("queued")
        while not gate["open"]:
            await asyncio.sleep(0.01)
        on_stage("visiting")
        return {"final_url": website}

    monkeypatch.setattr(app_module.reporting_collect, "collect", collect)
    with client:
        job = client.post("/api/report", json={"website": "acme.com"}).json()
        end = time.time() + 5
        while True:
            st = client.get(f"/api/report/{job['job_id']}").json()
            if st.get("queued"):
                break
            assert time.time() < end, st
            time.sleep(0.02)
        assert st["status"] == "running" and "waiting" in st["stage"].lower()
        gate["open"] = True
        done = _wait_done(client, job["job_id"])
    assert done["status"] == "done" and done["queued"] is False


def test_a_running_job_reports_a_stage_line(client, monkeypatch):
    _fake_pipeline(monkeypatch)
    gate = {"open": False}

    async def collect(website, on_stage=None):
        on_stage("visiting")
        while not gate["open"]:
            await asyncio.sleep(0.01)
        return {"final_url": website}

    monkeypatch.setattr(app_module.reporting_collect, "collect", collect)
    with client:
        job = client.post("/api/report", json={"website": "acme.com"}).json()
        end = time.time() + 5
        while True:
            st = client.get(f"/api/report/{job['job_id']}").json()
            if st.get("stage") and "visiting" in st["stage"].lower():
                break
            assert time.time() < end, st
            time.sleep(0.02)
        assert st["queued"] is False
        gate["open"] = True
        _wait_done(client, job["job_id"])


async def test_collect_reports_queued_and_visiting_from_the_browser_slot(monkeypatch):
    from analyzer import visuals
    from reporting import collect as collect_mod
    stages = []

    class Stop(Exception):
        pass

    async def acquire(on_queued=None, on_started=None):
        on_queued()
        on_started()
        raise Stop()

    monkeypatch.setattr(visuals, "_acquire_playwright_slot", acquire)
    with pytest.raises(Stop):
        await collect_mod.collect("https://acme.example", on_stage=stages.append)
    assert stages == ["queued", "visiting"]


def test_the_routes_require_the_api_key():
    import inspect
    for fn in (app_module.create_report, app_module.report_status, app_module.report_pdf):
        assert "require_api_key" in inspect.getsource(fn) or "_auth" in inspect.signature(fn).parameters
