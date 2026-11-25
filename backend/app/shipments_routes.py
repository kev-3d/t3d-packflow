"""Outgoing shipments: the "Export" tracking sheet (documents checked, tracking, e-mails sent)."""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import User, current_user, require_admin
from .database import Database, connect, get_shipping_quote, now_iso
from .requirements_routes import requirements_note_for

router = APIRouter()

FORWARDERS = ("DHL", "DHL insured", "TNT", "UPS", "MP", "Shiplowcost", "Other")
DOC_FIELDS = ("doc_pl", "doc_sds", "doc_stripe", "doc_tsca", "doc_sec232", "doc_coo")
US_ONLY_DOCS = ("doc_tsca", "doc_sec232")
EMAIL_FIELDS = ("email_client_sent", "email_forwarder_sent")
TEXT_FIELDS = ("client", "po", "destination", "invoice_number", "tracking_number", "forwarder", "notes")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_SDS = re.compile(r"\b(sds|msds|safety data sheets?)\b", re.I)
# Statuses at which an order is ready to be dispatched.
CANDIDATE_STATUSES = ("ready", "packed", "attention")


def _order_has_chemicals(db: Database, order_id: int) -> bool:
    """Products with TSCA wording are the chemical ones that travel with a safety data sheet."""
    row = db.execute(
        """SELECT 1 FROM order_items i JOIN products p ON p.id = i.product_id
           WHERE i.order_id = ? AND trim(p.tsca_name) <> '' LIMIT 1""", (order_id,)).fetchone()
    return row is not None


def applicable_docs(destination: str, *, chemicals: bool, sds_requested: bool) -> dict[str, int | None]:
    """Initial document checks: 0 = to do, None = not applicable for this shipment.

    PL and CoO always apply; TSCA and the Section 232 declaration only for the US; the SDS when the
    order carries chemicals or the customer asks for it; Stripe is opt-in (switched on by hand).
    """
    us = destination == "US"
    return {
        "doc_pl": 0, "doc_coo": 0, "doc_stripe": None,
        "doc_sds": 0 if (chemicals or sds_requested) else None,
        "doc_tsca": 0 if us else None, "doc_sec232": 0 if us else None,
    }


def _forwarder_from(courier_note: str, has_dhl_quote: bool) -> str:
    text = (courier_note or "").lower()
    for name in sorted(FORWARDERS, key=len, reverse=True):
        if name != "Other" and re.search(rf"\b{re.escape(name.lower())}\b", text):
            return name
    return "DHL" if has_dhl_quote else ""


