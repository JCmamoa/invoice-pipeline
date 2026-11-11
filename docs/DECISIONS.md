# Decisions (ADRs)

Short records of the choices that shape the pipeline, with the rejected
alternative and the consequence.

## ADR-001 — Postgres as the queue, not an external broker

**Context:** a pipeline like this often starts with RabbitMQ, Redis or Kafka.

**Decision:** `invoices` is both the business table and the work queue, claimed
with `SELECT ... FOR UPDATE SKIP LOCKED`.

**Why:** the queue item and the business row must change together (status,
validation, audit). Keeping them in one transaction removes the dual-write
problem. Phase 0 handles tens of invoices per minute, far below what Postgres
can claim.

**Consequence:** polling instead of push; higher idle latency (default 2s). If
volume grows by orders of magnitude, move the queue to a broker while keeping
Postgres as the system of record.

## ADR-002 — Strict JSON contract plus a deterministic mock

**Context:** LLMs are good at reading invoices but can hallucinate fields.

**Decision:** a fixed JSON schema, `temperature=0`, explicit "return null if not
present" instructions, and a deterministic regex extractor that implements the
same contract for offline use.

**Why:** the mock makes the demo and the tests reproducible and free; the strict
prompt reduces fabrication. Validation still treats every LLM output as
untrusted.

**Consequence:** two extractors to maintain. The mock only understands labeled
text; the LLM covers more layouts.

## ADR-003 — SHA-256 of the file as the idempotency key

**Context:** the same invoice can arrive by upload, folder and email.

**Decision:** `sha256` is `UNIQUE`; a repeated file returns the existing invoice.

**Why:** filenames are not reliable and message ids differ per channel. The file
content is the only stable identity. It is also cheap to compute before any
expensive work.

**Consequence:** two genuinely different invoices with identical bytes collapse
into one. This is the desired behavior for invoice documents.

## ADR-004 — Validation is separate from extraction

**Context:** asking the model to validate itself is convenient but unreliable.

**Decision:** the model returns raw values; a deterministic module parses money,
dates and CNPJ and applies the rules.

**Why:** checks such as the CNPJ checksum and money separator ambiguity must be
deterministic. Separating them also lets the mock and the LLM share the same
validation path.

**Consequence:** more code, but every rejection is explainable and testable.

## ADR-005 — `needs_review` instead of guessing

**Context:** a missing due date could be inferred from payment terms.

**Decision:** missing or invalid required fields produce `needs_review` with a
reason; values stay `NULL`.

**Why:** a wrong payment date is worse than a human review. The brief explicitly
requires "sem inventar nada" (do not invent anything).

**Consequence:** some invoices need manual attention. The UI makes the reason
visible and offers re-processing after a fix.

## ADR-006 — Archive the original before extraction

**Context:** the PDF is the legal proof of the invoice.

**Decision:** copy the file to `archive/YYYYMMDD/<uuid>.pdf` before inserting the
queue row, and store the relative path.

**Why:** an extraction failure must not lose the source document. Archiving
first also makes the invoice downloadable while it is still queued.

**Consequence:** orphan files can remain if the database insert fails; they are
harmless and can be cleaned by a periodic job.

## ADR-007 — FastAPI + Postgres, not a workflow engine

**Context:** tools like Airflow or Temporal could orchestrate the steps.

**Decision:** explicit stages in a FastAPI app and a worker process.

**Why:** the flow has one branch (processed vs. review) and a simple retry
policy. A workflow engine would add infrastructure and concepts without
removing code in Phase 0.

**Consequence:** multi-step compensation must be written by hand. If the flow
grows (approvals, ERP posting, multi-system sagas), revisit this.

## ADR-008 — Notification adapters with a mock fallback

**Context:** the brief mentions Slack, Teams and WhatsApp.

**Decision:** Slack and Teams incoming webhooks are supported; with no webhook,
the message is recorded as `mock` and shown in the UI. The message language is
configurable (`en`/`pt-BR`).

**Why:** the demo must work without external credentials, and the notification
must be observable. WhatsApp requires a provider account and is out of scope.

**Consequence:** the mock is not a delivery guarantee. In production, add an
outbox/retry table around the webhook call.

## ADR-009 — OCR is an optional, local fallback

**Context:** many suppliers send scanned PDFs, which have no text layer.

**Decision:** when `OCR_ENABLED=true`, render up to `OCR_MAX_PAGES` pages with
`pypdfium2` and run a local Tesseract installation. Digital invoices never touch
OCR. If OCR is disabled or produces too little text, the invoice goes to
`needs_review` with `no_text_layer`.

**Why:** OCR adds dependencies and a failure surface, so it must not affect the
common path. Running it locally (no external document-AI service) keeps the
zero-cost, self-hosted constraint and keeps invoice content on the host. The
`text_source` column makes the difference visible in the audit trail.

**Consequence:** the image is larger and OCR is CPU-bound (roughly 1–3s per
page). Low-quality scans can still fail; those invoices need a human or a
better scan. A document-AI fallback remains future work.

## ADR-010 — Vanilla JS UI with no build step

**Context:** a React/Vite UI would be conventional.

**Decision:** one HTML file, one CSS file and one JS file served by FastAPI.

**Why:** the demo must start with `docker compose up` and no Node toolchain. The
UI has four screens and no shared state beyond fetch calls.

**Consequence:** less component tooling; acceptable for Phase 0.

## ADR-011 — English artifacts, localizable notification

**Context:** the project convention is English for code, docs, logs and commits;
the manager may prefer Portuguese.

**Decision:** all repository artifacts are English. The notification template is
selected by `NOTIFY_LANGUAGE`.

**Why:** keeps the codebase consistent for contributors without forcing the
business message language.

**Consequence:** two message templates to keep in sync.

## ADR-012 — Demo reset endpoint behind `DEMO_MODE`

**Context:** a reviewer wants to clear data and replay the samples.

**Decision:** `POST /api/demo/reset` truncates the tables and deletes archived
files, but only when `DEMO_MODE=true`.

**Why:** a one-click reset makes the demo repeatable. The flag prevents an
accidental wipe in any non-demo deployment.

**Consequence:** `DEMO_MODE` must be `false` outside the demo; this is documented
in the operations guide.
