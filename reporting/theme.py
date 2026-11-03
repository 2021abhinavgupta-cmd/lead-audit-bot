"""
The MMGA audit PDF look, shared by the website design review (reporting/report.py)
and the long Instagram audit (reporting/social_pdf.py), so the two halves of the
combined PDF cannot drift apart.

A4 pages of fixed size (210 x 297 mm, printed with margin 0), a dark brown cover
and closing page, cream pages in between, orange accent, Arial, big uppercase
headings, small orange uppercase labels, numbered 01/02/03 lists, the orange
"Want us to do it?" box, and a footer with the brand on the left and a two digit
page number on the right.

Every helper escapes the text it is given. The only strings inserted raw are
the column headings of finding_block, which are fixed copy owned by the caller.
"""
from html import escape

INK = "#2a1f14"
DARK = "#1f1610"
CREAM = "#fff8ef"
PANEL = "#f1e3cf"
ACCENT = "#d9480f"
GREEN = "#2f7d4f"
PEACH = "#f6c9a8"

BRAND = "MMGA Agency"
BRAND_LINE = "MMGA Agency - mmga.agency"
PREPARED_BY = "Prepared by MMGA Agency, Mumbai"

# Severity tag text by impact, and the impact class (t-serious is the orange tag).
TAG = {"serious": "Fix first", "moderate": "Next", "minor": "Worth doing"}

CSS = f"""
@page {{ size: A4; margin: 0; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; font-family: Arial, Helvetica, 'Liberation Sans', sans-serif; color:{INK}; background:{CREAM}; }}
.page {{ width:210mm; height:297mm; padding:18mm 18mm 16mm; position:relative; overflow:hidden;
         page-break-after:always; background:{CREAM}; }}
.page.dark {{ background:{DARK}; color:{CREAM}; }}
.kicker {{ font-size:9pt; font-weight:bold; letter-spacing:2px; text-transform:uppercase; color:{ACCENT}; margin:0 0 4mm; }}
h1 {{ font-size:40pt; line-height:1.0; margin:0 0 6mm; text-transform:uppercase; letter-spacing:-0.5px; }}
h1.big {{ font-size:54pt; color:#f6c9a8; }}
h2 {{ font-size:18pt; line-height:1.15; margin:1mm 0 0; text-transform:uppercase; }}
h4 {{ font-size:8pt; letter-spacing:2px; text-transform:uppercase; color:{ACCENT}; margin:0 0 2mm; }}
p {{ font-size:10.5pt; line-height:1.55; margin:0 0 3mm; }}
.cap {{ font-size:8.5pt; color:#6b5a48; margin:2mm 0 0; }}
.footer {{ position:absolute; left:18mm; right:18mm; bottom:9mm; display:flex; justify-content:space-between;
           font-size:8pt; letter-spacing:1px; text-transform:uppercase; color:#8a7a68; }}
.dark .footer {{ color:#a99884; }}
.finding {{ margin-bottom:9mm; }}
.fhead {{ display:flex; gap:6mm; align-items:flex-start; margin-bottom:4mm; }}
.num {{ font-size:34pt; font-weight:bold; color:{ACCENT}; line-height:1; min-width:17mm; }}
.tag {{ display:inline-block; font-size:7.5pt; font-weight:bold; letter-spacing:1.5px; text-transform:uppercase;
        padding:1mm 3mm; border-radius:10mm; background:{PANEL}; color:{INK}; }}
.tag.t-serious {{ background:{ACCENT}; color:#fff; }}
.cols {{ display:flex; gap:8mm; }} .cols > div {{ flex:1; }}
.box {{ background:{DARK}; color:{CREAM}; border-radius:4mm; padding:7mm; }}
.box p {{ color:{CREAM}; }}
.ok {{ list-style:none; padding:0; margin:0; }}
.ok li {{ font-size:10pt; line-height:1.5; padding:2.4mm 0 2.4mm 7mm; border-bottom:1px solid {PANEL}; position:relative; }}
.ok li:before {{ content:''; position:absolute; left:0; top:4.2mm; width:2.6mm; height:2.6mm; border-radius:50%; background:{GREEN}; }}
.first {{ counter-reset:n; list-style:none; padding:0; margin:0; }}
.first li {{ position:relative; padding:0 0 5mm 13mm; font-size:10.5pt; line-height:1.5; }}
.first li:before {{ counter-increment:n; content:counter(n,decimal-leading-zero); position:absolute; left:0; top:-1mm;
                    font-size:17pt; font-weight:bold; color:{ACCENT}; }}
.first b {{ display:block; font-size:11.5pt; }}
.app li {{ font-size:10pt; line-height:1.5; margin-bottom:3mm; }}
.cta {{ background:{ACCENT}; color:#fff; border-radius:4mm; padding:8mm; margin-top:10mm; }}
.cta p {{ color:#fff; font-size:12pt; }} .cta h2 {{ color:#fff; margin-bottom:3mm; }}
"""


