from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterable

from openpyxl import load_workbook
from pypdf import PdfReader

from .po_lexicon import lexicon, normalize_token


HEADER_ALIASES = {
    "sku": {"sku", "codigo", "código", "product code", "item", "referencia"},
    "name": {"name", "nombre", "descripcion", "descripción", "product"},
    "length_cm": {"length", "largo", "largo cm", "length cm"},
    "width_cm": {"width", "ancho", "ancho cm", "width cm"},
    "height_cm": {"height", "alto", "alto cm", "height cm"},
    "weight_kg": {"weight", "peso", "peso kg", "weight kg"},
    "code": {"code", "codigo", "código", "box code", "caja"},
    "empty_weight_kg": {"empty weight", "peso caja", "tara", "empty weight kg"},
    "max_weight_kg": {"max weight", "peso maximo", "peso máximo", "capacidad kg"},
}


# ---------------------------------------------------------------------------
# Purchase-order PDFs
#
# pypdf emits text in content-stream order, which is not reading order: a table
# cell can end up on its own line, far from its label. The reader therefore
# places every fragment by its coordinates, groups fragments into visual rows
# and splits each row into cells. Order lines are then accepted only when the
# numbers agree arithmetically (quantity x unit price = amount). Anything that
# cannot be verified is kept as a warning, never silently dropped or invented.
#
# Wording is not in this file. Labels ("PO Number", "Bill To", "Total qty"), column
# names, company suffixes and currencies live in po_lexicon.json, so a new supplier
# wording is a data change. A labelled value is read from the same cell, the cell to
# its right or the cell under it, and is kept only if it has the expected shape.
# ---------------------------------------------------------------------------

ROW_TOLERANCE = 3.0        # pt: fragments this close vertically share a visual row
CELL_GAP = 12.0            # pt: horizontal gap that separates two cells of one row
CHAR_WIDTH = 4.6           # pt: approximate glyph advance, used to place unpositioned text
FOOTER_MARGIN = 60.0       # pt: rows this close to the bottom edge are page furniture
AMOUNT_TOLERANCE = 0.02

STRAY_LINE_DISTANCE = 30.0  # pt: a wrapped line placed this far from its own row was mispositioned by pypdf
STRAY_LINE_STEP = 11.0      # pt: where such a line is put back, one text line below its row

LABEL_BELOW_DISTANCE = 30.0    # pt: a value printed under its label sits within this distance
LABEL_COLUMN_TOLERANCE = 25.0  # pt: ...and starts this close to the label's x (or within the label's width)
TERMS_CONTINUATION = 15.0      # pt: a terms value that wraps continues on the next line, this close

NUMBER_WORD = re.compile(r"^\d+(?:[.,]\d+)*$")
IDENTIFIER_WORD = re.compile(r"^(?:\d{8,}|[A-Z]{1,6}\d{3,}(?:-[A-Z0-9]+)*)$")  # EAN or manufacturer code
MANUFACTURER_CODE = re.compile(r"^[A-Z]{1,6}\d{3,}(?:-[A-Z0-9]+)*$")
ITEM_CODE = re.compile(r"^(?=[A-Z0-9-]*\d)[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")  # "MG-50", "PK-100"
DATE_PATTERN = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")
ISO_DATE = re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")
TEXT_DATE_DAY_FIRST = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s./-]+([^\W\d_]{3,10})\.?,?[\s./-]+(\d{4})\b")
TEXT_DATE_MONTH_FIRST = re.compile(r"\b([^\W\d_]{3,10})\.?[\s./-]+(\d{1,2})(?:st|nd|rd|th)?,?[\s./-]+(\d{4})\b")
ITEM_NUMBER = re.compile(r"^(\d{1,3})\s+\S")
SKU_BRACKET = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9-]*)\]")
TAX_LABEL = re.compile(r"\b(?:TVA|VAT|IVA|GST|MwSt|Tax)\s*\d+(?:[.,]\d+)?\s*%", re.IGNORECASE)
PAGE_FURNITURE = re.compile(
    r"^(?:page|pagina|página|seite)\s*:?\s*\d+(?:\s*(?:of|/|de|von|di|sur)\s*\d+)?$", re.IGNORECASE
)
ADDRESS_LINE_GAP = 20.0   # pt: a larger vertical gap ends an address block
ADDRESS_COLUMN = 12.0     # pt: address lines start this close to the label's x
ADDRESS_MAX_LINES = 6

HEADER_FIELDS = (
    "order_number", "order_date", "requested_date", "client", "billing_address", "delivery_address",
    "vendor", "terms", "notes",
)
TOTAL_FIELDS = ("subtotal", "tax", "total", "quantity_total")
PRICE_COLUMNS = {"unit_price", "amount"}


@dataclass
class _Cell:
    x: float
    y: float
    text: str


@dataclass
class _Row:
    y: float
    cells: list[_Cell]

    @property
    def text(self) -> str:
        return " ".join(cell.text for cell in self.cells)


def _base_result(filename: str, text: str) -> dict[str, Any]:
    return {
        "order_number": f"PDF-{date.today():%Y%m%d}",
        "client": "Customer awaiting validation",
        "vendor": "",
        "order_date": date.today().isoformat(),
        "requested_date": None,
        "currency": "USD",
        "delivery_address": "",
        "billing_address": "",
        "terms": "",
        "notes": "",
        "subtotal": 0,
        "tax_amount": 0,
        "total_amount": 0,
        "source_type": "pdf",
        "filename": filename,
        "confidence": 0.25,
        "text": text,
        "items": [],
        "warnings": [],
        "field_sources": {},
    }


def _page_fragments(page: Any) -> list[_Cell]:
    """Text fragments in content-stream order, each with its page position (pt)."""
    fragments: list[_Cell] = []

    def visitor(text, cm, tm, font_dict, font_size):  # pypdf calls this per text operation
        value = " ".join(text.split())
        if not value:
            return
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        fragments.append(_Cell(x=x, y=y, text=value))

    page.extract_text(visitor_text=visitor)
    return fragments


def _is_unplaced(fragment: _Cell) -> bool:
    return abs(fragment.x) < 0.01 and abs(fragment.y) < 0.01


