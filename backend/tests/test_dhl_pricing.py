import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi import HTTPException
from reportlab.pdfgen import canvas

from app import database, dhl_pricing
from app.destinations import detect_destination
from app.dhl_pricing import PricingError, base_rate, piece_handling, quote, tariff_weight
from app.dhl_surcharges import demand_for, fuel_for, merge_fuel_weeks, parse_demand_text, parse_fuel_text
from app.main import ShippingQuotePayload, create_order_shipping_quote, get_dhl_config, get_order_shipping_quote

# Manual rates matching the reference DHL calculator's self-check (Germany, zone 1).
REFERENCE_INPUTS = dict(destination="DE", shipment_date="2026-11-04", service="express_worldwide",
                        fuel_source="manual", fuel_pct=49.5, demand_source="manual", demand_per_kg=0.3,
                        gogreen_per_kg=0.2, vat_pct=18)
SURCHARGES = {
    "fuel": {"weeks": [{"from": "2026-11-02", "to": "2026-11-08", "pct": 49.5},
                       {"from": "2026-11-09", "to": "2026-11-15", "pct": 50.0}]},
    "demand": {"valid_from": "2026-10-01", "valid_to": "2027-02-05",
               "rates": {"china_hk": 0.0, "asia": 0.0, "oceania": 0.5, "europe": 0.3, "americas": 0.5,
                         "mena": 0.0, "rest_of_world": 0.8},
               "regions": {"DE": "europe", "US": "americas", "AF": "rest_of_world", "CN": "china_hk"}},
    "defaults": {"vat_pct": 18.0, "gogreen_per_kg": 0.2},
}
FUEL_TEXT = """International Time Definite services
Effective Date Surcharges
November 16-22, 2026 50.00%
November 9-15, 2026 50.00%
October 26-November 1, 2026 49.25%
December 28-January 3, 2027 51.00%
Domestic Time Definite services
November 16-22, 2026 40.00%"""
DEMAND_TEXT = """DHL will be activating a Demand Surcharge
effective October 1, 2026 until February 5, 2027.
Kong 0.10 0.10 0.50 1.90 2.00 1.50 0.80
South Asia 0.10 0.10 0.50 1.10 1.40 0.50 0.80
Europe 0.00 0.00 0.50 0.30 0.50 0.00 0.80
Americas 0.00 0.00 0.50 0.00 0.30 0.00 0.80"""


def piece(weight, dims=(10, 10, 10), sequence=1):
    return {"sequence": sequence, "box_code": "B", "box_name": "Box", "dimensions_cm": list(dims),
            "gross_weight_kg": weight}


def line(result, key):
    return next((l["amount"] for l in result["lines"] if l["key"] == key), 0.0)


def pdf_bytes(lines):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for index, text in enumerate(lines):
        pdf.drawString(50, 800 - index * 16, text)
    pdf.save()
    return buffer.getvalue()


class TariffTests(unittest.TestCase):
    def test_rate_card_anchors_zone_1(self):
        for weight, expected in {0.5: 18.68, 1.5: 19.39, 10: 59.45, 10.001: 63.11, 20.001: 135.58,
                                 30.001: 197.12, 70.001: 433.38, 300.001: 2120.00}.items():
            self.assertAlmostEqual(base_rate(tariff_weight(weight), 1), expected, places=2, msg=weight)

    def test_other_zones_use_their_own_column_and_adders(self):
        self.assertAlmostEqual(base_rate(70, 6), 563.12)
        self.assertAlmostEqual(base_rate(10.5, 6), 79.72 + 6.59)  # zone 6 adder lives in column I
        self.assertAlmostEqual(base_rate(71, 3), 509.51 + 8.80)

    def test_weight_rounding_steps(self):
        self.assertEqual(tariff_weight(0.01), 0.5)
        self.assertEqual(tariff_weight(1.01), 1.5)
        self.assertEqual(tariff_weight(30.0), 30.0)
        self.assertEqual(tariff_weight(30.2), 31.0)

    def test_handling_precedence(self):
        self.assertEqual(piece_handling(25, (10, 10, 10))["amount"], 20)  # non-conveyable
        self.assertEqual(piece_handling(70, (101, 50, 40))["type"], "oversize")
        self.assertEqual(piece_handling(70.001, (101, 50, 40))["amount"], 100)
        self.assertEqual(piece_handling(1, (101, 100, 50))["type"], "overweight")  # volumetric 101 kg
        self.assertEqual(piece_handling(5, (90, 81, 10))["type"], "oversize")  # second side > 80
        self.assertEqual(piece_handling(5, (30, 30, 30))["amount"], 0)

    def test_zones_cover_fixed_broken_cells(self):
        zones = {z["code"]: z["zone"] for z in dhl_pricing.load_tariff()["zones"]}
        self.assertEqual((zones["AF"], zones["AL"], zones["HK"], zones["HU"], zones["SA"]), (6, 3, 5, 2, 5))
        self.assertEqual((zones["GB"], zones["GB-SA"], zones["US"]), (1, 3, 4))
        self.assertNotIn("MT", zones)


