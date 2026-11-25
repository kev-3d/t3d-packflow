import io
import os
import unittest
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.extraction import (
    _Cell,
    _detect_decimal,
    _restore_wrapped_lines,
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


# A price-less PO sent by a distributor: internal IDs and EAN before the description, quantity as the
# last column, an ISO date under its label, and a "Total qty" line that is not money.
QUANTITY_ONLY_LAYOUT = [
    (300, 800, "Example Printers Nordic AB"),
    (300, 700, "211 24 MALMO, SWEDEN"), (420, 700, "Purchase order 674993"),
    (34, 685, "Supplier"),
    (34, 674, "Thought3D"), (318, 674, "Our order ID"), (442, 674, "Reorder date"),
    (34, 664, "146, Morning Dew"), (318, 664, "674993"), (442, 664, "2026-11-20"),
    (34, 654, "SWQ 30 Swiegq"),
    (39, 566, "Pos Our Item EAN Supplier Item ID Description Quantity"),
    (39, 543, "1 23642 4721 5350583999945 MPC2018 Magigoo Pro PC - The 3D printing"), (542, 543, "150"),
    (359, 532, "adhesive"),
    (39, 516, "2 31879 16016 5350583000177 MGO2025 Magigoo Glide Original - 250 ml"), (547, 516, "5"),
    (495, 480, "Total qty 155,00"),
    (34, 440, "Delivery address:"),
    (34, 427, "Example Printers Nordic AB"),
    (34, 414, "Examplegatan 1"),
    (34, 401, "211 24 MALMO"),
    (34, 388, "Sweden"),
    (34, 340, "Please add our purchase order number 674993 to all documents."),
]


# An English PO in wording the parser has not seen in the reference documents: "PO Number", a written
# month, lines that start with their code, a description line that starts with "Total", a page footer
# inside the table, "Sales Tax" and "Grand Total", and a third-party vendor beside its label.
GENERIC_LAYOUT = [
    (50, 800, "Acme Robotics"), (400, 800, "PURCHASE ORDER"),
    (50, 786, "500 Market Street, Springfield"),
    (400, 780, "PO Number: AR-1001"),
    (400, 766, "Order Date: Oct 23, 2026"),
    (50, 740, "Bill To:"), (300, 740, "Vendor:"), (350, 740, "Northwind Ltd"),
    (50, 727, "Acme Robotics Inc."),
    (50, 690, "Item"), (120, 690, "Description"), (330, 690, "Qty"), (390, 690, "Unit Price"), (480, 690, "Amount"),
    (50, 670, "WG-10"), (120, 670, "Widget"), (330, 670, "4"), (390, 670, "2.50"), (480, 670, "10.00"),
    (120, 658, "Total protection for your desk"),
    (280, 645, "Page 1 of 1"),
    (50, 630, "WG-20"), (120, 630, "Gadget 500 ml"), (330, 630, "3"), (390, 630, "1.00"), (480, 630, "3.00"),
    (380, 600, "Sub Total"), (480, 600, "13.00"),
    (380, 586, "Sales Tax"), (480, 586, "1.04"),
    (380, 572, "Grand Total"), (480, 572, "$14.04"),
]


class NumberAndLineRulesTests(unittest.TestCase):
    def test_a_wrapped_line_mispositioned_by_pypdf_returns_under_its_row(self):
        fragments = [
            _Cell(39.4, 543.4, "1"), _Cell(0, 0, "Magigoo Pro PC - The 3D printing"),
            _Cell(359.5, 448.9, "adhesive"),  # pypdf reports it ~95 pt too low
            _Cell(542.5, 543.4, "150"),
        ]
        restored = _restore_wrapped_lines(fragments)
        self.assertAlmostEqual(restored[2].y, 543.4 - 11.0)
        self.assertEqual(restored[3], fragments[3])

    def test_a_line_far_from_unrelated_neighbours_stays_where_it_is(self):
        fragments = [_Cell(34, 236, "Delivery address:"), _Cell(34, 141, "Please add"), _Cell(298, 815, "Letterhead")]
        self.assertEqual(_restore_wrapped_lines(fragments), fragments)

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

    def test_reads_a_price_less_order_with_internal_ids_before_the_description(self):
        result = extract_pdf(make_pdf(QUANTITY_ONLY_LAYOUT), "order.pdf")

        self.assertEqual(result["order_number"], "674993")
        self.assertEqual(result["client"], "Example Printers Nordic AB")
        self.assertEqual(result["order_date"], "2026-11-20")
        self.assertEqual(
            [(i["sku_raw"], i["description"], i["quantity"], i["amount"]) for i in result["items"]],
            [
                ("MPC2018", "Magigoo Pro PC - The 3D printing adhesive", 150, 0.0),
                ("MGO2025", "Magigoo Glide Original - 250 ml", 5, 0.0),
            ],
        )
        self.assertEqual(result["total_amount"], 0)  # "Total qty 155,00" is a quantity, not money
        self.assertEqual(result["delivery_address"], "Example Printers Nordic AB\nExamplegatan 1\n211 24 MALMO\nSweden")
        self.assertEqual(result["warnings"], ["This purchase order has no prices; only quantities were read."])

    def test_a_total_quantity_that_disagrees_with_the_lines_is_flagged(self):
        layout = [cell if cell[2] != "Total qty 155,00" else (495, 480, "Total qty 160,00") for cell in QUANTITY_ONLY_LAYOUT]
        result = extract_pdf(make_pdf(layout), "order.pdf")

        self.assertTrue(any("total quantity is 160" in warning for warning in result["warnings"]))

    def test_reads_an_unseen_english_layout_from_the_lexicon(self):
        result = extract_pdf(make_pdf(GENERIC_LAYOUT), "order.pdf")

        self.assertEqual(result["order_number"], "AR-1001")
        self.assertEqual(result["order_date"], "2026-10-23")  # a written month is never ambiguous
        self.assertEqual(result["client"], "Acme Robotics Inc.")
        self.assertEqual(result["vendor"], "Northwind Ltd")
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(
            [(i["sku_raw"], i["description"], i["quantity"], i["unit_price"], i["amount"]) for i in result["items"]],
            [("WG-10", "Widget", 4, 2.5, 10.0), ("WG-20", "Gadget 500 ml", 3, 1.0, 3.0)],
        )
        self.assertEqual((result["subtotal"], result["tax_amount"], result["total_amount"]), (13.0, 1.04, 14.04))
        self.assertEqual(result["warnings"], [])
        self.assertIn("PO Number", result["field_sources"]["order_number"])

    def test_a_title_is_never_read_from_the_line_under_it(self):
        layout = [
            (400, 780, "Purchase Order"), (400, 766, "50859 Koeln"),
            (50, 600, "1"), (140, 600, "WIDGET"), (400, 600, "3"), (450, 600, "10.00"), (510, 600, "30.00"),
            (380, 480, "Total 30.00 EUR"),
        ]
        result = extract_pdf(make_pdf(layout), "order.pdf")

        self.assertTrue(result["order_number"].startswith("PDF-"))
        self.assertIn("Order number not found; a temporary number was assigned.", result["warnings"])

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
        path = self.folder / name
        if not path.exists():
            self.skipTest(f"{name} is not in PACKFLOW_PDF_FIXTURES")
        return extract_pdf(path.read_bytes(), name)

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

    def test_prima_order_without_prices(self):
        result = self.read("PurchaseOrder_DOC-02020_674993.pdf")
        self.assertEqual(result["order_number"], "674993")
        self.assertEqual(result["client"], "Prima Printer Nordic AB")
        self.assertEqual(result["order_date"], "2026-11-20")
        self.assertEqual([i["sku_raw"] for i in result["items"]], [
            "MPC2018", "MPP2018", "MPA2019", "MF2019", "MHT2019", "MPPGF2019",
            "MO2016", "MGK2025", "MGO2025", "MS2025", "MGK2025-SG", "MGPA2025",
        ])
        self.assertEqual([i["quantity"] for i in result["items"]], [150, 150, 1000, 50, 50, 25, 250, 10, 5, 250, 10, 5])
        self.assertEqual(result["items"][5]["description"], "Magigoo Pro PP - GF - The 3D printing adhesive")
        self.assertTrue(result["delivery_address"].startswith("3D Prima / Prima Printer Nordic AB"))
        self.assertEqual(result["warnings"], ["This purchase order has no prices; only quantities were read."])

    def test_sample_order_with_codes_instead_of_line_numbers(self):
        result = self.read("sample-po.pdf")
        self.assertEqual(result["order_number"], "EL-88213")
        self.assertEqual(result["client"], "Example Labs B.V.")
        self.assertEqual([(i["sku_raw"], i["description"], i["quantity"], i["amount"]) for i in result["items"]],
                         [("MG-50", "50 ml", 60, 570.0), ("MG-120", "120 ml", 12, 192.0), ("PK-100", "ProKit", 4, 180.0)])
        self.assertEqual(result["subtotal"], 942.0)


if __name__ == "__main__":
    unittest.main()
