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