class SurchargeTests(unittest.TestCase):
    def test_parse_fuel_weeks_only_international_and_year_rollover(self):
        weeks = parse_fuel_text(FUEL_TEXT)
        self.assertEqual([w["pct"] for w in weeks], [49.25, 50.0, 50.0, 51.0])
        self.assertEqual((weeks[0]["from"], weeks[0]["to"]), ("2026-10-26", "2026-11-01"))
        self.assertEqual((weeks[-1]["from"], weeks[-1]["to"]), ("2026-12-28", "2027-01-03"))
        merged = merge_fuel_weeks(weeks, [{"from": "2026-11-16", "to": "2026-11-22", "pct": 48.0}])
        self.assertEqual(len(merged), 4)
        self.assertEqual(next(w for w in merged if w["from"] == "2026-11-16")["pct"], 48.0)

    def test_parse_demand_europe_row_and_period(self):
        demand = parse_demand_text(DEMAND_TEXT)
        self.assertEqual((demand["valid_from"], demand["valid_to"]), ("2026-10-01", "2027-02-05"))
        self.assertEqual(demand["rates"]["europe"], 0.3)
        self.assertEqual(demand["rates"]["rest_of_world"], 0.8)
        self.assertEqual(demand["rates"]["china_hk"], 0.0)

    def test_lookups_by_date_and_region(self):
        self.assertEqual(fuel_for(date(2026, 11, 13), SURCHARGES)["pct"], 50.0)
        self.assertIsNone(fuel_for(date(2026, 11, 20), SURCHARGES))
        self.assertEqual(demand_for("AF", date(2026, 11, 13), SURCHARGES)["per_kg"], 0.8)
        self.assertEqual(demand_for("US", date(2026, 11, 13), SURCHARGES)["per_kg"], 0.5)
        self.assertEqual(demand_for("DE", date(2026, 9, 30), SURCHARGES)["per_kg"], 0.0)

    def test_shipped_schedule_covers_all_destinations(self):
        regions = json.loads(Path(dhl_pricing.__file__).with_name("dhl_surcharges.json").read_text("utf-8"))["demand"]["regions"]
        self.assertEqual(set(regions), {z["code"] for z in dhl_pricing.load_tariff()["zones"]})
        self.assertEqual((regions["DE"], regions["US"], regions["AU"], regions["AE"]), ("europe", "americas", "oceania", "mena"))


