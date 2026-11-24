from __future__ import annotations

import atexit
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .db import (
    Database,
    DatabaseError,
    IntegrityError,
    connect_postgres,
    connect_sqlite,
    drop_created_schemas,
    is_postgres_url,
)
from .language import english_plan_copy

__all__ = ["DB_PATH", "Database", "DatabaseError", "IntegrityError", "connect", "database_engine", "init_db"]

APP_DIR = Path(__file__).resolve().parent
SCHEMA_DIR = APP_DIR / "schema"
DEFAULT_DB_PATH = APP_DIR.parent / "data" / "packflow.db"
DB_PATH = Path(os.getenv("PACKFLOW_DB_PATH", DEFAULT_DB_PATH))

# Serialises concurrent schema migrations (several workers starting at once).
_MIGRATION_LOCK_ID = 7245_0001


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def database_engine() -> str:
    """'postgres' when DATABASE_URL (or the test URL) points to PostgreSQL, else 'sqlite'."""
    if os.getenv("PACKFLOW_TEST_DATABASE_URL") or is_postgres_url(os.getenv("DATABASE_URL", "").strip()):
        return "postgres"
    return "sqlite"


def _test_schema(path: Path) -> str:
    # The test-suite points DB_PATH at a fresh temporary file per test; on PostgreSQL each
    # such path gets its own schema, so tests stay isolated without code changes.
    return "test_" + hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:24]


def connect() -> Database:
    """Open a unit of work: ``with connect() as db`` commits on success, rolls back on error
    and always releases the connection."""
    test_url = os.getenv("PACKFLOW_TEST_DATABASE_URL", "").strip()
    if test_url:
        return connect_postgres(test_url, schema=_test_schema(DB_PATH))
    url = os.getenv("DATABASE_URL", "").strip()
    if is_postgres_url(url):
        return connect_postgres(url, max_size=int(os.getenv("PACKFLOW_DB_POOL_MAX", "5")))
    return connect_sqlite(DB_PATH)


if os.getenv("PACKFLOW_TEST_DATABASE_URL"):
    atexit.register(drop_created_schemas)


def init_db() -> None:
    with connect() as db:
        if db.dialect == "postgres":
            _apply_postgres_migrations(db)
        else:
            db.executescript((SCHEMA_DIR / "sqlite.sql").read_text(encoding="utf-8"))
            _migrate_schema(db)
        _apply_reference_packing_data(db)
        seed_demo_data(db)
        if os.getenv("PACKFLOW_SEED_REFERENCE_CATALOG", "").lower() in {"1", "true"}:
            seed_reference_catalog(db)
        _apply_tsca_catalog(db)


def _apply_postgres_migrations(db: Database) -> None:
    """Apply the numbered scripts in schema/postgres/ that are not yet recorded."""
    db.execute("SELECT pg_advisory_xact_lock(?)", (_MIGRATION_LOCK_ID,))
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row["version"] for row in db.execute("SELECT version FROM schema_migrations")}
    for script in sorted((SCHEMA_DIR / "postgres").glob("*.sql")):
        if script.stem in applied:
            continue
        db.executescript(script.read_text(encoding="utf-8"))
        db.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (script.stem, now_iso()))


