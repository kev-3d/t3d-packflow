from __future__ import annotations

import dataclasses
import json
import hashlib
import logging
import re
import unicodedata
from difflib import SequenceMatcher
from io import BytesIO
from contextlib import asynccontextmanager
from datetime import date
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from pydantic import BaseModel, Field

from . import dhl_pricing
from .database import (
    Database,
    DatabaseError,
    IntegrityError,
    database_engine,
    apply_tsca_catalog, connect, get_shipping_quote, init_db, latest_plan, now_iso, rows_to_dicts,
    save_shipping_quote,
)
from .destinations import detect_destination
from .geo import with_country_code
from .dhl_surcharges import REGION_LABELS, load_surcharges
from .extraction import (
    capacity_family_for, extract_email, extract_pdf, parse_catalog_excel, parse_clients_excel,
    parse_reference_workbook,
)
from .packing import DEFAULT_OBJECTIVE as DEFAULT_PACKING_OBJECTIVE
from .packing import OBJECTIVES as PACKING_OBJECTIVES
from .packing import PackingSettings, fill_existing_plan, pack_order
from .requirements_routes import requirements_note_for, router as requirements_router
from .labels import render_box_labels_pdf
from .reports import render_analysis_pdf
from .shipping_docs import DEFAULT_EXPORTER, build_shipment, render_packing_list_pdf
from . import tsca
from .naming import normalize_product_name
from .auth import bootstrap_admins, configuration_problems, current_user, require_admin
from .auth_routes import router as auth_router
from .env import PROJECT_ENV, PROJECT_ENV_LOCAL, load_env
from .slack_notify import notify_plan_ready

logger = logging.getLogger("packflow")


class ProductPayload(BaseModel):
    name: str
    length_cm: float = Field(gt=0)
    width_cm: float = Field(gt=0)
    height_cm: float = Field(gt=0)
    weight_kg: float = Field(gt=0)
    rotation_allowed: bool = True
    fragile: bool = False
    # Workbook "what fits" capacity family; omitted, it is kept (or derived from the name).
    capacity_family: str | None = None
    # TSCA certification fields; omitted, they are kept.
    ean: str | None = Field(default=None, max_length=32)
    tsca_name: str | None = Field(default=None, max_length=500)
    tsca_description: str | None = Field(default=None, max_length=4000)


class BoxPayload(BaseModel):
    code: str
    name: str
    length_cm: float = Field(gt=0)
    width_cm: float = Field(gt=0)
    height_cm: float = Field(gt=0)
    empty_weight_kg: float = Field(ge=0)
    max_weight_kg: float = Field(gt=0)
    # Units of each capacity family the box holds; omitted, the stored values are kept.
    capacities: dict[str, float] | None = None
    # Theoretical number of 50 ml / 250 ml products that fit; reference only, blank = unknown.
    reference_50ml: int | None = Field(default=None, ge=0)
    reference_250ml: int | None = Field(default=None, ge=0)


class OrderItemPayload(BaseModel):
    sku_raw: str = ""
    description: str
    quantity: int = Field(gt=0)
    unit_price: float = Field(default=0, ge=0)
    line_number: int | None = None
    requested_date: str | None = None
    tax_label: str = ""
    discount_percent: float = Field(default=0, ge=0, le=100)
    amount: float = Field(default=0, ge=0)
    is_suggested: bool = False


class SuggestedItemPayload(BaseModel):
    source_item_id: int
    quantity: int = Field(gt=0, le=100)
    expected_plan_id: int | None = Field(default=None, gt=0)