class DestinationDetectionTests(unittest.TestCase):
    def test_po_delivery_block_wins_over_malta_vendor_and_invoice(self):
        text = "Thought3D Ltd\nKordin Industrial Estate\nPaola\nMalta\nDelivery Address\nACME GmbH\nSinterstrasse 9\n44795 Bochum\nGermany\nInvoice Address\nAcme SARL\nParis\nFrance"
        self.assertEqual(detect_destination([("PO", text)]), {"code": "DE", "source": "PO (delivery address)"})

    def test_us_state_zip_and_client_fallbacks(self):
        po = "Vendor Address\nThought3D Ltd\nQormi, QRM4000 MT\nDeliver To\nRev1 Tech Warehouse\n2901 Auburn Rd\nAuburn Hills, MI 48326"
        self.assertEqual(detect_destination([("PO", po)])["code"], "US")
        self.assertEqual(detect_destination([("PO", ""), ("Client country", "US")])["code"], "US")
        self.assertEqual(detect_destination([("Client", "Unit B2\nTF1 7AF\nUnited Kingdom")])["code"], "GB")
        self.assertEqual(detect_destination([("x", "Trenton, New Jersey")])["code"], "US")
        self.assertIsNone(detect_destination([("x", "Paola PLA3000\nMalta"), ("y", "contact us")]))


class QuoteTests(unittest.TestCase):
    def test_reference_quote_matches_dhl_calculator(self):
        result = quote([piece(1.2)], REFERENCE_INPUTS, SURCHARGES)
        self.assertEqual(result["tariff_weight_kg"], 1.5)
        self.assertEqual(line(result, "transport"), 19.39)
        self.assertEqual(line(result, "demand"), 0.45)
        self.assertEqual(line(result, "fuel"), 9.82)
        self.assertEqual(line(result, "gogreen"), 0.30)
        self.assertEqual((result["subtotal"], result["vat"], result["total"]), (29.96, 5.39, 35.35))

    def test_surcharge_arithmetic_with_premium_remote_and_handling(self):
        inputs = dict(REFERENCE_INPUTS, service="premium_1200", remote_area=True, fuel_pct=20,
                      demand_per_kg=1, other_eur=5)
        result = quote([piece(10, (101, 20, 20))], inputs, SURCHARGES)  # oversize piece -> 20, volumetric 8.08 kg
        # 59.45 + 10 + 20 + 24 (remote minimum) + 10 demand = 123.45; fuel 20% = 24.69
        self.assertEqual(line(result, "fuel"), 24.69)
        self.assertEqual(result["subtotal"], 155.14)
        self.assertEqual(result["vat"], 27.93)
        self.assertEqual(result["total"], 183.07)

    def test_multi_piece_is_one_shipment_with_volumetric_weight(self):
        boxes = [piece(1.0, (40, 30, 20), 1), piece(0.4, (10, 10, 10), 2)]  # 4.8 kg vol + 0.4 kg
        result = quote(boxes, REFERENCE_INPUTS, SURCHARGES)
        self.assertEqual(result["tariff_weight_kg"], 5.5)
        self.assertEqual(line(result, "transport"), 36.23)

    def test_dhl_schedules_fill_fuel_and_demand_automatically(self):
        auto = dict(destination="AF", shipment_date="2026-11-13")
        result = quote([piece(1.2)], auto, SURCHARGES)
        self.assertEqual((result["rates"]["fuel_pct"], result["rates"]["demand_per_kg"]), (50.0, 0.8))
        self.assertEqual(result["rates"]["fuel_source"], "dhl")
        self.assertIn("Rest of the World", result["rates"]["demand_note"])
        self.assertEqual(quote([piece(1.2)], dict(auto, destination="DE"), SURCHARGES)["rates"]["demand_per_kg"], 0.3)
        legacy = quote([piece(1.2)], dict(auto, fuel_source="saved", demand_source="saved"), SURCHARGES)
        self.assertEqual(legacy["rates"]["fuel_source"], "dhl")
        with self.assertRaisesRegex(PricingError, "fuel surcharge covering 2026-11-20"):
            quote([piece(1.2)], dict(auto, shipment_date="2026-11-20"), SURCHARGES)
        manual = quote([piece(1.2)], dict(auto, shipment_date="2026-11-20", fuel_source="manual", fuel_pct=0), SURCHARGES)
        self.assertEqual(line(manual, "fuel"), 0)

    def test_rejects_bad_inputs(self):
        for inputs in (dict(REFERENCE_INPUTS, destination="MT"), dict(REFERENCE_INPUTS, shipment_date="2027-01-02"),
                       dict(REFERENCE_INPUTS, service="x"), dict(REFERENCE_INPUTS, fuel_pct=None)):
            with self.assertRaises(PricingError):
                quote([piece(1)], inputs, SURCHARGES)
        with self.assertRaises(PricingError):
            quote([piece(301)], REFERENCE_INPUTS, SURCHARGES)
        with self.assertRaises(PricingError):
            quote([], REFERENCE_INPUTS, SURCHARGES)


