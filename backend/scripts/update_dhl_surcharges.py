"""Refresh backend/app/dhl_surcharges.json from PDFs saved from DHL's surcharge pages.

    python backend/scripts/update_dhl_surcharges.py --fuel "DHL Express - Fuel Surcharge.pdf"
    python backend/scripts/update_dhl_surcharges.py --demand "Demand Surcharge - DHL.pdf"

Fuel weeks are merged with the ones already stored, so the history is kept.
The destination -> demand region map is curated here from DHL's "Destination Countries
and Territories" lists (Malta ships from the Europe origin row).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pypdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.dhl_pricing import load_tariff  # noqa: E402
from app.dhl_surcharges import (  # noqa: E402
    SURCHARGES_PATH, merge_fuel_weeks, parse_demand_text, parse_fuel_text,
)

# DHL destination regions (codes as used in the 2026 rate card zoning).
REGIONS = {
    "china_hk": "CN HK MO",
    "asia": "BD BT BN KH IN ID JP LA MY MV MN MM NP PK PH SG KR LK TW TH TL VN",
    "oceania": "AU CK FJ KI NR NC NU PG WS TO TV VU PF NZ SB",
    "europe": "AL AD AT BE BA BG IC HR CY CZ DK EE FI FR DE GI GR GG HU IS IL IT JE KV LV LI LT LU "
              "MT MC NL MK NO PL PT IE MD ME RO SM RS SK SI ES SE CH TR GB GB-SA VA",
    "americas": "AG AI AR AW BS BB BM BO BR BZ XB KY CA CL CO CR XC DM DO EC SV GD GT GP GY GF HT HN "
                "JM MQ MX MS XN NI PA PY PE SR XY XE KN LC XM VC TT TC UY VG US AS MP GU MH FM PW PR VI",
    "mena": "DZ BH EG JO KW LB MA OM QA SA TN AE",
    "rest_of_world": "AF AO AM AZ BY BJ BW BF BI CM CV CF TD KM CG CU CD DJ ER GQ SZ ET FK FO GA GM GE "
                     "GH GL GN GW IR IQ CI KZ KE KG LS LR LY MG MW ML MR MU YT MZ NA NE NG KP RE RU RW "
                     "ST SN SC SL XS SO ZA SS SH SD SY TJ TZ TG TM UG UA UZ VE YE ZM ZW",
}


def pdf_text(path: str) -> str:
    return "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(path).pages)


def region_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for region, codes in REGIONS.items():
        for code in codes.split():
            if code in mapping:
                raise ValueError(f"{code} is in two demand regions")
            mapping[code] = region
    destinations = {z["code"] for z in load_tariff()["zones"]}
    missing = sorted(destinations - set(mapping))
    if missing:
        print("Not in DHL's demand lists, priced as Rest of the World:", " ".join(missing))
    return {code: mapping.get(code, "rest_of_world") for code in sorted(destinations)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fuel", help="PDF of the DHL fuel surcharge page")
    parser.add_argument("--demand", help="PDF of the DHL demand surcharge page")
    args = parser.parse_args()
    if not args.fuel and not args.demand:
        parser.error("pass --fuel and/or --demand")

    data = json.loads(SURCHARGES_PATH.read_text(encoding="utf-8")) if SURCHARGES_PATH.exists() else {
        "fuel": {"service": "International Time Definite", "weeks": []},
        "demand": {},
        "defaults": {"vat_pct": 18.0, "gogreen_per_kg": 0.2},
    }
    if args.fuel:
        weeks = parse_fuel_text(pdf_text(args.fuel))
        data["fuel"]["weeks"] = merge_fuel_weeks(data["fuel"].get("weeks", []), weeks)
        data["fuel"]["source"] = Path(args.fuel).name
        data["fuel"]["imported_on"] = date.today().isoformat()
        print("Fuel weeks:", ", ".join(f"{w['from']}..{w['to']} {w['pct']}%" for w in weeks))
    if args.demand:
        demand = parse_demand_text(pdf_text(args.demand))
        if demand["rates"]["europe"] <= 0 and demand["rates"]["rest_of_world"] <= 0:
            raise ValueError("Suspicious demand row: all zero")
        data["demand"] = {**demand, "source": Path(args.demand).name,
                          "imported_on": date.today().isoformat(), "regions": region_map()}
        print("Demand", demand["valid_from"], "..", demand["valid_to"], demand["rates"])
    SURCHARGES_PATH.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {SURCHARGES_PATH}")


if __name__ == "__main__":
    main()
