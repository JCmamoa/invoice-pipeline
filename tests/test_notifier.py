from datetime import date
from decimal import Decimal

from invoice_pipeline.notifier import render_message


def _invoice(**overrides):
    base = {
        "id": "00000000-0000-0000-0000-000000000000",
        "supplier_name": "Acme Supplies Ltda",
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