def _restore_wrapped_lines(fragments: list[_Cell]) -> list[_Cell]:
    """Put back wrapped lines that pypdf placed far away from their row.

    Some generators draw a cell's second line ("adhesive" under "Magigoo Pro PC - The 3D printing")
    with a line-feed operator that pypdf mispositions by dozens of points, so it lands inside a later
    table row. Such a line is recognisable in the content stream: the positioned fragments before and
    after it belong to one row, and it alone is far from that row. It is moved just below that row.
    """
    positioned = [index for index, fragment in enumerate(fragments) if not _is_unplaced(fragment)]
    restored = list(fragments)
    for order, index in enumerate(positioned[1:-1], start=1):
        before = fragments[positioned[order - 1]]
        after = fragments[positioned[order + 1]]
        fragment = fragments[index]
        row_y = max(before.y, after.y)
        if abs(before.y - after.y) <= 8 and abs(fragment.y - row_y) > STRAY_LINE_DISTANCE:
            restored[index] = _Cell(fragment.x, min(before.y, after.y) - STRAY_LINE_STEP, fragment.text)
    return restored


def _layout_rows(fragments: list[_Cell]) -> list[_Row]:
    """Group fragments into visual rows (top to bottom) and split each row into cells."""
    placed: list[_Cell] = []
    for fragment in _restore_wrapped_lines(fragments):
        if abs(fragment.x) < 0.01 and abs(fragment.y) < 0.01:
            # pypdf could not place this text; it follows the previous fragment in the stream.
            if not placed:
                continue
            anchor = placed[-1]
            fragment = _Cell(anchor.x + len(anchor.text) * CHAR_WIDTH + 2, anchor.y, fragment.text)
        # Bold text is often drawn twice at almost the same position.
        duplicate = any(
            other.text == fragment.text and abs(other.y - fragment.y) < 1 and abs(other.x - fragment.x) < 1.5
            for other in placed[-40:]
        )
        if not duplicate:
            placed.append(fragment)

    grouped: list[_Row] = []
    for cell in sorted(placed, key=lambda item: (-item.y, item.x)):
        if grouped and abs(grouped[-1].y - cell.y) <= ROW_TOLERANCE:
            grouped[-1].cells.append(cell)
        else:
            grouped.append(_Row(y=cell.y, cells=[cell]))

    rows: list[_Row] = []
    for row in grouped:
        segments: list[_Cell] = []
        for cell in sorted(row.cells, key=lambda item: item.x):
            if segments:
                last = segments[-1]
                if cell.x - (last.x + len(last.text) * CHAR_WIDTH) < CELL_GAP:
                    segments[-1] = _Cell(last.x, row.y, f"{last.text} {cell.text}")
                    continue
            segments.append(_Cell(cell.x, row.y, cell.text))
        rows.append(_Row(y=row.y, cells=segments))
    return rows


def _document_rows(reader: PdfReader) -> list[_Row]:
    rows: list[_Row] = []
    for page in reader.pages:
        rows.extend(row for row in _layout_rows(_page_fragments(page)) if row.y >= FOOTER_MARGIN)
    return rows


def _is_amount_word(word: str) -> bool:
    return bool(NUMBER_WORD.match(word)) and not IDENTIFIER_WORD.match(word)


def _is_size(words: list[str], index: int) -> bool:
    """'250' in '250 ml' is part of the product name, not a quantity or price column."""
    following = normalize_token(words[index + 1]) if index + 1 < len(words) else ""
    return following in lexicon().measure_units


def _column_numbers(words: list[str]) -> list[str]:
    return [word for index, word in enumerate(words) if _is_amount_word(word) and not _is_size(words, index)]


def _to_number(token: str, decimal: str) -> float:
    compact = token.replace(" ", "").replace(" ", "")
    if decimal == ",":
        compact = compact.replace(".", "").replace(",", ".")
    else:
        compact = compact.replace(",", "")
    return float(compact)


def _detect_decimal(words: list[str]) -> str:
    """The document's decimal separator: the last separator of each number, voted over the whole document."""
    votes = {".": 0, ",": 0}
    for word in words:
        if not NUMBER_WORD.match(word):
            continue
        match = re.search(r"([.,])(\d+)$", word)
        if not match or len(match.group(2)) == 3:  # three digits after a separator is a thousands group
            continue
        votes[match.group(1)] += 1
    return "," if votes[","] > votes["."] else "."


def _date_parts(text: str) -> list[tuple[int, int, int]]:
    return [(int(a), int(b), int(c)) for a, b, c in DATE_PATTERN.findall(text)]


def _resolve_day_first(parts: list[tuple[int, int, int]], currency: str) -> tuple[bool, bool]:
    """Return (day_first, ambiguous). Any date with a value above 12 settles the document's format."""
    if any(first > 12 for first, _, _ in parts):
        return True, False
    if any(second > 12 for _, second, _ in parts):
        return False, False
    return currency != "USD", True


