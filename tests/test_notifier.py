from datetime import date
from decimal import Decimal

import httpx

from invoice_pipeline.notifier import (
    _post_webhook,
    render_message,
    slack_payload,
    teams_payload,
)


def _invoice(**overrides):
    base = {
        "id": "00000000-0000-0000-0000-000000000000",
        "supplier_name": "Acme Supplies Ltda",
        "supplier_cnpj": "11222333000181",
        "currency": "BRL",
        "total_amount": Decimal("1234.56"),
        "due_date": date(2026, 12, 15),
    }
    return {**base, **overrides}


def test_message_in_english():
    message = render_message(_invoice(), "en")
    assert "New invoice processed successfully" in message
    assert "Acme Supplies Ltda" in message
    assert "BRL 1,234.56" in message
    assert "2026-12-15" in message


def test_message_in_portuguese():
    message = render_message(_invoice(), "pt-BR")
    assert "Nova fatura processada com sucesso" in message
    assert "Aprovada para pagamento" in message


def test_missing_values_do_not_crash():
    message = render_message(_invoice(supplier_name=None, total_amount=None, due_date=None))
    assert "unknown" in message
    assert "n/a" in message


def test_slack_payload_has_blocks_and_text_fallback():
    message = render_message(_invoice(), "en")
    payload = slack_payload(_invoice(), message)
    assert payload["text"] == message
    assert payload["blocks"][0]["type"] == "header"
    facts = " ".join(
        field["text"]
        for block in payload["blocks"]
        if "fields" in block
        for field in block["fields"]
    )
    assert "Acme Supplies Ltda" in facts
    assert "BRL 1,234.56" in facts


def test_teams_payload_is_a_message_card():
    message = render_message(_invoice(), "en")
    payload = teams_payload(_invoice(), message)
    assert payload["@type"] == "MessageCard"
    assert payload["text"] == message
    facts = {fact["name"]: fact["value"] for fact in payload["sections"][0]["facts"]}
    assert facts["Supplier"] == "Acme Supplies Ltda"
    assert facts["Total"] == "BRL 1,234.56"
    assert facts["Due date"] == "2026-12-15"


def test_webhook_retries_with_plain_text_on_400(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)
        status = 400 if len(calls) == 1 else 200
        return httpx.Response(status, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    response = _post_webhook("https://example.test/hook", {"blocks": []}, "hello")

    assert response.status_code == 200
    assert calls == [{"blocks": []}, {"text": "hello"}]

