"""Notification adapters: Slack, Microsoft Teams and a local mock feed.

The message is only sent for invoices that passed validation. When no webhook is
configured the notification is recorded with status "mock" and shown in the demo
UI, so the full flow is observable without external credentials.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from . import metrics
from .config import Settings
from .pipeline import repository

log = logging.getLogger(__name__)

_TEMPLATES = {
    "en": (
        "New invoice processed successfully. Supplier: {supplier}. "
        "Total: {currency} {amount}. Due: {due}. Approved for payment."
    ),
    "pt-br": (
        "Nova fatura processada com sucesso. Fornecedor: {supplier}. "
        "Valor: {currency} {amount}. Vencimento: {due}. Aprovada para pagamento."
    ),
}


def render_message(invoice: dict[str, Any], language: str = "en") -> str:
    template = _TEMPLATES.get(language.lower(), _TEMPLATES["en"])
    amount = invoice.get("total_amount")
    amount_text = "n/a" if amount is None else f"{amount:,.2f}"
    return template.format(
        supplier=invoice.get("supplier_name") or "unknown",
        currency=invoice.get("currency") or "BRL",
        amount=amount_text,
        due=invoice.get("due_date") or "n/a",
    )


def notify(settings: Settings, invoice: dict[str, Any]) -> dict[str, Any]:
    """Send the notification and always record the attempt in Postgres."""
    invoice_id = str(invoice["id"])
    message = render_message(invoice, settings.notify_language)

    channel = "mock"
    if settings.slack_webhook_url:
        channel = "slack"
    elif settings.teams_webhook_url:
        channel = "teams"

    if channel == "mock":
        repository.record_notification(
            invoice_id, channel="mock", status="mock", message=message
        )
        repository.record_audit(invoice_id, "notification_mock", {"message": message})
        metrics.NOTIFICATIONS.labels(channel="mock", status="mock").inc()
        log.info(
            "notification recorded (mock)",
            extra={"invoice_id": invoice_id, "channel": "mock", "status": "mock"},
        )
        return {"channel": "mock", "status": "mock", "message": message}

    webhook = (
        settings.slack_webhook_url if channel == "slack" else settings.teams_webhook_url
    )
    status = "sent"
    error: str | None = None
    try:
        response = httpx.post(webhook, json={"text": message}, timeout=10)
        if response.status_code >= 400:
            status = "failed"
            error = f"webhook returned {response.status_code}: {response.text[:200]}"
    except httpx.HTTPError as exc:
        status = "failed"
        error = f"webhook request failed: {exc}"

    repository.record_notification(
        invoice_id, channel=channel, status=status, message=message, error=error
    )
    repository.record_audit(
        invoice_id,
        "notification_sent" if status == "sent" else "notification_failed",
        {"channel": channel, "status": status, "error": error},
    )
    metrics.NOTIFICATIONS.labels(channel=channel, status=status).inc()
    if status == "sent":
        log.info(
            "notification sent",
            extra={"invoice_id": invoice_id, "channel": channel, "status": status},
        )
    else:
        log.warning(
            "notification failed",
            extra={"invoice_id": invoice_id, "channel": channel, "status": status, "error": error},
        )
    return {"channel": channel, "status": status, "message": message, "error": error}
