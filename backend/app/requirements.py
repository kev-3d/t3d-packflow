"""Special client requirements: Excel parsing and tolerant company-name matching.

Company names in the requirements workbook rarely equal the names in the client list or on a
purchase order ("AddNorth" vs "Add North 3D AB", "Prima Printer Nordic AB….PRI001", combined names
such as "3DNet / Sem AS"). A missed match would silently hide a packing note, so matching is
deliberately tolerant and everything that is not an exact match is surfaced as a suggestion.
"""
from __future__ import annotations

import io
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Iterable

from openpyxl import load_workbook

from .extraction import _clean_cell, _normalize_header

REQUIREMENT_TEXT_FIELDS = (
    "invoice_email", "payment", "pricing_requirements", "docs_required", "courier", "comments",
)

_HEADER_ALIASES = {
    "company": {"company", "customer", "client", "cliente", "empresa"},
    "invoice_email": {"invoice email", "invoice e mail", "invoice emails"},
    "payment": {"payment", "pago"},
    "pricing_requirements": {"pricing invoice requirements", "pricing requirements", "invoice requirements", "pricing"},
    "docs_required": {"docs required", "documents required", "docs", "documentos"},
    "courier": {"courier", "carrier", "shipping method"},
    "comments": {"comments", "comment", "notes", "comentarios"},
}

# Suffixes that differ between a PO, the client list and the requirements sheet for the same company.
_LEGAL_TOKENS = {
    "inc", "llc", "ltd", "limited", "gmbh", "ab", "as", "sl", "slu", "sa", "co", "corp", "corporation",
    "company", "bv", "oy", "aps", "pvt", "pty", "cc", "srl", "spa", "ag", "kk", "sas", "sarl", "llp",
    "plc", "doo", "the",
}

SUGGESTION_THRESHOLD = 0.85


def name_key(value: Any) -> str:
    """Case, accent and punctuation-insensitive key; drops client-list codes like ``….PRI001``."""
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\.{2,}\s*[a-z0-9]+\s*$", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def loose_key(value: Any) -> str:
    """``name_key`` without legal suffixes (Inc, LLC, GmbH…), so "Fbrc8" equals "Fbrc8 LLC"."""
    key = name_key(value)
    tokens = [token for token in key.split() if token not in _LEGAL_TOKENS]
    return " ".join(tokens) or key


def company_name_variants(company: str, manual_aliases: Iterable[str] = ()) -> list[str]:
    """Names a company may appear under: itself, parts of combined names, parentheticals and manual aliases."""
    candidates = [company]
    base = re.sub(r"\([^)]*\)", " ", company)
    candidates.append(base)
    candidates += re.split(r"\s+t/a\s+|/|\n", base, flags=re.IGNORECASE)
    # A parenthetical is an alias only when it is a name ("Printalot Argentina"), not a country ("Mexico").
    candidates += [inner for inner in re.findall(r"\(([^)]*)\)", company) if len(inner.split()) >= 2]
    candidates += list(manual_aliases)
    seen: set[str] = set()
    variants: list[str] = []
    for candidate in candidates:
        key = name_key(candidate)
        if len(key) >= 3 and key not in seen:
            seen.add(key)
            variants.append(candidate.strip())
    return variants


Prepared = tuple[str, str, str]


def prepare(value: Any) -> Prepared:
    """Precompute ``(name_key, loose_key, compact loose key)`` once so large lists compare quickly."""
    strict = name_key(value)
    loose = loose_key(value)
    return strict, loose, loose.replace(" ", "")


def prepared_exact(left: Prepared, right: Prepared) -> bool:
    return bool(left[0]) and (left[0] == right[0] or left[1] == right[1])


def prepared_similarity(left: Prepared, right: Prepared) -> float:
    a, b = left[2], right[2]
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    matcher = SequenceMatcher(None, a, b)
    if matcher.real_quick_ratio() < SUGGESTION_THRESHOLD or matcher.quick_ratio() < SUGGESTION_THRESHOLD:
        return 0.0
    return matcher.ratio()


def names_match(left: str, right: str) -> bool:
    return prepared_exact(prepare(left), prepare(right))


def similarity(left: str, right: str) -> float:
    return prepared_similarity(prepare(left), prepare(right))


def best_match(
    variants: Iterable[Prepared], candidates: Iterable[tuple[Any, Prepared]],
) -> tuple[list[Any], tuple[Any, float] | None]:
    """Compare every name variant of one entity against ``(identifier, prepared name)`` candidates.

    Returns ``(exact identifiers, best suggestion)``. The suggestion is the closest non-exact candidate
    scoring at least ``SUGGESTION_THRESHOLD`` (or None).
    """
    variants = list(variants)
    exact: list[Any] = []
    best: tuple[Any, float] | None = None
    for identifier, candidate in candidates:
        if any(prepared_exact(variant, candidate) for variant in variants):
            exact.append(identifier)
            continue
        score = max((prepared_similarity(variant, candidate) for variant in variants), default=0.0)
        if score >= SUGGESTION_THRESHOLD and (best is None or score > best[1]):
            best = (identifier, score)
    return exact, best


def _header_columns(row: tuple[Any, ...]) -> dict[str, int]:
    columns: dict[str, int] = {}
    for index, header in enumerate(row):
        normalized = re.sub(r"[^a-z0-9]+", " ", _normalize_header(header)).strip()
        for field, aliases in _HEADER_ALIASES.items():
            if field not in columns and normalized in aliases:
                columns[field] = index
    return columns


def parse_requirements_excel(content: bytes) -> tuple[list[dict[str, str]], list[str]]:
    """Read special-client rows from the first sheet that has a Company header plus a requirement column."""
    try:
        workbook = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:  # openpyxl raises several types for corrupt files
        return [], [f"The Excel file could not be read ({exc})."]
    for sheet in workbook.worksheets:
        rows = list(sheet.iter_rows(values_only=True))
        for header_index, row in enumerate(rows[:30]):
            columns = _header_columns(row)
            if "company" in columns and any(field in columns for field in REQUIREMENT_TEXT_FIELDS):
                return _read_rows(rows[header_index + 1:], columns, header_index + 2)
    return [], ["Missing columns: a header row with Company and at least one of Docs Required, Courier, Comments or Pricing / Invoice requirements."]


def _read_rows(rows: list[tuple[Any, ...]], columns: dict[str, int], first_row_number: int) -> tuple[list[dict[str, str]], list[str]]:
    records: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    for row_number, row in enumerate(rows, start=first_row_number):
        def cell(field: str) -> str:
            index = columns.get(field)
            if index is None or index >= len(row):
                return ""
            return _clean_cell(row[index], multiline=field != "company")
        record = {"company": cell("company"), **{field: cell(field) for field in REQUIREMENT_TEXT_FIELDS}}
        if not any(record.values()):
            continue
        if not record["company"]:
            errors.append(f"Row {row_number}: Company is empty.")
            continue
        key = name_key(record["company"])
        if key in records:
            errors.append(f"Row {row_number}: {record['company']} appears more than once; the later row was used.")
        records[key] = record
    return list(records.values()), errors
