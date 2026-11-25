import json
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from app import database
from app.checklist_routes import ChecklistAction, list_checklist, update_checklist
from app.shipments_routes import ShipmentCreate, create_shipment, ensure_order_shipment


class ChecklistTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "test.db"
        database.init_db()

    def tearDown(self):
        database.DB_PATH = self.original
        self.tmp.cleanup()

    def finished_order(self, number="PO-7001", quantity=5):
        """An order with a packing plan, added to the Export list as Save & Finish does."""
        with database.connect() as db:
            now = database.now_iso()
            order_id = db.insert_returning_id(
                """INSERT INTO orders (order_number, client, delivery_address, order_date, status, created_at, updated_at)
                   VALUES (?, 'Fbrc8 LLC', 'Berlin\nGermany', '2026-11-20', 'packed', ?, ?) RETURNING id""",
                (number, now, now))
            product_id = db.insert_returning_id(
                """INSERT INTO products (sku, name, length_cm, width_cm, height_cm, weight_kg, rotation_allowed,
                       fragile, active, created_at)
                   VALUES (?, 'Glue', 10, 10, 10, 1, 1, 0, 1, ?) RETURNING id""", (f"SKU-{number}", now))
            db.execute("""INSERT INTO order_items (order_id, product_id, sku_raw, description, quantity, unit_price)
                          VALUES (?, ?, 'X', 'X', ?, 1)""", (order_id, product_id, quantity))
            plan = {"unit_count": quantity, "boxes": [{"box_code": "B3"}, {"box_code": "B5"}]}
            db.execute("""INSERT INTO packing_plans (order_id, box_count, average_utilization, total_weight_kg, result_json, created_at)
                          VALUES (?, 2, 80, 4, ?, ?)""", (order_id, json.dumps(plan), now))
            shipment_id = ensure_order_shipment(db, order_id)
        return order_id, shipment_id

    def act(self, shipment_id, action, **extra):
        return update_checklist(shipment_id, ChecklistAction(action=action, **extra))

    def test_finished_order_appears_to_pack_with_its_contents(self):
        order_id, shipment_id = self.finished_order()
        [package] = list_checklist()
        self.assertEqual(package["id"], shipment_id)
        self.assertEqual(package["order_id"], order_id)
        self.assertEqual(package["po"], "PO-7001")
        self.assertEqual(package["customer"], "Fbrc8 LLC")
        self.assertEqual(package["status"], "to_pack")
        self.assertEqual(package["items"], 5)
        self.assertEqual(package["boxes"], ["B3", "B5"])
        self.assertIsNone(package["pickup_date"])

    def test_full_lifecycle_and_undo(self):
        _, shipment_id = self.finished_order()
        self.assertEqual(self.act(shipment_id, "pack")["status"], "packed")
        assigned = self.act(shipment_id, "assign_pickup", pickup_date="2026-11-26", pickup_slot="afternoon")
        self.assertEqual((assigned["pickup_date"], assigned["pickup_slot"]), ("2026-11-26", "afternoon"))
        picked = self.act(shipment_id, "pick_up")
        self.assertEqual(picked["status"], "picked_up")
        self.assertIsNotNone(picked["picked_up_at"])
        # Still listed right after pickup, so the board can count it and undo it.
        self.assertEqual([p["status"] for p in list_checklist()], ["picked_up"])
        undone = self.act(shipment_id, "undo_pick_up")
        self.assertEqual(undone["status"], "packed")
        self.assertIsNone(undone["picked_up_at"])

    def test_pickup_requires_packed(self):
        _, shipment_id = self.finished_order()
        with self.assertRaises(HTTPException) as caught:
            self.act(shipment_id, "pick_up")
        self.assertEqual(caught.exception.status_code, 409)
        with self.assertRaises(HTTPException) as caught:
            self.act(shipment_id, "unpack")
        self.assertEqual(caught.exception.status_code, 409)

    def test_assign_validation_and_missing_shipment(self):
        _, shipment_id = self.finished_order()
        with self.assertRaises(HTTPException) as caught:
            self.act(shipment_id, "assign_pickup", pickup_date="26/11/2026")
        self.assertEqual(caught.exception.status_code, 422)
        with self.assertRaises(HTTPException) as caught:
            self.act(9999, "pack")
        self.assertEqual(caught.exception.status_code, 404)

    def test_old_pickups_leave_the_board(self):
        _, shipment_id = self.finished_order()
        with database.connect() as db:
            db.execute("UPDATE shipments SET pack_status = 'picked_up', picked_up_at = '2026-01-01T09:00:00+00:00' WHERE id = ?",
                       (shipment_id,))
        self.assertEqual(list_checklist(), [])

    def test_manual_shipment_without_order(self):
        shipment = create_shipment(ShipmentCreate(ship_date="2026-11-24", client="Trade show", destination="ES",
                                                  forwarder="UPS", tracking_number="1Z999"), user=None)
        [package] = list_checklist()
        self.assertEqual(package["id"], shipment["id"])
        self.assertEqual((package["carrier"], package["tracking"], package["items"], package["boxes"]), ("UPS", "1Z999", 0, []))


if __name__ == "__main__":
    unittest.main()
