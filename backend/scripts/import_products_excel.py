"""Update the products catalog from the "Products" tab of the Boxes and Prices workbook.

Usage (from backend/):
    venv/Scripts/python -m scripts.import_products_excel "path/to/Boxes and Prices.xlsx"

- Columns: Producto, weight (the header says g but the values are kg: 0.085 = 85 g),
  Dimensions (mm). "36 x 36 x 165" is length x width x height; "50 x 185" is a cylinder
  (diameter x height), stored as a 50 x 50 x 185 mm box.
- Products are matched by name and updated in place, so order lines keep their link;
  new names are inserted. Products missing from the sheet are left untouched.
- Prices are not stored: they come from each purchase order.
"""
from __future__ import annotations

import re
import sys

from openpyxl import load_workbook

from app.database import connect, init_db, now_iso
from app.extraction import capacity_family_for
from app.main import _internal_product_code, _normalize_product_name


# Catalog names that the workbook renamed: matched to the same product so orders keep their link.
RENAMED = {"magigoo glide kit original": "magigoo glide kit"}


def clean_name(raw: str) -> str:
    name = " ".join(str(raw).split())
    return re.sub(r"\s-\s(MO|SG)$", "", name)  # "Magigoo Original - MO" -> "Magigoo Original"


def parse_dimensions_cm(raw: str) -> tuple[float, float, float]:
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(raw))]
    if len(numbers) == 2:  # diameter x height
        numbers = [numbers[0], numbers[0], numbers[1]]
    if len(numbers) != 3:
        raise ValueError(f"Cannot read dimensions {raw!r}")
    return tuple(n / 10 for n in numbers)  # mm -> cm


def read_products(path: str) -> list[dict]:
    rows = load_workbook(path, data_only=True)["Products"].iter_rows(values_only=True)
    products = []
    started = False
    for row in rows:
        values = [v for v in row if v is not None]
        if not started:
            started = bool(values) and str(values[0]).strip() == "Producto"
            continue
        if len(values) < 3:
            continue
        name, weight, dims = values[:3]
        length, width, height = parse_dimensions_cm(dims)
        products.append({
            "name": clean_name(name), "length_cm": length, "width_cm": width,
            "height_cm": height, "weight_kg": float(str(weight).replace(",", ".")),
        })
    return products


def main(path: str) -> None:
    products = read_products(path)
    init_db()  # drops the old price column if it is still there
    updated = inserted = 0
    with connect() as db:
        existing = {_normalize_product_name(r["name"]): r for r in db.execute("SELECT id, name, capacity_family FROM products")}
        for p in products:
            dims = (p["length_cm"], p["width_cm"], p["height_cm"])
            family = capacity_family_for(p["name"], dims)
            key = _normalize_product_name(p["name"])
            row = existing.get(key) or existing.get(RENAMED.get(key, ""))
            if row:
                db.execute(
                    """UPDATE products SET name = ?, length_cm = ?, width_cm = ?, height_cm = ?, weight_kg = ?,
                           capacity_family = ?, active = 1 WHERE id = ?""",
                    (p["name"], *dims, p["weight_kg"], family or row["capacity_family"], row["id"]),
                )
                updated += 1
            else:
                db.execute(
                    """INSERT INTO products (sku, name, length_cm, width_cm, height_cm, weight_kg,
                           rotation_allowed, fragile, capacity_family, active, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, 1, ?)""",
                    (_internal_product_code(p["name"]), p["name"], *dims, p["weight_kg"], family, now_iso()),
                )
                inserted += 1
    print(f"Products updated: {updated}, inserted: {inserted}.")


if __name__ == "__main__":
    main(sys.argv[1])
