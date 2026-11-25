"""Shipment checklist (warehouse board): pack each finished order and hand it to the carrier.

Every shipment is a package on the board. Save & Finish adds the order's shipment (see
``shipments_routes.ensure_order_shipment``), so a finished PO shows up here as "to pack".
Lifecycle: to_pack -> packed -> picked_up, with a pickup day and slot assigned on the way.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .database import Database, connect, now_iso

router = APIRouter()

PACK_STATUSES = ("to_pack", "packed", "picked_up")
SLOTS = ("morning", "afternoon")
# Picked-up shipments stay in the response this long, so the board can count today's pickups and undo one.
PICKED_UP_WINDOW = timedelta(hours=36)

_SELECT = """SELECT s.id, s.order_id, s.po, s.client, s.destination, s.tracking_number, s.forwarder,
                    s.pack_status, s.pickup_date, s.pickup_slot, s.picked_up_at, o.order_number
               FROM shipments s LEFT JOIN orders o ON o.id = s.order_id"""


def _order_contents(db: Database, order_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Units and boxes per order, from its latest packing plan (falling back to the PO quantities)."""
    if not order_ids:
        return {}
    marks = ", ".join("?" for _ in order_ids)
    contents: dict[int, dict[str, Any]] = {
        row["order_id"]: {"items": int(row["units"] or 0), "boxes": []}
        for row in db.execute(
            f"SELECT order_id, SUM(quantity) AS units FROM order_items WHERE order_id IN ({marks}) GROUP BY order_id",
            tuple(order_ids)).fetchall()
    }
    plans = db.execute(
        f"""SELECT p.order_id, p.result_json FROM packing_plans p
            WHERE p.order_id IN ({marks})
              AND p.id = (SELECT MAX(id) FROM packing_plans latest WHERE latest.order_id = p.order_id)""",
        tuple(order_ids)).fetchall()
    for plan in plans:
        try:
            result = json.loads(plan["result_json"])
        except (TypeError, ValueError):
            continue
        entry = contents.setdefault(plan["order_id"], {"items": 0, "boxes": []})
        entry["boxes"] = [str(box.get("box_code") or box.get("box_name") or "") for box in result.get("boxes", [])]
        if result.get("unit_count"):
            entry["items"] = int(result["unit_count"])
    return contents


def _public(row: Any, contents: dict[int, dict[str, Any]]) -> dict[str, Any]:
    package = contents.get(row["order_id"], {"items": 0, "boxes": []})
    return {
        "id": row["id"],
        "order_id": row["order_id"],
        "po": row["po"] or row["order_number"] or "",
        "tracking": row["tracking_number"] or "",
        "carrier": row["forwarder"] or "",
        "customer": row["client"] or "",
        "destination": row["destination"] or "",
        "items": package["items"],
        "boxes": package["boxes"],
        "status": row["pack_status"],
        "pickup_date": row["pickup_date"],
        "pickup_slot": row["pickup_slot"],
        "picked_up_at": row["picked_up_at"],
    }


def _fetch(db: Database, shipment_id: int) -> dict[str, Any]:
    row = db.execute(f"{_SELECT} WHERE s.id = ?", (shipment_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Shipment not found.")
    return _public(row, _order_contents(db, [row["order_id"]] if row["order_id"] else []))


@router.get("/api/checklist")
def list_checklist() -> list[dict[str, Any]]:
    """Shipments still in the warehouse, plus those picked up in the last 36 hours."""
    cutoff = (datetime.now(timezone.utc) - PICKED_UP_WINDOW).isoformat(timespec="seconds")
    with connect() as db:
        rows = db.execute(
            f"{_SELECT} WHERE s.pack_status <> 'picked_up' OR s.picked_up_at >= ? ORDER BY s.id",
            (cutoff,)).fetchall()
        contents = _order_contents(db, sorted({row["order_id"] for row in rows if row["order_id"]}))
        return [_public(row, contents) for row in rows]


class ChecklistAction(BaseModel):
    action: Literal["pack", "unpack", "assign_pickup", "pick_up", "undo_pick_up"]
    pickup_date: str | None = Field(default=None, max_length=10)
    pickup_slot: Literal["morning", "afternoon"] | None = None


# action -> (status it requires, status it leaves)
_TRANSITIONS = {
    "pack": ("to_pack", "packed"),
    "unpack": ("packed", "to_pack"),
    "pick_up": ("packed", "picked_up"),
    "undo_pick_up": ("picked_up", "packed"),
}
_REFUSED = {
    "pack": "Only a package that is still to pack can be marked packed.",
    "unpack": "Only a packed package can go back to packing.",
    "pick_up": "A package must be packed before the carrier picks it up.",
    "undo_pick_up": "This package has not been picked up.",
    "assign_pickup": "A package that was already picked up cannot be rescheduled.",
}


@router.patch("/api/checklist/{shipment_id}")
def update_checklist(shipment_id: int, payload: ChecklistAction) -> dict[str, Any]:
    timestamp = now_iso()
    if payload.action == "assign_pickup":
        if not payload.pickup_date:
            raise HTTPException(422, "Choose a pickup day.")
        try:
            pickup_date = date.fromisoformat(payload.pickup_date).isoformat()
        except ValueError as exc:
            raise HTTPException(422, "Pickup day must be a valid date (YYYY-MM-DD).") from exc
        sql = ("UPDATE shipments SET pickup_date = ?, pickup_slot = ?, updated_at = ? "
               "WHERE id = ? AND pack_status <> 'picked_up'")
        params: tuple[Any, ...] = (pickup_date, payload.pickup_slot, timestamp, shipment_id)
    else:
        required, target = _TRANSITIONS[payload.action]
        picked_up_at = timestamp if target == "picked_up" else None
        # The status check sits in the WHERE clause, so two people acting at once cannot both win.
        sql = ("UPDATE shipments SET pack_status = ?, picked_up_at = ?, updated_at = ? "
               "WHERE id = ? AND pack_status = ?")
        params = (target, picked_up_at, timestamp, shipment_id, required)
    with connect() as db:
        if db.execute(sql, params).rowcount == 0:
            if not db.execute("SELECT 1 FROM shipments WHERE id = ?", (shipment_id,)).fetchone():
                raise HTTPException(404, "Shipment not found.")
            raise HTTPException(409, _REFUSED[payload.action])
        return _fetch(db, shipment_id)
