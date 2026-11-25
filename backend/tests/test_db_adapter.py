"""Database adapter contract. Runs on SQLite by default and on PostgreSQL when
PACKFLOW_TEST_DATABASE_URL is set, so both engines must behave the same."""
import os
import tempfile
import unittest
from pathlib import Path

from app import database
from app.db import to_pyformat


class PlaceholderTranslationTests(unittest.TestCase):
    def test_qmarks_become_pyformat_outside_literals(self):
        self.assertEqual(to_pyformat("SELECT * FROM t WHERE a = ? AND b = '?'"),
                         "SELECT * FROM t WHERE a = %s AND b = '?'")

    def test_percent_is_escaped_everywhere(self):
        self.assertEqual(to_pyformat("SELECT ? WHERE k LIKE 'packing_%' AND x % 2 = 0"),
                         "SELECT %s WHERE k LIKE 'packing_%%' AND x %% 2 = 0")


class AdapterContractTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.original = database.DB_PATH
        database.DB_PATH = Path(self.folder.name) / "adapter.db"
        database.init_db()

    def tearDown(self):
        database.DB_PATH = self.original
        # Fails on Windows if any SQLite connection was left open.
        self.folder.cleanup()

    def test_rows_behave_like_sqlite_rows(self):
        with database.connect() as db:
            row = db.execute("SELECT key, value FROM app_settings WHERE key = ?", ("demo_catalog_seeded",)).fetchone()
        self.assertEqual(row["key"], "demo_catalog_seeded")
        self.assertEqual(row[1], "1")
        self.assertEqual(dict(row), {"key": "demo_catalog_seeded", "value": "1"})
        key, value = row
        self.assertEqual((key, value), ("demo_catalog_seeded", "1"))

    def test_error_rolls_back_the_whole_unit_of_work(self):
        with self.assertRaises(database.IntegrityError):
            with database.connect() as db:
                db.execute("INSERT INTO app_settings (key, value) VALUES ('rollback_probe', '1')")
                db.execute("INSERT INTO app_settings (key, value) VALUES ('demo_catalog_seeded', '1')")
        with database.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM app_settings WHERE key = 'rollback_probe'").fetchone())

    def test_savepoint_keeps_the_transaction_usable_after_an_error(self):
        with database.connect() as db:
            db.execute("INSERT INTO app_settings (key, value) VALUES ('before', '1')")
            with self.assertRaises(database.IntegrityError):
                with db.savepoint():
                    db.execute("INSERT INTO app_settings (key, value) VALUES ('inside', '1')")
                    db.execute("INSERT INTO app_settings (key, value) VALUES ('demo_catalog_seeded', '1')")
            db.execute("INSERT INTO app_settings (key, value) VALUES ('after', '1')")
        with database.connect() as db:
            keys = {row["key"] for row in db.execute("SELECT key FROM app_settings")}
        self.assertIn("before", keys)
        self.assertIn("after", keys)
        self.assertNotIn("inside", keys)

    def test_returning_id_and_binary_round_trip(self):
        payload = bytes(range(256)) * 4 + b"%PDF ?"
        with database.connect() as db:
            order_id = db.insert_returning_id(
                "INSERT INTO orders (order_number, client, created_at, updated_at) VALUES (?, ?, ?, ?) RETURNING id",
                ("PO-ADAPTER", "Acme", database.now_iso(), database.now_iso()),
            )
            db.execute("INSERT INTO source_documents (order_id, filename, content) VALUES (?, ?, ?)",
                       (order_id, "a.pdf", payload))
        with database.connect() as db:
            stored = db.execute("SELECT content FROM source_documents WHERE order_id = ?", (order_id,)).fetchone()[0]
        self.assertEqual(bytes(stored), payload)

    def test_booleans_are_stored_as_integers(self):
        with database.connect() as db:
            db.execute("UPDATE products SET fragile = ? WHERE id = (SELECT MIN(id) FROM products)", (True,))
            value = db.execute("SELECT fragile FROM products WHERE id = (SELECT MIN(id) FROM products)").fetchone()[0]
        self.assertEqual(value, 1)

    def test_health_reports_the_engine(self):
        from app.main import health

        expected = "postgres" if os.getenv("PACKFLOW_TEST_DATABASE_URL") else "sqlite"
        self.assertEqual(health()["database"], expected)


if __name__ == "__main__":
    unittest.main()
