"""DHL weekly fuel surcharge and demand surcharge, published on mydhl.express.dhl.

The values live in ``dhl_surcharges.json``. ``backend/scripts/update_dhl_surcharges.py``
refreshes it from the PDFs saved from DHL's surcharge pages (print the page to PDF).

The weekly fuel rows are also refreshed automatically from DHL's public surcharge page
(``refresh_fuel_from_web``): the app fetches it on start-up, daily, and when a quote needs a week
that is not loaded yet. Fetched weeks are stored in the database (``app_settings``) because the
container disk is not persistent, and are merged over the bundled JSON by ``load_surcharges``.

* Fuel: "International Time Definite services" weekly table (date range -> %).
* Demand: "Time Definite International" matrix, row for the origin region (Europe,
  because shipments leave Malta), EUR per kg by destination region, plus the
  demand period dates.
"""

from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
import urllib.request
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

SURCHARGES_PATH = Path(__file__).resolve().parent / "dhl_surcharges.json"
FUEL_PAGE_URL = "https://mydhl.express.dhl/mt/en/ship/surcharges.html"
WEB_FUEL_KEY = "dhl_fuel_web"
FETCH_TIMEOUT_SECONDS = 10
# A quote that finds no published week triggers at most one extra fetch per hour.
ON_DEMAND_MIN_INTERVAL_SECONDS = 3600

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
def load_bundled_surcharges() -> dict[str, Any]:
    return json.loads(SURCHARGES_PATH.read_text(encoding="utf-8"))


_web_lock = threading.Lock()
_web_fuel: dict[str, Any] | None = None
_web_loaded = False
_last_attempt = 0.0


def _stored_web_fuel() -> dict[str, Any] | None:
    """Weeks fetched from DHL's website, read once from the database and then kept in memory."""
    global _web_fuel, _web_loaded
    if _web_loaded:
        return _web_fuel
    try:
        from .database import connect
        with connect() as db:
            row = db.execute("SELECT value FROM app_settings WHERE key = ?", (WEB_FUEL_KEY,)).fetchone()
        _web_fuel = json.loads(row["value"]) if row else None
        _web_loaded = True
    except Exception as error:  # No database yet (scripts, unit tests): use the bundled JSON only.
        logger.debug("Stored DHL fuel weeks unavailable: %s", error)
    return _web_fuel


def load_surcharges() -> dict[str, Any]:
    """Bundled surcharges with the fuel weeks fetched from DHL's website merged in."""
    bundled = load_bundled_surcharges()
    web = _stored_web_fuel()
    if not web or not web.get("weeks"):
        return bundled
    fuel = {
        **bundled["fuel"],
        "weeks": merge_fuel_weeks(bundled["fuel"]["weeks"], web["weeks"]),
        "source": "DHL website",
        "source_url": web.get("source_url", FUEL_PAGE_URL),
        "imported_on": web.get("fetched_on", bundled["fuel"].get("imported_on")),
    }
    return {**bundled, "fuel": fuel}


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


def html_to_text(page: str) -> str:
    """Visible text of an HTML page: scripts/styles dropped, tags turned into spaces, entities decoded."""
    page = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1\s*>", " ", page)
    return html.unescape(re.sub(r"(?s)<[^>]+>", " ", page))


def fetch_fuel_page(url: str = FUEL_PAGE_URL) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; PackFlow surcharge check)",
        "Accept": "text/html", "Accept-Language": "en",
    })
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:  # noqa: S310 - fixed DHL URL
        return response.read().decode("utf-8", "replace")


def refresh_fuel_from_web(fetch: Callable[[], str] = fetch_fuel_page) -> dict[str, Any]:
    """Fetch DHL's published weekly fuel rows and store them. Never raises: on any failure the
    previously stored weeks stay in use. Returns {"status": "updated"|"unchanged"|"failed", ...}."""
    global _web_fuel, _web_loaded, _last_attempt
    with _web_lock:
        _last_attempt = time.monotonic()
        try:
            weeks = parse_fuel_text(html_to_text(fetch()))
        except Exception as error:
            logger.warning("DHL fuel surcharge refresh failed: %s", error)
            return {"status": "failed", "error": str(error)}
        previous = _stored_web_fuel() or {}
        merged = merge_fuel_weeks(previous.get("weeks", []), weeks)
        record = {
            "weeks": merged,
            "fetched_on": datetime.now(timezone.utc).date().isoformat(),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_url": FUEL_PAGE_URL,
        }
        try:
            from .database import connect
            with connect() as db:
                db.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (WEB_FUEL_KEY, json.dumps(record)),
                )
        except Exception as error:  # Keep serving the fresh weeks from memory even if they could not be saved.
            logger.warning("Could not store the refreshed DHL fuel weeks: %s", error)
        changed = merged != previous.get("weeks")
        _web_fuel, _web_loaded = record, True
        logger.info("DHL fuel surcharge refreshed: %d weeks, latest %s to %s.", len(weeks), weeks[-1]["from"], weeks[-1]["to"])
        return {"status": "updated" if changed else "unchanged", "weeks": len(weeks), "latest": weeks[-1]}


def ensure_fuel_for(day: date, fetch: Callable[[], str] = fetch_fuel_page) -> None:
    """If no published week covers ``day``, try DHL's website once (rate-limited to one attempt per hour)."""
    if fuel_for(day) is not None:
        return
    if time.monotonic() - _last_attempt < ON_DEMAND_MIN_INTERVAL_SECONDS and _last_attempt:
        return
    refresh_fuel_from_web(fetch)


def reset_web_cache() -> None:
    """Forget the in-memory web weeks (tests switch databases between cases)."""
    global _web_fuel, _web_loaded, _last_attempt
    _web_fuel, _web_loaded, _last_attempt = None, False, 0.0
