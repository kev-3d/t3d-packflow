"""Replace the box_types and products catalog with the "Boxes and Prices" workbook.

Usage (from backend/):
    venv\\Scripts\\python -m scripts.import_catalog_excel "path\\to\\Boxes and Prices.xlsx"

- Boxes tab: Type, Dimensions (mm), Grams -> box_types (mm -> cm, g -> kg).
- Prices tab: Producto, weight, Precio (EUR) -> products. The weight column is
  labelled "(g)" but holds kilograms (0.085 = 85 g bottle), so it is stored as-is.
- The workbook has no product dimensions or box capacity; see PRODUCT_DIMENSIONS_CM
  and DEFAULT_MAX_WEIGHT_KG.
- Deletes every order (and its plans, reports, documents and quotes) first, because
  order lines reference the products being replaced.
"""
from __future__ import annotations

import re
import sys

from openpyxl import load_workbook

from app.database import connect, init_db, now_iso
from app.main import _internal_product_code

DEFAULT_MAX_WEIGHT_KG = 25.0

# Taken from the dimensions already in the catalog before this import (cm).
PRODUCT_DIMENSIONS_CM = [
    ("glide kit", (20.0, 15.0, 10.0)),
    ("pro kit", (20.0, 15.0, 6.0)),
    ("glide 250ml", (6.0, 6.0, 20.0)),
    ("50ml", (4.0, 4.0, 12.0)),
]


def _rows(sheet, header_first: str):
    rows = sheet.iter_rows(values_only=True)
    for row in rows:
        if any(str(v).strip() == header_first for v in row if v is not None):
            break
    for row in rows:
        values = [v for v in row if v is not None]
        if values:
            yield values


def _code(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", name.upper().replace('"', "IN")).strip("-")


def parse_boxes(sheet) -> list[dict]:
    boxes = []
    for name, dims, grams in _rows(sheet, "Type"):
        numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(dims))]
        if len(numbers) != 3:
            raise ValueError(f"Box {name!r}: cannot read dimensions {dims!r}")
        name = " ".join(str(name).split())
        boxes.append({
            "code": _code(name), "name": name,
            "length_cm": numbers[0] / 10, "width_cm": numbers[1] / 10, "height_cm": numbers[2] / 10,
            "empty_weight_kg": float(grams) / 1000, "max_weight_kg": DEFAULT_MAX_WEIGHT_KG,
        })
    return boxes


def parse_products(sheet) -> list[dict]:
    products = []
    for name, weight, price in _rows(sheet, "Producto"):
        name = " ".join(str(name).split())
        key = name.lower().replace(" - ", " ")
        dims = next((d for token, d in PRODUCT_DIMENSIONS_CM if token in key), None)
        if dims is None:
            raise ValueError(f"Product {name!r}: no dimensions mapped")
        products.append({
            "sku": _internal_product_code(name), "name": name,
            "length_cm": dims[0], "width_cm": dims[1], "height_cm": dims[2],
            "weight_kg": float(str(weight).replace(",", ".")), "price_eur": float(price),
        })
    return products


def main(path: str) -> None:
    workbook = load_workbook(path, data_only=True)
    boxes = parse_boxes(workbook["Boxes"])
    products = parse_products(workbook["Prices"])
    init_db()  # applies the price_eur migration
    timestamp = now_iso()
    with connect() as db:
        orders = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        db.execute("DELETE FROM orders")  # cascades to items, plans, reports, documents, quotes
        db.execute("DELETE FROM products")
        db.execute("DELETE FROM box_types")
        db.executemany(
            """INSERT INTO box_types (code, name, length_cm, width_cm, height_cm,
                   empty_weight_kg, max_weight_kg, active, created_at)
               VALUES (:code, :name, :length_cm, :width_cm, :height_cm,
                   :empty_weight_kg, :max_weight_kg, 1, :ts)""",
            [{**b, "ts": timestamp} for b in boxes],
        )
        db.executemany(
            """INSERT INTO products (sku, name, length_cm, width_cm, height_cm, weight_kg,
                   price_eur, rotation_allowed, fragile, active, created_at)
               VALUES (:sku, :name, :length_cm, :width_cm, :height_cm, :weight_kg,
                   :price_eur, 1, 0, 1, :ts)""",
            [{**p, "ts": timestamp} for p in products],
        )
    print(f"Deleted {orders} orders. Loaded {len(boxes)} boxes and {len(products)} products.")


if __name__ == "__main__":
    main(sys.argv[1])