class EmailOrderPayload(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    order_number: str | None = Field(default=None, min_length=1, max_length=200)
    client: str = ""
    subject: str = ""


class OrderReviewPayload(BaseModel):
    order_number: str = Field(min_length=1)
    client: str = Field(min_length=1)
    vendor: str = ""
    order_date: str | None = None
    requested_date: str | None = None
    currency: str = "USD"
    delivery_address: str = ""
    billing_address: str = ""
    terms: str = ""
    notes: str = ""
    subtotal: float = Field(default=0, ge=0)
    tax_amount: float = Field(default=0, ge=0)
    total_amount: float = Field(default=0, ge=0)
    items: list[OrderItemPayload]


class ShippingQuotePayload(BaseModel):
    destination: str = Field(min_length=2, max_length=10)
    shipment_date: str
    service: str = "express_worldwide"
    fuel_source: str = "dhl"
    fuel_pct: float | None = Field(default=None, ge=0, le=200)
    demand_source: str = "dhl"
    demand_per_kg: float | None = Field(default=None, ge=0, le=100)
    gogreen_per_kg: float | None = Field(default=None, ge=0, le=100)
    vat_pct: float | None = Field(default=None, ge=0, le=100)
    remote_area: bool = False
    other_eur: float = Field(default=0, ge=0)


def _line_amount(item: OrderItemPayload) -> float:
    if item.amount:
        return item.amount
    return round(
        item.quantity * item.unit_price * (1 - item.discount_percent / 100), 2
    )


_normalize_product_name = normalize_product_name


def _internal_product_code(name: str) -> str:
    normalized = _normalize_product_name(name)
    digest = hashlib.sha1(normalized.encode()).hexdigest()[:10].upper()
    return f"PRODUCT-{digest}"


_PRODUCT_VARIANTS = (
    "original", "supergrip", "ppgf", "pp", "pa", "pc", "flex", "metal", "ht", "abs", "pva",
)


def _product_family(normalized: str) -> str | None:
    """Classify a normalized product name into a packaging family."""
    words = set(normalized.split())
    if "glide" in words and "roller" in words:
        return "glide_roller"
    if "glide" in words and ("kit" in words or "kits" in words):
        return "glide_kit"
    if "prokit" in words or re.search(r"\bpro kit\b", normalized):
        return "pro_kit"
    if "glide" in words or re.search(r"\b250ml\b", normalized):
        return "glide_250ml"
    for size in ("120ml", "50ml", "10ml"):
        if re.search(rf"\b{size}\b", normalized):
            return f"bottle_{size}"
    if "magigoo" in words and not re.search(r"\d+ml\b", normalized):
        return "bottle_50ml"
    if re.fullmatch(r"\d+ml", normalized):
        return f"bottle_{normalized}"
    return None


def _product_variant(normalized: str) -> str | None:
    for word in normalized.split():
        if word in _PRODUCT_VARIANTS:
            return word
    return None


def _match_by_family_and_variant(
    products: list[Any], target: str
) -> Any | None:
    """Match PO wording such as 'MAGIGOO Glide 250ml - Supergrip' to one catalog product.

    Works for a catalog with one product per variant ('Magigoo Supergrip Glide 250ml') and
    for a generic one ('glide bottle 250ml'). A description naming a variant never falls
    back to a different variant; without a variant it takes the generic product, else Original.
    """
    family = _product_family(target)
    if family is None:
        return None
    variant = _product_variant(target)
    in_family = [
        (product, _product_variant(name))
        for product in products
        for name in [_normalize_product_name(product["name"])]
        if _product_family(name) == family
    ]
    wanted = [variant, None] if variant else [None, "original"]
    for candidate in wanted:
        match = next((product for product, v in in_family if v == candidate), None)
        if match is not None:
            return match
    return None


def _find_product_by_description(
    db: Database, description: str
) -> Any | None:
    target = _normalize_product_name(description)
    if not target:
        return None
    products = db.execute(
        "SELECT id, name FROM products WHERE active = 1 ORDER BY id"
    ).fetchall()
    exact = next(
        (product for product in products if _normalize_product_name(product["name"]) == target),
        None,
    )
    if exact:
        return exact

    family_match = _match_by_family_and_variant(products, target)
    if family_match is not None:
        return family_match

    product_names = {
        _normalize_product_name(product["name"]): product for product in products
    }
    rules = (
        ("glide roller", "glide roller"),
        ("glide kit", "glide kit"),
        ("glide 250ml", "glide bottle 250ml"),
        ("glide 250 ml", "glide bottle 250ml"),
        ("250ml bottle", "glide bottle 250ml"),
        ("250 ml bottle", "glide bottle 250ml"),
        ("120ml", "120ml"),
        ("120 ml", "120 ml"),
        ("50ml", "50ml"),
        ("50 ml", "50 ml"),
        ("10ml", "10ml"),
        ("10 ml", "10 ml"),
    )
    for phrase, catalog_name in rules:
        if phrase in target:
            catalog_product = next(
                (
                    product
                    for normalized_name, product in product_names.items()
                    if (normalized_name == catalog_name or
                        (catalog_name.startswith("glide") and catalog_name in normalized_name))
                ),
                None,
            )
            if catalog_product:
                return catalog_product
    if "magigoo" in target and not re.search(r"\d+ml\b", target) and "50ml" in product_names:
        return product_names["50ml"]
    variants = {"pp", "pa", "pc", "abs", "flex", "ht", "original", "pva"}
    target_variants = set(target.split()) & variants
    scored = [
        (SequenceMatcher(None, target, _normalize_product_name(product["name"])).ratio(), product)
        for product in products
        if (set(_normalize_product_name(product["name"]).split()) & variants) == target_variants
    ]
    best_score, best_product = max(scored, default=(0.0, None), key=lambda entry: entry[0])
    return best_product if best_score >= 0.88 else None


def _relink_order_items(db: Database, order_id: int) -> None:
    for item in db.execute(
        "SELECT id, description FROM order_items WHERE order_id = ?", (order_id,)
    ).fetchall():
        product = _find_product_by_description(db, item["description"])
        db.execute(
            "UPDATE order_items SET product_id = ? WHERE id = ?",
            (product["id"] if product else None, item["id"]),
        )


def _insert_order_item(
    db: Database, order_id: int, item: OrderItemPayload, position: int
) -> None:
    product = _find_product_by_description(db, item.description)
    db.execute(
        """
        INSERT INTO order_items
            (order_id, product_id, sku_raw, description, quantity, unit_price,
             line_number, requested_date, tax_label, discount_percent, amount,
             is_suggested)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            product["id"] if product else None,
            "",
            item.description or (product["name"] if product else "Producto sin identificar"),
            item.quantity,
            item.unit_price,
            item.line_number or position,
            item.requested_date,
            item.tax_label,
            item.discount_percent,
            _line_amount(item),
            int(item.is_suggested),
        ),
    )


def _packing_rows(db: Database, order_id: int) -> list[dict[str, Any]]:
    return rows_to_dicts(
        db.execute(
            """
            SELECT
                oi.id AS order_item_id, oi.quantity, oi.unit_price, oi.description,
                oi.is_suggested, p.id AS product_id, CAST(p.id AS TEXT) AS sku, p.name,
                p.length_cm, p.width_cm, p.height_cm, p.weight_kg,
                p.rotation_allowed, p.fragile, p.capacity_family
            FROM order_items oi
            JOIN products p ON p.id = oi.product_id
            WHERE oi.order_id = ? AND p.active = 1
            """,
            (order_id,),
        ).fetchall()
    )


def _fill_recommendations(
    db: Database,
    order_id: int,
    items: list[dict[str, Any]],
    boxes: list[dict[str, Any]],
    base_result: dict[str, Any],
) -> list[dict[str, Any]]:
    if not base_result["boxes"] or base_result["status"] != "complete":
        return []
    # Each recommendation is an alternative, evaluated in the fixed boxes.
    # Prices are optional: physical feasibility does not depend on pricing.
    recommendations = []
    seen = set()
    for source in items:
        if source["is_suggested"] or source["product_id"] in seen:
            continue
        seen.add(source["product_id"])
        addition = dict(source, quantity=100)
        trial = fill_existing_plan(base_result, items, [addition])
        quantity = 100 - len(trial["unplaced"])
        if not quantity:
            continue
        recommendations.append({
            "type": "product",
            "source_item_id": source["order_item_id"],
            "product_id": source["product_id"],
            "description": source["description"],
            "quantity": quantity,
            "unit_price": source["unit_price"],
            "amount": round(quantity * source["unit_price"], 2),
            "projected_utilization": trial["average_utilization"],
            "box_count": base_result["box_count"],
            "remaining_volume_l": sum(b["remaining_volume_l"] for b in trial["boxes"]),
            "requires_pricing": source["unit_price"] == 0,
            "alternative": True,
        })
    recommendations.sort(key=lambda r: (-r["projected_utilization"], r["amount"], r["source_item_id"]))
    return recommendations[:4]


def _refresh_order_totals(db: Database, order_id: int) -> None:
    row = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS subtotal FROM order_items WHERE order_id = ?",
        (order_id,),
    ).fetchone()
    tax_amount = db.execute(
        "SELECT tax_amount FROM orders WHERE id = ?", (order_id,)
    ).fetchone()["tax_amount"]
    subtotal = round(float(row["subtotal"]), 2)
    db.execute(
        "UPDATE orders SET subtotal = ?, total_amount = ?, updated_at = ? WHERE id = ?",
        (subtotal, round(subtotal + tax_amount, 2), now_iso(), order_id),
    )


def _packing_zone(db: Database, order_id: int) -> int | None:
    """DHL zone used to rank plans by shipping cost: the saved quote's destination, else
    the destination detected from the order and client data."""
    quote = get_shipping_quote(db, order_id)
    code = ((quote or {}).get("inputs") or {}).get("destination")
    try:
        if code:
            return int(dhl_pricing.zone_for(str(code))["zone"])
        detected = _detect_order_destination(db, order_id)
        return int(detected["zone"]) if detected else None
    except (dhl_pricing.PricingError, KeyError, TypeError, ValueError):
        return None


def _packing_settings(db: Database) -> PackingSettings:
    """Packing rules from ``app_settings`` (packing_allowance_cm, packing_gap_cm,
    packing_use_capacities, packing_allow_on_side, packing_max_parcel_kg); the defaults
    follow the DHL calculator reference app."""
    stored = {row["key"]: row["value"] for row in db.execute(
        "SELECT key, value FROM app_settings WHERE key LIKE 'packing_%'")}
    defaults = PackingSettings()

    def number(key: str, default: float) -> float:
        try:
            return max(0.0, float(stored.get(key, default)))
        except (TypeError, ValueError):
            return default

    def flag(key: str) -> bool:
        return str(stored.get(key, "1")).strip().lower() not in {"0", "false", "no", "off"}

    return PackingSettings(
        allowance_cm=number("packing_allowance_cm", defaults.allowance_cm),
        gap_cm=number("packing_gap_cm", defaults.gap_cm),
        use_capacities=flag("packing_use_capacities"),
        allow_on_side=flag("packing_allow_on_side"),
        # 0 turns the global limit off, leaving only each carton's own maximum.
        max_parcel_kg=number("packing_max_parcel_kg", defaults.max_parcel_kg) or None,
    )


MAX_ALLOWANCE_CM = 5.0


def _current_allowance(db: Database, order_id: int) -> float | None:
    """Wall clearance the order's latest plan was calculated with, if any."""
    plan = latest_plan(db, order_id)
    value = (((plan or {}).get("result") or {}).get("settings") or {}).get("allowance_cm")
    return float(value) if isinstance(value, (int, float)) and 0 <= value <= MAX_ALLOWANCE_CM else None


def _current_objective(db: Database, order_id: int) -> str:
    plan = latest_plan(db, order_id)
    objective = ((plan or {}).get("result") or {}).get("objective")
    return objective if objective in PACKING_OBJECTIVES else DEFAULT_PACKING_OBJECTIVE


def _run_plan(db: Database, order_id: int, objective: str | None = None,
              repack_additions: bool = True, allowance_cm: float | None = None) -> dict[str, Any]:
    """``allowance_cm``: wall clearance per side; omitted, the order keeps the one of its
    latest plan, and a first plan uses the packing_allowance_cm setting (0.5 cm by default)."""
    if allowance_cm is None:
        allowance_cm = _current_allowance(db, order_id)
    elif not 0 <= allowance_cm <= MAX_ALLOWANCE_CM:
        raise HTTPException(422, f"The wall clearance must be between 0 and {MAX_ALLOWANCE_CM:g} cm.")
    objective = objective or _current_objective(db, order_id)
    if objective not in PACKING_OBJECTIVES:
        raise HTTPException(422, f"Unknown packing objective {objective!r}.")
    _relink_order_items(db, order_id)
    total_items = db.execute(
        "SELECT COUNT(*) FROM order_items WHERE order_id = ?", (order_id,)
    ).fetchone()[0]
    items = _packing_rows(db, order_id)
    if total_items == 0:
        raise HTTPException(422, "The order has no line items.")
    if len(items) != total_items:
        missing = [
            row["description"]
            for row in db.execute(
                "SELECT description FROM order_items WHERE order_id = ? AND product_id IS NULL",
                (order_id,),
            ).fetchall()
        ]
        sample = ", ".join(missing[:4])
        suffix = "…" if len(missing) > 4 else ""
        raise HTTPException(
            422,
            f"Not found in the catalog: {sample}{suffix}. Import the reference data or update the description.",
        )
    boxes = rows_to_dicts(
        db.execute("SELECT * FROM box_types WHERE active = 1 ORDER BY length_cm * width_cm * height_cm").fetchall()
    )
    if not boxes:
        raise HTTPException(422, "There are no active box types.")
    primary_items = [item for item in items if not item["is_suggested"]]
    additions = [item for item in items if item["is_suggested"]]
    zone, settings = _packing_zone(db, order_id), _packing_settings(db)
    if allowance_cm is not None:
        settings = dataclasses.replace(settings, allowance_cm=float(allowance_cm))
    result = pack_order(primary_items, boxes, objective, zone, settings)
    if additions:
        if result["status"] != "complete":
            raise HTTPException(422, "The original order does not fit. Review the additional products.")
        base = result
        result = fill_existing_plan(base, items, additions)
        # Additions go into the existing boxes, so the objective comparison still applies.
        for key in ("objective", "objective_label", "dhl_zone", "dhl_zone_assumed", "options", "settings", "search"):
            result[key] = base[key]
        if result["status"] != "complete":
            if not repack_additions:
                raise HTTPException(422, "The additional products do not fit in the original order’s boxes.")
            # Added products accepted under earlier packing rules may no longer fit the
            # original cartons (wall allowance, full support, workbook capacities).
            result = pack_order(items, boxes, objective, zone, settings)
            if result["status"] != "complete":
                raise HTTPException(422, "The additional products do not fit in the original order’s boxes.")
            result["suggestions"].insert(0, {
                "type": "info",
                "title": "Order packed again with the added products",
                "detail": "The added products no longer fit in the original boxes with the current packing rules, so the whole order was packed again.",
            })
    result["product_recommendations"] = _fill_recommendations(
        db, order_id, items, boxes, result
    )
    db.execute(
        """
        INSERT INTO packing_plans
            (order_id, box_count, average_utilization, total_weight_kg,
             result_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            result["box_count"],
            result["average_utilization"],
            result["total_weight_kg"],
            json.dumps(result, ensure_ascii=False),
            now_iso(),
        ),
    )
    db.execute(
        "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
        ("packed" if result["status"] == "complete" else "attention", now_iso(), order_id),
    )
    return result


def _ensure_demo_plans() -> None:
    with connect() as db:
        for row in db.execute(
            "SELECT id FROM orders WHERE source_filename = 'orden-demo.pdf'"
        ).fetchall():
            exists = db.execute(
                "SELECT 1 FROM packing_plans WHERE order_id = ? LIMIT 1", (row["id"],)
            ).fetchone()
            if not exists:
                _run_plan(db, row["id"])


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Local uvicorn runs; Compose already injects these. .env.local wins over .env.
    for env_file in (PROJECT_ENV_LOCAL, PROJECT_ENV):
        load_env(env_file, prefix=("SLACK_", "CLERK_", "VITE_CLERK_", "AUTH_"))
    if problems := configuration_problems():
        logger.warning("Sign-in is disabled (every API call answers 503): %s. Set them in %s or the environment.",
                       "; ".join(problems), PROJECT_ENV)
    init_db()
    bootstrap_admins()
    _ensure_demo_plans()
    yield


app = FastAPI(
    title="PackFlow API",
    version="0.1.0",
    description="Purchase order extraction and 3D packing planning.",
    lifespan=lifespan,
    # Deny by default: every route needs a signed-in, active user (see auth.PUBLIC_PATHS).
    dependencies=[Depends(current_user)],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(requirements_router)
app.include_router(auth_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "database": database_engine()}


def _order_summary_rows(db: Database) -> list[dict[str, Any]]:
    return rows_to_dicts(
        db.execute(
            """
            SELECT
                o.*,
                COUNT(DISTINCT oi.id) AS line_count,
                COALESCE(SUM(oi.quantity), 0) AS unit_count,
                pp.box_count,
                pp.average_utilization,
                pp.total_weight_kg
            FROM orders o
            LEFT JOIN order_items oi ON oi.order_id = o.id
            LEFT JOIN packing_plans pp ON pp.id = (
                SELECT id FROM packing_plans
                WHERE order_id = o.id ORDER BY id DESC LIMIT 1
            )
            GROUP BY o.id, pp.id
            ORDER BY o.order_date DESC NULLS LAST, o.id DESC
            """
        ).fetchall()
    )


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    with connect() as db:
        orders = _order_summary_rows(db)
        completed = [order for order in orders if order["average_utilization"] is not None]
        return {
            "kpis": {
                "orders": len(orders),
                "ready": sum(order["status"] == "packed" for order in orders),
                "boxes": sum(order["box_count"] or 0 for order in orders),
                "average_utilization": round(
                    sum(order["average_utilization"] for order in completed) / len(completed), 1
                ) if completed else 0,
                "total_weight_kg": round(sum(order["total_weight_kg"] or 0 for order in orders), 1),
            },
            "orders": orders,
            "utilization_series": [
                {
                    "label": order["order_number"],
                    "value": order["average_utilization"] or 0,
                }
                for order in reversed(orders[:8])
            ],
        }


@app.get("/api/orders")
def list_orders() -> list[dict[str, Any]]:
    with connect() as db:
        return _order_summary_rows(db)


@app.delete("/api/orders", dependencies=[Depends(require_admin)])
def delete_all_orders() -> dict[str, int]:
    with connect() as db:
        # Foreign key cascades remove items, packing plans and saved PDFs atomically.
        count = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        db.execute("DELETE FROM orders")
        db.execute("INSERT INTO app_settings (key, value) VALUES ('demo_orders_seeded', '1') ON CONFLICT (key) DO UPDATE SET value = excluded.value")
    return {"deleted_orders": count}


@app.delete("/api/orders/{order_id}", dependencies=[Depends(require_admin)])
def delete_order(order_id: int) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT order_number FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Order not found.")
        # Foreign key cascades remove its items, packing plans and saved PDFs.
        db.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        # Deleting the last order by hand must not bring the demo orders back on restart.
        db.execute("INSERT INTO app_settings (key, value) VALUES ('demo_orders_seeded', '1') ON CONFLICT (key) DO UPDATE SET value = excluded.value")
    return {"deleted_order_id": order_id, "order_number": row["order_number"]}


@app.get("/api/orders/{order_id}")
def get_order(order_id: int) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Order not found.")
        order = dict(row)
        order["items"] = rows_to_dicts(
            db.execute(
                """
                SELECT
                    oi.*, p.sku, p.name AS product_name, p.weight_kg,
                    p.length_cm, p.width_cm, p.height_cm
                FROM order_items oi
                LEFT JOIN products p ON p.id = oi.product_id
                WHERE oi.order_id = ? ORDER BY oi.id
                """,
                (order_id,),
            ).fetchall()
        )
        order["source_available"] = bool(db.execute("SELECT 1 FROM source_documents WHERE order_id = ?", (order_id,)).fetchone())
        order["plan"] = latest_plan(db, order_id)
        order["client_requirements"] = requirements_note_for(db, order["client"])
        return order


@app.post("/api/orders/upload", status_code=201)
async def upload_order(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Upload a valid PDF file.")
    content = await file.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(413, "The PDF exceeds the 15 MB limit.")
    try:
        extracted = extract_pdf(content, file.filename)
    except Exception as exc:
        raise HTTPException(422, "The PDF could not be read. Check that it is not damaged or password-protected.") from exc
    with connect() as db:
        number = extracted["order_number"]
        existing = db.execute(
            """
            SELECT id, source_filename, status FROM orders
            WHERE order_number = ?
            """,
            (number,),
        ).fetchone()
        can_reprocess = bool(
            existing
            and existing["source_filename"] == file.filename
            and existing["status"] in {"needs_review", "ready"}
        )
        # Reading warnings stay visible on the order (for example, an ambiguous date or an unverified line)
        # so that nothing is accepted silently.
        notes = "\n".join(part for part in [
            extracted["notes"],
            ("Reading warnings: " + " ".join(extracted["warnings"])) if extracted.get("warnings") else "",
        ] if part)
        values = (
            extracted["client"], extracted["vendor"], extracted["order_date"],
            extracted["requested_date"], extracted["currency"], file.filename,
            extracted["delivery_address"], extracted["billing_address"],
            extracted["terms"], notes, extracted["subtotal"],
            extracted["tax_amount"], extracted["total_amount"],
            extracted["confidence"], now_iso(),
        )
        if can_reprocess:
            order_id = existing["id"]
            db.execute(
                """
                UPDATE orders SET
                    client = ?, vendor = ?, order_date = ?, requested_date = ?,
                    currency = ?, source_filename = ?, source_type = 'pdf',
                    delivery_address = ?, billing_address = ?, terms = ?, notes = ?,
                    subtotal = ?, tax_amount = ?, total_amount = ?,
                    status = 'needs_review', confidence = ?, updated_at = ?
                WHERE id = ?
                """,
                (*values, order_id),
            )
            db.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
        else:
            base = number
            suffix = 1
            while db.execute("SELECT 1 FROM orders WHERE order_number = ?", (number,)).fetchone():
                suffix += 1
                number = f"{base}-{suffix}"
            timestamp = now_iso()
            order_id = db.insert_returning_id(
                """
                INSERT INTO orders
                    (order_number, client, vendor, order_date, requested_date, currency,
                     source_filename, source_type, delivery_address, billing_address,
                     terms, notes, subtotal, tax_amount, total_amount,
                     status, confidence, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pdf', ?, ?, ?, ?, ?, ?, ?,
                        'needs_review', ?, ?, ?)
                RETURNING id
                """,
                (number, *values[:-1], timestamp, values[-1]),
            )
        db.execute(
            """INSERT INTO source_documents (order_id, filename, content) VALUES (?, ?, ?)
               ON CONFLICT (order_id) DO UPDATE SET filename = excluded.filename, content = excluded.content""",
            (order_id, file.filename, content),
        )
        for item in extracted["items"]:
            payload = OrderItemPayload(**item)
            _insert_order_item(db, order_id, payload, payload.line_number or 1)
    return get_order(order_id)


@app.post("/api/orders/email", status_code=201)
def create_email_order(payload: EmailOrderPayload) -> dict[str, Any]:
    extracted = extract_email(payload.text)
    if payload.order_number is not None and not payload.order_number.strip():
        raise HTTPException(422, "The order number cannot be empty.")
    if not extracted["items"]:
        raise HTTPException(422, "No lines with a product and quantity were found. For example, use: Magigoo Pro PP - 15 pcs.")
    # Use the existing order validation/insertion path; all unknown fields remain editable.
    review = OrderReviewPayload(
        order_number=payload.order_number or f"EMAIL-{uuid4().hex[:12].upper()}",
        client=payload.client.strip() or extracted["client"],
        notes=payload.text,
        items=[OrderItemPayload(**item) for item in extracted["items"]],
    )
    with connect() as db:
        order_id = _create_order(db, review, source_type="email", source_filename=payload.subject or None, confidence=extracted["confidence"])
    order = get_order(order_id)
    order["extraction_warnings"] = extracted["warnings"]
    return order


@app.post("/api/orders/manual", status_code=201)
def create_manual_order(payload: OrderReviewPayload) -> dict[str, Any]:
    with connect() as db:
        order_id = _create_order(db, payload)
    return get_order(order_id)


def _create_order(db: Database, payload: OrderReviewPayload, *, source_type: str = "manual",
                  source_filename: str | None = None, confidence: float = 1) -> int:
    if db.execute(
        "SELECT 1 FROM orders WHERE order_number = ?", (payload.order_number.strip(),)
    ).fetchone():
        raise HTTPException(409, "An order with this number already exists.")
    subtotal = payload.subtotal or round(
        sum(_line_amount(item) for item in payload.items), 2
    )
    total = payload.total_amount or round(subtotal + payload.tax_amount, 2)
    timestamp = now_iso()
    order_id = db.insert_returning_id(
        """
        INSERT INTO orders
            (order_number, client, vendor, order_date, requested_date, currency,
             source_filename, source_type, delivery_address, billing_address,
             terms, notes, subtotal, tax_amount, total_amount,
             status, confidence, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'needs_review', ?, ?, ?)
        RETURNING id
        """,
        (
            payload.order_number.strip(),
            payload.client.strip(),
            payload.vendor.strip(),
            payload.order_date,
            payload.requested_date,
            payload.currency.upper(),
            source_filename,
            source_type,
            payload.delivery_address,
            payload.billing_address,
            payload.terms,
            payload.notes,
            subtotal,
            payload.tax_amount,
            total,
            confidence,
            timestamp,
            timestamp,
        ),
    )
    for position, item in enumerate(payload.items, start=1):
        _insert_order_item(db, order_id, item, position)
    return order_id


@app.patch("/api/orders/{order_id}")
def review_order(order_id: int, payload: OrderReviewPayload) -> dict[str, Any]:
    with connect() as db:
        if not db.execute("SELECT 1 FROM orders WHERE id = ?", (order_id,)).fetchone():
            raise HTTPException(404, "Order not found.")
        try:
            db.execute(
                """
                UPDATE orders
                SET order_number = ?, client = ?, vendor = ?, order_date = ?,
                    requested_date = ?, currency = ?, delivery_address = ?,
                    billing_address = ?, terms = ?, notes = ?, subtotal = ?,
                    tax_amount = ?, total_amount = ?, status = 'ready',
                    confidence = 1, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.order_number.strip(),
                    payload.client.strip(),
                    payload.vendor.strip(),
                    payload.order_date,
                    payload.requested_date,
                    payload.currency.upper(),
                    payload.delivery_address,
                    payload.billing_address,
                    payload.terms,
                    payload.notes,
                    payload.subtotal
                    or round(sum(_line_amount(item) for item in payload.items), 2),
                    payload.tax_amount,
                    payload.total_amount
                    or round(
                        (payload.subtotal or sum(_line_amount(item) for item in payload.items))
                        + payload.tax_amount,
                        2,
                    ),
                    now_iso(),
                    order_id,
                ),
            )
        except IntegrityError as exc:
            raise HTTPException(409, "An order with this number already exists.") from exc
        db.execute("DELETE FROM packing_plans WHERE order_id = ?", (order_id,))
        db.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
        for position, item in enumerate(payload.items, start=1):
            _insert_order_item(db, order_id, item, position)
    return get_order(order_id)


@app.post("/api/orders/{order_id}/analyze")
def analyze_order(order_id: int, objective: str | None = None,
                  allowance_cm: float | None = None) -> dict[str, Any]:
    """Calculate the packing plan. ``objective``: fewest_boxes, lowest_dhl_cost or
    smallest_volume; ``allowance_cm``: wall clearance per side (0-5 cm). Omitted, the order
    keeps the values of its latest plan."""
    with connect() as db:
        if not db.execute("SELECT 1 FROM orders WHERE id = ?", (order_id,)).fetchone():
            raise HTTPException(404, "Order not found.")
        return _run_plan(db, order_id, objective, allowance_cm=allowance_cm)


@app.get("/api/orders/{order_id}/source.pdf")
def view_source_pdf(order_id: int) -> Response:
    with connect() as db:
        row = db.execute("SELECT filename, content FROM source_documents WHERE order_id = ?", (order_id,)).fetchone()
    if not row:
        raise HTTPException(404, "The original PDF is not available for this order. PDFs uploaded before source retention cannot be viewed.")
    filename = re.sub(r"[^A-Za-z0-9_.-]", "_", row["filename"])
    return Response(row["content"], media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{filename}"', "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


def _check_expected_plan(db: Database, order_id: int, expected: int | None) -> dict[str, Any] | None:
    plan = latest_plan(db, order_id)
    if expected is not None and (not plan or plan["id"] != expected):
        raise HTTPException(409, "The packing plan changed. Refresh and preview the addition again.")
    return plan


def _append_suggested_item(db: Database, order_id: int, source: Any, quantity: int) -> None:
    amount = round(quantity * source["unit_price"], 2)
    existing = db.execute(
        """
        SELECT id, quantity FROM order_items
        WHERE order_id = ? AND is_suggested = 1 AND product_id = ?
          AND description = ? AND unit_price = ?
        """,
        (order_id, source["product_id"], source["description"], source["unit_price"]),
    ).fetchone()
    if existing:
        quantity = existing["quantity"] + quantity
        db.execute(
            "UPDATE order_items SET quantity = ?, amount = ? WHERE id = ?",
            (quantity, round(quantity * source["unit_price"], 2), existing["id"]),
        )
    else:
        line_number = db.execute(
            "SELECT COALESCE(MAX(line_number), 0) + 1 FROM order_items WHERE order_id = ?",
            (order_id,),
        ).fetchone()[0]
        db.execute(
            """
            INSERT INTO order_items
                (order_id, product_id, sku_raw, description, quantity, unit_price,
                 line_number, requested_date, tax_label, discount_percent, amount,
                 is_suggested)
            VALUES (?, ?, '', ?, ?, ?, ?, ?, '', 0, ?, 1)
            """,
            (
                order_id, source["product_id"], source["description"], quantity,
                source["unit_price"], line_number, source["requested_date"], amount,
            ),
        )
    _refresh_order_totals(db, order_id)


@app.post("/api/orders/{order_id}/recommendations/preview")
def preview_recommendation(order_id: int, payload: SuggestedItemPayload) -> dict[str, Any]:
    with connect() as db:
        plan = _check_expected_plan(db, order_id, payload.expected_plan_id)
        if not plan or plan["result"]["status"] != "complete":
            raise HTTPException(422, "Calculate a complete plan before previewing an addition.")
        items = _packing_rows(db, order_id)
        source = next((item for item in items if item["order_item_id"] == payload.source_item_id and not item["is_suggested"]), None)
        if not source:
            raise HTTPException(404, "This recommendation is no longer available.")
        before = plan["result"]
        current_total = db.execute("SELECT total_amount FROM orders WHERE id = ?", (order_id,)).fetchone()[0]
        if not current_total:
            # Match the existing UI fallback for legacy/demo orders with zero totals.
            current_total = db.execute("SELECT COALESCE(SUM(quantity * unit_price), 0) FROM order_items WHERE order_id = ?", (order_id,)).fetchone()[0]
        source_row = db.execute("SELECT * FROM order_items WHERE id = ? AND order_id = ?", (payload.source_item_id, order_id)).fetchone()
        # Use the exact apply path, then roll back all rows/totals/plans. This also
        # handles additions merged into an existing suggested line identically.
        db.execute("SAVEPOINT recommendation_preview")
        try:
            _append_suggested_item(db, order_id, source_row, payload.quantity)
            projected = _run_plan(db, order_id)
            projected_total = db.execute("SELECT total_amount FROM orders WHERE id = ?", (order_id,)).fetchone()[0]
        finally:
            db.execute("ROLLBACK TO recommendation_preview")
            db.execute("RELEASE recommendation_preview")
        if projected["status"] != "complete":
            raise HTTPException(422, "These units do not fit in the current boxes. Refresh the recommendations.")
        if [(b["box_type_id"], b["dimensions_cm"]) for b in projected["boxes"]] != [(b["box_type_id"], b["dimensions_cm"]) for b in before["boxes"]]:
            raise HTTPException(409, "The catalog changed. Recalculate before previewing an addition.")
        changes = []
        for current, trial in zip(before["boxes"], projected["boxes"], strict=True):
            added = len(trial["placements"]) - len(current["placements"])
            if added:
                changes.append({"sequence": current["sequence"], "box_code": current["box_code"], "quantity": added,
                                "current_utilization": current["utilization"], "projected_utilization": trial["utilization"]})
        return {"plan_id": plan["id"], "source_item_id": payload.source_item_id, "quantity": payload.quantity,
                "description": source["description"], "amount": round(source["unit_price"] * payload.quantity, 2),
                "current_order_total": current_total, "projected_order_total": projected_total,
                "requires_pricing": source["unit_price"] == 0, "current_utilization": before["average_utilization"],
                "projected_utilization": projected["average_utilization"], "boxes": changes,
                "result": projected}


@app.post("/api/orders/{order_id}/suggested-items", status_code=201)
def add_suggested_item(order_id: int, payload: SuggestedItemPayload) -> dict[str, Any]:
    with connect() as db:
        _check_expected_plan(db, order_id, payload.expected_plan_id)
        source = db.execute(
            """
            SELECT oi.*, p.name AS product_name
            FROM order_items oi
            JOIN products p ON p.id = oi.product_id
            WHERE oi.id = ? AND oi.order_id = ? AND oi.is_suggested = 0
            """,
            (payload.source_item_id, order_id),
        ).fetchone()
        if not source:
            raise HTTPException(404, "This recommendation is no longer available.")
        _append_suggested_item(db, order_id, source, payload.quantity)
        # A new addition must fit the existing boxes; otherwise nothing is saved.
        _run_plan(db, order_id, repack_additions=False)
    return get_order(order_id)


@app.delete("/api/orders/{order_id}/suggested-items/{item_id}")
def remove_suggested_item(order_id: int, item_id: int, quantity: int | None = Query(default=None, gt=0)) -> dict[str, Any]:
    if not isinstance(quantity, int):
        quantity = None
    with connect() as db:
        item = db.execute(
            "SELECT id, quantity, unit_price FROM order_items WHERE id = ? AND order_id = ? AND is_suggested = 1",
            (item_id, order_id),
        ).fetchone()
        if not item:
            raise HTTPException(404, "The added product does not exist or belongs to the original order.")
        if quantity is not None and (quantity <= 0 or quantity > item["quantity"]):
            raise HTTPException(422, "The quantity exceeds the added units.")
        remaining = item["quantity"] - (quantity if quantity is not None else item["quantity"])
        if remaining:
            db.execute("UPDATE order_items SET quantity = ?, amount = ? WHERE id = ?", (remaining, round(remaining * item["unit_price"], 2), item_id))
        else:
            db.execute("DELETE FROM order_items WHERE id = ?", (item_id,))
        _refresh_order_totals(db, order_id)
        _run_plan(db, order_id)
    return get_order(order_id)


def _save_analysis_report(order_id: int) -> dict[str, Any]:
    order = get_order(order_id)
    if not order.get("plan") or order["plan"]["result"]["status"] != "complete":
        raise HTTPException(422, "Calculate a complete packing plan before saving the final analysis.")
    plan_id = order["plan"]["id"]
    safe_number = re.sub(r"[^A-Za-z0-9_-]", "", order["order_number"]) or str(order_id)
    filename = f"plan-{safe_number}.pdf"
    with connect() as db:
        row = db.execute("SELECT id, filename, created_at FROM analysis_reports WHERE plan_id = ?", (plan_id,)).fetchone()
        if row is None:
            content = render_analysis_pdf(order)
            db.execute("INSERT INTO analysis_reports (order_id, plan_id, filename, pdf_content, created_at) VALUES (?, ?, ?, ?, ?) "
                       "ON CONFLICT (plan_id) DO NOTHING",
                       (order_id, plan_id, filename, content, now_iso()))
            row = db.execute("SELECT id, filename, created_at FROM analysis_reports WHERE plan_id = ?", (plan_id,)).fetchone()
    return {**dict(row), "order_id": order_id, "plan_id": plan_id,
            "download_url": f"/api/orders/{order_id}/reports/{row['id']}/pdf"}


@app.post("/api/orders/{order_id}/reports", status_code=201)
def save_analysis_report(order_id: int) -> dict[str, Any]:
    """Save & Finish: store the final PDF and tell the packing team on Slack."""
    report = _save_analysis_report(order_id)
    return {**report, "slack_notification": notify_plan_ready(get_order(order_id))}


@app.get("/api/orders/{order_id}/reports")
def list_analysis_reports(order_id: int) -> list[dict[str, Any]]:
    get_order(order_id)
    with connect() as db:
        rows = db.execute("SELECT id, plan_id, filename, created_at FROM analysis_reports WHERE order_id = ? ORDER BY id DESC", (order_id,)).fetchall()
    return [{**dict(row), "download_url": f"/api/orders/{order_id}/reports/{row['id']}/pdf"} for row in rows]


@app.get("/api/orders/{order_id}/reports/{report_id}/pdf")
def download_analysis_report(order_id: int, report_id: int) -> Response:
    with connect() as db:
        row = db.execute("SELECT filename, pdf_content FROM analysis_reports WHERE id = ? AND order_id = ?", (report_id, order_id)).fetchone()
    if row is None:
        raise HTTPException(404, "Report not found.")
    return Response(content=row["pdf_content"], media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"'})


@app.get("/api/orders/{order_id}/export.pdf")
def export_order_pdf(order_id: int) -> Response:
    report = _save_analysis_report(order_id)
    return download_analysis_report(order_id, report["id"])


class ExporterPayload(BaseModel):
    company: str = Field(min_length=1)
    address: str = ""
    phone: str = ""
    contact: str = ""
    country_of_origin: str = ""
    packing_info: str = ""
    signatory_name: str = ""
    signatory_title: str = ""


def _load_exporter(db: Database) -> dict[str, str]:
    row = db.execute("SELECT value FROM app_settings WHERE key = 'exporter_profile'").fetchone()
    stored = json.loads(row["value"]) if row else {}
    return {**DEFAULT_EXPORTER, **{k: v for k, v in stored.items() if k in DEFAULT_EXPORTER}}


@app.get("/api/settings/exporter")
def get_exporter() -> dict[str, str]:
    with connect() as db:
        return _load_exporter(db)


@app.put("/api/settings/exporter", dependencies=[Depends(require_admin)])
def update_exporter(payload: ExporterPayload) -> dict[str, str]:
    with connect() as db:
        db.execute("INSERT INTO app_settings (key, value) VALUES ('exporter_profile', ?) "
                   "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                   (json.dumps(payload.model_dump()),))
        return _load_exporter(db)


def _shipment_for(order_id: int) -> dict[str, Any]:
    """Order + complete plan + exporter, consignee, destination and dispatch for shipping documents."""
    order = get_order(order_id)
    if not order.get("plan") or order["plan"]["result"]["status"] != "complete":
        raise HTTPException(422, "Calculate a complete packing plan before generating shipping documents.")
    with connect() as db:
        exporter = _load_exporter(db)
        client = db.execute("SELECT * FROM clients WHERE lower(trim(customer)) = lower(trim(?))",
                            (order["client"] or "",)).fetchone()
        destination = _detect_order_destination(db, order_id)
        quote = get_shipping_quote(db, order_id)
    client = dict(client) if client else {}
    address = (order.get("delivery_address") or client.get("shipping_address")
               or client.get("address") or "")
    country = (destination or {}).get("name") or client.get("country") or ""
    if country and country.lower() not in address.lower():
        address = f"{address}\n{country}".strip()
    consignee = {
        "name": order.get("client") or client.get("customer") or "",
        "address": address,
        "contact": client.get("contact_name", ""),
        "phone": client.get("phone_number", ""),
        "email": client.get("email", ""),
    }
    dispatch = ""
    if quote:
        service = dhl_pricing.SERVICES.get(quote["inputs"].get("service", "express_worldwide"), "Express")
        dispatch = f"Courier - DHL {service}"
    return build_shipment(order, exporter=exporter, consignee=consignee,
                          destination=country, dispatch=dispatch)


def _safe_number(order_number: str, fallback: int) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", order_number) or str(fallback)


@app.get("/api/orders/{order_id}/packing-list.pdf")
def export_packing_list(order_id: int) -> Response:
    shipment = _shipment_for(order_id)
    content = render_packing_list_pdf(shipment)
    filename = f"packing-list-{_safe_number(shipment['order_number'], order_id)}.pdf"
    return Response(content=content, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/orders/{order_id}/labels.pdf")
def export_box_labels(order_id: int, box: int | None = Query(default=None, ge=1)) -> Response:
    shipment = _shipment_for(order_id)
    try:
        content = render_box_labels_pdf(shipment, box)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    suffix = f"-box{box}" if box else ""
    filename = f"labels-{_safe_number(shipment['order_number'], order_id)}{suffix}.pdf"
    return Response(content=content, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# --------------------------------------------------------------------------- TSCA certification


class TscaItemPayload(BaseModel):
    product_id: int | None = None
    number: str = Field(default="", max_length=tsca.LIMITS["number"])
    name: str = Field(default="", max_length=tsca.LIMITS["name"])
    description: str = Field(default="", max_length=tsca.LIMITS["description"])


class TscaDraftPayload(BaseModel):
    date: str = Field(default="", max_length=tsca.LIMITS["date"])
    reference: str = Field(default="", max_length=tsca.LIMITS["reference"])
    certification: str = ""
    items: list[TscaItemPayload] = Field(default_factory=list, max_length=tsca.LIMITS["items"])


class TscaSavePayload(BaseModel):
    draft: TscaDraftPayload
    # The user's statement that they checked everything against their compliance records.
    confirmed: bool = False
    # Catalog products whose number (EAN), name and description should be remembered.
    remember_product_ids: list[int] = Field(default_factory=list)


def _order_destination(db: Database, order_id: int) -> dict[str, Any] | None:
    """Shipment destination: the saved DHL quote's destination, else the one detected from the order."""
    quote = get_shipping_quote(db, order_id)
    code = ((quote or {}).get("inputs") or {}).get("destination")
    if code:
        try:
            return dhl_pricing.zone_for(str(code))
        except (dhl_pricing.PricingError, KeyError, TypeError, ValueError):
            pass
    return _detect_order_destination(db, order_id)


def _tsca_state(db: Database, order_id: int) -> dict[str, Any]:
    order = db.execute("SELECT id, order_number FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None:
        raise HTTPException(404, "Order not found.")
    destination = _order_destination(db, order_id)
    saved = db.execute("SELECT draft_json, confirmed_at, updated_at FROM tsca_declarations WHERE order_id = ?",
                       (order_id,)).fetchone()
    if saved:
        draft = tsca.normalize_draft(json.loads(saved["draft_json"]))
        ids = [item["product_id"] for item in draft["items"] if item["product_id"] is not None]
        if ids:
            rows = db.execute(
                f"SELECT id, name, ean, tsca_name, tsca_description FROM products WHERE id IN ({', '.join('?' * len(ids))})",
                tuple(ids)).fetchall()
            tsca.fill_from_catalog(draft, {row["id"]: dict(row) for row in rows})
    else:
        lines = rows_to_dicts(db.execute(
            """SELECT oi.product_id, oi.sku_raw, oi.description, oi.quantity, p.sku,
                      p.name AS product_name, p.ean, p.tsca_name, p.tsca_description
               FROM order_items oi LEFT JOIN products p ON p.id = oi.product_id
               WHERE oi.order_id = ? ORDER BY oi.id""", (order_id,)).fetchall())
        draft = tsca.default_draft(dict(order), lines)
    return {
        "eligible": bool(destination and destination.get("code") in tsca.TSCA_DESTINATIONS),
        "destination": {"code": destination["code"], "name": destination["name"]} if destination else None,
        "saved": bool(saved),
        "confirmed": bool(saved and saved["confirmed_at"]),
        "updated_at": saved["updated_at"] if saved else None,
        "draft": draft,
        "issues": tsca.draft_issues(draft),
    }


def _require_tsca_eligible(state: dict[str, Any]) -> None:
    if not state["eligible"]:
        where = state["destination"]["name"] if state["destination"] else "an unknown destination"
        raise HTTPException(422, f"The TSCA certification is only for shipments to the United States. "
                                 f"This order ships to {where}.")


@app.get("/api/orders/{order_id}/tsca")
def get_tsca(order_id: int) -> dict[str, Any]:
    with connect() as db:
        return _tsca_state(db, order_id)


@app.put("/api/orders/{order_id}/tsca")
def save_tsca(order_id: int, payload: TscaSavePayload) -> dict[str, Any]:
    try:
        draft = tsca.normalize_draft(payload.draft.model_dump())
    except tsca.TscaError as exc:
        raise HTTPException(422, str(exc)) from exc
    issues = tsca.draft_issues(draft)
    if payload.confirmed and issues:
        raise HTTPException(422, " ".join(issues))
    with connect() as db:
        _require_tsca_eligible(_tsca_state(db, order_id))
        stamp = now_iso()
        db.execute(
            """INSERT INTO tsca_declarations (order_id, draft_json, confirmed_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (order_id) DO UPDATE SET draft_json = excluded.draft_json,
                   confirmed_at = excluded.confirmed_at, updated_at = excluded.updated_at""",
            (order_id, json.dumps(draft), stamp if payload.confirmed else None, stamp, stamp),
        )
        remember = set(payload.remember_product_ids)
        for item in draft["items"]:
            if item["product_id"] in remember:
                db.execute(
                    """UPDATE products SET tsca_name = ?, tsca_description = ?,
                           ean = CASE WHEN ? <> '' THEN ? ELSE ean END WHERE id = ?""",
                    (item["name"], item["description"], *(2 * [item["number"] if tsca.looks_like_ean(item["number"]) else ""]),
                     item["product_id"]),
                )
        return _tsca_state(db, order_id)


class TscaPreviewPayload(BaseModel):
    draft: TscaDraftPayload
    confirmed: bool = False


@app.post("/api/orders/{order_id}/tsca/preview.pdf")
def preview_tsca(order_id: int, payload: TscaPreviewPayload) -> Response:
    """Render the unsaved draft for the in-dialog viewer. Nothing is stored. It looks final only
    when the draft is complete and the user ticked the review; otherwise it is marked DRAFT."""
    with connect() as db:
        _require_tsca_eligible(_tsca_state(db, order_id))
    try:
        draft = tsca.normalize_draft(payload.draft.model_dump())
        content = tsca.render_tsca_pdf(draft, final=payload.confirmed and not tsca.draft_issues(draft))
    except tsca.TscaError as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(content=content, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="TSCA_Certification_preview.pdf"',
                             "Cache-Control": "no-store"})


@app.get("/api/orders/{order_id}/tsca.pdf")
def export_tsca(order_id: int, final: bool = Query(default=False)) -> Response:
    with connect() as db:
        state = _tsca_state(db, order_id)
        number = db.execute("SELECT order_number FROM orders WHERE id = ?", (order_id,)).fetchone()["order_number"]
    _require_tsca_eligible(state)
    if final and state["issues"]:
        raise HTTPException(422, " ".join(state["issues"]))
    if final and not state["confirmed"]:
        raise HTTPException(422, "Save the declaration with the final review confirmed before downloading the final PDF.")
    try:
        content = tsca.render_tsca_pdf(state["draft"], final=final)
    except tsca.TscaError as exc:
        raise HTTPException(422, str(exc)) from exc
    filename = f"TSCA_Certification_{_safe_number(number, order_id)}{'' if final else '_DRAFT'}.pdf"
    return Response(content=content, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/orders/{order_id}/export")
def export_order(order_id: int) -> StreamingResponse:
    order = get_order(order_id)
    if not order.get("plan"):
        raise HTTPException(422, "Calculate a packing plan before exporting it.")
    result = order["plan"]["result"]
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    navy = "0A1725"
    cyan = "19D3F3"
    pale = "DCE8ED"
    summary.append(["PACKFLOW", "Packing plan"])
    summary.append(["Order", order["order_number"]])
    summary.append(["Customer", order["client"]])
    summary.append(["Supplier", order["vendor"]])
    summary.append(["Date", order["order_date"]])
    summary.append(["Requested date", order["requested_date"]])
    summary.append(["Currency", order["currency"]])
    summary.append(["Terms", order["terms"]])
    summary.append(["Delivery address", order["delivery_address"]])
    summary.append(["Total", order["total_amount"]])
    summary.append(["Boxes", result["box_count"]])
    summary.append(["Units", result["unit_count"]])
    summary.append(["Total utilization", result["average_utilization"] / 100])
    summary.append(["Total gross weight (kg)", result["total_weight_kg"]])
    summary.append(["Method", result["method"]])
    summary["A1"].font = Font(bold=True, color=navy, size=18)
    summary["B1"].font = Font(bold=True, color=navy, size=14)
    summary["B12"].number_format = "0.0%"
    summary.column_dimensions["A"].width = 24
    summary.column_dimensions["B"].width = 54

    boxes_sheet = workbook.create_sheet("Boxes")
    boxes_sheet.append([
        "Box", "Type", "Length (cm)", "Width (cm)", "Height (cm)",
        "Utilization", "Gross weight (kg)", "Max. weight (kg)", "Available space (L)",
    ])
    for cell in boxes_sheet[1]:
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.font = Font(bold=True, color=pale)
        cell.alignment = Alignment(horizontal="center")
    for box in result["boxes"]:
        length, width, height = box["dimensions_cm"]
        boxes_sheet.append([
            box["sequence"], box["box_code"], length, width, height,
            box["utilization"] / 100, box["gross_weight_kg"],
            box["max_weight_kg"], box["remaining_volume_l"],
        ])
    for cell in boxes_sheet["F"][1:]:
        cell.number_format = "0.0%"
    boxes_sheet.freeze_panes = "A2"
    boxes_sheet.auto_filter.ref = boxes_sheet.dimensions
    for column in boxes_sheet.columns:
        letter = column[0].column_letter
        boxes_sheet.column_dimensions[letter].width = max(13, min(24, max(len(str(cell.value or "")) for cell in column) + 2))

    contents = workbook.create_sheet("Contents")
    contents.append([
        "Box", "Product", "Quantity", "Unit price",
        "Discount", "Tax", "Requested date", "Amount",
    ])
    for cell in contents[1]:
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.font = Font(bold=True, color=pale)
    item_details = {str(item["product_id"]): item for item in order["items"] if item["product_id"]}
    for box in result["boxes"]:
        for item in box["items"]:
            detail = item_details.get(item["sku"], {})
            contents.append([
                box["sequence"], item["name"], item["quantity"],
                detail.get("unit_price", 0), detail.get("discount_percent", 0) / 100,
                detail.get("tax_label", ""), detail.get("requested_date"),
                detail.get("amount", 0),
            ])
    contents.freeze_panes = "A2"
    contents.auto_filter.ref = contents.dimensions
    contents.column_dimensions["A"].width = 10
    contents.column_dimensions["B"].width = 38
    contents.column_dimensions["C"].width = 12
    for cell in contents["E"][1:]:
        cell.number_format = "0.0%"

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    safe_number = "".join(character for character in order["order_number"] if character.isalnum() or character in "-_")
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="plan-{safe_number}.xlsx"'},
    )


def _pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader  # noqa: PLC0415

        return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
    except Exception:  # A damaged source PDF must not break the order page.
        return ""


def _detect_order_destination(db: Database, order_id: int) -> dict[str, Any] | None:
    """Destination country from the PO: delivery address first, then the source document, then the client."""
    order = db.execute(
        "SELECT client, delivery_address, billing_address, notes FROM orders WHERE id = ?", (order_id,)
    ).fetchone()
    document = db.execute("SELECT content FROM source_documents WHERE order_id = ?", (order_id,)).fetchone()
    client = db.execute(
        "SELECT country, shipping_address FROM clients WHERE lower(trim(customer)) = lower(trim(?))",
        (order["client"] or "",),
    ).fetchone()
    sources = [
        ("Order delivery address", order["delivery_address"]),
        ("PO document", _pdf_text(document["content"]) if document else ""),
        ("Order email / notes", order["notes"]),
        ("Client country", client["country"] if client else ""),
        ("Client shipping address", client["shipping_address"] if client else ""),
        ("Order billing address", order["billing_address"]),
    ]
    found = detect_destination(sources)
    if not found:
        return None
    return {**dhl_pricing.zone_for(found["code"]), "source": found["source"]}


@app.get("/api/shipping/dhl")
def get_dhl_config() -> dict[str, Any]:
    tariff = dhl_pricing.load_tariff()
    surcharges = load_surcharges()
    regions = surcharges["demand"]["regions"]
    return {
        "meta": tariff["meta"],
        "services": [{"id": key, "label": label, "premium_eur": tariff["export"]["premium"][key]}
                     for key, label in dhl_pricing.SERVICES.items()],
        "destinations": [{**entry, "demand_region": regions.get(entry["code"], "rest_of_world")}
                         for entry in tariff["zones"]],
        "surcharges": {
            "fuel": surcharges["fuel"],
            "demand": {key: value for key, value in surcharges["demand"].items() if key != "regions"},
            "region_labels": REGION_LABELS,
            "defaults": surcharges["defaults"],
        },
    }


@app.get("/api/orders/{order_id}/shipping-quote")
def get_order_shipping_quote(order_id: int) -> dict[str, Any]:
    with connect() as db:
        if not db.execute("SELECT 1 FROM orders WHERE id = ?", (order_id,)).fetchone():
            raise HTTPException(404, "Order not found.")
        plan = latest_plan(db, order_id)
        quote = get_shipping_quote(db, order_id)
        detected = _detect_order_destination(db, order_id)
    return {
        "quote": quote,
        "stale": bool(quote and (not plan or quote["plan_id"] != plan["id"])),
        "detected_destination": detected,
    }


@app.post("/api/orders/{order_id}/shipping-quote")
def create_order_shipping_quote(order_id: int, payload: ShippingQuotePayload) -> dict[str, Any]:
    inputs = payload.model_dump()
    with connect() as db:
        if not db.execute("SELECT 1 FROM orders WHERE id = ?", (order_id,)).fetchone():
            raise HTTPException(404, "Order not found.")
        plan = latest_plan(db, order_id)
        boxes = (plan or {}).get("result", {}).get("boxes") or []
        if not boxes:
            raise HTTPException(422, "Analyze the order first: a packing plan with boxes is required for a DHL quote.")
        try:
            result = dhl_pricing.quote(boxes, inputs)
        except dhl_pricing.PricingError as exc:
            raise HTTPException(422, str(exc)) from exc
        quote = save_shipping_quote(db, order_id, plan["id"], inputs, result)
    return {"quote": quote, "stale": False, "detected_destination": None}


@app.get("/api/catalog/products")
def list_products() -> list[dict[str, Any]]:
    with connect() as db:
        return rows_to_dicts(db.execute("SELECT * FROM products ORDER BY name").fetchall())


@app.get("/api/catalog/boxes")
def list_boxes() -> list[dict[str, Any]]:
    with connect() as db:
        return rows_to_dicts(db.execute("SELECT * FROM box_types ORDER BY code").fetchall())


@app.post("/api/catalog/products", status_code=201, dependencies=[Depends(require_admin)])
def create_product(payload: ProductPayload) -> dict[str, Any]:
    with connect() as db:
        existing = _find_product_by_description(db, payload.name)
        if existing and _normalize_product_name(existing["name"]) == _normalize_product_name(payload.name):
            raise HTTPException(409, "A product with this name already exists.")
        try:
            product_id = db.insert_returning_id(
                """
                INSERT INTO products
                    (sku, name, length_cm, width_cm, height_cm, weight_kg,
                     rotation_allowed, fragile, capacity_family, ean, tsca_name, tsca_description,
                     active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                RETURNING id
                """,
                (
                    _internal_product_code(payload.name), payload.name, payload.length_cm,
                    payload.width_cm, payload.height_cm, payload.weight_kg,
                    int(payload.rotation_allowed), int(payload.fragile),
                    payload.capacity_family if payload.capacity_family is not None else capacity_family_for(
                        payload.name, (payload.length_cm, payload.width_cm, payload.height_cm)),
                    (payload.ean or "").strip(), (payload.tsca_name or "").strip(),
                    (payload.tsca_description or "").strip(), now_iso(),
                ),
            )
        except IntegrityError as exc:
            raise HTTPException(409, "A product with this name already exists.") from exc
        apply_tsca_catalog(db)  # A known price-list product gets its EAN; typed values are kept.
        return dict(db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone())


@app.post("/api/catalog/boxes", status_code=201, dependencies=[Depends(require_admin)])
def create_box(payload: BoxPayload) -> dict[str, Any]:
    with connect() as db:
        try:
            box_id = db.insert_returning_id(
                """
                INSERT INTO box_types
                    (code, name, length_cm, width_cm, height_cm,
                     empty_weight_kg, max_weight_kg, capacities, reference_50ml, reference_250ml,
                     active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                RETURNING id
                """,
                (
                    payload.code.upper(), payload.name, payload.length_cm,
                    payload.width_cm, payload.height_cm, payload.empty_weight_kg,
                    payload.max_weight_kg, json.dumps(payload.capacities or {}),
                    payload.reference_50ml, payload.reference_250ml, now_iso(),
                ),
            )
        except IntegrityError as exc:
            raise HTTPException(409, "This box code already exists.") from exc
        return dict(db.execute("SELECT * FROM box_types WHERE id = ?", (box_id,)).fetchone())


@app.put("/api/catalog/products/{product_id}", dependencies=[Depends(require_admin)])
def update_product(product_id: int, payload: ProductPayload) -> dict[str, Any]:
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "The product name is required.")
    with connect() as db:
        if db.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone() is None:
            raise HTTPException(404, "Product not found.")
        normalized = _normalize_product_name(name)
        for other in db.execute("SELECT id, name FROM products WHERE id != ?", (product_id,)):
            if _normalize_product_name(other["name"]) == normalized:
                raise HTTPException(409, "A product with this name already exists.")
        # The SKU stays the same so order lines and saved plans keep pointing at this product.
        db.execute(
            """
            UPDATE products SET name = ?, length_cm = ?, width_cm = ?, height_cm = ?,
                weight_kg = ?, rotation_allowed = ?, fragile = ?,
                capacity_family = COALESCE(?, capacity_family),
                ean = COALESCE(?, ean), tsca_name = COALESCE(?, tsca_name),
                tsca_description = COALESCE(?, tsca_description)
            WHERE id = ?
            """,
            (
                name, payload.length_cm, payload.width_cm, payload.height_cm,
                payload.weight_kg, int(payload.rotation_allowed),
                int(payload.fragile), payload.capacity_family,
                *(value.strip() if value is not None else None
                  for value in (payload.ean, payload.tsca_name, payload.tsca_description)),
                product_id,
            ),
        )
        return dict(db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone())


@app.put("/api/catalog/boxes/{box_id}", dependencies=[Depends(require_admin)])
def update_box(box_id: int, payload: BoxPayload) -> dict[str, Any]:
    code, name = payload.code.strip().upper(), payload.name.strip()
    if not code or not name:
        raise HTTPException(422, "The box code and name are required.")
    with connect() as db:
        if db.execute("SELECT 1 FROM box_types WHERE id = ?", (box_id,)).fetchone() is None:
            raise HTTPException(404, "Box type not found.")
        try:
            db.execute(
                """
                UPDATE box_types SET code = ?, name = ?, length_cm = ?, width_cm = ?,
                    height_cm = ?, empty_weight_kg = ?, max_weight_kg = ?,
                    capacities = COALESCE(?, capacities),
                    reference_50ml = ?, reference_250ml = ?
                WHERE id = ?
                """,
                (
                    code, name, payload.length_cm, payload.width_cm, payload.height_cm,
                    payload.empty_weight_kg, payload.max_weight_kg,
                    json.dumps(payload.capacities) if payload.capacities is not None else None,
                    payload.reference_50ml, payload.reference_250ml, box_id,
                ),
            )
        except IntegrityError as exc:
            raise HTTPException(409, "This box code already exists.") from exc
        return dict(db.execute("SELECT * FROM box_types WHERE id = ?", (box_id,)).fetchone())


@app.delete("/api/catalog/products/{product_id}", dependencies=[Depends(require_admin)])
def delete_product(product_id: int) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT name FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Product not found.")
        # Order lines keep their description and become unmatched; saved plans are snapshots.
        db.execute("UPDATE order_items SET product_id = NULL WHERE product_id = ?", (product_id,))
        db.execute("DELETE FROM products WHERE id = ?", (product_id,))
    return {"deleted_product_id": product_id, "name": row["name"]}


@app.delete("/api/catalog/boxes/{box_id}", dependencies=[Depends(require_admin)])
def delete_box(box_id: int) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT code FROM box_types WHERE id = ?", (box_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Box type not found.")
        # Saved packing plans store their box dimensions, so they remain readable.
        db.execute("DELETE FROM box_types WHERE id = ?", (box_id,))
    return {"deleted_box_id": box_id, "code": row["code"]}


def _upsert_product_record(db: Database, record: dict[str, Any]) -> None:
    family = record.get("capacity_family") or capacity_family_for(
        str(record["name"]), (record["length_cm"], record["width_cm"], record["height_cm"]))
    product = _find_product_by_description(db, str(record["name"]))
    is_same_name = bool(
        product
        and _normalize_product_name(product["name"])
        == _normalize_product_name(str(record["name"]))
    )
    if is_same_name:
        db.execute(
            """
            UPDATE products SET name=?, length_cm=?, width_cm=?, height_cm=?,
                weight_kg=?, capacity_family=CASE WHEN ? = '' THEN capacity_family ELSE ? END,
                active=1 WHERE id=?
            """,
            (
                record["name"], record["length_cm"], record["width_cm"],
                record["height_cm"], record["weight_kg"], family, family, product["id"],
            ),
        )
        return
    db.execute(
        """
        INSERT INTO products
            (sku, name, length_cm, width_cm, height_cm, weight_kg,
             rotation_allowed, fragile, capacity_family, active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, 1, ?)
        """,
        (
            _internal_product_code(str(record["name"])), record["name"],
            record["length_cm"], record["width_cm"], record["height_cm"],
            record["weight_kg"], family, now_iso(),
        ),
    )


def _upsert_box_record(db: Database, record: dict[str, Any]) -> None:
    db.execute(
        """
        INSERT INTO box_types
            (code, name, length_cm, width_cm, height_cm,
             empty_weight_kg, max_weight_kg, capacities, active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(code) DO UPDATE SET
            name=excluded.name, length_cm=excluded.length_cm,
            width_cm=excluded.width_cm, height_cm=excluded.height_cm,
            empty_weight_kg=excluded.empty_weight_kg,
            max_weight_kg=excluded.max_weight_kg,
            capacities=CASE WHEN excluded.capacities = '{}' THEN box_types.capacities ELSE excluded.capacities END,
            active=1
        """,
        (
            str(record["code"]).upper(), record["name"], record["length_cm"],
            record["width_cm"], record["height_cm"], record["empty_weight_kg"],
            record["max_weight_kg"], json.dumps(record.get("capacities") or {}), now_iso(),
        ),
    )


@app.post("/api/catalog/{catalog_type}/import", dependencies=[Depends(require_admin)])
async def import_catalog(catalog_type: str, file: UploadFile = File(...)) -> dict[str, Any]:
    if catalog_type not in {"products", "boxes"}:
        raise HTTPException(404, "Unknown catalog.")
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Upload an Excel .xlsx file.")
    content = await file.read()
    reference = parse_reference_workbook(content)
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    if not reference:
        records, errors = parse_catalog_excel(content, catalog_type)
    imported = 0
    imported_products = 0
    imported_boxes = 0
    with connect() as db:
        if reference:
            for record in reference["products"]:
                try:
                    with db.savepoint():
                        _upsert_product_record(db, record)
                    imported += 1
                    imported_products += 1
                except DatabaseError as exc:
                    errors.append(f"Could not import product {record.get('name')}: {exc}")
            for record in reference["boxes"]:
                try:
                    with db.savepoint():
                        _upsert_box_record(db, record)
                    imported += 1
                    imported_boxes += 1
                except DatabaseError as exc:
                    errors.append(f"Could not import box {record.get('name')}: {exc}")
            for order in db.execute("SELECT id FROM orders").fetchall():
                _relink_order_items(db, order["id"])
            apply_tsca_catalog(db)
            return {
                "imported": imported,
                "imported_products": imported_products,
                "imported_boxes": imported_boxes,
                "reference_workbook": True,
                "errors": errors,
            }
        for record in records:
            try:
                with db.savepoint():
                    if catalog_type == "products":
                        _upsert_product_record(db, record)
                    else:
                        _upsert_box_record(db, record)
                imported += 1
            except DatabaseError as exc:
                errors.append(f"Could not import {record}: {exc}")
        if catalog_type == "products":
            apply_tsca_catalog(db)
    return {"imported": imported, "errors": errors}


@app.get("/api/clients")
def list_clients() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT * FROM clients ORDER BY lower(customer)").fetchall()
    return [with_country_code(dict(row)) for row in rows]


@app.post("/api/clients/import", dependencies=[Depends(require_admin)])
async def import_clients(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Upload an Excel .xlsx file.")
    records, errors = parse_clients_excel(await file.read())
    if not records and errors:
        raise HTTPException(400, errors[0])
    created = 0
    updated = 0
    with connect() as db:
        for record in records:
            timestamp = now_iso()
            existing = db.execute(
                "SELECT id FROM clients WHERE lower(trim(customer)) = lower(trim(?))",
                (record["customer"],),
            ).fetchone()
            if existing:
                db.execute(
                    """UPDATE clients SET customer=?, phone_number=?, email=?, contact_name=?,
                           address=?, country=?, shipping_address=?, updated_at=? WHERE id=?""",
                    (record["customer"], record["phone_number"], record["email"],
                     record["contact_name"], record["address"], record["country"],
                     record["shipping_address"], timestamp, existing["id"]),
                )
                updated += 1
            else:
                db.execute(
                    """INSERT INTO clients
                           (customer, phone_number, email, contact_name, address, country,
                            shipping_address, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (record["customer"], record["phone_number"], record["email"],
                     record["contact_name"], record["address"], record["country"],
                     record["shipping_address"], timestamp, timestamp),
                )
                created += 1
    return {"imported": created + updated, "created": created, "updated": updated, "errors": errors}


class ClientPayload(BaseModel):
    customer: str = Field(min_length=1, max_length=300)
    phone_number: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=300)
    contact_name: str = Field(default="", max_length=300)
    address: str = Field(default="", max_length=2000)
    country: str = Field(default="", max_length=120)
    shipping_address: str = Field(default="", max_length=2000)


def _client_or_404(db: Database, client_id: int) -> Any:
    row = db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Client not found.")
    return row


@app.patch("/api/clients/{client_id}", dependencies=[Depends(require_admin)])
def update_client(client_id: int, payload: ClientPayload) -> dict[str, Any]:
    values = {field: value.strip() for field, value in payload.model_dump().items()}
    if not values["customer"]:
        raise HTTPException(422, "Customer is required.")
    with connect() as db:
        _client_or_404(db, client_id)
        try:
            db.execute(
                """UPDATE clients SET customer=?, phone_number=?, email=?, contact_name=?,
                       address=?, country=?, shipping_address=?, updated_at=? WHERE id=?""",
                (values["customer"], values["phone_number"], values["email"],
                 values["contact_name"], values["address"], values["country"],
                 values["shipping_address"], now_iso(), client_id),
            )
        except IntegrityError as exc:
            raise HTTPException(409, "Another client already uses this customer name.") from exc
        return with_country_code(dict(_client_or_404(db, client_id)))


@app.delete("/api/clients", dependencies=[Depends(require_admin)])
def delete_all_clients() -> dict[str, int]:
    with connect() as db:
        count = db.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        db.execute("DELETE FROM clients")
    return {"deleted_clients": count}


@app.delete("/api/clients/{client_id}", dependencies=[Depends(require_admin)])
def delete_client(client_id: int) -> dict[str, Any]:
    with connect() as db:
        row = _client_or_404(db, client_id)
        db.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    return {"deleted_client_id": client_id, "customer": row["customer"]}