class ShippingEndpointTests(unittest.TestCase):
    def setUp(self):
        self.original = database.DB_PATH
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        database.DB_PATH = Path(self.tmp.name) / "dhl.db"
        database.init_db()
        with database.connect() as db:
            now = database.now_iso()
            db.execute("INSERT INTO orders (order_number, client, created_at, updated_at) VALUES ('DHL-1', 'Acme', ?, ?)",
                       (now, now))
            self.order_id = db.execute("SELECT id FROM orders WHERE order_number = 'DHL-1'").fetchone()["id"]
            db.execute("INSERT INTO clients (customer, country, created_at, updated_at) VALUES ('Acme', 'France', ?, ?)",
                       (now, now))
            self.add_plan(db, 1.2)

    def tearDown(self):
        database.DB_PATH = self.original
        self.tmp.cleanup()

    def add_plan(self, db, weight):
        result = {"boxes": [piece(weight)], "box_count": 1, "total_weight_kg": weight}
        db.execute("""INSERT INTO packing_plans (order_id, box_count, average_utilization, total_weight_kg,
                      result_json, created_at) VALUES (?, 1, 0.5, ?, ?, ?)""",
                   (self.order_id, weight, json.dumps(result), database.now_iso()))

    def test_destination_from_po_document_then_client(self):
        self.assertEqual(get_order_shipping_quote(self.order_id)["detected_destination"]["code"], "FR")
        with database.connect() as db:
            db.execute("INSERT INTO source_documents (order_id, filename, content) VALUES (?, 'po.pdf', ?)",
                       (self.order_id, pdf_bytes(["Thought3D Ltd", "Paola", "Malta", "Delivery Address",
                                                  "ACME GmbH", "44795 Bochum", "Germany"])))
        detected = get_order_shipping_quote(self.order_id)["detected_destination"]
        self.assertEqual((detected["code"], detected["zone"], detected["source"]), ("DE", 1, "PO document (delivery address)"))

    def test_saves_quote_and_marks_stale(self):
        self.assertIsNone(get_order_shipping_quote(self.order_id)["quote"])
        saved = create_order_shipping_quote(self.order_id, ShippingQuotePayload(**REFERENCE_INPUTS))
        self.assertEqual(saved["quote"]["result"]["total"], 35.35)
        loaded = get_order_shipping_quote(self.order_id)
        self.assertEqual(loaded["quote"]["inputs"]["destination"], "DE")
        self.assertFalse(loaded["stale"])
        with database.connect() as db:
            self.add_plan(db, 3)
        self.assertTrue(get_order_shipping_quote(self.order_id)["stale"])

    def test_config_exposes_schedules_and_regions(self):
        config = get_dhl_config()
        self.assertEqual(config["meta"]["currency"], "EUR")
        self.assertTrue(config["surcharges"]["fuel"]["weeks"])
        self.assertNotIn("regions", config["surcharges"]["demand"])
        germany = next(d for d in config["destinations"] if d["code"] == "DE")
        self.assertEqual((germany["zone"], germany["demand_region"]), (1, "europe"))

    def test_quote_errors_are_422(self):
        with self.assertRaises(HTTPException) as error:
            create_order_shipping_quote(self.order_id, ShippingQuotePayload(destination="MT", shipment_date="2026-11-12"))
        self.assertEqual(error.exception.status_code, 422)
        with self.assertRaises(HTTPException) as missing:
            create_order_shipping_quote(999, ShippingQuotePayload(**REFERENCE_INPUTS))
        self.assertEqual(missing.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
