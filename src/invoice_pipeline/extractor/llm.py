"""OpenAI-compatible extraction with a deterministic mock fallback."""

from __future__ import annotations

import json
import logging
import re

import httpx

from ..config import Settings
from ..schemas import ExtractedInvoice
from .mock import mock_extract

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an invoice extraction engine.
Return a single JSON object with exactly these keys:
supplier_name, supplier_cnpj, total_amount, currency, due_date, confidence, notes.

Rules:
- Use only information explicitly present in the document.
- If a value is not present, return null for that key. Never guess or invent.
- supplier_cnpj: keep the original formatting (e.g. "11.222.333/0001-81").
- total_amount: the printed numeric value as a string (e.g. "1.234,56").
- currency: ISO code (BRL, USD, EUR, GBP) only when an explicit symbol or code exists.
- due_date: ISO date (YYYY-MM-DD) only when the document states a due date.
- confidence: a number from 0 to 1 for the overall extraction.
- notes: short free-text notes or null.

Respond with JSON only, no markdown and no extra text."""


class LLMError(Exception):
    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


def _parse_content(content: str) -> ExtractedInvoice:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise LLMError("model did not return JSON", transient=True) from None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError("model returned invalid JSON", transient=True) from exc
    if not isinstance(data, dict):
        raise LLMError("model JSON is not an object", transient=True)
    return ExtractedInvoice.model_validate(data)


def extract_with_llm(text: str, settings: Settings) -> ExtractedInvoice:
    if not settings.llm_api_key:
        raise LLMError("LLM_API_KEY is not set", transient=False)

    url = f"{settings.llm_base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, object] = {
        "model": settings.llm_model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Extract the invoice fields from this document:\n\n{text}",
            },
        ],
        "response_format": {"type": "json_object"},
    }

    response: httpx.Response | None = None
    for attempt in range(2):
        try:
            response = httpx.post(
                url, json=payload, headers=headers, timeout=settings.llm_timeout
            )
        except httpx.TimeoutException as exc:
            raise LLMError(f"LLM request timed out: {exc}", transient=True) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}", transient=True) from exc

        if response.status_code == 400 and "response_format" in payload and attempt == 0:
            # Some OpenAI-compatible servers reject response_format.
            payload.pop("response_format", None)
            continue
        break

    assert response is not None
    if response.status_code == 429 or response.status_code >= 500:
        raise LLMError(
            f"LLM returned {response.status_code}: {response.text[:300]}", transient=True
        )
    if response.status_code >= 400:
        raise LLMError(
            f"LLM returned {response.status_code}: {response.text[:300]}", transient=False
        )

    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMError("unexpected LLM response body", transient=True) from exc
    return _parse_content(content)


def extract(text: str, settings: Settings) -> tuple[ExtractedInvoice, str]:
    """Return the extraction and the mode that produced it."""
    mode = settings.llm_mode.lower()

    if mode == "mock":
        return mock_extract(text), "mock"

    if mode == "openai":
        return extract_with_llm(text, settings), "openai"

    if mode == "auto":
        if settings.llm_api_key:
            try:
                return extract_with_llm(text, settings), "openai"
            except LLMError as exc:
                if exc.transient:
                    raise
                log.warning("LLM unavailable, falling back to mock: %s", exc)
                return mock_extract(text), "mock-fallback"
        return mock_extract(text), "mock"

    raise LLMError(f"unknown LLM_MODE: {settings.llm_mode}", transient=False)
