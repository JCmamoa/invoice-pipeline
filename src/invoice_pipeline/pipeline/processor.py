"""The processing pipeline for one invoice.

Stages: read the archived PDF -> extract text -> LLM or mock extraction ->
deterministic validation -> persist -> notify when complete.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .. import metrics, notifier
from ..config import Settings
from ..extractor import LLMError, OcrError, PdfError, extract, extract_text, ocr_pdf
from ..extractor.validate import validate_extraction
from ..schemas import ExtractedInvoice, ValidatedInvoice
from . import repository
from .ingest import archived_file

log = logging.getLogger(__name__)

RETRY_DELAYS_SECONDS = (10, 30, 60)


def _review_for_pdf_error(
    invoice_id: str, reason: str, settings: Settings
) -> None:
    """A PDF without a text layer needs a human, not a retry."""
    extracted = ExtractedInvoice(notes=f"pdf:{reason}")
    validated = ValidatedInvoice(validation_errors=[reason])
    repository.save_result(
        invoice_id=invoice_id,
        status="needs_review",
        extracted=extracted,
        validated=validated,
        llm_mode="none",
        text_source="none",
    )
    repository.record_audit(invoice_id, "needs_review", {"reason": reason})
    metrics.INVOICES_PROCESSED.labels(status="needs_review").inc()
    log.warning(
        "invoice needs review",
        extra={"invoice_id": invoice_id, "status": "needs_review", "error": reason},
    )


def _read_text(data: bytes, settings: Settings) -> tuple[str, str]:
    """Return the invoice text and where it came from.

    Digital PDFs use the embedded text layer. When that layer is missing and
    OCR is enabled, the pages are rendered and passed to Tesseract.
    """
    try:
        return extract_text(data), "text_layer"
    except PdfError as exc:
        if exc.reason != "no_text_layer" or not settings.ocr_enabled:
            raise

    try:
        text = ocr_pdf(
            data,
            language=settings.ocr_language,
            dpi=settings.ocr_dpi,
            max_pages=settings.ocr_max_pages,
            min_chars=settings.ocr_min_chars,
        )
    except OcrError as exc:
        metrics.OCR.labels(result=exc.reason).inc()
        log.warning("ocr fallback failed", extra={"error": exc.reason})
        raise PdfError("no_text_layer") from exc

    metrics.OCR.labels(result="success").inc()
    log.info("ocr fallback produced text")
    return text, "ocr"


def process_invoice(row: dict[str, Any], settings: Settings) -> str:
    invoice_id = str(row["id"])
    path = archived_file(settings, row["archived_path"])
    data = path.read_bytes()
    text, text_source = _read_text(data, settings)

    started = time.perf_counter()
    extracted, mode = extract(text, settings)
    validated = validate_extraction(extracted, settings.min_confidence)
    metrics.EXTRACTION_DURATION.labels(mode=mode).observe(time.perf_counter() - started)

    status = "processed" if validated.is_complete else "needs_review"
    repository.save_result(
        invoice_id=invoice_id,
        status=status,
        extracted=extracted,
        validated=validated,
        llm_mode=mode,
        text_source=text_source,
    )
    repository.record_audit(
        invoice_id,
        "extracted",
        {
            "mode": mode,
            "text_source": text_source,
            "confidence": validated.confidence,
            "errors": validated.validation_errors,
            "warnings": validated.validation_warnings,
        },
    )
    repository.record_audit(
        invoice_id,
        status,
        {
            "supplier": validated.supplier_name,
            "cnpj": validated.supplier_cnpj,
            "total": str(validated.total_amount) if validated.total_amount else None,
            "due_date": validated.due_date.isoformat() if validated.due_date else None,
        },
    )
    metrics.INVOICES_PROCESSED.labels(status=status).inc()
    log.info(
        "invoice processed",
        extra={"invoice_id": invoice_id, "status": status},
    )

    if status == "processed":
        merged = {
            **row,
            "supplier_name": validated.supplier_name,
            "total_amount": validated.total_amount,
            "currency": validated.currency,
            "due_date": validated.due_date,
        }
        notifier.notify(settings, merged)
    return status


def process_next(settings: Settings) -> bool:
    """Claim and process one invoice. Returns False when the queue is empty."""
    row = repository.claim_next()
    if row is None:
        return False

    invoice_id = str(row["id"])
    repository.record_audit(invoice_id, "processing_started", {"attempt": row["attempts"]})

    try:
        process_invoice(row, settings)
    except PdfError as exc:
        if exc.reason == "no_text_layer":
            _review_for_pdf_error(invoice_id, exc.reason, settings)
        else:
            repository.mark_failed(invoice_id, f"pdf:{exc.reason}")
            repository.record_audit(invoice_id, "failed", {"reason": f"pdf:{exc.reason}"})
            metrics.INVOICES_PROCESSED.labels(status="failed").inc()
            log.error(
                "invoice failed: unreadable PDF",
                extra={"invoice_id": invoice_id, "status": "failed", "error": exc.reason},
            )
    except LLMError as exc:
        _handle_llm_error(invoice_id, row["attempts"], exc, settings)
    except Exception as exc:  # noqa: BLE001 - a single invoice must not kill the worker
        repository.mark_failed(invoice_id, f"processing_error: {exc}")
        repository.record_audit(invoice_id, "failed", {"error": str(exc)})
        metrics.INVOICES_PROCESSED.labels(status="failed").inc()
        log.exception(
            "invoice failed",
            extra={"invoice_id": invoice_id, "status": "failed", "error": str(exc)},
        )
    return True


def _handle_llm_error(
    invoice_id: str, attempts: int, exc: LLMError, settings: Settings
) -> None:
    if exc.transient and attempts < settings.max_attempts:
        delay = RETRY_DELAYS_SECONDS[min(attempts - 1, len(RETRY_DELAYS_SECONDS) - 1)]
        repository.requeue_with_delay(invoice_id, delay, f"llm: {exc}")
        repository.record_audit(
            invoice_id, "retry_scheduled", {"attempt": attempts, "delay": delay, "error": str(exc)}
        )
        log.warning(
            "invoice scheduled for retry",
            extra={
                "invoice_id": invoice_id,
                "status": "queued",
                "attempt": attempts,
                "error": str(exc),
            },
        )
        return

    repository.mark_failed(invoice_id, f"llm: {exc}")
    repository.record_audit(invoice_id, "failed", {"error": str(exc), "transient": exc.transient})
    metrics.INVOICES_PROCESSED.labels(status="failed").inc()
    log.error(
        "invoice failed: extraction error",
        extra={"invoice_id": invoice_id, "status": "failed", "error": str(exc)},
    )