def _to_iso(parts: tuple[int, int, int], day_first: bool) -> str | None:
    first, second, year = parts
    day, month = (first, second) if day_first else (second, first)
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _valid_iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _cell_date(text: str) -> tuple[int, int, int] | str | None:
    """The first date near the start of a text.

    ISO ("2026-11-20") and written months ("23 Oct 2026", "October 23, 2026") are unambiguous and come
    back as an ISO string; numeric "11/10/2026" comes back as (first, second, year) for the caller to
    order by the document's format.
    """
    head = text[:40]
    months = lexicon().months
    found: list[tuple[int, tuple[int, int, int] | str]] = []
    for match in ISO_DATE.finditer(head):
        iso = _valid_iso(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if iso:
            found.append((match.start(), iso))
            break
    for match in DATE_PATTERN.finditer(head):
        found.append((match.start(), (int(match.group(1)), int(match.group(2)), int(match.group(3)))))
        break
    for match in TEXT_DATE_DAY_FIRST.finditer(head):
        month = months.get(normalize_token(match.group(2)))
        iso = month and _valid_iso(int(match.group(3)), month, int(match.group(1)))
        if iso:
            found.append((match.start(), iso))
            break
    for match in TEXT_DATE_MONTH_FIRST.finditer(head):
        month = months.get(normalize_token(match.group(1)))
        iso = month and _valid_iso(int(match.group(3)), month, int(match.group(2)))
        if iso:
            found.append((match.start(), iso))
            break
    return min(found, key=lambda item: item[0])[1] if found else None


def _solve_line(values: list[float]) -> tuple[int, float, float, float] | None:
    """Find (quantity, unit price, amount, discount %) among a row's numbers so that quantity x price = amount.

    The search runs from the right, so the amount is the rightmost number that fits. A discount column is
    accepted only when it reproduces the amount exactly.
    """
    for amount_index in range(len(values) - 1, 1, -1):
        amount = values[amount_index]
        for price_index in range(amount_index - 1, 0, -1):
            price = values[price_index]
            for qty_index in range(price_index - 1, -1, -1):
                quantity = values[qty_index]
                if quantity <= 0 or price <= 0 or amount <= 0 or quantity != int(quantity):
                    continue
                gross = quantity * price
                if abs(gross - amount) <= AMOUNT_TOLERANCE:
                    return int(quantity), price, amount, 0.0
                for discount_index in range(qty_index + 1, amount_index):
                    discount = values[discount_index]
                    if discount_index == price_index or not 0 < discount < 100:
                        continue
                    if abs(gross * (1 - discount / 100) - amount) <= AMOUNT_TOLERANCE:
                        return int(quantity), price, amount, discount
    return None


# -- labelled values ---------------------------------------------------------------------------


def _label_value(
    rows: list[_Row], index: int, cell: _Cell, remainder: str, accept: Callable[[str], str | None], below: bool,
) -> tuple[str, str] | None:
    """The value of a label: after it in the same cell, in the next cell of the row, or under it."""
    if remainder:
        value = accept(remainder)
        return (value, "same cell") if value else None
    row = rows[index]
    following = [other for other in row.cells if other.x > cell.x]
    if following:
        value = accept(following[0].text)
        if value:
            return value, "right"
    if not below:
        return None
    width = max(len(cell.text) * CHAR_WIDTH, LABEL_COLUMN_TOLERANCE)
    for other_row in rows[index + 1:index + 4]:
        if not 0 < row.y - other_row.y <= LABEL_BELOW_DISTANCE:
            break
        for other in other_row.cells:
            if cell.x - LABEL_COLUMN_TOLERANCE <= other.x <= cell.x + width:
                value = accept(other.text)
                if value:
                    return value, "below"
    return None


def _find_field(
    rows: list[_Row], fields: Iterable[str], accept: Callable[[str], str | None], *, anywhere: bool = True,
) -> tuple[str, str] | None:
    """(value, how it was found) for the first label of `fields` whose value passes `accept`.

    Labels at the start of a cell are tried first; with `anywhere`, labels inside a sentence or a merged
    cell ("SWEDEN Purchase order 674993") are tried next.
    """
    lex = lexicon()
    fields = tuple(fields)
    for inside in ((False, True) if anywhere else (False,)):
        for index, row in enumerate(rows):
            for cell in row.cells:
                hit = lex.find_label(cell.text, fields, anywhere=inside)
                if not hit:
                    continue
                # A document title ("Purchase Order") is followed by its number, never by what is printed under it.
                below = not inside and not lex.is_title(cell.text)
                found = _label_value(rows, index, cell, hit.remainder, accept, below)
                if found:
                    return found[0], f"label '{hit.synonym}' ({found[1]})"
    return None


def _accept_order_number(text: str) -> str | None:
    words = text.split()
    if not words:
        return None
    token = words[0].strip(":#,;()[]").rstrip(".")
    if not 3 <= len(token) <= 30 or not re.search(r"\d", token):
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_.-]*", token) or _cell_date(token):
        return None
    return token.upper()


def _accept_date(text: str) -> str | None:
    return text if _cell_date(text) else None


def _accept_name(text: str) -> str | None:
    """A company or person name: has a real word, is not an address, contact line or label."""
    lex = lexicon()
    value = re.split(r"\s+\|\s+", text)[0].strip(" -,:")
    if not value or len(value) > 80 or not re.search(r"[^\W\d_]{3,}", value):
        return None
    if "@" in value or re.search(r"\bwww\.|https?:", value, re.IGNORECASE):
        return None
    if lex.is_excluded_client(value) or lex.find_label(value, HEADER_FIELDS):
        return None
    return value


def _accept_client(text: str) -> str | None:
    value = _accept_name(text)
    return value if value and not lexicon().is_own_company(value) else None


def _labeled_block(rows: list[_Row], fields: tuple[str, ...]) -> str:
    """The lines written under (or after) a label such as "Delivery address:", in the label's column."""
    lex = lexicon()
    for index, row in enumerate(rows):
        for cell in row.cells:
            hit = lex.find_label(cell.text, fields)
            if not hit:
                continue
            lines = [hit.remainder] if hit.remainder else []
            previous_y = row.y
            for other_row in rows[index + 1:]:
                if not 0 < previous_y - other_row.y <= ADDRESS_LINE_GAP or len(lines) >= ADDRESS_MAX_LINES:
                    break
                line = next((other.text for other in other_row.cells if abs(other.x - cell.x) <= ADDRESS_COLUMN), None)
                if line is None or line.rstrip().endswith(":"):
                    break
                lines.append(line.strip())
                previous_y = other_row.y
            if lines:
                return "\n".join(lines)
    return ""


def _vendor_block_texts(rows: list[_Row]) -> set[str]:
    """Texts under a "Supplier"/"Vendor" label: they name the seller, never the customer."""
    lex = lexicon()
    texts: set[str] = set()
    for index, row in enumerate(rows):
        for cell in row.cells:
            hit = lex.find_label(cell.text, ("vendor",))
            if not hit:
                continue
            if hit.remainder:  # "Vendor: Other Supplier Ltd"
                texts.update({cell.text, hit.remainder})
                continue
            texts.update(other.text for other in row.cells if other.x > cell.x)  # "Vendor:" | "Other Supplier Ltd"
            previous_y = row.y
            for other_row in rows[index + 1:index + 1 + ADDRESS_MAX_LINES]:
                if not 0 < previous_y - other_row.y <= ADDRESS_LINE_GAP:
                    break
                texts.update(other.text for other in other_row.cells if abs(other.x - cell.x) <= ADDRESS_COLUMN)
                previous_y = other_row.y
    return texts


