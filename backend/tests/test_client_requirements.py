import asyncio
import io
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException, UploadFile
from openpyxl import Workbook

from app import database
from app.main import get_order
from app.requirements import company_name_variants, name_key, names_match, parse_requirements_excel, similarity
from app.requirements_routes import (
    ClientRequirementPayload, delete_all_client_requirements, delete_client_requirement,
    import_client_requirements, list_client_requirements, update_client_requirement,
)

HEADER = ["Company", "Invoice Email:", "Payment ", "Pricing / Invoice requirements", "Docs Required", "Courier", "Comments"]


def workbook_bytes(rows, title="Special clients"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class NameMatchingTest(unittest.TestCase):
    def test_keys_ignore_case_accents_punctuation_and_client_codes(self):
        self.assertEqual(name_key("Prima Printer Nordic AB….PRI001"), name_key("prima  printer nordic ab"))
        self.assertTrue(names_match("Fbrc8", "Fbrc8 LLC"))
        self.assertTrue(names_match("3dörtgen", "3DORTGEN"))
        self.assertFalse(names_match("Atlas", "Atlantis"))

    def test_combined_names_produce_variants_but_countries_do_not(self):
        self.assertEqual(company_name_variants("3DNet / Sem AS"), ["3DNet / Sem AS", "3DNet", "Sem AS"])
        self.assertIn("DIYElectronics", company_name_variants("Neesin Investments CC T/A DIYElectronics"))
        self.assertIn("Printalot Argentina", company_name_variants("Kaviflex (Printalot Argentina)"))
        self.assertNotIn("Mexico", company_name_variants("Voxart (Mexico)"))

    def test_similarity_flags_near_names_but_not_unrelated_ones(self):
        self.assertGreaterEqual(similarity("AddNorth", "Add North 3D AB"), 0.85)
        self.assertLess(similarity("Multi3DPrint", "3D Printz"), 0.85)


class ParseTest(unittest.TestCase):
    def test_finds_columns_with_colons_and_trailing_spaces_and_keeps_line_breaks(self):
        records, errors = parse_requirements_excel(workbook_bytes([
            ["Special clients"],
            HEADER,
            ["3DJake", "a@x.test\nb@x.test", None, "No shipping charge", "PO# on box", "TNT road", "Check lead time"],
            [None, None, None, None, None, None, None],
        ]))
        self.assertEqual(errors, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["invoice_email"], "a@x.test\nb@x.test")
        self.assertEqual(records[0]["docs_required"], "PO# on box")

    def test_rejects_workbook_without_company_header(self):
        records, errors = parse_requirements_excel(workbook_bytes([["Name", "Notes"], ["Acme", "x"]]))
        self.assertEqual(records, [])
        self.assertTrue(errors)


class RequirementsFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "test.db"
        database.init_db()
        with database.connect() as db:
            now = database.now_iso()
            for customer in ("Fbrc8 LLC", "Add North 3D AB", "Prima Printer Nordic AB….PRI001", "Other Co"):
                db.execute("INSERT INTO clients (customer, created_at, updated_at) VALUES (?, ?, ?)", (customer, now, now))

    def tearDown(self):
        database.DB_PATH = self.original
        self.tmp.cleanup()

    def upload(self, rows):
        return asyncio.run(import_client_requirements(UploadFile(io.BytesIO(workbook_bytes(rows)), filename="r.xlsx")))

    def make_order(self, client):
        with database.connect() as db:
            now = database.now_iso()
            cursor = db.execute(
                "INSERT INTO orders (order_number, client, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (f"PO-{client}", client, now, now),
            )
            return cursor.lastrowid

    def seed(self):
        return self.upload([
            HEADER,
            ["Fbrc8", None, None, None, "COO required", "DHL", "Fragile labels"],
            ["AddNorth", None, None, None, "Invoice in box", None, None],
            ["Prima Printer Nordic AB", None, "stripe", None, None, None, None],
            ["Unknown Corp", None, None, None, "Docs", None, None],
        ])

    def test_import_links_exact_matches_and_suggests_near_ones(self):
        result = self.seed()
        self.assertEqual((result["created"], result["linked"], result["suggested"], result["unlinked"]), (4, 2, 1, 1))
        rows = {row["company"]: row for row in list_client_requirements()}
        self.assertEqual(rows["Fbrc8"]["client_name"], "Fbrc8 LLC")
        self.assertIsNone(rows["AddNorth"]["client_id"])
        self.assertEqual(rows["AddNorth"]["suggested_client"]["customer"], "Add North 3D AB")
        self.assertIsNone(rows["Unknown Corp"]["suggested_client"])

    def test_reimport_updates_text_overwrites_blanks_and_keeps_manual_links(self):
        self.seed()
        row = next(r for r in list_client_requirements() if r["company"] == "AddNorth")
        update_client_requirement(row["id"], ClientRequirementPayload(
            company="AddNorth", aliases=["Add North"], client_id=row["suggested_client"]["id"], docs_required="Invoice in box"))
        result = self.upload([HEADER, ["AddNorth", None, None, None, None, "FedEx", None]])
        self.assertEqual((result["created"], result["updated"]), (0, 1))
        again = next(r for r in list_client_requirements() if r["company"] == "AddNorth")
        self.assertEqual(again["docs_required"], "")
        self.assertEqual(again["courier"], "FedEx")
        self.assertEqual(again["client_name"], "Add North 3D AB")
        self.assertEqual(again["aliases"], ["Add North"])

    def test_order_note_matches_by_name_alias_and_linked_client(self):
        self.seed()
        note = get_order(self.make_order("fbrc8 llc"))["client_requirements"]
        self.assertEqual((note["match"], note["docs_required"], note["courier"]), ("exact", "COO required", "DHL"))
        self.assertEqual(note["comments"], "Fragile labels")
        # Linked client name matches even though the order name differs from the sheet's company name.
        row = next(r for r in list_client_requirements() if r["company"] == "AddNorth")
        update_client_requirement(row["id"], ClientRequirementPayload(
            company="AddNorth", client_id=row["suggested_client"]["id"], docs_required="Invoice in box"))
        self.assertEqual(get_order(self.make_order("Add North 3D AB"))["client_requirements"]["match"], "exact")

    def test_order_note_is_suggested_for_near_names_and_absent_for_others(self):
        self.seed()
        near = get_order(self.make_order("Add North 3D"))["client_requirements"]
        self.assertEqual((near["match"], near["company"]), ("suggested", "AddNorth"))
        self.assertIsNone(get_order(self.make_order("Totally Different Ltd"))["client_requirements"])

    def test_billing_only_rows_do_not_raise_a_note(self):
        self.seed()
        self.assertIsNone(get_order(self.make_order("Prima Printer Nordic AB"))["client_requirements"])

    def test_deleting_a_client_keeps_the_requirement_unlinked(self):
        self.seed()
        with database.connect() as db:
            db.execute("DELETE FROM clients WHERE customer = 'Fbrc8 LLC'")
        row = next(r for r in list_client_requirements() if r["company"] == "Fbrc8")
        self.assertIsNone(row["client_id"])
        self.assertEqual(get_order(self.make_order("Fbrc8"))["client_requirements"]["match"], "exact")

    def test_update_validation_and_delete(self):
        self.seed()
        first, second = list_client_requirements()[:2]
        with self.assertRaises(HTTPException) as bad_client:
            update_client_requirement(first["id"], ClientRequirementPayload(company=first["company"], client_id=9999))
        self.assertEqual(bad_client.exception.status_code, 422)
        with self.assertRaises(HTTPException) as conflict:
            update_client_requirement(first["id"], ClientRequirementPayload(company=second["company"]))
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(delete_client_requirement(first["id"])["company"], first["company"])
        with self.assertRaises(HTTPException):
            delete_client_requirement(first["id"])
        self.assertEqual(delete_all_client_requirements(), {"deleted_requirements": 3})

    def test_import_rejects_non_excel_and_unrecognised_workbooks(self):
        with self.assertRaises(HTTPException):
            asyncio.run(import_client_requirements(UploadFile(io.BytesIO(b"x"), filename="r.csv")))
        with self.assertRaises(HTTPException):
            self.upload([["Name"], ["x"]])


if __name__ == "__main__":
    unittest.main()
