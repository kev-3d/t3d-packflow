import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi import HTTPException
from pypdf import PdfReader

from app import database, tsca
from app.main import (ProductPayload, TscaPreviewPayload, TscaSavePayload, create_product, export_tsca, get_tsca,
                      list_products, preview_tsca, save_tsca, update_product)


def pdf_text(content: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(content))
    return "\n".join(page.extract_text() for page in reader.pages), len(reader.pages)


def item(number="5350583000191", name="Magigoo Glide kit", description="Water based adhesive"):
    return {"product_id": None, "number": number, "name": name, "description": description}


def complete_draft(**changes):
    return {"date": "November 2026", "reference": "SH-1", "certification": "negative",
            "items": [item()], **changes}


class DraftTests(unittest.TestCase):
    def test_default_draft_dedupes_products_and_prefers_catalog_wording(self):
        lines = [
            {"product_id": 1, "quantity": 2, "description": "PO text", "sku_raw": "MGK", "sku": "PRODUCT-0123456789",
             "product_name": "Glide kit", "ean": "5350583000191", "tsca_name": "", "tsca_description": "Adhesive"},
            {"product_id": 1, "quantity": 5, "description": "PO text", "sku_raw": "MGK", "sku": "X",
             "product_name": "Glide kit", "ean": "5350583000191", "tsca_name": "", "tsca_description": "Adhesive"},
            {"product_id": 2, "quantity": 1, "description": "Roller", "sku_raw": "MGR2025", "sku": "PRODUCT-AAAAAAAAAA",
             "product_name": "Glide roller", "ean": "", "tsca_name": "Magigoo Glide roller", "tsca_description": ""},
            {"product_id": None, "quantity": 1, "description": "Sample", "sku_raw": "", "sku": None},
        ]
        draft = tsca.default_draft({"order_number": "PO-7"}, lines, today=date(2026, 11, 24))
        self.assertEqual(draft["date"], "November 2026")
        self.assertEqual(draft["reference"], "PO-7")
        self.assertEqual(draft["certification"], "")
        self.assertEqual([i["number"] for i in draft["items"]], ["5350583000191", "MGR2025", ""])
        self.assertEqual([i["name"] for i in draft["items"]], ["Glide kit", "Magigoo Glide roller", "Sample"])
        self.assertEqual(draft["items"][0]["description"], "Adhesive")

    def test_issues_list_everything_missing(self):
        draft = tsca.normalize_draft({"date": "", "reference": "", "certification": "",
                                      "items": [item(number="", description="")]})
        issues = tsca.draft_issues(draft)
        self.assertEqual(len(issues), 3)
        self.assertIn("product number, description", issues[2])
        self.assertEqual(tsca.draft_issues(tsca.normalize_draft(complete_draft())), [])
        self.assertIn("Add at least one product.", tsca.draft_issues(tsca.normalize_draft(complete_draft(items=[]))))

    def test_rejects_invalid_structure_and_long_text(self):
        for bad in ({**complete_draft(), "certification": "maybe"},
                    complete_draft(items=[item(description="x" * 4001)]),
                    complete_draft(items=[item()] * 201),
                    complete_draft(date="x" * 101), "nope"):
            with self.assertRaises(tsca.TscaError):
                tsca.normalize_draft(bad)