def _find_client(rows: list[_Row]) -> tuple[str, str]:
    """(client name, how it was found).

    1. A legal-entity name ("... GmbH & Co. KG", "... Nordic AB") that is not ours and not under the
       supplier label: it beats a bare brand, as some sender lines only show "3Dmensionals".
    2. The value of a customer label ("Customer", "Sold To", "Bill To").
    3. The letterhead: a purchase order is issued by the customer, so the first name at the top of the
       document that is not a title, a label, an address or our own company.
    """
    lex = lexicon()
    vendor_texts = _vendor_block_texts(rows)
    for row in rows:
        for cell in row.cells:
            if cell.text in vendor_texts:
                continue
            for segment in re.split(r"\s+\|\s+", cell.text):
                segment = segment.strip(" -,")
                if segment in vendor_texts:
                    continue
                if lex.has_company_suffix(segment) and _accept_client(segment):
                    return segment, "company name"
    found = _find_field(rows, ("client", "billing_address"), _accept_client, anywhere=False)
    if found:
        return found
    for row in rows[:8]:
        for cell in row.cells:
            for segment in re.split(r"\s+\|\s+", cell.text):
                segment = segment.strip(" -,")
                if not segment or segment[0].isdigit() or lex.is_title(segment) or segment in vendor_texts:
                    continue
                name = _accept_client(segment)
                if name:
                    return name, "letterhead"
    return "", ""


def _find_vendor(rows: list[_Row], text: str) -> tuple[str, str]:
    lex = lexicon()
    if lex.is_own_company(text):
        return lex.own_company_name, "own company"
    found = _find_field(rows, ("vendor",), _accept_name, anywhere=False)
    return found if found else ("", "")


def _find_terms(rows: list[_Row]) -> str:
    lex = lexicon()
    for index, row in enumerate(rows):
        for cell in row.cells:
            hit = lex.find_label(cell.text, ("terms",))
            if not hit:
                continue
            value = [hit.remainder] if hit.remainder else []
            if not value:
                value = [other.text for other in row.cells if other.x > cell.x][:1]
            if index + 1 < len(rows) and 0 < row.y - rows[index + 1].y <= TERMS_CONTINUATION:
                for other in rows[index + 1].cells:
                    # Continuation words sit to the right of the label, not under the left-hand address block.
                    if cell.x - 2 <= other.x <= cell.x + 220 and not re.match(r"^(?:ein|vat|tva|tel)\b", other.text, re.IGNORECASE):
                        value.append(other.text)
            return " ".join(part for part in value if part)
    return ""


def _find_notes(rows: list[_Row]) -> str:
    lex = lexicon()
    for index, row in enumerate(rows):
        hit = lex.find_label(row.cells[0].text, ("notes",)) if row.cells else None
        if not hit:
            continue
        rest = " ".join([hit.remainder] + [cell.text for cell in row.cells[1:]]).strip()
        if rest:
            return rest
        if index + 1 < len(rows):
            return rows[index + 1].text.strip()
    return ""


def _currency(preferred: str, text: str) -> str | None:
    """The currency named most often, first among the totals and then in the whole document."""
    currencies = lexicon().currencies
    for zone in (preferred, text):
        counts: dict[str, int] = {}
        for code, markers in currencies.items():
            count = 0
            for marker in markers:
                if marker.isalpha():
                    count += len(re.findall(rf"(?<![A-Za-z]){re.escape(marker)}(?![A-Za-z])", zone))
                else:
                    count += zone.count(marker)
            counts[code] = count
        best = max(counts, key=lambda code: counts[code])  # ties keep the order of po_lexicon.json
        if counts[best]:
            return best
    return None


# -- the item table ----------------------------------------------------------------------------


def _column_fields(row: _Row) -> set[str]:
    """Column names in a row; a row with figures is data, never a header."""
    if any(_is_amount_word(word) for word in row.text.split()):
        return set()
    return lexicon().column_fields(row.text)


def _is_header(row: _Row) -> bool:
    return len(_column_fields(row)) >= 2


def _is_item_start(row: _Row) -> bool:
    text = row.text
    if SKU_BRACKET.match(text):
        return True
    if not ITEM_NUMBER.match(text):
        return False
    return len([word for word in text.split()[1:] if _is_amount_word(word)]) >= 2


def _is_table_item_start(row: _Row, quantity_only: bool) -> bool:
    """Inside a table, a line may also start with its product code ("MG-50  50 ml  60  9.50  570.00")."""
    if _is_item_start(row):
        return True
    words = row.text.split()
    if not words or not (MANUFACTURER_CODE.match(words[0]) or ITEM_CODE.match(words[0])):
        return False
    return len(_column_numbers(words[1:])) >= (1 if quantity_only else 2)


def _is_quantity_only(fields: set[str]) -> bool:
    """A table whose header has a quantity column but no price or amount column (a price-less PO)."""
    return "quantity" in fields and not fields & PRICE_COLUMNS


def _table_header_index(rows: list[_Row]) -> int | None:
    """The first column-header row that names a quantity column and is followed by an item row."""
    for index, row in enumerate(rows):
        fields = _column_fields(row)
        if "quantity" in fields and len(fields) >= 2:
            quantity_only = _is_quantity_only(fields)
            if any(_is_table_item_start(later, quantity_only) for later in rows[index + 1:]):
                return index
    return None


def _is_table_end(row: _Row) -> bool:
    """A totals line with a figure ("Sub Total 975.50", "Total qty 1.955,00") or a notes/terms label.

    Only the first cell counts, so "TVA 20%" inside a line, or a description that starts with
    "Total", never ends the table.
    """
    if not row.cells:
        return False
    lex = lexicon()
    first = row.cells[0].text
    hit = lex.find_label(first, TOTAL_FIELDS)
    if hit:
        rest = " ".join([hit.remainder] + [cell.text for cell in row.cells[1:]])
        return _amount_on_line(rest, ".") is not None
    return lex.find_label(first, ("notes", "terms")) is not None


def _item_blocks(rows: list[_Row], start: int = 0, quantity_only: bool = False) -> tuple[list[list[_Row]], int, int]:
    """Return (item blocks, index of the first item row, index where the table ends).

    Scanning starts at ``start`` (the row after the table header when there is one), so an address
    such as "211 24 MALMÖ ... 674993" above the table is never read as an order line.
    """
    in_table = start > 0
    blocks: list[list[_Row]] = []
    first_index = len(rows)
    end_index = len(rows)
    for index in range(start, len(rows)):
        row = rows[index]
        if PAGE_FURNITURE.match(row.text.strip()):
            continue  # "Page 1 of 2" between the lines of a multi-page table
        if _is_table_item_start(row, quantity_only) if in_table else _is_item_start(row):
            if not blocks:
                first_index = index
            blocks.append([row])
            continue
        if blocks and _is_table_end(row):
            end_index = index
            break
        if blocks and not _is_header(row):
            blocks[-1].append(row)
    return blocks, first_index, end_index


