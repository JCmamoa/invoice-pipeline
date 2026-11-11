import shutil
from pathlib import Path
from types import SimpleNamespace

import pytesseract
import pytest

from invoice_pipeline.extractor.ocr import OcrError, ocr_pdf
from invoice_pipeline.extractor.pdf import PdfError, extract_text
from invoice_pipeline.pipeline.processor import _read_text

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
SCANNED = SAMPLES / "scanned_invoice.pdf"
HAS_TESSERACT = shutil.which("tesseract") is not None


def test_scanned_sample_has_no_text_layer():
    with pytest.raises(PdfError) as excinfo:
        extract_text(SCANNED.read_bytes())
    assert excinfo.value.reason == "no_text_layer"


def test_corrupt_data_raises_corrupt_pdf():
    with pytest.raises(OcrError) as excinfo:
        ocr_pdf(b"not a pdf")
    assert excinfo.value.reason == "corrupt_pdf"


def test_missing_tesseract_raises_ocr_unavailable(monkeypatch):
    def boom(*args, **kwargs):
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "image_to_string", boom)
    with pytest.raises(OcrError) as excinfo:
        ocr_pdf(SCANNED.read_bytes())
    assert excinfo.value.reason == "ocr_unavailable"


def test_ocr_is_skipped_when_disabled():
    settings = SimpleNamespace(ocr_enabled=False)
    with pytest.raises(PdfError) as excinfo:
        _read_text(SCANNED.read_bytes(), settings)
    assert excinfo.value.reason == "no_text_layer"


@pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract is not installed")
def test_ocr_reads_the_scanned_sample():
    text = ocr_pdf(SCANNED.read_bytes(), language="eng")
    assert "Acme" in text
    assert "1,234.56" in text or "1.234,56" in text


@pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract is not installed")
def test_read_text_falls_back_to_ocr():
    settings = SimpleNamespace(
        ocr_enabled=True,
        ocr_language="eng",
        ocr_dpi=200,
        ocr_max_pages=5,
        ocr_min_chars=25,
    )
    text, source = _read_text(SCANNED.read_bytes(), settings)
    assert source == "ocr"
    assert "Acme" in text
