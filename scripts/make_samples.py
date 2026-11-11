#!/usr/bin/env python3
"""Generate the sample invoice PDFs used by the demo and by tests.

The writer is intentionally tiny and stdlib-only: the samples are text-based
PDFs (not scans), which is what the extraction pipeline expects. Run it from the
repository root:

    python3 scripts/make_samples.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(path: Path, lines: list[str], font_size: int = 11) -> None:
    leading = font_size + 5
    parts = [
        "BT",
        f"/F1 {font_size} Tf",
        f"{leading} TL",
        "72 750 Td",
    ]
    for line in lines:
        parts.append(f"({_escape(line)}) Tj T*")
    parts.append("ET")
    stream = "\n".join(parts).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]

    output = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_position = len(output)
    size = len(objects) + 1
    output += f"xref\n0 {size}\n".encode("ascii")
    output += b"0000000000 65535 f \n"
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode("ascii")
    output += (
        f"trailer\n<< /Size {size} /Root 1 0 R >>\n"
        f"startxref\n{xref_position}\n%%EOF\n"
    ).encode("ascii")
    path.write_bytes(bytes(output))


SAMPLES_DATA: dict[str, list[str]] = {
    "acme_invoice.pdf": [
        "ACME SUPPLIES LTDA",
        "",
        "Supplier: Acme Supplies Ltda",
        "CNPJ: 11.222.333/0001-81",
        "Invoice: INV-2026-0042",
        "Issue date: 2026-11-01",
        "Due date: 2026-12-15",
        "Description: Industrial spare parts",
        "Subtotal: R$ 1,000.00",
        "Tax: R$ 234.56",
        "Total: R$ 1,234.56",
        "Payment terms: bank transfer",
    ],
    "globex_invoice.pdf": [
        "GLOBEX TRADING INC",
        "",
        "Fornecedor: Globex Trading Inc",
        "Tax ID / CNPJ: 12.345.678/0001-95",
        "Invoice: GX-7781",
        "Data de Vencimento: 2026-12-05",
        "Description: Cloud services",
        "Valor Total: US$ 4,250.00",
    ],
    "missing_due_date.pdf": [
        "INITECH SERVICES LTDA",
        "",
        "Supplier: Initech Services Ltda",
        "CNPJ: 98.765.432/0001-98",
        "Invoice: IT-9001",
        "Description: Consulting hours",
        "Total: R$ 780,00",
        "Payment terms: 30 days",
    ],
    "invalid_cnpj.pdf": [
        "BAD DATA SUPPLIER",
        "",
        "Fornecedor: Bad Data Supplier",
        "CNPJ: 11.222.333/0001-80",
        "Invoice: BD-0001",
        "Vencimento: 2026-12-20",
        "Total: R$ 99,90",
    ],
}


def main() -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    for name, lines in SAMPLES_DATA.items():
        path = SAMPLES / name
        write_pdf(path, lines)
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