def _item_title(block: list[_Row]) -> str:
    first_words = block[0].text.split()
    if ITEM_NUMBER.match(block[0].text):
        first_words = first_words[1:]
    words: list[str] = []
    for position, word in enumerate(first_words):
        token = normalize_token(word)
        tax_word = token in {"tva", "vat", "iva", "gst", "mwst"}
        skippable = IDENTIFIER_WORD.match(word) or SKU_BRACKET.fullmatch(word) or tax_word
        if _is_amount_word(word) and _is_size(first_words, position):
            words.append(word)  # "250 ml" belongs to the description, even as its first word
            continue
        if not words and (skippable or _is_amount_word(word) or ITEM_CODE.match(word)):
            continue  # internal item IDs, EAN and codes printed before the description
        if _is_amount_word(word) or DATE_PATTERN.search(word) or ":" in word or "%" in word or word in {"€", "$", "£"}:
            break  # the numeric columns start here
        if skippable:
            continue
        words.append(word)
    title = " ".join(words)
    if not title and len(block) > 1:
        title = block[1].text
    elif title:
        # A wrapped description continues on the next row in lower case ("... The 3D printing" / "adhesive").
        for row in block[1:]:
            text = row.text.strip()
            if len(row.cells) != 1 or not text[:1].islower() or re.search(r"\d", text):
                break
            title = f"{title} {text}"
    if title.count("(") > title.count(")") and len(block) > 1 and ")" in block[1].text:
        title = f"{title} {block[1].text.split(')')[0]})"  # keep "(Printing Adhesive)" whole
    return title.strip()


def _item_code(first_row: _Row, numbered: bool) -> str:
    """The SKU in brackets, else the first manufacturer code on the line ("MPC2018", "MGK2025-SG"),
    else a hyphenated code that opens the line ("MG-50")."""
    bracket = SKU_BRACKET.search(first_row.text)
    if bracket:
        return bracket.group(1).upper()
    words = first_row.text.split()[1:] if numbered else first_row.text.split()
    code = next((word for word in words if MANUFACTURER_CODE.match(word)), "")
    if not code and words and ITEM_CODE.match(words[0]):
        code = words[0]
    return code


def _parse_quantity_only_item(block: list[_Row], line_number: int) -> tuple[dict[str, Any] | None, list[str]]:
    """A line of a price-less PO: the quantity is the last whole number of the line's first row."""
    words = block[0].text.split()[1:] if ITEM_NUMBER.match(block[0].text) else block[0].text.split()
    quantities = _column_numbers(words)
    quantity = None
    if quantities:
        token = re.sub(r"[.,]0+$", "", quantities[-1])  # "150,00" is 150
        if token.isdigit() and int(token) > 0:
            quantity = int(token)
    if quantity is None:
        return None, [f"Line {line_number}: no quantity could be read, so the line was skipped."]
    return {
        "line_number": line_number,
        "sku_raw": _item_code(block[0], ITEM_NUMBER.match(block[0].text) is not None),
        "description": _item_title(block),
        "quantity": quantity,
        "unit_price": 0.0,
        "requested_date": None,
        "tax_label": "",
        "discount_percent": 0.0,
        "amount": 0.0,
    }, []


def _parse_item(block: list[_Row], decimal: str, day_first: bool, line_number: int) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    numbered = ITEM_NUMBER.match(block[0].text) is not None
    values: list[str] = []
    for row_index, row in enumerate(block):
        words = row.text.split()
        if row_index == 0 and numbered:
            words = words[1:]  # the printed line number is not an amount
        values.extend(_column_numbers(words))
    numbers = [_to_number(word, decimal) for word in values]

    solved = _solve_line(numbers)
    if solved:
        quantity, unit_price, amount, discount = solved
    else:
        positive = [int(number) for number in numbers if number > 0 and number == int(number)]
        if not positive:
            return None, [f"Line {line_number}: no quantity could be read, so the line was skipped."]
        quantity, unit_price, amount, discount = positive[0], 0.0, 0.0, 0.0
        warnings.append(f"Line {line_number}: quantity x price does not match the amount. Check quantity and price.")

    tax = TAX_LABEL.search(" ".join(row.text for row in block))
    requested = None
    date_match = DATE_PATTERN.search(" ".join(row.text for row in block))
    if date_match:
        requested = _to_iso(tuple(int(part) for part in date_match.groups()), day_first)

    item = {
        "line_number": line_number,
        "sku_raw": _item_code(block[0], numbered),
        "description": _item_title(block),
        "quantity": quantity,
        "unit_price": unit_price,
        "requested_date": requested,
        "tax_label": tax.group(0) if tax else "",
        "discount_percent": discount,
        "amount": amount,
    }
    return item, warnings


def _amount_on_line(text: str, decimal: str) -> float | None:
    cleaned = re.sub(r"\d+(?:[.,]\d+)?\s*%", " ", text)  # "TVA 20%" is a rate, not an amount
    cleaned = re.sub(r"[€$£]", " ", cleaned)
    words = cleaned.split()
    found = None
    for index, word in enumerate(words):
        if not _is_amount_word(word):
            continue
        token = word
        if index and re.fullmatch(r"\d{1,3}", words[index - 1]) and re.fullmatch(r"\d{3}[.,]\d{2}", word):
            token = f"{words[index - 1]}{word}"  # "1 150,00" is one amount
        found = token
    return _to_number(found, decimal) if found else None


def _totals(rows: list[_Row], decimal: str) -> dict[str, float]:
    """The first subtotal, tax, total and quantity total, each read from a line that starts with its label."""
    lex = lexicon()
    found: dict[str, float] = {}
    for row in rows:
        if not row.cells:
            continue
        hit = lex.find_label(row.cells[0].text, TOTAL_FIELDS)
        if not hit or hit.field in found:
            continue
        rest = " ".join([hit.remainder] + [cell.text for cell in row.cells[1:]])
        value = _amount_on_line(rest, decimal)
        if value is not None:
            found[hit.field] = value
    return found


