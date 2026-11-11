"""Optional OCR fallback for PDFs without a text layer.

Scanned invoices have no embedded text, so `pypdf` returns an empty string.
When `OCR_ENABLED=true` the worker renders up to `OCR_MAX_PAGES` pages with
`pypdfium2` and sends the images to a local Tesseract installation.

OCR is deliberately a fallback, never the primary path: digital invoices are
read from their text layer, which is faster and exact.
"""

from __future__ import annotations

import contextlib
import logging

import pypdfium2 as pdfium
import pytesseract

log = logging.getLogger(__name__)


class OcrError(Exception):
    """OCR could not produce usable text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def ocr_pdf(
    data: bytes,
    *,
    language: str = "eng",
    dpi: int = 200,
    max_pages: int = 5,
    min_chars: int = 25,
) -> str:
    """Render the PDF and return the OCR text of its first pages.

    Raises OcrError with a stable reason:
      corrupt_pdf     - the file cannot be opened as a PDF
      ocr_unavailable - Tesseract is not installed in the image
      ocr_failed      - Tesseract failed on a page
      ocr_empty       - the OCR produced less than `min_chars` characters
    """
    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:  # noqa: BLE001 - pypdfium2 raises many types
        raise OcrError("corrupt_pdf") from exc

    scale = max(dpi, 72) / 72
    pages: list[str] = []
    try:
        for index in range(min(len(document), max_pages)):
            page = document[index]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            pages.append(pytesseract.image_to_string(image, lang=language).strip())
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrError("ocr_unavailable") from exc
    except Exception as exc:  # noqa: BLE001 - Tesseract raises opaque errors
        log.warning("ocr page failed: %s", exc)
        raise OcrError("ocr_failed") from exc
    finally:
        with contextlib.suppress(Exception):
            document.close()

    text = "\n".join(page for page in pages if page).strip()
    if len(text) < min_chars:
        raise OcrError("ocr_empty")
    return text
