import unittest

from app.po_lexicon import lexicon, normalize_token


class NormalizationTests(unittest.TestCase):
    def test_tokens_lose_case_accents_and_edge_punctuation(self):
        self.assertEqual(normalize_token("No.:"), "no")
        self.assertEqual(normalize_token("QTÉ"), "qte")
        self.assertEqual(normalize_token("Nº"), "no")
        self.assertEqual(normalize_token("#"), "#")
        self.assertEqual(normalize_token("(%)"), "%")
        self.assertEqual(normalize_token("d'expédition"), "d'expedition")


class LabelMatchingTests(unittest.TestCase):
    def setUp(self):
        self.lex = lexicon()

    def field(self, text, fields=None, **options):
        fields = fields or self.lex.labels.keys()
        hit = self.lex.find_label(text, fields, **options)
        return (hit.field, hit.remainder) if hit else None

    def test_the_longest_synonym_wins_across_fields(self):
        self.assertEqual(self.field("Total qty 1.955,00"), ("quantity_total", "1.955,00"))
        self.assertEqual(self.field("Total Net 1,097.50 €"), ("subtotal", "1,097.50 €"))
        self.assertEqual(self.field("Sub Total 975.50"), ("subtotal", "975.50"))
        self.assertEqual(self.field("Total $975.50"), ("total", "$975.50"))
        self.assertEqual(self.field("DATE DE LA DEMANDE"), ("requested_date", ""))

    def test_a_shorter_label_of_another_field_is_not_returned(self):
        # "Date de la demande" is a requested date, so asking for an order date finds nothing.
        self.assertIsNone(self.field("Date de la demande 02/10/2026", ["order_date"]))
        self.assertIsNone(self.field("Total qty 155,00", ["total"]))

    def test_labels_match_whole_words_not_substrings(self):
        self.assertIsNone(self.field("Subtotals are shown below", ["total"]))
        self.assertIsNone(self.field("Totally new formula", ["total"]))

    def test_the_value_follows_the_label_without_its_colon(self):
        self.assertEqual(self.field("PO Number: EL-88213"), ("order_number", "EL-88213"))
        self.assertEqual(self.field("Date : 10/23/2026"), ("order_date", "10/23/2026"))
        self.assertEqual(self.field("# PO-5042"), ("order_number", "PO-5042"))

    def test_labels_inside_a_sentence_need_anywhere(self):
        text = "211 24 MALMO, SWEDEN Purchase order 674993"
        self.assertIsNone(self.field(text, ["order_number"]))
        self.assertEqual(self.field(text, ["order_number"], anywhere=True), ("order_number", "674993"))

    def test_column_names_of_a_header_row(self):
        self.assertEqual(
            self.lex.column_fields("# Item & Description Qty Rate Amount"),
            {"position", "description", "quantity", "unit_price", "amount"},
        )
        self.assertEqual(
            self.lex.column_fields("Pos Our Item EAN Supplier Item ID Description Quantity"),
            {"position", "sku", "ean", "description", "quantity"},
        )


class CompanyTests(unittest.TestCase):
    def setUp(self):
        self.lex = lexicon()

    def test_company_suffixes_are_case_sensitive(self):
        self.assertTrue(self.lex.has_company_suffix("PONTIALIS GmbH & Co. KG"))
        self.assertTrue(self.lex.has_company_suffix("Prima Printer Nordic AB"))
        self.assertFalse(self.lex.has_company_suffix("Net weight 5 kg"))

    def test_our_own_company_is_recognised_in_any_spelling(self):
        self.assertTrue(self.lex.is_own_company("Thought3D Ltd"))
        self.assertTrue(self.lex.is_own_company("THOUGHT 3D LIMITED"))
        self.assertFalse(self.lex.is_own_company("Hava 3D"))


if __name__ == "__main__":
    unittest.main()
