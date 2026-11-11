from pathlib import Path

from invoice_pipeline.extractor.mock import mock_extract
from invoice_pipeline.extractor.pdf import extract_text
from invoice_pipeline.extractor.validate import validate_extraction

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def _run(name: str):
    text = extract_text((SAMPLES / name).read_bytes())
    extracted = mock_extract(text)
    return extracted, validate_extraction(extracted)


def test_acme_is_processed():
    extracted, validated = _run("acme_invoice.pdf")
    assert validated.is_complete
    assert validated.supplier_name == "Acme Supplies Ltda"
    assert validated.supplier_cnpj == "11222333000181"
    assert str(validated.total_amount) == "1234.56"
    assert validated.currency == "BRL"
    assert validated.due_date.isoformat() == "2026-12-15"


def test_globex_handles_portuguese_labels_and_usd():
    _, validated = _run("globex_invoice.pdf")
    assert validated.is_complete
    assert validated.supplier_name == "Globex Trading Inc"
    assert validated.currency == "USD"
    assert str(validated.total_amount) == "4250.00"


def test_missing_due_date_goes_to_review():
    _, validated = _run("missing_due_date.pdf")
    assert not validated.is_complete
    assert validated.validation_errors == ["missing_due_date"]
    assert validated.total_amount is not None


def test_invalid_cnpj_goes_to_review():
    _, validated = _run("invalid_cnpj.pdf")
    assert not validated.is_complete
    assert validated.validation_errors == ["invalid_cnpj"]


def test_subtotal_is_not_mistaken_for_total():
    _, validated = _run("acme_invoice.pdf")
    assert str(validated.total_amount) == "1234.56"
