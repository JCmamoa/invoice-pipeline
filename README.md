# invoice-pipeline — Phase 0

End-to-end automation for a repetitive finance task: a supplier sends a PDF
invoice, the pipeline extracts **CNPJ**, **total amount** and **due date** with a
strict LLM prompt, validates the values, stores them in Postgres, archives the
original PDF and notifies the responsible manager.

The design principle is **never invent data**. When a value is not explicitly
present or fails a deterministic check, the invoice goes to `needs_review` with
a reason instead of being guessed.

**Public browser demo:** <https://jcmamoa.github.io/invoice-pipeline-demo/>
(extraction runs entirely in your browser, no upload to a server).

---

## What it demonstrates

| Guarantee | How |
|---|---|
| **Trigger** | upload API, watched folder (`inbox/`), optional IMAP mailbox |
| **Strict extraction** | JSON-only prompt; missing fields are `null`, never fabricated |
| **Validation** | CNPJ checksum, money parsing (`1.234,56` and `1,234.56`), plausible due date, confidence floor |
| **Idempotency** | SHA-256 unique: the same file is never processed twice |
| **Durable queue** | Postgres `FOR UPDATE SKIP LOCKED`; retries with 10s/30s/60s backoff |
| **Crash recovery** | stale `processing` claims are requeued by a reaper |
| **Audit trail** | every transition is recorded in `audit_log` |
| **Original preserved** | the PDF is archived before extraction, downloadable from the UI |
| **Notification** | Slack/Teams webhook, or a local mock feed when no webhook is set |
| **Observability** | Prometheus metrics from both API and worker |

---

## Architecture

```
 PDF (upload / folder / IMAP)
        │
        ▼
 ┌──────────────┐   sha256 dedup   ┌──────────────┐
 │  FastAPI     │─────────────────▶│  Postgres    │
 │  intake      │   queued row     │  invoices    │
 └──────┬───────┘                  │  audit_log   │
        │ archive original         │  notifications│
        ▼                          └──────┬───────┘
   data/archive/...                       │ SKIP LOCKED
                                          ▼
                                   ┌──────────────┐
                                   │   worker     │
                                   │  1. read PDF │
                                   │  2. extract  │──▶ LLM (OpenAI-compatible)
                                   │  3. validate │      or deterministic mock
                                   │  4. persist  │
                                   └──────┬───────┘
                                          │ if complete
                                          ▼
                                   Slack / Teams / mock feed
```

| Component | Role |
|---|---|
| `api` | upload, listing, detail, archive download, demo controls, web UI, `/metrics` |
| `worker` | folder watcher, queue consumer, stale-claim reaper, optional IMAP poller |
| `postgres` | invoices, queue state, audit log, notification history |
| `data` volume | `inbox/` (drop zone) and `archive/` (original PDFs) |

The web UI is served by the API at `/`; the OpenAPI docs are at `/docs`.

---

## Run it

Prerequisites: Docker + Compose v2. No Python on the host is required.

```bash
docker compose up -d --build
curl -sf http://localhost:8000/readyz   # wait until it returns 200
```

Open <http://localhost:8000> and click **Load sample invoices**. The worker picks
them up within a couple of seconds:

| Sample | Result |
|---|---|
| `acme_invoice.pdf` | `processed` — BRL 1,234.56 |
| `globex_invoice.pdf` | `processed` — USD 4,250.00, Portuguese labels |
| `missing_due_date.pdf` | `needs_review` — `missing_due_date` |
| `invalid_cnpj.pdf` | `needs_review` — `invalid_cnpj` |

With `make` installed:

```bash
make up
make wait
make seed
```

### Use a real LLM

The default `LLM_MODE=mock` is deterministic and offline. To use any
OpenAI-compatible endpoint (OpenAI, OpenRouter, Ollama, vLLM):

```bash
cat > .env <<'EOF'
LLM_MODE=openai
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
EOF
docker compose up -d
```

`LLM_MODE=auto` uses the LLM when a key is present and falls back to the mock on
non-transient configuration errors.

### Drop files in the folder

The worker watches `/data/inbox` inside the `data` volume. From the host:

```bash
docker compose cp ./my-invoice.pdf worker:/data/inbox/
```

Files are enqueued after `INBOX_SETTLE_SECONDS` (default 2s, to avoid reading a
partial upload) and then moved to `inbox/processed/`.

