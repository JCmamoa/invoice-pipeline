"""Text layer extraction for PDFs."""

from __future__ import annotations

import io

from pypdf import PdfReader


class PdfError(Exception):
    """A PDF could not be read or has no extractable text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def extract_text(data: bytes) -> str:
    """Return the concatenated text layer of every page.

    Raises PdfError('corrupt_pdf') when the file is not a readable PDF and
    PdfError('no_text_layer') when the PDF is a scan without embedded text.
    """
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - pypdf raises many types
        raise PdfError("corrupt_pdf") from exc

    text = "\n".join(part for part in pages if part).strip()
    if not text:
        raise PdfError("no_text_layer")
    return text
