import io
import os
import unittest
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.extraction import (
    _detect_decimal,
    _is_item_start,
    _layout_rows,
    _page_fragments,
    _resolve_day_first,
    _solve_line,
    _to_number,
    extract_pdf,
)
from pypdf import PdfReader


def make_pdf(lines):
    """A one-page PDF with each (x, y, text) drawn at its position, as a supplier's generator would."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setFont("Helvetica", 9)
    for x, y, text in lines:
        pdf.drawString(x, y, text)
    pdf.save()
    return buffer.getvalue()


SUPPLIER_LAYOUT = [
    (380, 700, "Order No.:"), (440, 700, "P2624730"),
    (380, 690, "Date:"), (440, 690, "11/10/2026"),
    (50, 650, "3DMENSIONALS - PONTIALIS GmbH & Co. KG"),
    (50, 620, "Pos."), (140, 620, "Description"), (400, 620, "Qty"), (450, 620, "Price"), (510, 620, "Total"),
    (50, 600, "1"), (80, 600, "MPP2018"), (140, 600, "[MPP2018] MAGIGOO PP Klebestift (Printing"),
    (400, 600, "25"), (450, 600, "11.90"), (510, 600, "297.50"),
    (140, 586, "Adhesive) for optimal adhesion"),
    (50, 570, "2"), (80, 570, "MO2016"), (140, 570, "[MO2016] MAGIGOO Original Klebestift"),
    (400, 570, "100"), (450, 570, "8.00"), (510, 570, "800.00"),
    (380, 480, "Total Net 1,097.50 EUR"),
]


class NumberAndLineRulesTests(unittest.TestCase):
    def test_solves_quantity_price_and_amount_from_the_row_numbers(self):
        self.assertEqual(_solve_line([25, 11.9, 297.5]), (25, 11.9, 297.5, 0.0))

    def test_ignores_a_sub_line_number_between_quantity_and_price(self):
        # Rev1 prints "10.00" with a small "1" under the quantity column.
        self.assertEqual(_solve_line([10, 1, 65, 650]), (10, 65, 650, 0.0))

    def test_accepts_a_discount_column_only_when_it_reproduces_the_amount(self):
        self.assertEqual(_solve_line([25, 10, 10, 225]), (25, 10, 225, 10.0))
        self.assertIsNone(_solve_line([25, 10, 10, 260]))

    def test_rejects_rows_whose_numbers_do_not_add_up(self):
        self.assertIsNone(_solve_line([3, 5, 7]))

    def test_parses_both_decimal_conventions(self):
        self.assertEqual(_to_number("1,097.50", "."), 1097.5)
        self.assertEqual(_to_number("150,00", ","), 150.0)
        self.assertEqual(_to_number("1150,00", ","), 1150.0)

    def test_detects_the_decimal_separator_per_document(self):
        self.assertEqual(_detect_decimal(["1,097.50", "297.50", "11.90"]), ".")
        self.assertEqual(_detect_decimal(["150,00", "25,00", "10,0000"]), ",")

    def test_date_order_comes_from_the_document_not_the_locale(self):
        self.assertEqual(_resolve_day_first([(21, 9, 2026)], "EUR"), (True, False))
        self.assertEqual(_resolve_day_first([(10, 23, 2026)], "USD"), (False, False))
        self.assertEqual(_resolve_day_first([(11, 10, 2026)], "EUR"), (True, True))

    def test_item_rows_need_a_number_or_a_sku_not_just_an_address_line(self):
        rows = _layout_rows(_page_fragments(PdfReader(io.BytesIO(make_pdf([(50, 600, "72000 LE MANS")]))).pages[0]))
        self.assertFalse(_is_item_start(rows[0]))


class PurchaseOrderPdfTests(unittest.TestCase):
    def test_reads_header_items_and_totals_from_the_drawn_layout(self):
        result = extract_pdf(make_pdf(SUPPLIER_LAYOUT), "order.pdf")

        self.assertEqual(result["order_number"], "P2624730")
        self.assertEqual(result["client"], "3DMENSIONALS - PONTIALIS GmbH & Co. KG")
        self.assertEqual(result["currency"], "EUR")
        self.assertEqual(result["order_date"], "2026-10-11")  # 11/10 in a EUR document is read day-first
        self.assertEqual(result["subtotal"], 1097.5)
        self.assertEqual(result["total_amount"], 1097.5)
        self.assertEqual(
            [(i["sku_raw"], i["quantity"], i["unit_price"], i["amount"]) for i in result["items"]],
            [("MPP2018", 25, 11.9, 297.5), ("MO2016", 100, 8.0, 800.0)],
        )
        self.assertTrue(result["items"][0]["description"].startswith("MAGIGOO PP Klebestift"))
        self.assertIn("(Printing Adhesive)", result["items"][0]["description"])
        self.assertTrue(any("ambiguous" in warning for warning in result["warnings"]))

    def test_a_line_that_does_not_add_up_is_kept_and_flagged(self):
        layout = [
            (380, 700, "Order No.: PO-77"),
            (50, 600, "1"), (140, 600, "WIDGET"), (400, 600, "3"), (450, 600, "10.00"), (510, 600, "25.00"),
            (380, 480, "Total $25.00"),
        ]
        result = extract_pdf(make_pdf(layout), "order.pdf")

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["quantity"], 3)
        self.assertTrue(any("does not match the amount" in warning for warning in result["warnings"]))
        self.assertLess(result["confidence"], 0.95)

    def test_a_document_without_product_lines_says_so(self):
        result = extract_pdf(make_pdf([(50, 600, "Hello, this is a letter without a product table.")]), "letter.pdf")

        self.assertEqual(result["items"], [])
        self.assertTrue(result["warnings"][0].startswith("No product lines were found"))
        self.assertLessEqual(result["confidence"], 0.2)


@unittest.skipUnless(os.environ.get("PACKFLOW_PDF_FIXTURES"), "set PACKFLOW_PDF_FIXTURES to the folder with the reference POs")
class ReferenceDocumentTests(unittest.TestCase):
    """Real supplier documents. They are kept outside the repository because they contain customer data."""

    @classmethod
    def setUpClass(cls):
        cls.folder = Path(os.environ["PACKFLOW_PDF_FIXTURES"])

    def read(self, name):
        return extract_pdf((self.folder / name).read_bytes(), name)

    def test_3dmensionals_order(self):
        result = self.read("Purchase Order - P2624730.pdf")
        self.assertEqual(result["order_number"], "P2624730")
        self.assertEqual(result["client"], "3DMENSIONALS - PONTIALIS GmbH & Co. KG")
        self.assertEqual([(i["quantity"], i["unit_price"], i["amount"]) for i in result["items"]],
                         [(25, 11.9, 297.5), (100, 8.0, 800.0)])
        self.assertEqual(result["subtotal"], 1097.5)

    def test_hava_order(self):
        result = self.read("Bon de commande - P04486 (1).pdf")
        self.assertEqual(result["order_number"], "P04486")
        self.assertEqual(result["order_date"], "2026-09-21")
        self.assertEqual(result["requested_date"], "2026-10-02")
        self.assertEqual([(i["sku_raw"], i["quantity"], i["unit_price"]) for i in result["items"]],
                         [("MPA2019", 25, 10.0), ("MHT2019", 25, 20.0), ("MO2016", 50, 8.0)])
        self.assertEqual(result["total_amount"], 1150.0)
        self.assertEqual(result["warnings"], [])

    def test_rev1_order(self):
        result = self.read("PO-5042.pdf")
        self.assertEqual(result["order_number"], "PO-5042")
        self.assertEqual(result["terms"], "Pre-Payment Required")
        self.assertEqual([i["quantity"] for i in result["items"]], [10, 2, 2, 2, 5])
        self.assertEqual(result["subtotal"], 975.5)
        self.assertEqual(result["warnings"], [])


if __name__ == "__main__":
    unittest.main()