### Email trigger (optional)

Set the `IMAP_*` variables and `IMAP_ENABLED=true` in `.env`; the worker polls
unseen messages, enqueues PDF attachments and marks each message as seen. See
[`docs/OPERATIONS.md`](docs/OPERATIONS.md).

---

## API

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/invoices` | upload a PDF (multipart `file`); `202` new, `200` duplicate |
| `GET` | `/api/invoices` | list, optional `status`, `limit`, `offset` |
| `GET` | `/api/invoices/{id}` | invoice + notifications + audit trail |
| `GET` | `/api/invoices/{id}/file` | download the archived original |
| `POST` | `/api/invoices/{id}/retry` | requeue a `failed`/`needs_review` invoice |
| `GET` | `/api/notifications` | recent notification feed |
| `GET` | `/api/stats` | counts by status + queue depth |
| `POST` | `/api/demo/seed` | enqueue the bundled samples |
| `POST` | `/api/demo/reset` | clear demo data (when `DEMO_MODE=true`) |
| `GET` | `/healthz`, `/readyz`, `/metrics` | operations |

Example:

```bash
curl -s -X POST http://localhost:8000/api/invoices \
  -F file=@samples/acme_invoice.pdf | jq
```

Full reference: [`docs/API.md`](docs/API.md).

---

## Data model

`invoices` is both the business table and the work queue:

| Column | Meaning |
|---|---|
| `status` | `queued` → `processing` → `processed` / `needs_review` / `failed` |
| `sha256` | unique file fingerprint (idempotency) |
| `supplier_name`, `supplier_cnpj` | normalized supplier |
| `total_amount`, `currency` | parsed total |
| `due_date` | parsed due date |
| `confidence` | extraction confidence (0..1) |
| `extraction` | raw JSON returned by the extractor |
| `validation_errors`, `validation_warnings` | why it needs review |
| `archived_path` | relative path to the original PDF |
| `attempts`, `next_attempt_at` | retry state |

`audit_log` records `ingested`, `processing_started`, `extracted`, `processed`,
`needs_review`, `failed`, `retry_scheduled`, `notification_sent` and
`notification_failed`. `notifications` stores the exact message sent.

---

## Validation rules

An invoice is `processed` only when all of these hold:

1. `supplier_name` is present.
2. `supplier_cnpj` has 14 digits and a valid checksum.
3. `total_amount` parses as a positive number (both `1.234,56` and `1,234.56`).
4. `due_date` parses and is between 2000-01-01 and today + 10 years.
5. `confidence >= MIN_CONFIDENCE` when the extractor reports a confidence.

Missing values are stored as `NULL`; they are never replaced by placeholders.

---

## Tests

```bash
# unit tests (no database)
pip install -e ".[dev]"
pytest

# API integration test (needs Postgres with db/001_schema.sql)
TEST_DATABASE_URL='postgresql://invoice:invoice@localhost:5434/invoice' \
  pytest tests/test_api_integration.py -v
```

The unit suite covers money/date/CNPJ parsing, strict validation, the
deterministic extractor against the four sample PDFs, and notification
templates. CI runs lint, unit tests, the Postgres integration test and a Docker
build.

---

## Operational notes

- **Demo only:** no authentication, default credentials, ports bound to the
  host. Do not expose it or upload real invoices.
- **`DEMO_MODE=true`** exposes `/api/demo/reset`; set it to `false` outside the
  demo.
- **Postgres has no automatic retention.** Plan partition rotation for
  `invoices`/`audit_log` in production.
- **Scanned PDFs** have no text layer; they go to `needs_review` with
  `no_text_layer`. OCR is intentionally out of scope for Phase 0.
- **`value_sum` equivalent:** totals are stored per invoice; there is no
  aggregation layer yet.

Runbooks and troubleshooting: [`docs/OPERATIONS.md`](docs/OPERATIONS.md).
Design rationale: [`docs/DECISIONS.md`](docs/DECISIONS.md).

---

## Next steps

1. Tenant/API-key authentication and per-tenant quotas.
2. OCR fallback for scanned invoices.
3. Outbox pattern so a notification is retried even if the API process dies
   between the commit and the webhook call.
4. ERP export (CSV/webhook) and a reconciliation report.
5. OpenTelemetry tracing across API → queue → worker → webhook.
6. Retention/partitioning for `invoices`, `audit_log` and the archive.
