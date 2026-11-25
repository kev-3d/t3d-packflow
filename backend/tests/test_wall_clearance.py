import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from app import database
from app.main import OrderItemPayload, OrderReviewPayload, analyze_order, create_manual_order, get_order


class WallClearanceTests(unittest.TestCase):
    def setUp(self):
        self.original_path = database.DB_PATH
        self.temp_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        database.DB_PATH = Path(self.temp_directory.name) / "clearance.db"
        database.init_db()
        with database.connect() as db:
            product = db.execute("SELECT name FROM products WHERE active = 1 ORDER BY id LIMIT 1").fetchone()["name"]
        self.order = create_manual_order(OrderReviewPayload(
            order_number="TEST-CLEAR", client="Test", currency="EUR",
            items=[OrderItemPayload(description=product, quantity=3, unit_price=1, amount=3)],
        ))

    def tearDown(self):
        database.DB_PATH = self.original_path
        self.temp_directory.cleanup()

    def allowance(self):
        return get_order(self.order["id"])["plan"]["result"]["settings"]["allowance_cm"]

    def test_default_then_change_then_kept_on_recalculation(self):
        analyze_order(self.order["id"])
        self.assertEqual(self.allowance(), 0.5)
        analyze_order(self.order["id"], allowance_cm=0.2)
        self.assertEqual(self.allowance(), 0.2)
        analyze_order(self.order["id"], objective="smallest_volume")
        self.assertEqual(self.allowance(), 0.2)

    def test_out_of_range_clearance_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            analyze_order(self.order["id"], allowance_cm=6)
        self.assertEqual(raised.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
