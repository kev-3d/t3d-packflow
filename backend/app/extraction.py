from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any

from openpyxl import load_workbook
from pypdf import PdfReader


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
# ---------------------------------------------------------------------------

ROW_TOLERANCE = 3.0        # pt: fragments this close vertically share a visual row
CELL_GAP = 12.0            # pt: horizontal gap that separates two cells of one row
CHAR_WIDTH = 4.6           # pt: approximate glyph advance, used to place unpositioned text
FOOTER_MARGIN = 60.0       # pt: rows this close to the bottom edge are page furniture
AMOUNT_TOLERANCE = 0.02

NUMBER_WORD = re.compile(r"^\d+(?:[.,]\d+)*$")
IDENTIFIER_WORD = re.compile(r"^(?:\d{8,}|[A-Z]{1,6}\d{3,})$")  # EAN or manufacturer code
DATE_PATTERN = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")
ITEM_NUMBER = re.compile(r"^(\d{1,3})\s+\S")
SKU_BRACKET = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9-]*)\]")
TAX_LABEL = re.compile(r"\b(?:TVA|VAT)\s*\d+(?:[.,]\d+)?\s*%", re.IGNORECASE)
TOTALS_START = re.compile(
    r"^(?:sub\s*total|subtotal|total\s+net|net\s+total|montant\s+ht|total\s+ht|total\b|notes?\b|terms?\s*:|page\b)",
    re.IGNORECASE,
)
SUBTOTAL_LINE = re.compile(r"^(?:sub\s*total|subtotal|total\s+net|net\s+total|montant\s+ht|total\s+ht)\b", re.IGNORECASE)
TAX_LINE = re.compile(r"^(?:tva|vat|tax|taxes)\b", re.IGNORECASE)
TOTAL_LINE = re.compile(r"^total\b(?!\s+(?:net|ht)\b)", re.IGNORECASE)
DATE_LABEL = re.compile(r"^(?:order\s+)?date\b(?!\s+de\s+la\s+demande)", re.IGNORECASE)
ORDER_NUMBER_PATTERNS = (
    re.compile(
        r"\b(?:order\s*(?:no|number|nr)\.?|bon de commande|purchase order|commande|po\s*(?:#|no\.?))\s*(?:#|n[°º]\.?)?\s*:?\s*#?\s*"
        r"((?:[A-Za-z]{1,6}-?)?\d[\w-]*)",
        re.IGNORECASE,
    ),
    re.compile(r"#\s*((?:[A-Za-z]{1,6}-?)?\d[\w-]*)"),
)
# Company names are case-sensitive on purpose: "kg" or "ag..." must not look like a legal suffix.
COMPANY_SUFFIX = re.compile(r"\b(?:GmbH|Ltd|Inc|LLC|SAS|SARL|KG|Corp|Limited|Technologies|Labs)\b")
CLIENT_SUFFIX = re.compile(r"\b(?:GmbH|Ltd|Inc|LLC|SAS|SARL|KG|Corp|Limited|Technologies|Labs)\b|\b3D")
CLIENT_EXCLUDE = re.compile(
    r"thought3d|address|adresse|invoice|delivery|deliver|expédition|vendor|buyer|contact|"
    r"purchase order|bon de commande|terms|payment|\bein\b|\bvat\b|\btva\b|\bdate\b",
    re.IGNORECASE,
)
HEADER_WORDS = {
    "description", "désignation", "designation", "item", "qty", "quantité", "qté", "quantite",
    "price", "rate", "prix", "unitaire", "amount", "montant", "total", "rem", "taxes", "tax",
    "manufacturer", "ean", "pos", "unit", "date", "demande", "purchase", "no",
}


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


def _layout_rows(fragments: list[_Cell]) -> list[_Row]:
    """Group fragments into visual rows (top to bottom) and split each row into cells."""
    placed: list[_Cell] = []
    for fragment in fragments:
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


def _to_number(token: str, decimal: str) -> float:
    compact = token.replace(" ", "").replace(" ", "")
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


def _is_item_start(row: _Row) -> bool:
    text = row.text
    if SKU_BRACKET.match(text):
        return True
    if not ITEM_NUMBER.match(text):
        return False
    return len([word for word in text.split()[1:] if _is_amount_word(word)]) >= 2


