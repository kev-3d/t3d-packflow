"""Load the "50ml" and "Glide 250ml" columns of the Boxes tab into box_types.

Usage (from backend/):
    venv/Scripts/python -m scripts.import_box_references "path/to/Boxes and Prices.xlsx"

Those columns say how many 50 ml / 250 ml products fit in each box in theory. They are stored
as reference_50ml / reference_250ml and are informational only: packing does not enforce them.
Boxes are matched by name; no other box field is changed. A blank cell stores NULL.
"""
from __future__ import annotations

import sys

from openpyxl import load_workbook

from app.database import connect, init_db


def key(name: str) -> str:
    return " ".join(str(name).lower().split())


def read_references(path: str) -> list[tuple[str, int | None, int | None]]:
    rows = load_workbook(path, data_only=True)["Boxes"].iter_rows(values_only=True)
    out, started = [], False
    for row in rows:
        cells = list(row)[1:6]  # Type, Dimensions, Grams, 50ml, Glide 250ml
        if not started:
            started = cells and str(cells[0]).strip() == "Type"
            continue
        if not cells or cells[0] is None:
            continue
        as_int = lambda v: None if v is None else int(round(float(v)))  # noqa: E731
        out.append((str(cells[0]), as_int(cells[3]), as_int(cells[4])))
    return out


def main(path: str) -> None:
    refs = read_references(path)
    init_db()  # adds the reference columns if missing
    with connect() as db:
        boxes = {key(r["name"]): r["id"] for r in db.execute("SELECT id, name FROM box_types")}
        updated, missing = 0, []
        for name, ref50, ref250 in refs:
            box_id = boxes.get(key(name))
            if box_id is None:
                missing.append(name)
                continue
            db.execute("UPDATE box_types SET reference_50ml = ?, reference_250ml = ? WHERE id = ?", (ref50, ref250, box_id))
            updated += 1
    print(f"Boxes updated: {updated}." + (f" Not found in the catalog: {', '.join(missing)}" if missing else ""))


if __name__ == "__main__":
    main(sys.argv[1])
