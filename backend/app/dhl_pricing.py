"""DHL Express Worldwide export pricing from Malta (2026 rate card).

Pure functions: no database access. The tariff comes from ``dhl_tariff_2026.json``,
built once from the DHL workbook by ``backend/scripts/build_dhl_tariff.py``.

The shipment is priced as ONE multi-piece shipment: each piece weighs the higher of
its actual and volumetric weight, the pieces are summed and the total is rounded up
to the next 0.5 kg (1 kg above 30 kg). Fuel applies to transport, premium, handling,
remote area and demand charges, not to GoGreen or other flat charges. VAT applies to
the whole subtotal.
"""

from __future__ import annotations

import json
import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .dhl_surcharges import demand_for, fuel_for, load_surcharges

TARIFF_PATH = Path(__file__).resolve().parent / "dhl_tariff_2026.json"
SERVICES = {
    "express_worldwide": "Express Worldwide",
    "premium_1200": "Express 12:00",
    "premium_0900": "Express 9:00",
}
RATE_SOURCES = ("dhl", "manual")
EPS = 1e-9


class PricingError(ValueError):
    """Input that cannot produce a trustworthy DHL estimate."""


@lru_cache(maxsize=1)
def load_tariff() -> dict[str, Any]:
    return json.loads(TARIFF_PATH.read_text(encoding="utf-8"))