def footer(left: str, right) -> str:
    """The page footer: brand on the left, a two digit page number (or any text) on the right."""
    right = f"{right:02d}" if isinstance(right, int) else escape(str(right))
    return f'<div class="footer"><span>{escape(left)}</span><span>{right}</span></div>'


def page(inner: str, *, dark: bool = False) -> str:
    return f'<section class="page{" dark" if dark else ""}">{inner}</section>'


def cover(*, kicker: str, name: str, subline: str, intro: str, footer_right: str) -> str:
    """The dark first page: a label, 'A free review for', the big name, an orange
    uppercase line (website or handle) and one paragraph."""
    return page(
        f'<p class="kicker">{escape(kicker)}</p>'
        f'<div style="margin-top:34mm"><p class="kicker" style="color:{PEACH}">A free review for</p>'
        f'<h1 class="big">{escape(name)}</h1>'
        f'<p style="font-size:13pt;letter-spacing:3px;text-transform:uppercase;color:{ACCENT};font-weight:bold">'
        f'{escape(subline)}</p>'
        f'<p style="font-size:13pt;max-width:120mm;margin-top:10mm">{escape(intro)}</p></div>'
        f'{footer(PREPARED_BY, footer_right)}', dark=True)


def first_list(items: list[tuple[str, str]]) -> str:
    """The numbered 01/02/03 list (cream text, for the dark box and the dark closing page)."""
    lis = "".join(f"<li><b>{escape(t)}</b>{escape(x)}</li>" for t, x in items)
    return f'<ol class="first" style="color:{CREAM}">{lis}</ol>'


def finding_block(*, number: int, impact: str, title: str,
                  columns: list[list[tuple[str, str]]], extra: str = "", tag: str = "") -> str:
    """One numbered finding: orange number, severity tag, uppercase title and
    side by side columns of (heading, text). Headings are fixed copy, inserted raw."""
    tag = tag or TAG.get(impact, "Next")
    cols = "".join("<div>" + "".join(f"<h4>{h}</h4><p>{escape(t)}</p>" for h, t in col) + "</div>"
                   for col in columns)
    return (
        f'<div class="finding"><div class="fhead"><span class="num">{number:02d}</span>'
        f'<div><span class="tag t-{escape(impact)}">{escape(tag)}</span>'
        f'<h2>{escape(title)}</h2></div></div>'
        f'<div class="cols">{cols}</div>{extra}</div>'
    )


def cta_text(contact_email: str) -> str:
    contact = escape(contact_email or "")
    reply = "Reply to this email" if not contact else f"Reply to this email or write to {contact}"
    return (f"{reply} and we'll send a fixed quote for whichever part you'd like to start with. "
            "No call needed.")


def closing_page(steps: list[tuple[str, str]], *, contact_email: str, number: int, sign: str = "") -> str:
    """The dark last page: 'What we'd do first' as 01/02/03, then the orange box."""
    sign_html = f"<p>{escape(sign)}</p>" if sign else ""
    return page(
        "<p class=\"kicker\">Where to start</p><h1>What we'd do first</h1>"
        + first_list(steps)
        + f'<div class="cta"><h2>Want us to do it?</h2><p>{cta_text(contact_email)}</p>{sign_html}</div>'
        + footer(BRAND_LINE, number), dark=True)


def document(title: str, pages: list[str], *, extra_css: str = "") -> str:
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{escape(title)}</title><style>{CSS}{extra_css}</style></head>'
            f'<body>{"".join(pages)}</body></html>')
