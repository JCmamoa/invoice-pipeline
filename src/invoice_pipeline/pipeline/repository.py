"""Postgres persistence for invoices, the work queue, notifications and audit."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from ..db import get_pool
from ..schemas import ExtractedInvoice, ValidatedInvoice

STATUSES = ("queued", "processing", "processed", "needs_review", "failed")


def insert_invoice(
    *,
    invoice_id: str,
    source: str,
    source_ref: str | None,
    original_filename: str,
    sha256: str,
    archived_path: str,
) -> tuple[dict[str, Any], bool]:
    """Insert a queued invoice. Returns (row, duplicate)."""
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            INSERT INTO invoices
                (id, source, source_ref, original_filename, sha256, archived_path)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (sha256) DO NOTHING
            RETURNING *
            """,
            (invoice_id, source, source_ref, original_filename, sha256, archived_path),
        ).fetchone()
        if row is not None:
            return row, False
        existing = conn.execute(
            "SELECT * FROM invoices WHERE sha256 = %s", (sha256,)
        ).fetchone()
        assert existing is not None
        return existing, True


def find_by_sha(sha256: str) -> dict[str, Any] | None:
    with get_pool().connection() as conn:
        return conn.execute(
            "SELECT * FROM invoices WHERE sha256 = %s", (sha256,)
        ).fetchone()


def claim_next() -> dict[str, Any] | None:
    with get_pool().connection() as conn:
        return conn.execute(
            """
            WITH next AS (
                SELECT id
                  FROM invoices
                 WHERE status = 'queued'
                   AND next_attempt_at <= now()
                 ORDER BY created_at
                 FOR UPDATE SKIP LOCKED
                 LIMIT 1
            )
            UPDATE invoices AS invoice
               SET status = 'processing',
                   attempts = invoice.attempts + 1,
                   updated_at = now()
              FROM next
             WHERE invoice.id = next.id
            RETURNING invoice.*
            """
        ).fetchone()


def save_result(
    *,
    invoice_id: str,
    status: str,
    extracted: ExtractedInvoice,
    validated: ValidatedInvoice,
    llm_mode: str,
) -> None:
    with get_pool().connection() as conn:
        conn.execute(
            """
            UPDATE invoices
               SET status = %s,
                   supplier_name = %s,
                   supplier_cnpj = %s,
                   total_amount = %s,
                   currency = %s,
                   due_date = %s,
                   confidence = %s,
                   extraction = %s,
                   validation_errors = %s,
                   validation_warnings = %s,
                   llm_mode = %s,
                   error = NULL,
                   processed_at = now(),
                   updated_at = now()
             WHERE id = %s
            """,
            (
                status,
                validated.supplier_name,
                validated.supplier_cnpj,
                validated.total_amount,
                validated.currency,
                validated.due_date,
                validated.confidence,
                Jsonb(extracted.model_dump()),
                Jsonb(validated.validation_errors),
                Jsonb(validated.validation_warnings),
                llm_mode,
                invoice_id,
            ),
        )


def requeue_with_delay(invoice_id: str, delay_seconds: int, error: str) -> None:
    with get_pool().connection() as conn:
        conn.execute(
            """
            UPDATE invoices
               SET status = 'queued',
                   next_attempt_at = now() + make_interval(secs => %s),
                   error = %s,
                   updated_at = now()
             WHERE id = %s
            """,
            (delay_seconds, error, invoice_id),
        )


def mark_failed(invoice_id: str, error: str) -> None:
    with get_pool().connection() as conn:
        conn.execute(
            """
            UPDATE invoices
               SET status = 'failed',
                   error = %s,
                   processed_at = now(),
                   updated_at = now()
             WHERE id = %s
            """,
            (error, invoice_id),
        )