def cents(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def zone_for(code: str, tariff: dict[str, Any] | None = None) -> dict[str, Any]:
    tariff = tariff or load_tariff()
    for entry in tariff["zones"]:
        if entry["code"] == code:
            return entry
    raise PricingError(f"No DHL export zone for destination {code!r}.")


def tariff_weight(weight_kg: float) -> float:
    """Round a shipment weight up to the rate-card step: 0.5 kg up to 30 kg, then 1 kg."""
    if weight_kg <= 0:
        return 0.5
    if weight_kg <= 30 + EPS:
        return max(0.5, math.ceil(weight_kg * 2 - EPS) / 2)
    return float(math.ceil(weight_kg - EPS))


def _adder_band(current: float, tariff: dict[str, Any]) -> dict[str, Any]:
    for band in tariff["export"]["adders"]:
        if band["from_kg"] - 0.1 - EPS <= current < band["to_kg"] - EPS:
            return band
    raise PricingError(f"No DHL adder rate for weights above {current:g} kg.")


def base_rate(weight_kg: float, zone: int, tariff: dict[str, Any] | None = None) -> float:
    """Transport charge for an already rounded tariff weight."""
    tariff = tariff or load_tariff()
    if not 1 <= zone <= 6:
        raise PricingError(f"Zone {zone} is outside the export zones 1-6.")
    rows = tariff["export"]["non_documents"]
    start = max((row for row in rows if row[0] <= weight_kg + EPS), key=lambda row: row[0], default=rows[0])
    current, amount = start[0], Decimal(str(start[zone]))
    while current < weight_kg - EPS:
        band = _adder_band(current, tariff)
        current += band["step_kg"]
        amount += Decimal(str(band["rates"][zone - 1]))
    return float(cents(amount))


def piece_handling(actual_kg: float, dims_cm: Iterable[float], tariff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Per-piece handling surcharge. Overweight takes precedence; the 20 EUR ones don't stack."""
    tariff = tariff or load_tariff()
    s = tariff["surcharges"]
    sides = sorted((float(d) for d in dims_cm), reverse=True)
    volumetric = math.prod(sides) / tariff["meta"]["volumetric_divisor"]
    if max(actual_kg, volumetric) > s["overweight_piece"]["over_kg"] + EPS:
        return {"type": "overweight", "label": "Overweight piece", "amount": s["overweight_piece"]["amount"]}
    if sides[0] > s["oversize_piece"]["longest_side_cm"] + EPS or sides[1] > s["oversize_piece"]["second_side_cm"] + EPS:
        return {"type": "oversize", "label": "Oversize piece", "amount": s["oversize_piece"]["amount"]}
    nc = s["non_conveyable_piece"]
    if nc["from_kg"] - EPS <= actual_kg <= nc["to_kg"] + EPS:
        return {"type": "non_conveyable", "label": "Non-conveyable piece", "amount": nc["amount"]}
    return {"type": "none", "label": "None", "amount": 0.0}


def resolve_rates(inputs: dict[str, Any], surcharges: dict[str, Any], day: date, destination: str) -> dict[str, Any]:
    """Fuel % and demand EUR/kg from DHL's published schedules, or a manual override."""
    resolved: dict[str, Any] = {}
    fuel_source = "dhl" if inputs.get("fuel_source", "dhl") == "saved" else inputs.get("fuel_source", "dhl")
    demand_source = "dhl" if inputs.get("demand_source", "dhl") == "saved" else inputs.get("demand_source", "dhl")
    for source, label in ((fuel_source, "fuel surcharge"), (demand_source, "demand surcharge")):
        if source not in RATE_SOURCES:
            raise PricingError(f"Unknown {label} source {source!r}.")

    if fuel_source == "manual":
        value = inputs.get("fuel_pct")
        if value is None:
            raise PricingError("Enter a manual fuel surcharge value.")
        resolved.update(fuel_pct=float(value), fuel_note="Manual override")
    else:
        week = fuel_for(day, surcharges)
        if week is None:
            weeks = surcharges["fuel"]["weeks"]
            raise PricingError(
                f"DHL has not published a fuel surcharge covering {day.isoformat()} yet "
                f"(published weeks: {weeks[0]['from']} to {weeks[-1]['to']}). Use a manual override."
            )
        resolved.update(fuel_pct=float(week["pct"]), fuel_note=f"DHL week {week['from']} to {week['to']}")
    resolved["fuel_source"] = fuel_source

    if demand_source == "manual":
        value = inputs.get("demand_per_kg")
        if value is None:
            raise PricingError("Enter a manual demand surcharge value.")
        resolved.update(demand_per_kg=float(value), demand_note="Manual override")
    else:
        demand = demand_for(destination, day, surcharges)
        note = (f"Europe → {demand['region_label']}, {demand['valid_from']} to {demand['valid_to']}"
                if demand["active"] else f"Outside the DHL demand period ({demand['valid_from']} to {demand['valid_to']})")
        resolved.update(demand_per_kg=demand["per_kg"], demand_note=note, demand_region=demand["region"])
    resolved["demand_source"] = demand_source
    if resolved["fuel_pct"] < 0 or resolved["demand_per_kg"] < 0:
        raise PricingError("Surcharges cannot be negative.")

    defaults = surcharges.get("defaults", {})
    for value_key, default in (("gogreen_per_kg", defaults.get("gogreen_per_kg", 0.2)), ("vat_pct", defaults.get("vat_pct", 18.0))):
        value = inputs.get(value_key)
        value = default if value is None else value
        if float(value) < 0 or (value_key == "vat_pct" and float(value) > 100):
            raise PricingError(f"Invalid {value_key.replace('_', ' ')}.")
        resolved[value_key] = float(value)
    return resolved


def quote(boxes: list[dict[str, Any]], inputs: dict[str, Any], surcharges: dict[str, Any] | None = None,
          tariff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Price packed boxes as one DHL export shipment. ``boxes`` use the packing-plan shape."""
    tariff = tariff or load_tariff()
    surcharges = surcharges or load_surcharges()
    meta = tariff["meta"]
    if not boxes:
        raise PricingError("The packing plan has no boxes to ship.")
    try:
        day = date.fromisoformat(str(inputs.get("shipment_date") or ""))
    except ValueError as exc:
        raise PricingError("Enter a valid shipment date.") from exc
    if day > date.fromisoformat(meta["valid_to"]):
        raise PricingError(f"The {meta['version']} rate card is only valid until {meta['valid_to']}.")
    destination = zone_for(str(inputs.get("destination") or ""), tariff)
    service = inputs.get("service", "express_worldwide")
    if service not in SERVICES:
        raise PricingError(f"Unknown DHL service {service!r}.")
    rates = resolve_rates(inputs, surcharges, day, destination["code"])

    pieces = []
    for index, box in enumerate(boxes, start=1):
        dims = [float(d) for d in box["dimensions_cm"]]
        actual = float(box["gross_weight_kg"])
        if actual > meta["max_piece_kg"] + EPS or max(dims) > meta["max_piece_cm"] + EPS:
            raise PricingError(f"Box {index} exceeds DHL's {meta['max_piece_kg']} kg / {meta['max_piece_cm']} cm piece limit.")
        volumetric = math.prod(dims) / meta["volumetric_divisor"]
        pieces.append({
            "sequence": box.get("sequence", index),
            "box_code": box.get("box_code"),
            "box_name": box.get("box_name"),
            "dimensions_cm": dims,
            "actual_kg": round(actual, 3),
            "volumetric_kg": round(volumetric, 3),
            "chargeable_kg": round(max(actual, volumetric), 3),
            "handling": piece_handling(actual, dims, tariff),
        })

    chargeable = sum(max(p["actual_kg"], p["volumetric_kg"]) for p in pieces)
    if chargeable > meta["max_shipment_kg"] + EPS:
        raise PricingError(f"The shipment exceeds DHL's {meta['max_shipment_kg']} kg limit.")
    weight = tariff_weight(chargeable)
    zone = destination["zone"]
    surcharges = tariff["surcharges"]

    transport = cents(base_rate(weight, zone, tariff))
    premium = cents(tariff["export"]["premium"][service])
    handling = cents(sum(p["handling"]["amount"] for p in pieces))
    remote = cents(0)
    if inputs.get("remote_area"):
        r = surcharges["remote_area_delivery"]
        remote = cents(max(r["minimum"], r["per_kg"] * weight))
    demand = cents(Decimal(str(rates["demand_per_kg"])) * Decimal(str(weight)))
    fuel_base = transport + premium + handling + remote + demand
    fuel = cents(fuel_base * Decimal(str(rates["fuel_pct"])) / 100)
    gogreen = cents(Decimal(str(rates["gogreen_per_kg"])) * Decimal(str(weight)))
    other = cents(inputs.get("other_eur") or 0)
    if other < 0:
        raise PricingError("Other charges cannot be negative.")
    subtotal = fuel_base + fuel + gogreen + other
    vat = cents(subtotal * Decimal(str(rates["vat_pct"])) / 100)
    total = subtotal + vat

    lines = [
        ("transport", f"{SERVICES['express_worldwide']} · zone {zone} · {weight:g} kg", transport),
        ("premium", f"{SERVICES[service]} option", premium),
        ("handling", "Piece handling surcharges", handling),
        ("remote", "Remote area delivery", remote),
        ("demand", f"Demand surcharge · {rates['demand_per_kg']:g} EUR/kg", demand),
        ("fuel", f"Fuel surcharge · {rates['fuel_pct']:g}%", fuel),
        ("gogreen", f"GoGreen Plus · {rates['gogreen_per_kg']:g} EUR/kg", gogreen),
        ("other", "Other charges", other),
    ]
    return {
        "carrier": meta["carrier"],
        "tariff_version": meta["version"],
        "currency": meta["currency"],
        "route": "export",
        "origin": meta["origin"],
        "destination": destination,
        "service": service,
        "service_label": SERVICES[service],
        "shipment_date": day.isoformat(),
        "pieces": pieces,
        "actual_kg": round(sum(p["actual_kg"] for p in pieces), 3),
        "volumetric_kg": round(sum(p["volumetric_kg"] for p in pieces), 3),
        "chargeable_kg": round(chargeable, 3),
        "tariff_weight_kg": weight,
        "rates": rates,
        "remote_area": bool(inputs.get("remote_area")),
        "lines": [{"key": k, "label": label, "amount": float(v)} for k, label, v in lines if v or k == "transport"],
        "subtotal": float(subtotal),
        "vat": float(vat),
        "total": float(total),
    }
