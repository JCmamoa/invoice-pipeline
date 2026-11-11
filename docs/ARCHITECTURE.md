# Architecture

Phase 0 reference. For the reasoning behind each choice, see
[DECISIONS.md](DECISIONS.md).

## Overview

```
 PDF source
 ┌──────────────┬──────────────┬──────────────┐
 │ upload API   │ inbox folder │ IMAP mailbox │
 └──────┬───────┴──────┬───────┴──────┬───────┘
        └──────────────┼──────────────┘
                       ▼
              ┌─────────────────┐
              │  ingest         │  validate PDF magic + size
              │  sha256 dedup   │  archive original
              └────────┬────────┘
                       ▼
              ┌─────────────────┐
              │  Postgres       │  invoices (queue + business state)
              │                 │  audit_log, notifications
              └────────┬────────┘
                       │ FOR UPDATE SKIP LOCKED
                       ▼
              ┌─────────────────┐
              │  worker         │
              │  pdf → text     │
              │  extract        │──▶ OpenAI-compatible LLM
              │  validate       │    or deterministic mock
              │  persist        │
              └────────┬────────┘
                       │ only when complete
                       ▼
              ┌─────────────────┐
              │  notifier       │──▶ Slack / Teams webhook
              │                 │    or mock feed
              └─────────────────┘
```

## Components

| Service | Responsibility | State |
|---|---|---|
| `api` | upload, listing, detail, archive download, demo controls, web UI, metrics | stateless |
| `worker` | folder watcher, queue consumer, reaper, optional IMAP poller | stateless |
| `postgres` | invoice queue, business data, audit log, notifications | `pgdata` volume |
| `data` volume | `inbox/` drop zone and `archive/` original PDFs | volume |

The API and the worker share the database; they do not call each other. This
means the worker can be restarted or scaled independently, and an API outage
does not stop processing.

## Invoice lifecycle

### 1. Ingest

1. Read at most `MAX_PDF_BYTES + 1` bytes.
2. Reject empty files, non-PDF magic bytes and oversized bodies.
3. Compute SHA-256. If a row already exists, return it as a duplicate.
4. Copy the original to `archive/YYYYMMDD/<uuid>.pdf`.
5. Insert `invoices` with `status='queued'`.
6. Write the `ingested` audit entry.

Archiving happens **before** enqueueing so the original is preserved even if
extraction fails.

### 2. Process

The worker claims one row per iteration:

```sql
WITH next AS (
    SELECT id FROM invoices
     WHERE status = 'queued' AND next_attempt_at <= now()
     ORDER BY created_at
     FOR UPDATE SKIP LOCKED
     LIMIT 1
)
UPDATE invoices SET status = 'processing', attempts = attempts + 1, ...
FROM next WHERE invoices.id = next.id
RETURNING invoices.*;
```

Then:

1. Read the archived PDF.
2. Extract the text layer with `pypdf`.
3. If there is no text layer and OCR is enabled, render the first pages with
   `pypdfium2` and read them with Tesseract (`text_source=ocr`).
4. Call the extractor (LLM or mock) with the strict prompt.
5. Validate the returned fields deterministically.
6. Persist `processed` or `needs_review`.
7. Notify when `processed`.

### 3. Statuses

| Status | Meaning |
|---|---|
| `queued` | waiting for a worker (or waiting for `next_attempt_at`) |
| `processing` | claimed by a worker |
| `processed` | all required fields present and valid; notification sent |
| `needs_review` | at least one required field missing or invalid |
| `failed` | unreadable PDF or extraction error after all attempts |

## Queue semantics

- Claiming uses `FOR UPDATE SKIP LOCKED`, so multiple workers can run in
  parallel without duplicate claims.
- Transient LLM errors (timeout, 429, 5xx) are requeued with delays of
  10s/30s/60s.
- After `MAX_ATTEMPTS`, the invoice becomes `failed` and can be retried
  manually through the API.
- A reaper requeues `processing` rows older than `STALE_PROCESSING_SECONDS`
  (default 5 minutes), so a crashed worker does not leave an invoice stuck.
  If the attempts limit is already exhausted, the reaper marks it `failed`.

## Idempotency

The unique key is the **SHA-256 of the file**, not the filename. Re-uploading
the same bytes returns the existing invoice with `duplicate: true` and does not
create a new queue item. This covers uploads, folder drops and IMAP copies of
the same attachment.

