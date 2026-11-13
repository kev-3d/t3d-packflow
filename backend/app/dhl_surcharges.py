"""DHL weekly fuel surcharge and demand surcharge, published on mydhl.express.dhl.

The values live in ``dhl_surcharges.json``. ``backend/scripts/update_dhl_surcharges.py``
refreshes it from the PDFs saved from DHL's surcharge pages (print the page to PDF):

* Fuel: "International Time Definite services" weekly table (date range -> %).
* Demand: "Time Definite International" matrix, row for the origin region (Europe,
  because shipments leave Malta), EUR per kg by destination region, plus the
  demand period dates.
"""

from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

SURCHARGES_PATH = Path(__file__).resolve().parent / "dhl_surcharges.json"

# Column order of DHL's demand matrix (destinations).
DEMAND_REGIONS = ["china_hk", "asia", "oceania", "europe", "americas", "mena", "rest_of_world"]
REGION_LABELS = {
    "china_hk": "China & Hong Kong",
    "asia": "Asia",
    "oceania": "Oceania",
    "europe": "Europe",
    "americas": "Americas",
    "mena": "Middle East & North Africa",
    "rest_of_world": "Rest of the World",
}
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], start=1)}
_MONTH = "(" + "|".join(MONTHS) + ")"
FUEL_ROW = re.compile(rf"{_MONTH}\s+(\d{{1,2}})\s*-\s*(?:{_MONTH}\s+)?(\d{{1,2}}),\s*(\d{{4}})\s+(\d+(?:\.\d+)?)%")
LONG_DATE = re.compile(rf"{_MONTH}\s+(\d{{1,2}}),\s*(\d{{4}})")
NUM = r"(\d+\.\d+)"


@lru_cache(maxsize=1)
def load_surcharges() -> dict[str, Any]:
    return json.loads(SURCHARGES_PATH.read_text(encoding="utf-8"))


def fuel_for(day: date, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The published weekly fuel surcharge row covering ``day``, if any."""
    data = data or load_surcharges()
    for week in data["fuel"]["weeks"]:
        if date.fromisoformat(week["from"]) <= day <= date.fromisoformat(week["to"]):
            return week
    return None


def demand_for(code: str, day: date, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Demand surcharge (EUR/kg) for a destination; 0 outside DHL's demand period."""
    data = data or load_surcharges()
    demand = data["demand"]
    region = demand["regions"].get(code, "rest_of_world")
    active = date.fromisoformat(demand["valid_from"]) <= day <= date.fromisoformat(demand["valid_to"])
    return {
        "per_kg": float(demand["rates"][region]) if active else 0.0,
        "region": region,
        "region_label": REGION_LABELS[region],
        "active": active,
        "valid_from": demand["valid_from"],
        "valid_to": demand["valid_to"],
    }


# --- Parsing of the PDFs saved from DHL's website -------------------------------------

def parse_fuel_text(text: str) -> list[dict[str, Any]]:
    """Weekly International Time Definite fuel rows (the first table on the page)."""
    section = text.split("Domestic Time Definite", 1)[0]
    weeks = []
    for m1, d1, m2, d2, year, pct in FUEL_ROW.findall(section):
        end_month = MONTHS[m2 or m1]
        end = date(int(year), end_month, int(d2))
        start_year = int(year) - 1 if MONTHS[m1] > end_month else int(year)
        start = date(start_year, MONTHS[m1], int(d1))
        weeks.append({"from": start.isoformat(), "to": end.isoformat(), "pct": float(pct)})
    if not weeks:
        raise ValueError("No weekly International fuel surcharge rows found in the fuel PDF.")
    return sorted(weeks, key=lambda w: w["from"])


def parse_demand_text(text: str, origin: str = "Europe") -> dict[str, Any]:
    """Demand period and the origin row of the Time Definite International matrix."""
    period = re.search(rf"effective\s+{LONG_DATE.pattern}\s+until\s+{LONG_DATE.pattern}", text, re.S)
    if not period:
        raise ValueError("Demand surcharge period ('effective … until …') not found.")
    m1, d1, y1, m2, d2, y2 = period.groups()
    row = re.search(rf"^\s*{re.escape(origin)}\s+" + r"\s+".join([NUM] * 7), text, re.M)
    if not row:
        raise ValueError(f"Demand matrix row for origin '{origin}' not found.")
    return {
        "valid_from": date(int(y1), MONTHS[m1], int(d1)).isoformat(),
        "valid_to": date(int(y2), MONTHS[m2], int(d2)).isoformat(),
        "origin_region": origin,
        "rates": {region: float(value) for region, value in zip(DEMAND_REGIONS, row.groups())},
    }


def merge_fuel_weeks(existing: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep history; a newly published row for the same week replaces the old one."""
    merged = {(w["from"], w["to"]): w for w in existing}
    merged.update({(w["from"], w["to"]): w for w in new})
    return sorted(merged.values(), key=lambda w: w["from"])
