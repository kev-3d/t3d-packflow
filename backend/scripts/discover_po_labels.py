"""List label candidates found in a folder of purchase-order PDFs, to grow app/po_lexicon.json.

A candidate is text printed just before a value (a date, an amount or a code): in the same cell
("Ship Date: 12/01/2026"), in the next cell of the row, or in the cell under it. Candidates the
lexicon already knows are marked, so the output shows what is missing. Nothing is changed: a person
reviews the list and adds the useful synonyms to po_lexicon.json.

usage (from backend/):  python scripts/discover_po_labels.py <folder with PDFs> [--all]
"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pypdf import PdfReader  # noqa: E402

from app.extraction import _cell_date, _document_rows, _is_amount_word  # noqa: E402
from app.po_lexicon import lexicon, word_tokens  # noqa: E402

LABEL = re.compile(r"^([^\W\d_][^:]{1,38}?)\s*:\s*(\S.*)?$")


def _is_value(text: str) -> bool:
    first = text.split()[0] if text.split() else ""
    return bool(_cell_date(text)) or _is_amount_word(first.strip("$€£")) or bool(re.fullmatch(r"#?(?=\S*\d)[A-Z0-9][A-Z0-9-]{2,}", first))


def _candidates(rows):
    for index, row in enumerate(rows):
        for position, cell in enumerate(row.cells):
            match = LABEL.match(cell.text)
            if match and match.group(2) and _is_value(match.group(2)):
                yield match.group(1), match.group(2)
                continue
            label = cell.text.rstrip(":").strip()
            if not (2 <= len(label) <= 40) or re.search(r"\d", label) or len(label.split()) > 5:
                continue
            neighbours = row.cells[position + 1:position + 2]
            if index + 1 < len(rows):
                neighbours += [other for other in rows[index + 1].cells if abs(other.x - cell.x) <= 25]
            for other in neighbours:
                if _is_value(other.text):
                    yield label, other.text
                    break


def main() -> None:
    folder = Path(sys.argv[1])
    show_all = "--all" in sys.argv
    lex = lexicon()
    known = {phrase for table in (lex.labels, lex.columns) for synonyms in table.values() for _, phrase in synonyms}
    counts: Counter[str] = Counter()
    examples: dict[str, str] = {}
    files: defaultdict[str, set[str]] = defaultdict(set)
    for path in sorted(folder.glob("*.pdf")):
        try:
            rows = _document_rows(PdfReader(io.BytesIO(path.read_bytes())))
        except Exception as exc:  # a damaged file must not stop the survey
            print(f"! {path.name}: {exc}", file=sys.stderr)
            continue
        for label, value in _candidates(rows):
            key = " ".join(token for _, token in word_tokens(label))
            if not key:
                continue
            counts[key] += 1
            examples.setdefault(key, f"{label}: {value[:30]}")
            files[key].add(path.name)
    print(f"{'files':>5} {'seen':>5}  known  label  (example)")
    for key, count in counts.most_common():
        is_known = tuple(key.split()) in known
        if is_known and not show_all:
            continue
        print(f"{len(files[key]):>5} {count:>5}  {'yes' if is_known else ' - ':>5}  {key}  ({examples[key]})")


if __name__ == "__main__":
    main()