def _ensure_column(db: Database, table: str, column: str, definition: str) -> None:
    existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_schema(db: Database) -> None:
    """Additive migrations for SQLite databases created by earlier versions (SQLite only;
    PostgreSQL uses the numbered scripts in schema/postgres/)."""
    order_columns = {
        "vendor": "TEXT NOT NULL DEFAULT ''",
        "requested_date": "TEXT",
        "source_type": "TEXT NOT NULL DEFAULT 'pdf'",
        "delivery_address": "TEXT NOT NULL DEFAULT ''",
        "billing_address": "TEXT NOT NULL DEFAULT ''",
        "terms": "TEXT NOT NULL DEFAULT ''",
        "notes": "TEXT NOT NULL DEFAULT ''",
        "subtotal": "REAL NOT NULL DEFAULT 0",
        "tax_amount": "REAL NOT NULL DEFAULT 0",
        "total_amount": "REAL NOT NULL DEFAULT 0",
    }
    item_columns = {
        "line_number": "INTEGER",
        "requested_date": "TEXT",
        "tax_label": "TEXT NOT NULL DEFAULT ''",
        "discount_percent": "REAL NOT NULL DEFAULT 0",
        "amount": "REAL NOT NULL DEFAULT 0",
        "is_suggested": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, definition in order_columns.items():
        _ensure_column(db, "orders", column, definition)
    for column, definition in item_columns.items():
        _ensure_column(db, "order_items", column, definition)
    # Prices come from each purchase order, not from the catalog.
    if "price_eur" in {row["name"] for row in db.execute("PRAGMA table_info(products)")}:
        db.execute("ALTER TABLE products DROP COLUMN price_eur")
    # Packing data from the workbook "what fits" sheet: a product's capacity family and,
    # per box type, how many units of each family it holds (JSON).
    _ensure_column(db, "products", "capacity_family", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(db, "box_types", "capacities", "TEXT NOT NULL DEFAULT '{}'")
    # Reference only (not enforced by packing): how many 50 ml / 250 ml products fit in the box in theory.
    _ensure_column(db, "box_types", "reference_50ml", "INTEGER")
    _ensure_column(db, "box_types", "reference_250ml", "INTEGER")
    # TSCA certification: product number (EAN) and the wording printed on the declaration.
    _ensure_column(db, "products", "ean", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(db, "products", "tsca_name", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(db, "products", "tsca_description", "TEXT NOT NULL DEFAULT ''")


def _apply_reference_packing_data(db: Database) -> None:
    """One-time backfill of the workbook packing data for existing records.

    A box gets the "what fits" capacities of the reference box with the same code or the
    same inner dimensions (catalogs often rename boxes); its provisional 25 kg limit
    becomes 20 kg, as in the DHL calculator. A product gets its capacity family from its
    name or its packed size. Only empty values are filled; user edits are kept.
    """
    from .extraction import capacity_family_for  # local import: extraction does not need the database

    if db.execute("SELECT 1 FROM app_settings WHERE key = 'reference_packing_data_v2'").fetchone():
        return
    db.execute("INSERT INTO app_settings (key, value) VALUES ('reference_packing_data_v2', '1') ON CONFLICT (key) DO NOTHING")
    catalog = json.loads((APP_DIR / "reference_catalog.json").read_text(encoding="utf-8"))

    def size(row: Any) -> tuple[float, ...]:
        return tuple(sorted(round(float(row[key]), 2) for key in ("length_cm", "width_cm", "height_cm")))

    by_code = {box["code"]: box for box in catalog["boxes"]}
    by_size = {size(box): box for box in catalog["boxes"]}
    for row in db.execute("SELECT id, code, length_cm, width_cm, height_cm, capacities FROM box_types").fetchall():
        reference = by_code.get(row["code"]) or by_size.get(size(row))
        if reference is None:
            continue
        if row["capacities"] in ("", "{}"):
            db.execute("UPDATE box_types SET capacities = ? WHERE id = ?",
                       (json.dumps(reference.get("capacities", {})), row["id"]))
        db.execute("UPDATE box_types SET max_weight_kg = ? WHERE id = ? AND max_weight_kg = 25",
                   (reference["max_weight_kg"], row["id"]))
    for row in db.execute(
        "SELECT id, name, length_cm, width_cm, height_cm FROM products WHERE capacity_family = ''"
    ).fetchall():
        family = capacity_family_for(row["name"], (row["length_cm"], row["width_cm"], row["height_cm"]))
        if family:
            db.execute("UPDATE products SET capacity_family = ? WHERE id = ?", (family, row["id"]))


def load_tsca_catalog() -> dict[str, dict[str, Any]]:
    """EAN and TSCA wording keyed by every normalized product-name alias."""
    from .naming import normalize_product_name

    catalog = json.loads((APP_DIR / "tsca_catalog.json").read_text(encoding="utf-8"))
    return {normalize_product_name(alias): entry for entry in catalog["products"] for alias in entry["aliases"]}


def apply_tsca_catalog(db: Database) -> int:
    """Fill empty EAN / TSCA name / TSCA description from the price-list catalog by product name.

    Values already present (typed by a user) are never overwritten. Returns the products touched.
    """
    from .naming import normalize_product_name

    by_alias = load_tsca_catalog()
    touched = 0
    for row in db.execute("SELECT id, name, ean, tsca_name, tsca_description FROM products").fetchall():
        entry = by_alias.get(normalize_product_name(row["name"]))
        if entry is None:
            continue
        values = {"ean": entry["ean"], "tsca_name": entry.get("tsca_name", ""),
                  "tsca_description": entry.get("tsca_description", "")}
        updates = {key: value for key, value in values.items() if value and not (row[key] or "").strip()}
        if updates:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            db.execute(f"UPDATE products SET {assignments} WHERE id = ?", (*updates.values(), row["id"]))
            touched += 1
    return touched


def _apply_tsca_catalog(db: Database) -> None:
    """One-time backfill of the price-list EANs for existing catalogs."""
    if db.execute("SELECT 1 FROM app_settings WHERE key = 'tsca_catalog_v1'").fetchone():
        return
    db.execute("INSERT INTO app_settings (key, value) VALUES ('tsca_catalog_v1', '1') ON CONFLICT (key) DO NOTHING")
    apply_tsca_catalog(db)


def rows_to_dicts(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def seed_reference_catalog(db: Database) -> None:
    """Restore the demo's reference catalog on fresh ephemeral instances.

    Only insert missing records. User edits, disabled records and imported
    measurements must survive subsequent startup calls on the same database.
    """
    catalog = json.loads((APP_DIR / "reference_catalog.json").read_text(encoding="utf-8"))
    for product in catalog["products"]:
        db.execute(
            """INSERT INTO products
                (sku, name, length_cm, width_cm, height_cm, weight_kg,
                 rotation_allowed, fragile, capacity_family, active, created_at)
               SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?
               WHERE NOT EXISTS (SELECT 1 FROM products WHERE lower(trim(name)) = lower(?))
               ON CONFLICT(sku) DO NOTHING""",
            (product["sku"], product["name"], product["length_cm"], product["width_cm"],
             product["height_cm"], product["weight_kg"], product["rotation_allowed"],
             product["fragile"], product.get("capacity_family", ""), now_iso(), product["name"]),
        )
    for box in catalog["boxes"]:
        db.execute(
            """INSERT INTO box_types
                (code, name, length_cm, width_cm, height_cm, empty_weight_kg,
                 max_weight_kg, capacities, active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(code) DO NOTHING""",
            (box["code"], box["name"], box["length_cm"], box["width_cm"], box["height_cm"],
             box["empty_weight_kg"], box["max_weight_kg"], json.dumps(box.get("capacities", {})), now_iso()),
        )
    apply_tsca_catalog(db)


def seed_demo_data(db: Database) -> None:
    # Seed the demo catalog only once, so records the user deletes do not return on restart.
    catalog_seeded = db.execute("SELECT 1 FROM app_settings WHERE key = 'demo_catalog_seeded'").fetchone()
    db.execute("INSERT INTO app_settings (key, value) VALUES ('demo_catalog_seeded', '1') ON CONFLICT (key) DO NOTHING")
    if not catalog_seeded and db.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        timestamp = now_iso()
        db.executemany(
            """
            INSERT INTO products
                (sku, name, length_cm, width_cm, height_cm, weight_kg,
                 rotation_allowed, fragile, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            [
                ("GLV-100", "Guantes de nitrilo · caja", 24, 13, 8, 0.72, 1, 0, timestamp),
                ("KIT-210", "Kit de primeros auxilios", 30, 22, 12, 1.80, 1, 0, timestamp),
                ("FLT-400", "Filtro industrial F400", 28, 28, 6, 0.45, 1, 0, timestamp),
                ("PUMP-08", "Bomba compacta P8", 36, 20, 18, 4.20, 0, 1, timestamp),
                ("TUBE-50", "Set de tubos reforzados", 20, 10, 10, 0.35, 1, 0, timestamp),
                ("SENSOR-X", "Sensor de presión X", 16, 12, 7, 0.28, 1, 1, timestamp),
                ("VALVE-20", "Válvula sanitaria 20 mm", 18, 14, 11, 0.62, 1, 0, timestamp),
            ],
        )

    if not catalog_seeded and db.execute("SELECT COUNT(*) FROM box_types").fetchone()[0] == 0:
        timestamp = now_iso()
        db.executemany(
            """
            INSERT INTO box_types
                (code, name, length_cm, width_cm, height_cm, empty_weight_kg,
                 max_weight_kg, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            [
                ("BX-S", "Caja compacta", 32, 24, 16, 0.24, 8, timestamp),
                ("BX-M", "Caja estándar", 48, 34, 28, 0.55, 18, timestamp),
                ("BX-L", "Caja de carga", 62, 42, 40, 0.90, 30, timestamp),
            ],
        )

    seeded = db.execute("SELECT 1 FROM app_settings WHERE key = 'demo_orders_seeded'").fetchone()
    db.execute("INSERT INTO app_settings (key, value) VALUES ('demo_orders_seeded', '1') ON CONFLICT (key) DO NOTHING")
    if not seeded and db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0:
        products = {
            row["sku"]: row["id"]
            for row in db.execute("SELECT id, sku FROM products").fetchall()
        }
        timestamp = now_iso()
        demo_orders = [
            (
                "PO-1048",
                "Northstar Labs",
                "2026-10-27",
                0.96,
                [("GLV-100", 6, 14.50), ("SENSOR-X", 2, 89), ("FLT-400", 4, 28.75)],
            ),
            (
                "PO-1047",
                "Atlas Manufacturing",
                "2026-10-26",
                0.92,
                [("PUMP-08", 2, 349), ("TUBE-50", 3, 22), ("VALVE-20", 4, 47.50)],
            ),
            (
                "PO-1046",
                "Meridian Health",
                "2026-10-25",
                0.88,
                [("KIT-210", 5, 74), ("GLV-100", 8, 14.50), ("SENSOR-X", 3, 89)],
            ),
        ]
        for number, client, date, confidence, items in demo_orders:
            order_id = db.insert_returning_id(
                """
                INSERT INTO orders
                    (order_number, client, order_date, currency, source_filename,
                     status, confidence, created_at, updated_at)
                VALUES (?, ?, ?, 'USD', 'orden-demo.pdf', 'ready', ?, ?, ?)
                RETURNING id
                """,
                (number, client, date, confidence, timestamp, timestamp),
            )
            db.executemany(
                """
                INSERT INTO order_items
                    (order_id, product_id, sku_raw, description, quantity, unit_price)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        order_id,
                        products[sku],
                        sku,
                        db.execute("SELECT name FROM products WHERE sku = ?", (sku,)).fetchone()[0],
                        quantity,
                        price,
                    )
                    for sku, quantity, price in items
                ],
            )


def get_shipping_quote(db: Database, order_id: int) -> dict[str, Any] | None:
    row = db.execute("SELECT * FROM shipping_quotes WHERE order_id = ?", (order_id,)).fetchone()
    if not row:
        return None
    return {
        "order_id": row["order_id"],
        "plan_id": row["plan_id"],
        "inputs": json.loads(row["inputs_json"]),
        "result": json.loads(row["result_json"]),
        "tariff_version": row["tariff_version"],
        "created_at": row["created_at"],
    }


def save_shipping_quote(
    db: Database, order_id: int, plan_id: int | None, inputs: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    db.execute(
        """INSERT INTO shipping_quotes
           (order_id, plan_id, inputs_json, result_json, tariff_version, created_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (order_id) DO UPDATE SET
               plan_id = excluded.plan_id, inputs_json = excluded.inputs_json,
               result_json = excluded.result_json, tariff_version = excluded.tariff_version,
               created_at = excluded.created_at""",
        (order_id, plan_id, json.dumps(inputs), json.dumps(result), result["tariff_version"], now_iso()),
    )
    return get_shipping_quote(db, order_id)  # type: ignore[return-value]


def latest_plan(db: Database, order_id: int) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT * FROM packing_plans WHERE order_id = ? ORDER BY id DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    if not row:
        return None
    plan = dict(row)
    plan["result"] = english_plan_copy(json.loads(plan.pop("result_json")))
    return plan