def requeue_manual(invoice_id: str) -> bool:
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            UPDATE invoices
               SET status = 'queued',
                   attempts = 0,
                   next_attempt_at = now(),
                   error = NULL,
                   validation_errors = '[]'::jsonb,
                   validation_warnings = '[]'::jsonb,
                   processed_at = NULL,
                   updated_at = now()
             WHERE id = %s
               AND status IN ('failed', 'needs_review')
            RETURNING id
            """,
            (invoice_id,),
        ).fetchone()
        return row is not None


def reap_stale(stale_seconds: int, max_attempts: int) -> int:
    with get_pool().connection() as conn:
        exhausted = conn.execute(
            """
            UPDATE invoices
               SET status = 'failed',
                   error = 'processing claim expired after all attempts',
                   processed_at = now(),
                   updated_at = now()
             WHERE status = 'processing'
               AND updated_at < now() - make_interval(secs => %s)
               AND attempts >= %s
            """,
            (stale_seconds, max_attempts),
        ).rowcount
        recovered = conn.execute(
            """
            UPDATE invoices
               SET status = 'queued',
                   next_attempt_at = now(),
                   error = 'processing claim expired; requeued',
                   updated_at = now()
             WHERE status = 'processing'
               AND updated_at < now() - make_interval(secs => %s)
            """,
            (stale_seconds,),
        ).rowcount
        return (exhausted or 0) + (recovered or 0)


def get_invoice(invoice_id: str) -> dict[str, Any] | None:
    with get_pool().connection() as conn:
        return conn.execute(
            "SELECT * FROM invoices WHERE id = %s", (invoice_id,)
        ).fetchone()


def list_invoices(
    *, status: str | None = None, limit: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    with get_pool().connection() as conn:
        if status:
            return conn.execute(
                """
                SELECT * FROM invoices
                 WHERE status = %s
                 ORDER BY created_at DESC
                 LIMIT %s OFFSET %s
                """,
                (status, limit, offset),
            ).fetchall()
        return conn.execute(
            """
            SELECT * FROM invoices
             ORDER BY created_at DESC
             LIMIT %s OFFSET %s
            """,
            (limit, offset),
        ).fetchall()


def queue_depth() -> int:
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT count(*) AS depth FROM invoices WHERE status IN ('queued', 'processing')"
        ).fetchone()
        return int(row["depth"]) if row else 0


def status_counts() -> dict[str, int]:
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT status, count(*) AS total FROM invoices GROUP BY status"
        ).fetchall()
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[row["status"]] = int(row["total"])
    counts["total"] = sum(counts[status] for status in STATUSES)
    return counts


def record_audit(invoice_id: str, action: str, detail: dict[str, Any] | None = None) -> None:
    with get_pool().connection() as conn:
        conn.execute(
            "INSERT INTO audit_log (invoice_id, action, detail) VALUES (%s, %s, %s)",
            (invoice_id, action, Jsonb(detail or {})),
        )


def audit_for(invoice_id: str) -> list[dict[str, Any]]:
    with get_pool().connection() as conn:
        return conn.execute(
            """
            SELECT id, action, detail, created_at
              FROM audit_log
             WHERE invoice_id = %s
             ORDER BY created_at
            """,
            (invoice_id,),
        ).fetchall()


def record_notification(
    invoice_id: str,
    *,
    channel: str,
    status: str,
    message: str,
    error: str | None = None,
) -> None:
    with get_pool().connection() as conn:
        conn.execute(
            """
            INSERT INTO notifications (invoice_id, channel, status, message, error)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (invoice_id, channel, status, message, error),
        )


def notifications_for(invoice_id: str) -> list[dict[str, Any]]:
    with get_pool().connection() as conn:
        return conn.execute(
            """
            SELECT id, channel, status, message, error, created_at
              FROM notifications
             WHERE invoice_id = %s
             ORDER BY created_at
            """,
            (invoice_id,),
        ).fetchall()


def recent_notifications(limit: int = 20) -> list[dict[str, Any]]:
    with get_pool().connection() as conn:
        return conn.execute(
            """
            SELECT n.id, n.invoice_id, n.channel, n.status, n.message, n.created_at,
                   i.supplier_name, i.total_amount, i.currency
              FROM notifications n
              JOIN invoices i ON i.id = n.invoice_id
             ORDER BY n.created_at DESC
             LIMIT %s
            """,
            (limit,),
        ).fetchall()


def reset_demo_data() -> None:
    with get_pool().connection() as conn:
        conn.execute("TRUNCATE notifications, audit_log, invoices RESTART IDENTITY")
