import tempfile
import unittest
from pathlib import Path

from app import database
from app.geo import country_code
from app.main import ClientPayload, list_clients, update_client


class CountryCodeTest(unittest.TestCase):
    def test_resolves_common_spellings(self):
        cases = {
            "United States": "US", "USA": "US", "United Kingdom": "GB", "UK": "GB",
            "Germany": "DE", "Deutschland": "DE", "España": "ES", "Switzerland": "CH",
            "United Arab Emirates": "AE", "Saudi Arabia": "SA", "New Zealand": "NZ",
        }
        for text, code in cases.items():
            with self.subTest(text=text):
                self.assertEqual(country_code(text), code)

    def test_resolves_origin_country(self):
        # Malta is the shipping origin, so it is missing from the DHL destination names.
        self.assertEqual(country_code("Malta"), "MT")
        self.assertEqual(country_code(" malta "), "MT")

    def test_blank_or_unknown_country_has_no_code(self):
        self.assertIsNone(country_code(""))
        self.assertIsNone(country_code("   "))
        self.assertIsNone(country_code("Atlantis"))


class ClientCountryCodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "test.db"
        database.init_db()
        with database.connect() as db:
            timestamp = database.now_iso()
            db.executemany(
                "INSERT INTO clients (customer, country, created_at, updated_at) VALUES (?, ?, ?, ?)",
                [("Acme", "USA", timestamp, timestamp), ("NoCountry Ltd", "", timestamp, timestamp)],
            )

    def tearDown(self):
        database.DB_PATH = self.original
        self.tmp.cleanup()

    def test_list_includes_country_code(self):
        codes = {client["customer"]: client["country_code"] for client in list_clients()}
        self.assertEqual(codes, {"Acme": "US", "NoCountry Ltd": None})

    def test_update_returns_new_country_code(self):
        client_id = next(c["id"] for c in list_clients() if c["customer"] == "NoCountry Ltd")
        updated = update_client(client_id, ClientPayload(customer="NoCountry Ltd", country="France"))
        self.assertEqual(updated["country_code"], "FR")


if __name__ == "__main__":
    unittest.main()