class PdfTests(unittest.TestCase):
    def test_final_certificate_matches_template_wording(self):
        text, pages = pdf_text(tsca.render_tsca_pdf(complete_draft(), final=True))
        self.assertEqual(pages, 1)
        for expected in ("CERTIFICATE OF COMPLIANCE", "TSCA Certification", "DATE OF ISSUE", "November 2026",
                         "SH-1", "12 months", "1 item", "CERTIFICATION STATEMENT", "Negative certification",
                         "PRODUCTS COVERED", "5350583000191", "Magigoo Glide kit", "Page 1 of 1",
                         "info@thought3d.com", "MT 2217-2828"):
            self.assertIn(expected, text)
        self.assertNotIn("DRAFT", text)

    def test_draft_is_marked_and_long_tables_repeat_the_header(self):
        content = tsca.render_tsca_pdf(complete_draft(certification="", items=[item()] * 45), final=False)
        text, pages = pdf_text(content)
        self.assertGreater(pages, 1)
        self.assertEqual(text.count("Product number (EAN)"), pages)
        self.assertIn("DRAFT - NOT FOR SUBMISSION", text)
        self.assertIn(f"Page {pages} of {pages}", text)

    def test_final_refuses_an_incomplete_draft(self):
        with self.assertRaises(tsca.TscaError):
            tsca.render_tsca_pdf(complete_draft(certification=""), final=True)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "tsca.db"
        database.init_db()
        with database.connect() as db:
            now = database.now_iso()
            self.product_id = db.insert_returning_id(
                """INSERT INTO products (sku, name, length_cm, width_cm, height_cm, weight_kg, created_at)
                   VALUES ('PRODUCT-1111111111', 'Glide kit', 21, 13, 7.5, .63, ?) RETURNING id""", (now,))
            self.order_id = db.insert_returning_id(
                """INSERT INTO orders (order_number, client, delivery_address, created_at, updated_at)
                   VALUES ('PO-US-1', 'Acme', '1 Main St, Austin, TX 78701, United States', ?, ?) RETURNING id""",
                (now, now))
            db.execute("INSERT INTO order_items (order_id, product_id, sku_raw, description, quantity) VALUES (?, ?, 'MGK', 'Glide kit', 3)",
                       (self.order_id, self.product_id))
            self.other_id = db.insert_returning_id(
                """INSERT INTO orders (order_number, client, delivery_address, created_at, updated_at)
                   VALUES ('PO-DE-1', 'Beta', 'Hauptstr. 1, 10115 Berlin, Germany', ?, ?) RETURNING id""", (now, now))

    def tearDown(self):
        database.DB_PATH = self.original
        self.tmp.cleanup()

    def save(self, order_id, draft, confirmed=False, remember=()):
        return save_tsca(order_id, TscaSavePayload(draft=draft, confirmed=confirmed, remember_product_ids=list(remember)))

    def rejects(self, call):
        with self.assertRaises(HTTPException) as caught:
            call()
        self.assertEqual(caught.exception.status_code, 422)

    def test_only_us_shipments_are_eligible(self):
        us = get_tsca(self.order_id)
        self.assertTrue(us["eligible"])
        self.assertEqual(us["destination"]["code"], "US")
        self.assertEqual(us["draft"]["reference"], "PO-US-1")
        self.assertEqual(us["draft"]["items"][0]["product_id"], self.product_id)
        self.assertFalse(get_tsca(self.other_id)["eligible"])
        self.rejects(lambda: export_tsca(self.other_id, final=False))
        self.rejects(lambda: self.save(self.other_id, complete_draft()))

    def test_saved_quote_destination_takes_precedence(self):
        with database.connect() as db:
            db.execute("INSERT INTO shipping_quotes (order_id, inputs_json, result_json, tariff_version, created_at) "
                       "VALUES (?, ?, '{}', 'v', ?)", (self.other_id, json.dumps({"destination": "US"}), database.now_iso()))
        self.assertTrue(get_tsca(self.other_id)["eligible"])

    def test_final_pdf_requires_a_confirmed_complete_draft(self):
        self.assertIn("DRAFT", pdf_text(export_tsca(self.order_id, final=False).body)[0])
        self.rejects(lambda: export_tsca(self.order_id, final=True))
        self.rejects(lambda: self.save(self.order_id, complete_draft(certification=""), confirmed=True))
        saved = self.save(self.order_id, complete_draft())
        self.assertTrue(saved["saved"])
        self.assertFalse(saved["confirmed"])
        self.rejects(lambda: export_tsca(self.order_id, final=True))
        self.assertTrue(self.save(self.order_id, complete_draft(), confirmed=True)["confirmed"])
        response = export_tsca(self.order_id, final=True)
        self.assertIn("TSCA_Certification_PO-US-1.pdf", response.headers["content-disposition"])
        self.assertNotIn("DRAFT", pdf_text(response.body)[0])
        # Saving again without confirming clears the final review.
        self.assertFalse(self.save(self.order_id, complete_draft())["confirmed"])

    def test_remember_stores_wording_and_ean_in_the_catalog(self):
        row = {**item(), "product_id": self.product_id, "name": "Magigoo Glide kit Original",
               "description": "Water based adhesive"}
        self.save(self.order_id, complete_draft(items=[row]), remember=[self.product_id])
        product = next(p for p in list_products() if p["id"] == self.product_id)
        self.assertEqual((product["ean"], product["tsca_name"], product["tsca_description"]),
                         ("5350583000191", "Magigoo Glide kit Original", "Water based adhesive"))
        with database.connect() as db:
            db.execute("DELETE FROM tsca_declarations")
        fresh = get_tsca(self.order_id)["draft"]["items"][0]
        self.assertEqual(fresh["name"], "Magigoo Glide kit Original")
        self.assertEqual(fresh["number"], "5350583000191")

    def test_product_update_keeps_tsca_fields_when_omitted(self):
        body = {"name": "Glide kit", "length_cm": 21, "width_cm": 13, "height_cm": 7.5, "weight_kg": .63}
        update_product(self.product_id, ProductPayload(**body, ean="5350583000191", tsca_description="D"))
        product = update_product(self.product_id, ProductPayload(**body))
        self.assertEqual((product["ean"], product["tsca_description"]), ("5350583000191", "D"))


    def test_price_list_eans_fill_new_drafts_and_never_overwrite(self):
        with database.connect() as db:
            db.execute("DELETE FROM app_settings WHERE key = 'tsca_catalog_v1'")
            db.execute("UPDATE products SET name = 'Magigoo Glide kit - Supergrip' WHERE id = ?", (self.product_id,))
            typed = db.insert_returning_id(
                """INSERT INTO products (sku, name, length_cm, width_cm, height_cm, weight_kg, ean, created_at)
                   VALUES ('PRODUCT-2222222222', 'Magigoo PP', 3.5, 3.5, 16.5, .085, '999', ?) RETURNING id""",
                (database.now_iso(),))
        database.init_db()
        products = {p["id"]: p for p in list_products()}
        self.assertEqual(products[self.product_id]["ean"], "5350583000337")
        self.assertEqual(products[self.product_id]["tsca_name"], "Magigoo Glide Kit Supergrip")
        self.assertEqual(products[typed]["ean"], "999")  # a typed value is kept
        self.assertIn("polypropylene", products[typed]["tsca_description"])
        item = get_tsca(self.order_id)["draft"]["items"][0]
        self.assertEqual((item["number"], item["name"]), ("5350583000337", "Magigoo Glide Kit Supergrip"))
        # The backfill runs once: a cleared EAN stays cleared on the next start.
        update_product(self.product_id, ProductPayload(name="Magigoo Glide kit - Supergrip", length_cm=21,
                                                       width_cm=13, height_cm=7.5, weight_kg=.63, ean=""))
        database.init_db()
        self.assertEqual(next(p for p in list_products() if p["id"] == self.product_id)["ean"], "")

    def test_every_price_list_entry_matches_its_catalog_name(self):
        expected = {
            "Magigoo Original": "5356660999303", "Magigoo Supergrip": "5350583000207", "Magigoo PP": "5350583999921",
            "Magigoo PC": "5350583999945", "Magigoo PPGF": "5350583999976", "Magigoo PA": "5350583999969",
            "Magigoo Flex": "5350583999983", "Magigoo HT": "5350583999990", "Magigoo Metal": "5350583999808",
            "Magigoo Original Glide 250ml": "5350583000177", "Magigoo Supergrip Glide 250ml": "5350583000221",
            "Magigoo PA Glide 250ml": "5350583000238", "Magigoo PC Glide 250ml": "5350583000245",
            "Magigoo PP Glide 250ml": "5350583000252", "Magigoo HT Glide 250ml": "5350583000214",
            "Magigoo Glide Kit Original": "5350583000191", "Magigoo Glide kit - Supergrip": "5350583000337",
            "Magigoo Pro Kit": "5350583999952", "Magigoo Glide roller": "5350583000184",
        }
        from app.naming import normalize_product_name
        catalog = database.load_tsca_catalog()
        self.assertEqual({name: catalog[normalize_product_name(name)]["ean"] for name in expected}, expected)
        self.assertNotIn(normalize_product_name("Bottle 50ml - No box no instructions"), catalog)
        created = create_product(ProductPayload(name="Magigoo HT Glide 250ml", length_cm=5, width_cm=5,
                                                height_cm=19, weight_kg=.3))
        self.assertEqual(created["ean"], "5350583000214")

    def test_saved_draft_rows_get_the_catalog_ean(self):
        row = {**item(number="", description=""), "product_id": self.product_id, "name": ""}
        self.save(self.order_id, complete_draft(items=[row]))
        with database.connect() as db:
            db.execute("UPDATE products SET ean = '5350583000191', tsca_description = 'Kit' WHERE id = ?", (self.product_id,))
        filled = get_tsca(self.order_id)["draft"]["items"][0]
        self.assertEqual((filled["number"], filled["name"], filled["description"]), ("5350583000191", "Glide kit", "Kit"))

    def test_preview_renders_unsaved_drafts_inline(self):
        response = preview_tsca(self.order_id, TscaPreviewPayload(draft=complete_draft(certification="")))
        self.assertTrue(response.headers["content-disposition"].startswith("inline"))
        self.assertIn("DRAFT", pdf_text(response.body)[0])
        self.assertIn("DRAFT", pdf_text(preview_tsca(self.order_id, TscaPreviewPayload(draft=complete_draft())).body)[0])
        final = preview_tsca(self.order_id, TscaPreviewPayload(draft=complete_draft(), confirmed=True))
        self.assertNotIn("DRAFT", pdf_text(final.body)[0])
        self.assertFalse(get_tsca(self.order_id)["saved"])  # nothing stored
        self.rejects(lambda: preview_tsca(self.other_id, TscaPreviewPayload(draft=complete_draft())))


if __name__ == "__main__":
    unittest.main()
