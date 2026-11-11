"""Normalization and validation of extracted invoice fields.

The rule is simple: never invent data. A missing or invalid field becomes a
validation error and the invoice is routed to human review. Values are only
accepted when they are present in the document and pass a deterministic check
(CNPJ checksum, money format, plausible date).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from ..schemas import ExtractedInvoice, ValidatedInvoice

_CNPJ_WEIGHTS_1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_CNPJ_WEIGHTS_2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%Y/%m/%d")
_SUPPORTED_CURRENCIES = {"BRL", "USD", "EUR", "GBP"}


def normalize_cnpj(raw: str | None) -> str | None:
    """Return the 14 digits of a CNPJ, or None when the length is wrong."""
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    return digits if len(digits) == 14 else None


def cnpj_is_valid(raw: str) -> bool:
    digits = [int(char) for char in re.sub(r"\D", "", raw)]
    if len(digits) != 14 or len(set(digits)) == 1:
        return False

    def check_digit(base: list[int], weights: tuple[int, ...]) -> int:
        total = sum(value * weight for value, weight in zip(base, weights, strict=True))
        remainder = total % 11
        return 0 if remainder < 2 else 11 - remainder

    first = check_digit(digits[:12], _CNPJ_WEIGHTS_1)
    second = check_digit(digits[:13], _CNPJ_WEIGHTS_2)
    return first == digits[12] and second == digits[13]


def parse_money(raw: str | None) -> Decimal | None:
    """Parse '1.234,56', '1,234.56', '42.50', 'R$ 10' and similar forms."""
    if raw is None:
        return None
    cleaned = re.sub(r"[^\d,.\-]", "", str(raw)).strip()
    if not cleaned or not re.search(r"\d", cleaned):
        return None

    negative = cleaned.startswith("-")
    cleaned = cleaned.lstrip("-")
    if not cleaned:
        return None

    if "." in cleaned and "," in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        if cleaned.count(",") == 1 and len(cleaned.rsplit(",", 1)[1]) == 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif (
        "." in cleaned
        and cleaned.count(".") == 1
        and len(cleaned.rsplit(".", 1)[1]) != 2
    ):
        # A single dot with three digits after it is a thousands separator.
        cleaned = cleaned.replace(".", "")

    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    text = str(raw).strip().split("T")[0].split(" ")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _plausible_due_date(value: date) -> bool:
    today = date.today()
    return date(2000, 1, 1) <= value <= date(today.year + 10, 12, 31)


def validate_extraction(
    extracted: ExtractedInvoice, min_confidence: float = 0.55
) -> ValidatedInvoice:
    errors: list[str] = []
    warnings: list[str] = []

    supplier_name = (extracted.supplier_name or "").strip() or None
    if not supplier_name:
        errors.append("missing_supplier_name")

    cnpj = normalize_cnpj(extracted.supplier_cnpj)
    if cnpj is None:
        errors.append("missing_cnpj")
    elif not cnpj_is_valid(cnpj):
        errors.append("invalid_cnpj")

    total = parse_money(extracted.total_amount)
    if total is None:
        errors.append("missing_total")
    elif total <= 0:
        errors.append("invalid_total")

    due = parse_date(extracted.due_date)
    if due is None:
        errors.append("missing_due_date")
    elif not _plausible_due_date(due):
        errors.append("implausible_due_date")

    currency = (extracted.currency or "").strip().upper() or None
    if currency is None:
        currency = "BRL"
        warnings.append("currency_inferred")
    elif currency not in _SUPPORTED_CURRENCIES:
        warnings.append(f"unknown_currency:{currency}")

    confidence = extracted.confidence
    if confidence is not None:
        confidence = max(0.0, min(1.0, float(confidence)))
        if confidence < min_confidence:
            errors.append("low_confidence")

    return ValidatedInvoice(
        supplier_name=supplier_name,
        supplier_cnpj=cnpj,
        total_amount=total,
        currency=currency,
        due_date=due,
        confidence=confidence,
        validation_errors=errors,
        validation_warnings=warnings,
    )