def order_defaults(db: Database, order_id: int) -> dict[str, Any]:
    """Client, PO, destination, forwarder and document checks taken from an order."""
    from .main import _detect_order_destination  # local import: main registers this router

    order = db.execute("SELECT id, order_number, client FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None:
        raise HTTPException(404, "Order not found.")
    detected = _detect_order_destination(db, order_id)
    destination = (detected or {}).get("code", "")
    note = requirements_note_for(db, order["client"])
    exact_note = note if note and note["match"] == "exact" else None
    docs = applicable_docs(
        destination,
        chemicals=_order_has_chemicals(db, order_id),
        sds_requested=bool(exact_note and _SDS.search(exact_note.get("docs_required") or "")),
    )
    return {
        "order_id": order_id, "client": order["client"] or "", "po": order["order_number"] or "",
        "destination": destination,
        "forwarder": _forwarder_from((exact_note or {}).get("courier", ""), get_shipping_quote(db, order_id) is not None),
        **docs,
    }


def _insert(db: Database, values: dict[str, Any], ship_date: str, created_by: str) -> int:
    timestamp = now_iso()
    columns = ["ship_date", "order_id", *TEXT_FIELDS, *DOC_FIELDS, "created_by", "created_at", "updated_at"]
    row = [ship_date, values["order_id"], *(values.get(f, "") for f in TEXT_FIELDS),
           *(values[f] for f in DOC_FIELDS), created_by, timestamp, timestamp]
    return db.insert_returning_id(
        f"INSERT INTO shipments ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) RETURNING id",
        tuple(row),
    )


def ensure_order_shipment(db: Database, order_id: int, created_by: str = "") -> int | None:
    """Add the order to the Export list (shipping today) when it is not there yet.

    Called by Save & Finish. Returns the new shipment id, or None when the order already has one,
    so edits made on the Export page are never overwritten.
    """
    if db.execute("SELECT 1 FROM shipments WHERE order_id = ?", (order_id,)).fetchone():
        return None
    return _insert(db, order_defaults(db, order_id), date.today().isoformat(), created_by)


def _progress(row: dict[str, Any]) -> dict[str, int]:
    """Steps done out of the steps that apply: documents, invoice, tracking and both e-mails."""
    steps = [row[field] for field in DOC_FIELDS if row[field] is not None]
    steps += [bool(row["invoice_number"].strip()), bool(row["tracking_number"].strip())]
    steps += [row[field] for field in EMAIL_FIELDS]
    done = sum(1 for step in steps if step)
    return {"done": done, "total": len(steps)}


def _public(db: Database, row: Any) -> dict[str, Any]:
    shipment = dict(row)
    for field in (*DOC_FIELDS, *EMAIL_FIELDS):
        if shipment[field] is not None:
            shipment[field] = bool(shipment[field])
    progress = _progress(shipment)
    shipment["progress"] = progress
    shipment["complete"] = progress["done"] == progress["total"]
    shipment["order_number"] = row["order_number"]
    shipment["requirements"] = requirements_note_for(db, shipment["client"])
    return shipment


_SELECT = """SELECT s.*, o.order_number FROM shipments s LEFT JOIN orders o ON o.id = s.order_id"""


def _fetch(db: Database, shipment_id: int) -> dict[str, Any]:
    row = db.execute(f"{_SELECT} WHERE s.id = ?", (shipment_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Shipment not found.")
    return _public(db, row)


def _clean_destination(value: str) -> str:
    code = value.strip().upper()
    if code and not _COUNTRY.match(code):
        raise HTTPException(422, "Destination must be a two-letter country code (e.g. US, DE).")
    return code


def _clean_date(value: str) -> str:
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise HTTPException(422, "Ship date must be a valid date (YYYY-MM-DD).") from exc


@router.get("/api/shipments")
def list_shipments(
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    q: str = "",
    forwarder: str = "",
    destination: str = "",
    pending: bool = False,
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if date_from:
        clauses.append("s.ship_date >= ?")
        params.append(_clean_date(date_from))
    if date_to:
        clauses.append("s.ship_date <= ?")
        params.append(_clean_date(date_to))
    if forwarder:
        clauses.append("s.forwarder = ?")
        params.append(forwarder)
    if destination:
        clauses.append("s.destination = ?")
        params.append(destination.strip().upper())
    if q.strip():
        clauses.append("(lower(s.client) LIKE ? OR lower(s.po) LIKE ? OR lower(s.tracking_number) LIKE ? OR lower(s.invoice_number) LIKE ?)")
        params.extend([f"%{q.strip().lower()}%"] * 4)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as db:
        rows = db.execute(f"{_SELECT}{where} ORDER BY s.ship_date DESC, s.id DESC", tuple(params)).fetchall()
        shipments = [_public(db, row) for row in rows]
    return [s for s in shipments if not s["complete"]] if pending else shipments


@router.get("/api/shipments/order-candidates")
def shipment_order_candidates() -> list[dict[str, Any]]:
    """Orders ready to dispatch that have no shipment yet, newest first."""
    placeholders = ", ".join("?" for _ in CANDIDATE_STATUSES)
    with connect() as db:
        rows = db.execute(
            f"""SELECT o.id, o.order_number, o.client, o.order_date, o.status FROM orders o
                WHERE o.status IN ({placeholders})
                  AND NOT EXISTS (SELECT 1 FROM shipments s WHERE s.order_id = o.id)
                ORDER BY o.order_date DESC, o.id DESC""", CANDIDATE_STATUSES).fetchall()
        return [dict(row) for row in rows]


@router.get("/api/shipments/order-defaults/{order_id}")
def shipment_order_defaults(order_id: int) -> dict[str, Any]:
    with connect() as db:
        return order_defaults(db, order_id)


class ShipmentCreate(BaseModel):
    ship_date: str = Field(min_length=1, max_length=10)
    order_id: int | None = None
    client: str = Field(default="", max_length=300)
    po: str = Field(default="", max_length=200)
    destination: str = Field(default="", max_length=2)
    invoice_number: str = Field(default="", max_length=100)
    tracking_number: str = Field(default="", max_length=200)
    forwarder: str = Field(default="", max_length=60)
    notes: str = Field(default="", max_length=2000)


@router.post("/api/shipments")
def create_shipment(payload: ShipmentCreate, user: User | None = Depends(current_user)) -> dict[str, Any]:
    ship_date = _clean_date(payload.ship_date)
    with connect() as db:
        values: dict[str, Any] = {"order_id": None}
        if payload.order_id is not None:
            if db.execute("SELECT 1 FROM shipments WHERE order_id = ?", (payload.order_id,)).fetchone():
                raise HTTPException(409, "This order already has a shipment.")
            values = order_defaults(db, payload.order_id)
        detected = values.get("destination", "")
        # Values typed in the form win over those taken from the order.
        for field in TEXT_FIELDS:
            typed = getattr(payload, field).strip()
            if typed or field not in values:
                values[field] = typed
        values["destination"] = _clean_destination(values["destination"])
        if not values["client"]:
            raise HTTPException(422, "Client is required.")
        if payload.order_id is None:
            if not values["destination"]:
                raise HTTPException(422, "Destination is required for a shipment without an order.")
            values.update(applicable_docs(values["destination"], chemicals=False, sds_requested=False))
        elif values["destination"] != detected:
            values.update({field: (0 if values["destination"] == "US" else None) for field in US_ONLY_DOCS})
        shipment_id = _insert(db, values, ship_date, user.email if user else "")
        return _fetch(db, shipment_id)


class ShipmentUpdate(BaseModel):
    ship_date: str | None = Field(default=None, max_length=10)
    client: str | None = Field(default=None, max_length=300)
    po: str | None = Field(default=None, max_length=200)
    destination: str | None = Field(default=None, max_length=2)
    invoice_number: str | None = Field(default=None, max_length=100)
    tracking_number: str | None = Field(default=None, max_length=200)
    forwarder: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=2000)
    # Documents: true = done, false = to do, null = not applicable. Omit a field to leave it as is.
    doc_pl: bool | None = None
    doc_sds: bool | None = None
    doc_stripe: bool | None = None
    doc_tsca: bool | None = None
    doc_sec232: bool | None = None
    doc_coo: bool | None = None
    email_client_sent: bool | None = None
    email_forwarder_sent: bool | None = None


@router.patch("/api/shipments/{shipment_id}")
def update_shipment(shipment_id: int, payload: ShipmentUpdate) -> dict[str, Any]:
    sent = payload.model_fields_set
    updates: dict[str, Any] = {}
    if "ship_date" in sent and payload.ship_date is not None:
        updates["ship_date"] = _clean_date(payload.ship_date)
    for field in TEXT_FIELDS:
        if field in sent and getattr(payload, field) is not None:
            updates[field] = getattr(payload, field).strip()
    if "client" in updates and not updates["client"]:
        raise HTTPException(422, "Client is required.")
    if "destination" in updates:
        updates["destination"] = _clean_destination(updates["destination"])
    for field in DOC_FIELDS:
        if field in sent:
            value = getattr(payload, field)
            updates[field] = None if value is None else int(value)
    timestamp = now_iso()
    for field in EMAIL_FIELDS:
        if field in sent and getattr(payload, field) is not None:
            value = getattr(payload, field)
            updates[field] = int(value)
            updates[f"{field}_at"] = timestamp if value else None
    with connect() as db:
        current = db.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
        if current is None:
            raise HTTPException(404, "Shipment not found.")
        # A new destination switches the US-only documents on or off unless they are set in the same request.
        if "destination" in updates and updates["destination"] != current["destination"]:
            us = updates["destination"] == "US"
            for field in US_ONLY_DOCS:
                if field not in sent:
                    updates[field] = (current[field] if current[field] is not None else 0) if us else None
        if updates:
            assignments = ", ".join(f"{field} = ?" for field in updates)
            db.execute(f"UPDATE shipments SET {assignments}, updated_at = ? WHERE id = ?",
                       (*updates.values(), timestamp, shipment_id))
        return _fetch(db, shipment_id)


@router.delete("/api/shipments/{shipment_id}", dependencies=[Depends(require_admin)])
def delete_shipment(shipment_id: int) -> dict[str, int]:
    with connect() as db:
        if not db.execute("SELECT 1 FROM shipments WHERE id = ?", (shipment_id,)).fetchone():
            raise HTTPException(404, "Shipment not found.")
        db.execute("DELETE FROM shipments WHERE id = ?", (shipment_id,))
    return {"deleted_shipment_id": shipment_id}
