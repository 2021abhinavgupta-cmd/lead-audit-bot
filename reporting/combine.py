"""Merge finished PDFs into one file (the Social tab's combined website and
Instagram audit). Pure python (pypdf), no browser. Pages are copied as they
are, so each part keeps its own page size; nothing is added between parts."""

import io

from pypdf import PdfReader, PdfWriter


class CombineError(Exception):
    """A part could not be read, or had no pages. Nothing partial is returned."""


def page_count(pdf: bytes) -> int:
    """Pages in a PDF; CombineError when it is unreadable or empty."""
    try:
        n = len(PdfReader(io.BytesIO(pdf)).pages)
    except Exception as ex:
        raise CombineError(f"not a readable PDF: {ex}") from ex
    if not n:
        raise CombineError("the PDF has no pages")
    return n


def merge_pdfs(parts: list[bytes], *, title: str) -> bytes:
    writer = PdfWriter()
    for n, part in enumerate(parts, 1):
        try:
            reader = PdfReader(io.BytesIO(part))
            pages = list(reader.pages)
        except Exception as ex:
            raise CombineError(f"part {n} is not a readable PDF: {ex}") from ex
        if not pages:
            raise CombineError(f"part {n} has no pages")
        for page in pages:
            writer.add_page(page)
    writer.add_metadata({"/Title": title})
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
