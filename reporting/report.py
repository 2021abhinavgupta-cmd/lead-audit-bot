"""
Builds the design and conversion report from what reporting/collect.py measured.

build_report() and render_html() are pure (no network, no browser), so what the
report says is unit-tested; render_pdf() is the only part that needs Chromium.

Accuracy rules carried over from the rest of the project: every finding quotes
a measurement; a thing is listed as "already working" only when a measurement
says so; nothing is said about a measurement that failed.
"""
import base64
from datetime import date
from html import escape

from analyzer import design_review
from analyzer.visuals import _acquire_playwright_slot, _release_playwright_slot

from reporting import theme
from reporting.theme import ACCENT, CREAM, DARK, GREEN, INK, PANEL  # noqa: F401  (kept importable)

_RANK = {"serious": 0, "moderate": 1, "minor": 2}
_TAG = theme.TAG

_PRESENTATION = {
    "hero_video_no_headline": (
        "The first screen is a video player",
        "Lead with one sentence: who you help and with what, then a clear next step such as "
        "\"Let's talk\" and \"See our work\". Keep the video as a silent loop behind or beside "
        "the words, and let a button open the full version with sound.",
        "desktop_image",
    ),
    "no_headline_first_screen": (
        "The first screen has no headline",
        "Lead with one sentence: who you help and with what, in real text, then a clear next step.",
        "desktop_image",
    ),
    "proof_leaves_site": (
        "Project cards send visitors away",
        "Give your strongest projects a short page of their own: the task, what you did, one result "
        "and a few images. Keep the outside link at the bottom of the page, not instead of it.",
        "card_image",
    ),
    "contact_form_no_reply_time": (
        "The contact form never says when you'll reply",
        "Add one line under the button, for example \"We reply within one working day\", and offer "
        "WhatsApp as the faster way.",
        None,
    ),
}


# Findings about something that is absent are shown on the first screen, where it is missing.
_FIRST_SCREEN_PICTURE = {
    "no_main_heading": "Home page, desktop, first screen: no line here is marked as the main heading.",
    "no_first_screen_cta": "Home page, desktop, first screen: nothing here asks the visitor to get in touch.",
}


def _sentence(text: str) -> str:
    text = (text or "").strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def build_report(company: str, website: str, collected: dict, *, contact_email: str = "",
                 today: date | None = None) -> dict:
    """Turn raw measurements into a structured report. Pure."""
    c = collected or {}
    findings: list[dict] = []

    for f in c.get("presentation_findings") or []:
        title, advice, image_key = _PRESENTATION.get(
            f.get("key"), (None, None, None))
        if not title:
            continue
        findings.append({
            "key": f["key"], "title": title, "saw": _sentence(f.get("description")),
            "advice": advice, "impact": f.get("impact", "moderate"),
            "image": c.get(image_key) if image_key else None,
            "caption": ("Home page, desktop, first screen." if image_key == "desktop_image"
                        else "The home page, scrolled to the project cards." if image_key == "card_image"
                        else ""),
        })

    if c.get("mobile_overflow") is True and c.get("mobile_scroll_width"):
        findings.append({
            "key": "mobile_sideways", "title": "On a phone the page scrolls sideways",
            "saw": (f"On a phone-sized screen (390px wide) the page is {c['mobile_scroll_width']}px wide, "
                    "so a visitor can drag the page sideways and part of it sits off screen."),
            "advice": "Find the section wider than the screen (often an image, a table or a fixed-width "
                      "block) and let it shrink to the screen width.",
            "impact": "moderate", "image": c.get("mobile_image"),
            "caption": "Home page, phone-sized screen, first screen.",
        })

    shots = c.get("design_shots") or {}
    for d in design_review.design_findings(c.get("design_probe")):
        shot = shots.get(d["key"]) or {}
        image, caption = shot.get("image"), shot.get("caption", "")
        if not image and d["key"] in _FIRST_SCREEN_PICTURE and c.get("desktop_image"):
            image, caption = c["desktop_image"], _FIRST_SCREEN_PICTURE[d["key"]]
        findings.append({
            "key": d["key"], "title": d["title"], "saw": d["saw"], "advice": d["advice"],
            "impact": d["impact"], "image": image, "caption": caption,
        })

    # A finding of the same key only once, then most important first (stable).
    seen, unique = set(), []
    for f in findings:
        if f["key"] not in seen:
            seen.add(f["key"])
            unique.append(f)
    unique.sort(key=lambda f: _RANK.get(f["impact"], 1))
    for i, f in enumerate(unique, 1):
        f["number"] = i
        f["tag"] = _TAG.get(f["impact"], "Next")

    positives = list(design_review.design_positives(c.get("design_probe")))
    lcp = (c.get("real_web_vitals") or {}).get("lcp_ms")
    if isinstance(lcp, (int, float)) and 0 < lcp <= 2500:
        positives.append(
            f"The main content appeared after {lcp / 1000:.1f} seconds in our test browser, which is quick.")
    if str(c.get("final_url") or "").startswith("https://"):
        positives.append("The site is served securely over https.")
    if c.get("mobile_overflow") is False:
        positives.append("The page fits a phone screen without sideways scrolling.")

    appendix = [_sentence(s["description"]) for s in (c.get("seo_findings") or []) if s.get("description")]
    if isinstance(lcp, (int, float)) and lcp > 2500:
        appendix.append(
            f"The main content appeared after {lcp / 1000:.1f} seconds in our test browser on a desktop connection.")

    return {
        "company": (company or "").strip() or website, "website": website,
        "findings": unique, "positives": positives, "appendix": appendix,
        "desktop_image": c.get("desktop_image"), "mobile_image": c.get("mobile_image"),
        "contact_email": contact_email, "date": (today or date.today()).strftime("%d %B %Y"),
    }