def _is_header(row: _Row) -> bool:
    words = row.text.split()
    labels = {word.lower().strip(".:()#") for word in words}
    return len(labels & HEADER_WORDS) >= 2 and not any(_is_amount_word(word) for word in words)


def _item_blocks(rows: list[_Row]) -> tuple[list[list[_Row]], int, int]:
    """Return (item blocks, index of the first item row, index where the table ends)."""
    blocks: list[list[_Row]] = []
    first_index = len(rows)
    end_index = len(rows)
    for index, row in enumerate(rows):
        if _is_item_start(row):
            if not blocks:
                first_index = index
            blocks.append([row])
            continue
        if blocks and TOTALS_START.match(row.text):
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
    for word in first_words:
        if _is_amount_word(word) or DATE_PATTERN.search(word) or ":" in word or "%" in word:
            break  # the numeric columns start here
        if IDENTIFIER_WORD.match(word) or SKU_BRACKET.fullmatch(word) or word.lower() in {"tva", "vat"}:
            continue
        words.append(word)
    title = " ".join(words)
    if not title and len(block) > 1:
        title = block[1].text
    if title.count("(") > title.count(")") and len(block) > 1 and ")" in block[1].text:
        title = f"{title} {block[1].text.split(')')[0]})"  # keep "(Printing Adhesive)" whole
    return title.strip()


def _parse_item(block: list[_Row], decimal: str, day_first: bool, line_number: int) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    numbered = ITEM_NUMBER.match(block[0].text) is not None
    sku = SKU_BRACKET.search(block[0].text)
    values: list[str] = []
    for row_index, row in enumerate(block):
        words = row.text.split()
        if row_index == 0 and numbered:
            words = words[1:]  # the printed line number is not an amount
        values.extend(word for word in words if _is_amount_word(word))
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
        "sku_raw": sku.group(1).upper() if sku else "",
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


def _labeled_date(rows: list[_Row]) -> tuple[int, int, int] | None:
    for index, row in enumerate(rows):
        if not DATE_LABEL.match(row.text):
            continue
        candidates = [row]
        if index + 1 < len(rows):
            candidates.append(rows[index + 1])
        if index:
            candidates.append(rows[index - 1])
        for candidate in candidates:
            parts = _date_parts(candidate.text)
            if parts:
                return parts[0]
    return None


def _find_order_number(rows: list[_Row]) -> str | None:
    for pattern in ORDER_NUMBER_PATTERNS:
        for row in rows:
            match = pattern.search(row.text)
            if match and re.search(r"\d", match.group(1)):
                return match.group(1).upper()
    return None


def _find_client(rows: list[_Row]) -> str:
    # A legal-entity name beats a bare brand: some sender lines only show "3Dmensionals".
    for pattern in (COMPANY_SUFFIX, CLIENT_SUFFIX):
        for row in rows:
            for cell in row.cells:
                if pattern.search(cell.text) and not CLIENT_EXCLUDE.search(cell.text):
                    return re.split(r"\s+\|\s+", cell.text)[0].strip(" -,")
    return ""


def _find_terms(rows: list[_Row]) -> str:
    for index, row in enumerate(rows):
        for cell in row.cells:
            match = re.match(r"terms?\s*:?\s*(.*)$", cell.text, re.IGNORECASE)
            if not match:
                continue
            value = [match.group(1).strip()] if match.group(1).strip() else []
            if index + 1 < len(rows):
                for other in rows[index + 1].cells:
                    # Continuation words sit to the right of the label, not under the left-hand address block.
                    if cell.x - 2 <= other.x <= cell.x + 220 and not re.match(r"^(?:ein|vat|tva|tel)\b", other.text, re.IGNORECASE):
                        value.append(other.text)
            return " ".join(part for part in value if part)
    return ""


def _find_notes(rows: list[_Row]) -> str:
    for index, row in enumerate(rows):
        match = re.match(r"notes?\s*:?\s*(.*)$", row.text, re.IGNORECASE)
        if not match:
            continue
        if match.group(1).strip():
            return match.group(1).strip()
        if index + 1 < len(rows):
            return rows[index + 1].text.strip()
    return ""


def _currency(text: str) -> str | None:
    if "€" in text or re.search(r"\bEUR\b", text):
        return "EUR"
    if "£" in text or re.search(r"\bGBP\b", text):
        return "GBP"
    if "$" in text or re.search(r"\bUSD\b", text):
        return "USD"
    return None