def extract_pdf(content: bytes, filename: str) -> dict[str, Any]:
    reader = PdfReader(io.BytesIO(content))
    rows = _document_rows(reader)
    text = "\n".join(row.text for row in rows)
    result = _base_result(filename, text)
    sources: dict[str, str] = result["field_sources"]
    warnings: list[str] = []

    header_index = _table_header_index(rows)
    quantity_only = header_index is not None and _is_quantity_only(_column_fields(rows[header_index]))
    blocks, first_index, end_index = _item_blocks(
        rows, header_index + 1 if header_index is not None else 0, quantity_only
    )
    # The header zone (order number, dates, client) ends at the table header, or at the first line.
    before = rows[:header_index] if header_index is not None else rows[:first_index]
    after = rows[end_index:] if blocks else rows

    decimal = _detect_decimal(text.split())
    currency = _currency("\n".join(row.text for row in after), text)
    if currency is None:
        if not quantity_only:
            warnings.append("Currency symbol not found; USD was used. Check the currency.")
        currency = "USD"
    else:
        sources["currency"] = "currency marker"
    dates = _date_parts(text)
    day_first, ambiguous_format = _resolve_day_first(dates, currency)

    items: list[dict[str, Any]] = []
    for block in blocks:
        if quantity_only:
            item, item_warnings = _parse_quantity_only_item(block, len(items) + 1)
        else:
            item, item_warnings = _parse_item(block, decimal, day_first, len(items) + 1)
        warnings.extend(item_warnings)
        if item:
            items.append(item)

    found = _find_field(before, ("order_number",), _accept_order_number) or _find_field(rows, ("order_number",), _accept_order_number)
    if found:
        result["order_number"], sources["order_number"] = found
    else:
        warnings.append("Order number not found; a temporary number was assigned.")

    found = _find_field(before, ("order_date",), _accept_date) or _find_field(rows, ("order_date",), _accept_date)
    order_parts = _cell_date(found[0]) if found else None
    if isinstance(order_parts, str):
        result["order_date"] = order_parts  # ISO and written-month dates are unambiguous
        sources["order_date"] = found[1]
    elif order_parts:
        order_date = _to_iso(order_parts, day_first)
        if order_date:
            result["order_date"] = order_date
            sources["order_date"] = found[1]
            if ambiguous_format and order_parts[0] <= 12 and order_parts[1] <= 12:
                warnings.append(
                    f"Order date {order_parts[0]:02d}/{order_parts[1]:02d}/{order_parts[2]} is ambiguous; "
                    f"read as {result['order_date']}. Verify it."
                )
    else:
        warnings.append("Order date not found; today's date was used.")

    client, how = _find_client(before)
    if client:
        result["client"], sources["client"] = client, how
    else:
        warnings.append("Customer name not found; review the client before calculating.")

    vendor, how = _find_vendor(before or rows, text)
    if vendor:
        result["vendor"], sources["vendor"] = vendor, how

    requested_dates = [item["requested_date"] for item in items if item["requested_date"]]
    if requested_dates:
        result["requested_date"] = min(requested_dates)
        sources["requested_date"] = "item lines"
    else:
        found = _find_field(before, ("requested_date",), _accept_date, anywhere=False)
        parts = _cell_date(found[0]) if found else None
        requested = parts if isinstance(parts, str) else _to_iso(parts, day_first) if parts else None
        if requested:
            result["requested_date"], sources["requested_date"] = requested, found[1]

    totals = _totals(after, decimal)
    subtotal, tax, total = totals.get("subtotal"), totals.get("tax"), totals.get("total")
    total_quantity = totals.get("quantity_total")

    quantity_sum = sum(item["quantity"] for item in items)
    if items and total_quantity is not None and abs(quantity_sum - total_quantity) > 0.01:
        warnings.append(
            f"The line quantities add up to {quantity_sum}, but the document total quantity is "
            f"{total_quantity:g}. Check the quantities."
        )

    if quantity_only and subtotal is None and total is None:
        # The document carries quantities only; there is no amount to reconcile.
        subtotal = tax = total = 0.0
        if items:
            warnings.append("This purchase order has no prices; only quantities were read.")
    else:
        line_sum = round(sum(item["amount"] for item in items), 2)
        if subtotal is None:
            subtotal = line_sum
            if items:
                warnings.append("Subtotal not found; the sum of the lines was used.")
        elif items and abs(line_sum - subtotal) > 0.05:
            warnings.append(
                f"The lines add up to {line_sum:.2f}, but the document subtotal is {subtotal:.2f}. "
                "Check the quantities and amounts."
            )
        tax = tax or 0.0
        total = total if total is not None else round(subtotal + tax, 2)
        if abs(subtotal + tax - total) > 0.05:
            warnings.append(f"Subtotal plus tax ({subtotal + tax:.2f}) does not match the total ({total:.2f}).")

    if not items:
        warnings.insert(0, "No product lines were found in this PDF. Check the document or add the lines manually.")

    result.update(
        {
            "terms": _find_terms(before),
            "notes": _find_notes(after) or _find_notes(rows),
            "delivery_address": _labeled_block(rows, ("delivery_address",)),
            "billing_address": _labeled_block(rows, ("billing_address",)),
            "currency": currency,
            "subtotal": subtotal,
            "tax_amount": tax,
            "total_amount": total,
            "items": items,
            "warnings": warnings,
        }
    )
    confidence = 0.95 if items else 0.2
    result["confidence"] = round(max(0.2, min(0.95, confidence - 0.1 * len(warnings))), 2)
    return result


