"""Data contracts for extraction and validation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class ExtractedInvoice(BaseModel):
    """Raw shape returned by the LLM or the deterministic mock extractor.

    Values are kept as strings because invoices print money and dates in
    different formats; parsing and validation happen in a separate step.
    """

    supplier_name: str | None = None
    supplier_cnpj: str | None = None
    total_amount: str | None = None
    currency: str | None = None
    due_date: str | None = None
    confidence: float | None = None
    notes: str | None = None


class ValidatedInvoice(BaseModel):
    """Normalized values plus the reasons a human may need to review them."""

    supplier_name: str | None = None
    supplier_cnpj: str | None = None
    total_amount: Decimal | None = None
    currency: str | None = None
    due_date: date | None = None
    confidence: float | None = None
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.validation_errors