def extract_pdf(content: bytes, filename: str) -> dict[str, Any]:
    reader = PdfReader(io.BytesIO(content))
    rows = _document_rows(reader)
    text = "\n".join(row.text for row in rows)
    result = _base_result(filename, text)
    warnings: list[str] = []

    blocks, first_index, end_index = _item_blocks(rows)
    before = rows[:first_index]
    after = rows[end_index:] if blocks else rows

    decimal = _detect_decimal(text.split())
    currency = _currency(text)
    if currency is None:
        warnings.append("Currency symbol not found; USD was used. Check the currency.")
        currency = "USD"
    dates = _date_parts(text)
    day_first, ambiguous_format = _resolve_day_first(dates, currency)

    items: list[dict[str, Any]] = []
    for block in blocks:
        item, item_warnings = _parse_item(block, decimal, day_first, len(items) + 1)
        warnings.extend(item_warnings)
        if item:
            items.append(item)

    order_number = _find_order_number(before) or _find_order_number(rows)
    if order_number:
        result["order_number"] = order_number
    else:
        warnings.append("Order number not found; a temporary number was assigned.")

    order_parts = _labeled_date(before)
    if order_parts:
        order_date = _to_iso(order_parts, day_first)
        if order_date:
            result["order_date"] = order_date
            if ambiguous_format and order_parts[0] <= 12 and order_parts[1] <= 12:
                warnings.append(
                    f"Order date {order_parts[0]:02d}/{order_parts[1]:02d}/{order_parts[2]} is ambiguous; "
                    f"read as {result['order_date']}. Verify it."
                )
    else:
        warnings.append("Order date not found; today's date was used.")

    client = _find_client(before)
    if client:
        result["client"] = client
    else:
        warnings.append("Customer name not found; review the client before calculating.")

    if "thought3d" in text.lower():
        result["vendor"] = "Thought3D Ltd"

    requested_dates = [item["requested_date"] for item in items if item["requested_date"]]
    result["requested_date"] = min(requested_dates) if requested_dates else None

    subtotal = tax = total = None
    for row in after:
        line = row.text
        if subtotal is None and SUBTOTAL_LINE.match(line):
            subtotal = _amount_on_line(line, decimal)
        elif tax is None and TAX_LINE.match(line):
            tax = _amount_on_line(line, decimal)
        elif total is None and TOTAL_LINE.match(line):
            total = _amount_on_line(line, decimal)

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


def _normalize_header(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


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

_CLIENT_HEADER_ALIASES = {
    "customer": {"customer", "client", "cliente", "customer name", "company"},
    "phone_number": {"phone number", "phone", "telephone", "telefono", "tel"},
    "email": {"email", "e-mail", "email address", "correo"},
    "contact_name": {"contact name", "contact", "contacto"},
    "address": {"address", "direccion", "billing address"},
    "country": {"country", "pais"},
    "shipping_address": {"shipping address", "ship to", "delivery address", "direccion de envio"},
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
        # Skip blank rows and footers (e.g. an export timestamp) outside the client columns.
        if not any(record.values()):
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


def parse_reference_workbook(content: bytes) -> dict[str, list[dict[str, Any]]] | None:
    """Read the operational volume/weight calculator supplied by the user.

    The workbook is intentionally not a flat import template: product weights live in
    columns A:B of one sheet and shipping boxes live in a matrix on another sheet.
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
            }
        )

    boxes_sheet = workbook["box types-what fits"]
    boxes: list[dict[str, Any]] = []
    for row in boxes_sheet.iter_rows(min_row=3, max_row=15, min_col=1, max_col=3, values_only=True):
        name, raw_dimensions, weight_grams = row
        dimensions = _parse_mm_dimensions(raw_dimensions)
        if not name or not dimensions or not isinstance(weight_grams, (int, float)):
            continue
        length, width, height = dimensions
        boxes.append(
            {
                "code": re.sub(r"[^A-Z0-9]+", "-", str(name).upper()).strip("-"),
                "name": str(name).strip(),
                "length_cm": length,
                "width_cm": width,
                "height_cm": height,
                "empty_weight_kg": round(float(weight_grams) / 1000, 4),
                "max_weight_kg": 25.0,
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
