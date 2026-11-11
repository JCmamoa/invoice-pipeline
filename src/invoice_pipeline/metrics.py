"""Prometheus metrics shared by the API and the worker."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

INVOICES_INGESTED = Counter(
    "invoice_ingested_total",
    "PDFs accepted for processing.",
    ["source", "duplicate"],
)

INVOICES_PROCESSED = Counter(
    "invoice_processed_total",
    "Invoices that reached a terminal state.",
    ["status"],
)

EXTRACTION_DURATION = Histogram(
    "invoice_extraction_duration_seconds",
    "Time spent extracting and validating one invoice.",
    ["mode"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

OCR = Counter(
    "invoice_ocr_total",
    "OCR fallback attempts and their result.",
    ["result"],
)

NOTIFICATIONS = Counter(
    "invoice_notifications_total",
    "Notification attempts by channel and result.",
    ["channel", "status"],
)

QUEUE_DEPTH = Gauge(
    "invoice_queue_depth",
    "Invoices queued or currently being processed.",
)

API_REQUESTS = Counter(
    "invoice_api_requests_total",
    "HTTP requests handled by the API.",
    ["method", "route", "status"],
)
