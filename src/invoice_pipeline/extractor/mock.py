"""Deterministic, offline extractor used by the demo and by tests.

It applies label-based regular expressions to the PDF text. This is the same
contract the LLM must satisfy: return only values that are explicitly present,
and leave everything else empty so validation can route the invoice to review.
"""

from __future__ import annotations

import re

from ..schemas import ExtractedInvoice

_CNPJ_RE = re.compile(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}")
_DATE_RE = re.compile(r"\b(\d{2}[/.-]\d{2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")
# A number with optional thousands/decimal separators. Parsing is delegated to
# validate.parse_money, which understands both 1.234,56 and 1,234.56.
_MONEY_RE = re.compile(r"[-+]?\d[\d.,\s]*\d|[-+]?\d")

_SUPPLIER_LABELS = (
    "fornecedor",
    "supplier",
    "emitente",
    "razão social",
    "razao social",
    "vendor",
)
_CNPJ_LABELS = ("cnpj", "tax id")
_TOTAL_LABELS = (
    "valor total",
    "total amount",
    "amount due",
    "valor a pagar",
    "total",
    "amount",
)
_DUE_LABELS = ("data de vencimento", "vencimento", "due date", "due")


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _tail_after_label(line: str, labels: tuple[str, ...]) -> str | None:
    lowered = line.lower()
    for label in labels:
        # Word boundaries avoid matching "total" inside "subtotal".
        match = re.search(rf"(?<![a-z]){re.escape(label)}(?![a-z])", lowered)
        if not match:
            continue
        tail = line[match.end() :].strip(" :-\t|")
        if tail:
            return tail
    return None


def _first_match(regex: re.Pattern[str], value: str | None) -> str | None:
    if not value:
        return None
    match = regex.search(value)
    return match.group(0).strip() if match else None


def _detect_currency(text: str) -> str | None:
    lowered = text.lower()
    if "r$" in lowered or re.search(r"\bbrl\b", lowered):
        return "BRL"
    if "us$" in lowered or re.search(r"\busd\b", lowered):
        return "USD"
    if "€" in text or re.search(r"\beur\b", lowered):
        return "EUR"
    if "£" in text or re.search(r"\bgbp\b", lowered):
        return "GBP"
    return None


def mock_extract(text: str) -> ExtractedInvoice:
    lines = _lines(text)

    supplier_name: str | None = None
    cnpj: str | None = None
    total: str | None = None
    due: str | None = None

    for line in lines:
        if supplier_name is None:
            tail = _tail_after_label(line, _SUPPLIER_LABELS)
            if tail:
                # Skip lines whose value is only a tax id.
                candidate = re.sub(r"(?i)(cnpj|tax id)\s*[:\-]?\s*.*$", "", tail).strip()
                if candidate and not _CNPJ_RE.fullmatch(candidate):
                    supplier_name = candidate
        if cnpj is None:
            tail = _tail_after_label(line, _CNPJ_LABELS)
            cnpj = _first_match(_CNPJ_RE, tail)
        if total is None:
            tail = _tail_after_label(line, _TOTAL_LABELS)
            total = _first_match(_MONEY_RE, tail)
        if due is None:
            tail = _tail_after_label(line, _DUE_LABELS)
            due = _first_match(_DATE_RE, tail)

    if cnpj is None:
        cnpj = _first_match(_CNPJ_RE, text)
    if total is None:
        total = _first_match(_MONEY_RE, text)
    if due is None:
        due = _first_match(_DATE_RE, text)

    found = sum(value is not None for value in (supplier_name, cnpj, total, due))
    confidence = {4: 0.95, 3: 0.7, 2: 0.5, 1: 0.3, 0: 0.1}[found]

    return ExtractedInvoice(
        supplier_name=supplier_name,
        supplier_cnpj=cnpj,
        total_amount=total,
        currency=_detect_currency(text),
        due_date=due,
        confidence=confidence,
        notes="deterministic mock extractor",
    )
