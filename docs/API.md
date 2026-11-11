# API

Base URL (local): `http://localhost:8000`.

> **Authentication:** Phase 0 has none. Do not expose this API publicly. In
> production, authenticate the caller and derive the tenant from the credential.

Interactive schema: `GET /docs` (Swagger UI) and `GET /openapi.json`.

## Limits

| Limit | Default | Configurable |
|---|---|---|
| upload size | 15 MiB | `MAX_PDF_BYTES` |
| list page size | 200 | fixed (`limit` max) |
| request timeout | uvicorn default | — |
| accepted type | PDF magic `%PDF-` | fixed |

Scanned PDFs without a text layer are OCRed when `OCR_ENABLED=true`; otherwise
the invoice is accepted but ends in `needs_review` with `no_text_layer`.

## POST /api/invoices

Upload a PDF. Multipart form field: `file`. Optional query parameter `source`
(default `upload`).

```bash
curl -s -X POST http://localhost:8000/api/invoices \
  -F file=@samples/acme_invoice.pdf
```

`202 Accepted` — new invoice queued:

```json
{
  "invoice": {
    "id": "0b9f...",
    "status": "queued",
    "original_filename": "acme_invoice.pdf",
    "sha256": "…",
    "created_at": "2026-11-10T21:30:00+00:00"
  },
  "duplicate": false
}
```

`200 OK` — the same bytes were already ingested:

```json
{ "invoice": { "id": "0b9f...", "status": "processed", "...": "..." }, "duplicate": true }
```

Errors:

| HTTP | `detail` | When |
|---|---|---|
| 400 | `empty file` | zero-byte body |
| 400 | `not a PDF` | missing `%PDF-` magic |
| 400 | `file too large` | above `MAX_PDF_BYTES` |

## GET /api/invoices

Query: `status` (optional), `limit` (1..200, default 50), `offset` (default 0).

```json
{ "invoices": [ { "id": "…", "status": "processed", "...": "..." } ], "count": 1 }
```

## GET /api/invoices/{id}

Returns the invoice, its notification history and the full audit trail.

```json
{
  "invoice": {
    "id": "…",
    "status": "processed",
    "supplier_name": "Acme Supplies Ltda",
    "supplier_cnpj": "11222333000181",
    "total_amount": "1234.56",
    "currency": "BRL",
    "due_date": "2026-12-15",
    "confidence": "0.950",
    "llm_mode": "mock",
    "text_source": "text_layer",
    "validation_errors": [],
    "validation_warnings": [],
    "extraction": { "supplier_name": "Acme Supplies Ltda", "...": "..." },
    "archived_path": "20261110/<id>.pdf"
  },
  "notifications": [
    {
      "channel": "mock",
      "status": "mock",
      "message": "New invoice processed successfully. Supplier: Acme Supplies Ltda. Total: BRL 1,234.56. Due: 2026-12-15. Approved for payment."
    }
  ],
  "audit": [
    { "action": "ingested", "detail": { "source": "upload" } },
    { "action": "processing_started", "detail": { "attempt": 1 } },
    { "action": "extracted", "detail": { "mode": "mock" } },
    { "action": "processed", "detail": { "supplier": "Acme Supplies Ltda" } },
    { "action": "notification_mock", "detail": { "message": "…" } }
  ]
}
```

## GET /api/invoices/{id}/file

Returns the archived original PDF (`application/pdf`). `404` when the invoice or
the archive file does not exist.

## POST /api/invoices/{id}/retry

Requeues an invoice in `failed` or `needs_review`, resetting `attempts` and the
validation state. `202` on success, `409` when the status is not retryable.

## GET /api/notifications

Recent notifications (joined with the supplier). Query: `limit` (1..100,
default 20).

## GET /api/stats

```json
{
  "queued": 0,
  "processing": 0,
  "processed": 12,
  "needs_review": 2,
  "failed": 0,
  "total": 14,
  "queue_depth": 0
}
```

## Demo endpoints

Enabled only when `DEMO_MODE=true`.

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/demo/samples` | list bundled sample filenames |
| `GET` | `/api/demo/samples/{name}` | download a sample PDF |
| `POST` | `/api/demo/seed` | enqueue every sample |
| `POST` | `/api/demo/reset` | truncate tables and delete archived files |

## Operations

| Route | Description |
|---|---|
| `GET /healthz` | process is alive |
| `GET /readyz` | Postgres answers (`200`) or not (`503`) |
| `GET /metrics` | Prometheus text exposition |
| `GET /api/health/dependencies` | dependency flags (hidden from the docs) |

## Error format

FastAPI errors use `detail`:

```json
{ "detail": "not a PDF" }
```

Validation errors from the framework return the standard `422` body.
