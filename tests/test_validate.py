from datetime import date
from decimal import Decimal

from invoice_pipeline.extractor.validate import (
    cnpj_is_valid,
    normalize_cnpj,
    parse_date,
    parse_money,
    validate_extraction,
)
from invoice_pipeline.schemas import ExtractedInvoice


def test_parse_money_understands_both_separator_styles():
    assert parse_money("1.234,56") == Decimal("1234.56")
    assert parse_money("1,234.56") == Decimal("1234.56")
    assert parse_money("R$ 780,00") == Decimal("780.00")
    assert parse_money("US$ 4,250.00") == Decimal("4250.00")
    assert parse_money("42.50") == Decimal("42.50")
    assert parse_money("10") == Decimal("10")


def test_parse_money_rejects_garbage():
    assert parse_money(None) is None
    assert parse_money("") is None
    assert parse_money("no amount here") is None


def test_parse_date_formats():
    assert parse_date("2026-12-15") == date(2026, 12, 15)
    assert parse_date("15/12/2026") == date(2026, 12, 15)
    assert parse_date("2026-12-15T10:00:00Z") == date(2026, 12, 15)
    assert parse_date("not a date") is None


def test_cnpj_validation():
    assert cnpj_is_valid("11.222.333/0001-81")
    assert cnpj_is_valid("12345678000195")
    assert not cnpj_is_valid("11.222.333/0001-80")
    assert not cnpj_is_valid("11111111111111")


def test_normalize_cnpj():
    assert normalize_cnpj("11.222.333/0001-81") == "11222333000181"
    assert normalize_cnpj("123") is None
    assert normalize_cnpj(None) is None


def test_complete_extraction_has_no_errors():
    extracted = ExtractedInvoice(
        supplier_name="Acme Supplies Ltda",
        supplier_cnpj="11.222.333/0001-81",
        total_amount="1.234,56",
        currency="BRL",
        due_date="2026-12-15",
        confidence=0.95,
    )
    validated = validate_extraction(extracted)
    assert validated.is_complete
    assert validated.total_amount == Decimal("1234.56")
    assert validated.due_date == date(2026, 12, 15)
    assert validated.validation_errors == []


def test_missing_fields_are_reported_not_invented():
    validated = validate_extraction(ExtractedInvoice(confidence=0.9))
    assert not validated.is_complete
    assert "missing_supplier_name" in validated.validation_errors
    assert "missing_cnpj" in validated.validation_errors
    assert "missing_total" in validated.validation_errors
    assert "missing_due_date" in validated.validation_errors
    assert validated.total_amount is None
    assert validated.due_date is None


def test_invalid_cnpj_is_rejected():
    validated = validate_extraction(
        ExtractedInvoice(
            supplier_name="Bad",
            supplier_cnpj="11.222.333/0001-80",
            total_amount="99,90",
            due_date="2026-12-20",
            confidence=0.95,
        )
    )
    assert "invalid_cnpj" in validated.validation_errors


def test_low_confidence_requires_review():
    validated = validate_extraction(
        ExtractedInvoice(
            supplier_name="Acme",
            supplier_cnpj="11.222.333/0001-81",
            total_amount="10,00",
            due_date="2026-12-15",
            confidence=0.2,
        ),
        min_confidence=0.55,
    )
    assert "low_confidence" in validated.validation_errors
