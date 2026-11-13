"""Convert the DHL Express Malta 2026 rate card workbook into a clean JSON tariff.

The workbook is a PDF-to-Excel conversion (merged cells, prices packed into text,
shifted columns), so it is converted once here and the validated JSON is committed:

    python backend/scripts/build_dhl_tariff.py "path/to/DHL 2026.xlsx"

Only the Express Worldwide EXPORT rates (Table 1), export/import zoning (Table 3),
the volumetric divisor (Table 8) and per-piece/per-kg surcharges (Table 11) are used.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import openpyxl

OUTPUT = Path(__file__).resolve().parents[1] / "app" / "dhl_tariff_2026.json"
ZONE_COLUMNS = "CDEFGH"  # rate rows: zones 1-6
ADDER_ZONE_COLUMNS = "CDEFGI"  # adder rows: zone 6 shifts to column I
NUMBER = re.compile(r"\d+(?:\.\d+)?")
CODE = re.compile(r"\(([A-Z]{2})\)")

# Zone cells that the PDF conversion broke apart; fixed by hand from the rate card.
MANUAL_ZONES = {
    "ST": ("Sao Tome And Principe", 6),
    "SA": ("Saudi Arabia", 5),
    "HN": ("Honduras", 6),
    "HK": ("Hong Kong SAR China", 5),
    "HU": ("Hungary", 2),
    "MP": ("Northern Mariana Islands", 6),
}
SKIP_CELLS = {"Table 3!K4", "Table 3!E34", "Table 3!E35", "Table 3!H39", "Table 3!K47", "Table 3!K48"}

# Anchors from the reference DHL calculator (zone 1, export non-documents).
ANCHORS = {0.5: 18.68, 1.5: 19.39, 10: 59.45, 10.001: 63.11, 20.001: 135.58,
           30.001: 197.12, 70.001: 433.38, 300.001: 2120.00}


def money(value) -> float:
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    match = NUMBER.search(str(value or ""))
    if not match:
        raise ValueError(f"Expected a price, got {value!r}")
    return round(float(match.group()), 2)


def rate_rows(ws) -> list[list[float]]:
    rows = []
    for r in range(9, 53):  # "Non-documents from 0.5 KG" block
        weight = ws[f"A{r}"].value
        rows.append([float(weight), *[money(ws[f"{c}{r}"].value) for c in ZONE_COLUMNS]])
    return rows


def adder_rows(ws) -> list[dict]:
    bands = []
    for r, step in ((54, 0.5), (55, 0.5), (57, 1.0), (58, 1.0), (59, 1.0)):
        numbers = [float(n) for n in NUMBER.findall(f"{ws[f'A{r}'].value} {ws[f'B{r}'].value or ''}")]
        bands.append({
            "from_kg": numbers[0],
            "to_kg": numbers[1],
            "step_kg": step,
            "rates": [money(ws[f"{c}{r}"].value) for c in ADDER_ZONE_COLUMNS],
        })
    return bands


def zones(ws) -> list[dict]:
    found: dict[str, dict] = {}
    for row in range(4, 61):
        for name_col, zone_col in (("A", "B"), ("E", "F"), ("H", "I"), ("K", "M")):
            ref = f"{name_col}{row}"
            names, zone_values = ws[ref].value, ws[f"{zone_col}{row}"].value
            if f"Table 3!{ref}" in SKIP_CELLS or not names or zone_values is None:
                continue
            codes = CODE.findall(str(names))
            zone_list = [int(z) for z in str(zone_values).split()]
            labels = [part.strip() for part in re.split(r"\([A-Z]{2}\)", str(names).replace("\n", " ")) if part.strip()]
            if len(codes) != len(zone_list) or len(labels) != len(codes):
                raise ValueError(f"Cannot split zone cell {ref}: {names!r} / {zone_values!r}")
            for code, label, zone in zip(codes, labels, zone_list):
                if code in found:
                    raise ValueError(f"Duplicate country {code} at {ref}")
                found[code] = {"code": code, "name": label, "zone": zone}
    for code, (name, zone) in MANUAL_ZONES.items():
        if code in found:
            raise ValueError(f"Manual zone {code} already parsed")
        found[code] = {"code": code, "name": name, "zone": zone}
    found["GB"] = {"code": "GB", "name": "United Kingdom (rest of UK)", "zone": 1}
    found["GB-SA"] = {
        "code": "GB-SA",
        "name": "United Kingdom (listed service areas: LON, MAN, GLA, EDI…)",
        "zone": 3,
    }
    return sorted(found.values(), key=lambda c: c["name"])


def build(path: Path) -> dict:
    wb = openpyxl.load_workbook(path, data_only=True)
    t1, t3, t8 = wb["Table 1"], wb["Table 3"], wb["Table 8"]
    divisor = t8["D4"].value
    if divisor != 5000:
        raise ValueError(f"Unexpected volumetric divisor {divisor!r}")
    return {
        "meta": {
            "source": path.name + " (DHL Express Malta rate card, customer Thought3D Ltd)",
            "version": "DHL-MT-2026",
            "carrier": "DHL Express",
            "origin": "MT",
            "currency": "EUR",
            "valid_to": "2026-12-31",
            "volumetric_divisor": divisor,
            "max_piece_kg": 300,
            "max_piece_cm": 300,
            "max_shipment_kg": 3000,
            "notes": [
                "Rates exclude VAT, fuel surcharge and other surcharges.",
                "Fuel % and demand surcharge are published weekly on the DHL website and are not in the rate card.",
            ],
        },
        "export": {
            "service": "Express Worldwide",
            "non_documents": rate_rows(t1),
            "adders": adder_rows(t1),
            "premium": {"express_worldwide": 0.0, "premium_1200": 10.0, "premium_0900": 35.0},
        },
        "zones": zones(t3),
        "surcharges": {
            "oversize_piece": {"amount": 20.0, "longest_side_cm": 100, "second_side_cm": 80},
            "overweight_piece": {"amount": 100.0, "over_kg": 70},
            "non_conveyable_piece": {"amount": 20.0, "from_kg": 25, "to_kg": 70},
            "remote_area_delivery": {"per_kg": 0.5, "minimum": 24.0},
            "gogreen_plus": {"per_kg": 0.2, "waived_in_rate_card": True},
        },
    }


def validate(tariff: dict) -> None:
    from app.dhl_pricing import base_rate, tariff_weight  # noqa: PLC0415

    for row in tariff["export"]["non_documents"]:
        if len(row) != 7 or row[0] <= 0:
            raise ValueError(f"Bad rate row {row}")
    for weight, expected in ANCHORS.items():
        got = base_rate(tariff_weight(weight), 1, tariff)
        if abs(got - expected) > 0.005:
            raise ValueError(f"Anchor {weight} kg: expected {expected}, got {got}")
    codes = {z["code"] for z in tariff["zones"]}
    for code in ("DE", "IT", "US", "CN", "AU", "AF", "HK", "SA", "ST", "MP"):
        if code not in codes:
            raise ValueError(f"Missing zone for {code}")
    if any(not 1 <= z["zone"] <= 6 for z in tariff["zones"]):
        raise ValueError("Zone outside 1-6")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: build_dhl_tariff.py <DHL 2026.xlsx>")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    tariff = build(Path(sys.argv[1]))
    validate(tariff)
    OUTPUT.write_text(json.dumps(tariff, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT} with {len(tariff['zones'])} destinations")


if __name__ == "__main__":
    main()