## Extraction contract

The LLM receives a fixed system prompt and must return only:

```json
{
  "supplier_name": "string|null",
  "supplier_cnpj": "string|null",
  "total_amount": "string|null",
  "currency": "string|null",
  "due_date": "YYYY-MM-DD|null",
  "confidence": 0.0,
  "notes": "string|null"
}
```

Rules:

- Only values explicitly present in the document.
- Missing values must be `null`; the model must not guess.
- Money and CNPJ keep their printed formatting; normalization happens in code.
- `due_date` is ISO only when the document states it.

The response is parsed with Pydantic. A response that is not valid JSON is a
transient error (retried); a valid response with missing fields is a
`needs_review`.

The deterministic mock implements the same contract with label-based regular
expressions, so the demo and the tests run offline.

## OCR fallback

Digital invoices are read from their embedded text layer: exact and fast.
Scanned invoices have no such layer, so the worker optionally renders pages and
runs Tesseract locally:

| Setting | Default | Effect |
|---|---|---|
| `OCR_ENABLED` | `true` | turn the fallback on or off |
| `OCR_LANG` | `eng` | Tesseract languages, e.g. `eng+por` |
| `OCR_DPI` | `200` | render resolution |
| `OCR_MAX_PAGES` | `5` | bound on CPU per document |
| `OCR_MIN_CHARS` | `25` | below this the result is discarded |

The OCR text then follows the same extraction and validation path. The source
is persisted in `invoices.text_source` (`text_layer`, `ocr` or `none`) and in
the `extracted` audit entry, so a reviewer can tell how a field was read.

If OCR is disabled, unavailable or returns too little text, the invoice goes to
`needs_review` with `no_text_layer`. OCR failures never fabricate data.

## Validation

Validation is intentionally independent from the LLM:

| Field | Rule | Error |
|---|---|---|
| `supplier_name` | non-empty | `missing_supplier_name` |
| `supplier_cnpj` | 14 digits + checksum | `missing_cnpj`, `invalid_cnpj` |
| `total_amount` | parses and > 0 | `missing_total`, `invalid_total` |
| `due_date` | parses, 2000-01-01..today+10y | `missing_due_date`, `implausible_due_date` |
| `confidence` | `>= MIN_CONFIDENCE` | `low_confidence` |

Warnings (`currency_inferred`, `unknown_currency:<code>`) do not block
processing.

## Notifications

`processed` invoices produce one notification. The channel is selected at
runtime:

1. `SLACK_WEBHOOK_URL` → Slack incoming webhook, Block Kit payload with a
   `text` fallback.
2. `TEAMS_WEBHOOK_URL` → Teams incoming webhook, `MessageCard` payload.
3. Otherwise → `mock`: the message is stored in `notifications` and shown in the
   UI feed.

If the channel rejects the rich payload (`400/413/415/422`), the client retries
once with `{"text": message}` so older incoming webhooks keep working.

The notification row is written in all cases, including webhook failures, so the
audit trail is complete. Webhook failure does **not** roll back the invoice:
the data is already persisted and a human can resend.

## Observability

Both processes export Prometheus metrics:

| Metric | Meaning |
|---|---|
| `invoice_ingested_total{source,duplicate}` | intake volume |
| `invoice_processed_total{status}` | terminal outcomes |
| `invoice_extraction_duration_seconds{mode}` | extraction latency |
| `invoice_notifications_total{channel,status}` | delivery results |
| `invoice_ocr_total{result}` | OCR fallback outcomes |
| `invoice_queue_depth` | queued + processing |
| `invoice_api_requests_total{method,route,status}` | API traffic |

Logs are JSON with `invoice_id`, `status` and `error` fields where relevant.

## Limits and security

Phase 0 is a local demo:

- No authentication; `DEMO_MODE=true` enables destructive demo endpoints.
- Default database credentials and ports bound to the host.
- No TLS.
- The archive is a local volume, not an object store.

Before exposing it publicly:

1. Put the API behind a proxy with TLS and authentication.
2. Derive the tenant from the API key, never from the payload.
3. Rotate database credentials and disable the demo endpoints.
4. Move the archive to S3/MinIO with lifecycle policies.
5. Add per-tenant rate limits.