# --------------------------------------------------------------------------- HTML
# The look (CSS, cover, finding blocks, closing page, footer) lives in
# reporting/theme.py and is shared with the long Instagram audit.

def _img(data: bytes | None, alt: str, extra_style: str = "") -> str:
    if not data:
        return ""
    return (f'<img alt="{escape(alt)}" style="display:block;width:100%;max-height:150mm;object-fit:cover;'
            f'object-position:top;border-radius:6px;border:1px solid {PANEL};{extra_style}" '
            f'src="data:image/jpeg;base64,{base64.b64encode(data).decode()}">')


def _finding_block(f: dict) -> str:
    img = ""
    if f.get("image"):
        img = (f'<div style="margin-top:6mm">{_img(f["image"], f["title"])}'
               f'<p class="cap">{escape(f.get("caption") or "")}</p></div>')
    return theme.finding_block(
        number=f["number"], impact=f["impact"], tag=f["tag"], title=f["title"],
        columns=[[("What we saw", f["saw"])], [("What we'd do", f["advice"])]], extra=img)


_CSS = theme.CSS
_footer = theme.footer


def render_html(report: dict) -> str:
    """The whole report as one self-contained HTML document (images inlined)."""
    company, website = report["company"], report["website"]
    findings = report["findings"]
    brand = theme.BRAND
    pages: list[str] = []
    n = 1

    pages.append(theme.cover(
        kicker=f"Design & conversion review - {report['date']}", name=company, subline=website,
        intro=("What a first-time visitor sees on your home page, what makes them trust you, "
               "and what stands between them and getting in touch."),
        footer_right=f"{len(findings)} findings"))
    n += 1

    top = findings[:3]
    steps = [(f["title"], f["advice"]) for f in top]
    firsts = theme.first_list(steps or [("Nothing urgent", "The measurements below found no serious problem.")])
    works = "".join(f"<li>{escape(p)}</li>" for p in report["positives"]) or (
        "<li>We found nothing we could confirm as a clear strength from measurements alone.</li>")
    pages.append(
        f'<section class="page"><p class="kicker">The short version</p>'
        f'<h1>{len(findings)} thing{"s" if len(findings) != 1 else ""} worth fixing</h1>'
        f'<p style="max-width:150mm">We looked at your home page the way a new visitor would, on a desktop and on a '
        f'phone, and measured what we saw. Every point below rests on a number or a screenshot, not on opinion.</p>'
        f'<div class="cols" style="margin-top:8mm"><div class="box"><h4>Do these first</h4>'
        f'{firsts}</div>'
        f'<div><h4>Already working, keep it</h4><ul class="ok">{works}</ul></div></div>'
        f'{_footer(brand, n)}</section>'
    )
    n += 1

    # Findings with a picture get a page each; the rest are paired two to a page.
    queue: list[dict] = []
    for f in findings:
        if f.get("image"):
            if queue:
                pages.append(f'<section class="page">{"".join(_finding_block(x) for x in queue)}{_footer(brand, n)}</section>')
                n += 1
                queue = []
            pages.append(f'<section class="page">{_finding_block(f)}{_footer(brand, n)}</section>')
            n += 1
        else:
            queue.append(f)
            if len(queue) == 2:
                pages.append(f'<section class="page">{"".join(_finding_block(x) for x in queue)}{_footer(brand, n)}</section>')
                n += 1
                queue = []
    if queue:
        pages.append(f'<section class="page">{"".join(_finding_block(x) for x in queue)}{_footer(brand, n)}</section>')
        n += 1

    if report["appendix"]:
        items = "".join(f"<li>{escape(a)}</li>" for a in report["appendix"])
        pages.append(
            f'<section class="page"><p class="kicker">Appendix - not the point of this review</p>'
            f'<h1>Under the hood</h1><p>A few smaller things found while measuring.</p>'
            f'<ul class="app" style="padding-left:6mm">{items}</ul>{_footer(brand, n)}</section>'
        )
        n += 1

    pages.append(theme.closing_page(steps, contact_email=report.get("contact_email") or "", number=n))

    return theme.document(f"Design and conversion review for {company}", pages)


# --------------------------------------------------------------------------- PDF

async def render_pdf(html: str, margin_mm: int = 0) -> bytes:
    """HTML to an A4 PDF with Chromium. Holds the shared browser slot."""
    from playwright.async_api import async_playwright
    sem = await _acquire_playwright_slot()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(html, wait_until="load")
                return await page.pdf(format="A4", print_background=True,
                                      margin={"top": f"{margin_mm}mm", "right": "0", "bottom": f"{margin_mm}mm", "left": "0"})
            finally:
                await browser.close()
    finally:
        _release_playwright_slot(sem)