def _header_map(headers: list[Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, raw in enumerate(headers):
        normalized = _normalize_header(raw)
        for canonical, aliases in HEADER_ALIASES.items():
            if normalized in aliases:
                result[canonical] = index
    return result


def parse_catalog_excel(content: bytes, catalog_type: str) -> tuple[list[dict[str, Any]], list[str]]:
    workbook = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return [], ["The file contains no rows."]
    columns = _header_map(list(rows[0]))
    required = (
        ["name", "length_cm", "width_cm", "height_cm", "weight_kg"]
        if catalog_type == "products"
        else ["code", "name", "length_cm", "width_cm", "height_cm", "empty_weight_kg", "max_weight_kg"]
    )
    missing = [field for field in required if field not in columns]
    if missing:
        return [], [f"Missing required columns: {', '.join(missing)}"]

    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for row_number, row in enumerate(rows[1:], start=2):
        if not any(value is not None for value in row):
            continue
        try:
            record = {field: row[columns[field]] for field in required}
            for field in required:
                if field not in {"code", "name"}:
                    record[field] = float(record[field])
            records.append(record)
        except (TypeError, ValueError, IndexError) as exc:
            errors.append(f"Row {row_number}: invalid data ({exc}).")
    return records, errors


CLIENT_FIELDS = (
    "customer", "phone_number", "email", "contact_name",
    "address", "country", "shipping_address",
)

_EXPORT_TIMESTAMP = re.compile(
    r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+.*\d{4}.*\d{1,2}:\d{2}", re.IGNORECASE,
)


def _is_export_timestamp_row(record: dict[str, str]) -> bool:
    """A footer like "Monday, 16 November 2026 09:15 AM GMT+01:00" with no other client data."""
    others = any(value for field, value in record.items() if field != "customer")
    return not others and bool(_EXPORT_TIMESTAMP.match(record.get("customer", "")))


_CLIENT_HEADER_ALIASES = {
    "customer": {"customer", "client", "cliente", "customer name", "company"},
    "phone_number": {"phone number", "phone", "telephone", "telefono", "tel"},
    "email": {"email", "e-mail", "email address", "correo"},
    "contact_name": {"contact name", "contact", "contacto"},
    "address": {"address", "direccion", "billing address", "bill address", "bill to"},
    "country": {"country", "pais"},
    "shipping_address": {"shipping address", "ship address", "ship to", "delivery address", "direccion de envio"},
}


def _normalize_header(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[\s_]+", " ", text).strip().lower()


MULTILINE_CLIENT_FIELDS = {"address", "shipping_address"}
CLIENT_HEADER_SEARCH_ROWS = 30


def _clean_cell(value: Any, *, multiline: bool = False) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    if not multiline:
        return re.sub(r"\s+", " ", text).strip()
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def _client_header_columns(row: tuple[Any, ...]) -> dict[str, int]:
    columns: dict[str, int] = {}
    for index, header in enumerate(row):
        normalized = _normalize_header(header)
        for field, aliases in _CLIENT_HEADER_ALIASES.items():
            if field not in columns and normalized in aliases:
                columns[field] = index
    return columns


def parse_clients_excel(content: bytes) -> tuple[list[dict[str, str]], list[str]]:
    """Read client rows from the first sheet.

    Exported customer lists often start with a title block, so the header row is
    located within the first rows (in any column) instead of assumed to be row 1.
    """
    try:
        workbook = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:  # openpyxl raises several types for corrupt files
        return [], [f"The Excel file could not be read ({exc})."]
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return [], ["The file contains no rows."]
    header_index = None
    columns: dict[str, int] = {}
    for index, row in enumerate(rows[:CLIENT_HEADER_SEARCH_ROWS]):
        candidate = _client_header_columns(row)
        # Require Customer plus one more known column so a title cell is never mistaken for the header.
        if "customer" in candidate and len(candidate) >= 2:
            header_index, columns = index, candidate
            break
    if header_index is None:
        return [], ["Missing required column: Customer. Add a header row with Customer, Phone Number, Email, Contact Name, Address, Country and Shipping Address."]

    records: list[dict[str, str]] = []
    errors: list[str] = []
    for row_number, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        record = {
            field: _clean_cell(row[columns[field]], multiline=field in MULTILINE_CLIENT_FIELDS)
            if field in columns and columns[field] < len(row) else ""
            for field in CLIENT_FIELDS
        }
        # Skip blank rows and footers (e.g. an export timestamp), even when the footer sits in the Customer column.
        if not any(record.values()) or _is_export_timestamp_row(record):
            continue
        if not record["customer"]:
            errors.append(f"Row {row_number}: Customer is empty.")
            continue
        records.append(record)
    return records, errors


def _reference_product_dimensions(name: str) -> tuple[float, float, float]:
    normalized = re.sub(r"\s+", " ", name.lower()).strip()
    explicit = re.search(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*cm", normalized)
    if explicit:
        return tuple(float(explicit.group(index)) for index in range(1, 4))
    if "glide bottle 250" in normalized:
        return 6.0, 6.0, 20.0
    if "glide roller" in normalized:
        return 20.0, 5.0, 5.0
    if "glide kit" in normalized:
        return 20.0, 15.0, 10.0
    if "prokit" in normalized:
        return 20.0, 15.0, 6.0
    if "120 ml" in normalized:
        return 5.0, 5.0, 16.0
    if "50 ml" in normalized:
        return 4.0, 4.0, 12.0
    if "10 ml" in normalized:
        return 2.5, 2.5, 8.0
    if "sachet" in normalized:
        return 10.0, 7.0, 0.5
    return 10.0, 10.0, 10.0


def _parse_mm_dimensions(value: Any) -> tuple[float, float, float] | None:
    numbers = re.findall(r"\d+(?:\.\d+)?", str(value or ""))
    if len(numbers) < 3:
        return None
    return tuple(round(float(number) / 10, 3) for number in numbers[:3])


# Box maximum weight for workbook imports; the sheet has no column for it. The DHL
# calculator reference app uses 20 kg per parcel.
DEFAULT_BOX_MAX_WEIGHT_KG = 20.0

# "what fits" column headers -> capacity family. A product joins a family by name.
CAPACITY_COLUMNS = {
    "50ml": "50ml", "120ml": "120ml", "10ml": "10ml", "250ml": "250ml", "prokits": "prokits",
    "glide250ml": "glide_250ml", "glidekit": "glide_kit",
}


# Packed size of each workbook family (cm, any order), from the reference products.
CAPACITY_FAMILY_SIZES = {
    (4.0, 4.0, 12.0): "50ml",
    (5.0, 5.0, 16.0): "120ml",
    (2.5, 2.5, 8.0): "10ml",
    (6.0, 6.0, 20.0): "glide_250ml",
    (6.0, 15.0, 20.0): "prokits",
    (10.0, 15.0, 20.0): "glide_kit",
}


def capacity_family_for(name: str, dims: tuple[float, float, float] | None = None) -> str:
    """Capacity family of a catalog product (variants share one pool).

    By name first ("50 ml", "Glide 250ml", "Pro Kit", "Glide kit"); otherwise a product
    whose packed size equals a family's reference size joins that family, so variants
    named only by type ("Magigoo PA", "Magigoo HT") share the 50 ml pool.
    """
    normalized = re.sub(r"\s+", " ", name.lower()).strip()
    if "glide" in normalized and "250" in normalized:
        return "glide_250ml"
    if "glide kit" in normalized:
        return "glide_kit"
    if re.search(r"pro\s*kit", normalized):
        return "prokits"
    for size in ("120", "50", "10"):
        if re.search(rf"\b{size}\s*ml\b", normalized):
            return f"{size}ml"
    if dims and all(isinstance(side, (int, float)) for side in dims):
        return CAPACITY_FAMILY_SIZES.get(tuple(sorted(round(float(side), 2) for side in dims)), "")
    return ""


def _capacity_value(value: Any) -> float | None:
    """Numeric capacity; "67 / 75" keeps the lower value, text such as "8x50 (400)" is skipped."""
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*", str(value or ""))
    if match:
        return min(float(match.group(1)), float(match.group(2)))
    return None


def parse_reference_workbook(content: bytes) -> dict[str, list[dict[str, Any]]] | None:
    """Read the operational volume/weight calculator supplied by the user.

    The workbook is intentionally not a flat import template: product weights live in
    columns A:B of one sheet and shipping boxes live in a matrix on another sheet, whose
    columns D onwards say how many units of each product family a box holds.
    """
    workbook = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    required_sheets = {"boxes bottles volumes", "box types-what fits"}
    if not required_sheets.issubset(set(workbook.sheetnames)):
        return None

    products_sheet = workbook["boxes bottles volumes"]
    products: list[dict[str, Any]] = []
    for row in products_sheet.iter_rows(min_row=3, max_row=20, min_col=1, max_col=2, values_only=True):
        name, weight = row
        if not name or not isinstance(weight, (int, float)) or weight <= 0:
            continue
        length, width, height = _reference_product_dimensions(str(name))
        products.append(
            {
                "name": str(name).strip(),
                "length_cm": length,
                "width_cm": width,
                "height_cm": height,
                "weight_kg": float(weight),
                "capacity_family": capacity_family_for(str(name), (length, width, height)),
            }
        )

    boxes_sheet = workbook["box types-what fits"]
    header = next(boxes_sheet.iter_rows(min_row=2, max_row=2, values_only=True), ())
    families = {
        index: CAPACITY_COLUMNS[key]
        for index, title in enumerate(header)
        if index >= 3 and (key := re.sub(r"[^a-z0-9]", "", str(title or "").lower())) in CAPACITY_COLUMNS
    }
    boxes: list[dict[str, Any]] = []
    for row in boxes_sheet.iter_rows(min_row=3, max_row=15, values_only=True):
        name, raw_dimensions, weight_grams = (list(row) + [None] * 3)[:3]
        dimensions = _parse_mm_dimensions(raw_dimensions)
        if not name or not dimensions or not isinstance(weight_grams, (int, float)):
            continue
        length, width, height = dimensions
        capacities = {
            family: capacity
            for index, family in families.items()
            if index < len(row) and (capacity := _capacity_value(row[index])) is not None
        }
        boxes.append(
            {
                "code": re.sub(r"[^A-Z0-9]+", "-", str(name).upper()).strip("-"),
                "name": str(name).strip(),
                "length_cm": length,
                "width_cm": width,
                "height_cm": height,
                "empty_weight_kg": round(float(weight_grams) / 1000, 4),
                "max_weight_kg": DEFAULT_BOX_MAX_WEIGHT_KG,
                "capacities": capacities,
            }
        )
    return {"products": products, "boxes": boxes}



def extract_email(text: str) -> dict[str, Any]:
    """Conservative line-based parser for pasted email bodies (no external service).

    Explicit quantity markers avoid treating 120ml, dates or prices as quantities.
    Stop before signatures and quoted email history to avoid duplicate orders.
    """
    items = []
    warnings = []
    client = "Customer awaiting validation"
    lines = text.splitlines()
    unit = r"(?:pcs\.?|pieces?|units?|unidades?|uds\.?|piezas?)"
    patterns = [
        re.compile(rf"^(?P<description>.+?)\s*(?:[–—:;\-]\s*|\s+)(?P<quantity>\d+)\s*{unit}\s*[.;]?$", re.I),
        re.compile(rf"^(?P<quantity>\d+)\s*(?:{unit}\s*(?:of\s+|de\s+)?|[x×]\s*)(?P<description>.+?)\s*$", re.I),
        re.compile(r"^(?P<description>.+?)\s+[–—:;\-]\s*(?P<quantity>\d+)\s*$", re.I),
        re.compile(r"^(?P<description>.+?)\s+[x×]\s*(?P<quantity>\d+)\s*$", re.I),
    ]
    for index, raw in enumerate(lines):
        line = re.sub(r"^\s*(?:[•*\-]\s+|\d+[.)]\s+)?", "", raw).strip()
        if re.match(r"^(?:>|On .+wrote:|El .+escribió:|[-]+\s*(?:Original|Forwarded) message)", line, re.I):
            break
        if re.match(r"^(?:best regards|kind regards|regards|saludos|atentamente)[,!.:\s]*$", line, re.I):
            following = next((v.strip() for v in lines[index + 1:] if v.strip()), "")
            if following and len(following) <= 120 and not following.startswith(">"):
                client = following
            break
        match = next((m for pattern in patterns if (m := pattern.fullmatch(line))), None)
        if match:
            quantity = int(match["quantity"])
            description = match["description"].strip(" \t-–—:;•")
            if quantity <= 0 or not re.search(r"[^\W\d_]", description):
                warnings.append(f"Line {index + 1}: invalid product or quantity.")
                continue
            items.append({"description": description, "quantity": quantity,
                          "unit_price": 0, "line_number": len(items) + 1})
        elif re.search(rf"\d+\s*{unit}\b", line, re.I):
            warnings.append(f"Line {index + 1}: review the product and quantity manually.")
    return {"client": client, "items": items, "warnings": warnings,
            "confidence": 0.8 if items and not warnings else 0.4}
