"""Packing list generated from a complete packing plan."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pypdf import PdfReader

from app import database
from app.main import (ExporterPayload, OrderItemPayload, OrderReviewPayload, analyze_order,
                      create_manual_order, export_packing_list, get_exporter, get_order,
                      update_exporter, _shipment_for)
from app.labels import render_box_labels_pdf
from app.main import export_box_labels
from app.shipping_docs import build_shipment, format_date, product_code, render_packing_list_pdf


def pdf_text(content: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages), len(reader.pages)


class PackingListTests(unittest.TestCase):
    def setUp(self):
        # sqlite connections are released by GC, which Windows needs before deleting the file.
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path_patch = patch.object(database, "DB_PATH", Path(self.folder.name) / "docs.db")
        self.path_patch.start()
        database.init_db()

    def tearDown(self):
        self.path_patch.stop()
        self.folder.cleanup()

    def make_order(self, quantity=3):
        order = create_manual_order(OrderReviewPayload(
            order_number="QA-PL-1", client="Synthetic GmbH", order_date="2026-11-10",
            delivery_address="Sinterstraße 9\n44795 Bochum\nGermany",
            items=[OrderItemPayload(description="Guantes de nitrilo · caja", quantity=quantity, unit_price=10)]))
        analyze_order(order["id"])
        return get_order(order["id"])

    def test_shipment_totals_match_the_plan(self):
        order = self.make_order()
        result = order["plan"]["result"]
        shipment = _shipment_for(order["id"])
        self.assertEqual(shipment["totals"]["boxes"], result["box_count"])
        self.assertEqual(shipment["totals"]["units"], result["unit_count"])
        self.assertAlmostEqual(shipment["totals"]["gross_weight_kg"], result["total_weight_kg"], places=2)
        # Real product code, not the internal product id the plan stores as "sku".
        product_code = order["items"][0]["sku"]
        self.assertTrue(all(line["code"] == product_code for b in shipment["boxes"] for line in b["lines"]))
        self.assertGreater(shipment["totals"]["net_weight_kg"], 0)
        self.assertLessEqual(shipment["totals"]["net_weight_kg"], shipment["totals"]["gross_weight_kg"])
        self.assertEqual(shipment["destination"], "Germany")

    def test_packing_list_pdf_contents(self):
        order = self.make_order()
        response = export_packing_list(order["id"])
        self.assertIn("packing-list-QA-PL-1.pdf", response.headers["content-disposition"])
        text, pages = pdf_text(response.body)
        for expected in ["PACKING LIST", "QA-PL-1", "10/11/2026", "Synthetic GmbH", "Thought3D Ltd",
                         "Consignment Total", "Total no. of boxes", f"Page 1 of {pages}"]:
            self.assertIn(expected, text)

    def test_exporter_settings_are_used_in_the_document(self):
        self.assertEqual(get_exporter()["company"], "Thought3D Ltd.")
        self.assertIn("Qormi", get_exporter()["address"])
        update_exporter(ExporterPayload(company="Acme Export", address="1 Road", signatory_name="Ana"))
        order = self.make_order()
        text, _ = pdf_text(export_packing_list(order["id"]).body)
        self.assertIn("Acme Export", text)
        self.assertIn("Ana", text)

    def test_requires_a_complete_plan(self):
        order = create_manual_order(OrderReviewPayload(
            order_number="QA-PL-2", client="X",
            items=[OrderItemPayload(description="Guantes de nitrilo · caja", quantity=1)]))
        with self.assertRaises(HTTPException) as ctx:
            export_packing_list(order["id"])
        self.assertEqual(ctx.exception.status_code, 422)

    def test_long_orders_span_pages_with_page_numbers(self):
        # Synthetic plan (no packing run) with enough lines to overflow one A4 page.
        boxes = [{"sequence": n, "box_code": "B40", "dimensions_cm": [40, 30, 30], "gross_weight_kg": 5.5,
                  "product_weight_kg": 5, "items": [{"sku": str(i), "name": f"Item {i}", "quantity": 2}
                                                    for i in range(1, 9)]} for n in range(1, 9)]
        order = {"order_number": "QA-LONG", "order_date": "2026-11-10", "client": "C",
                 "items": [{"product_id": i, "sku": f"SKU{i}", "description": f"Product {i}", "weight_kg": 0.5}
                           for i in range(1, 9)],
                 "plan": {"result": {"boxes": boxes}}}
        shipment = build_shipment(order)
        self.assertEqual(shipment["totals"]["units"], 128)
        text, pages = pdf_text(render_packing_list_pdf(shipment))
        self.assertGreater(pages, 1)
        self.assertIn(f"Page {pages} of {pages}", text)
        self.assertIn("SKU8", text)

    def test_one_label_page_per_box_with_contents(self):
        order = self.make_order()
        shipment = _shipment_for(order["id"])
        response = export_box_labels(order["id"], None)
        self.assertIn("labels-QA-PL-1.pdf", response.headers["content-disposition"])
        text, pages = pdf_text(response.body)
        self.assertEqual(pages, shipment["totals"]["boxes"])
        total = shipment["totals"]["boxes"]
        for box in shipment["boxes"]:
            self.assertIn(f"BOX {box['sequence']}/{total}", text)
        self.assertIn(shipment["boxes"][0]["lines"][0]["code"], text)
        self.assertIn("Synthetic GmbH", text)

    def test_single_box_label_and_unknown_box(self):
        order = self.make_order()
        response = export_box_labels(order["id"], 1)
        self.assertIn("labels-QA-PL-1-box1.pdf", response.headers["content-disposition"])
        self.assertEqual(pdf_text(response.body)[1], 1)
        with self.assertRaises(HTTPException) as ctx:
            export_box_labels(order["id"], 99)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_label_is_10_by_10_cm(self):
        shipment = build_shipment(self.synthetic_order(lines=3))
        page = PdfReader(io.BytesIO(render_box_labels_pdf(shipment, 1))).pages[0]
        # 10 cm = 283.46 pt
        self.assertAlmostEqual(float(page.mediabox.width), 283.46, places=1)
        self.assertAlmostEqual(float(page.mediabox.height), 283.46, places=1)

    def test_small_box_lists_every_line(self):
        shipment = build_shipment(self.synthetic_order(lines=3))
        text, _ = pdf_text(render_box_labels_pdf(shipment, 1))
        for code in ["SKU1", "SKU2", "SKU3"]:
            self.assertIn(code, text)
        self.assertNotIn("more line(s)", text)

    def test_crowded_label_still_fits_on_one_page(self):
        shipment = build_shipment(self.synthetic_order(lines=30, long_names=True))
        text, pages = pdf_text(render_box_labels_pdf(shipment, 1))
        self.assertEqual(pages, 1)
        self.assertIn("more line(s) - see packing list", text)

    def test_packing_list_has_a_3d_guide_page_per_box(self):
        order = self.make_order()
        shipment = _shipment_for(order["id"])
        reader = PdfReader(io.BytesIO(export_packing_list(order["id"]).body))
        pages = [page.extract_text() or "" for page in reader.pages]
        total = shipment["totals"]["boxes"]
        self.assertEqual(len(pages), 1 + total)
        self.assertNotIn("Measurements", pages[0])
        self.assertNotIn("Total volume", pages[0])
        self.assertIn("PACKING LIST", pages[0])
        for box, text in zip(shipment["boxes"], pages[1:]):
            self.assertTrue(box["placements"])
            for expected in ["PACKING GUIDE", f"BOX {box['sequence']} / {total}", "3D VIEW", "Layer 1",
                             "FRONT", "Packed by"]:
                self.assertIn(expected, text)

    def test_guide_without_positions_still_renders(self):
        shipment = build_shipment(self.synthetic_order(lines=2, boxes=1))
        text, pages = pdf_text(render_packing_list_pdf(shipment))
        self.assertEqual(pages, 2)
        self.assertIn("3D layout not available", text)

    def test_from_and_to_are_never_truncated(self):
        name = "Very Long Consignee Name International Trading Company GmbH & Co. KG"
        address = ("c/o Versandmanufaktur GmbH, Logistikzentrum Nord, Halle 7, Tor 12\n"
                   "Sinterstraße 9-11, Gewerbegebiet Am Kanal\n44795 Bochum\nGermany")
        shipment = build_shipment(self.synthetic_order(lines=3), destination="Germany",
                                  consignee={"name": name, "address": address,
                                             "phone": "+49 234 1234567", "email": "logistics.inbound@example-company.de"})
        text, pages = pdf_text(render_box_labels_pdf(shipment, 1))
        flat = " ".join(text.split())
        self.assertEqual(pages, 1)
        for part in [*name.split(), "Logistikzentrum", "Gewerbegebiet", "Bochum", "+49 234 1234567",
                     "Qormi,", "QRM 4000"]:
            self.assertIn(part, flat)
        self.assertIn("logistics.inbound@", flat.replace(" ", ""))

    def test_country_colors_are_unique_and_readable(self):
        from app.country_style import COUNTRY_COLORS, PALETTE, color_hex

        def luminance(hex_color):
            channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        self.assertEqual(len(set(COUNTRY_COLORS.values())), len(COUNTRY_COLORS))
        for color in set(PALETTE) | set(COUNTRY_COLORS.values()):
            self.assertGreaterEqual(1.05 / (luminance(color) + 0.05), 4.5, color)  # white text, WCAG AA
        self.assertEqual(color_hex("ZZ"), color_hex("zz"))  # stable fallback

    def test_destination_flag_assets_exist(self):
        from app.country_style import COUNTRY_COLORS
        from app.labels import FLAGS_DIR, country_code
        for code in COUNTRY_COLORS:
            self.assertTrue((FLAGS_DIR / f"{code.lower()}.png").exists(), code)
        self.assertEqual(country_code("United States"), "US")
        self.assertEqual(country_code("United Kingdom"), "GB")

    @staticmethod
    def synthetic_order(lines=8, boxes=8, long_names=False):
        name = "Very long product description for a 3D printing adhesive stick " if long_names else "Product "
        box_list = [{"sequence": n, "box_code": "B40", "dimensions_cm": [40, 30, 30], "gross_weight_kg": 5.5,
                     "product_weight_kg": 5, "items": [{"sku": str(i), "name": f"Item {i}", "quantity": 2}
                                                       for i in range(1, lines + 1)]} for n in range(1, boxes + 1)]
        return {"order_number": "QA-LONG", "order_date": "2026-11-10", "client": "C",
                "items": [{"product_id": i, "sku": f"SKU{i}", "description": f"{name}{i}", "weight_kg": 0.5}
                          for i in range(1, lines + 1)],
                "plan": {"result": {"boxes": box_list}}}

    def test_product_code_prefers_po_code_and_hides_internal_hash(self):
        self.assertEqual(product_code({"sku_raw": "MPP2018", "sku": "PRODUCT-7AEAE9C44E"}), "MPP2018")
        self.assertEqual(product_code({"sku_raw": "", "sku": "PRODUCT-7AEAE9C44E"}), "")
        self.assertEqual(product_code({"sku_raw": "", "sku": "GLV-100"}), "GLV-100")

    def test_format_date(self):
        self.assertEqual(format_date("2026-11-10"), "10/11/2026")
        self.assertEqual(format_date("next week"), "next week")
        self.assertEqual(format_date(None), "")


if __name__ == "__main__":
    unittest.main()
