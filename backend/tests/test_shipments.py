import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from app import database
from app.shipments_routes import (
    ShipmentCreate, ShipmentUpdate, applicable_docs, create_shipment, delete_shipment,
    list_shipments, shipment_order_candidates, update_shipment,
)


class ApplicableDocsTest(unittest.TestCase):
    def test_us_needs_tsca_and_section_232(self):
        docs = applicable_docs("US", chemicals=False, sds_requested=False)
        self.assertEqual(docs["doc_tsca"], 0)
        self.assertEqual(docs["doc_sec232"], 0)
        self.assertIsNone(docs["doc_sds"])
        self.assertIsNone(docs["doc_stripe"])
        self.assertEqual(docs["doc_pl"], 0)
        self.assertEqual(docs["doc_coo"], 0)

    def test_outside_us_skips_us_documents(self):
        docs = applicable_docs("DE", chemicals=True, sds_requested=False)
        self.assertIsNone(docs["doc_tsca"])
        self.assertIsNone(docs["doc_sec232"])
        self.assertEqual(docs["doc_sds"], 0)

    def test_sds_when_customer_asks(self):
        self.assertEqual(applicable_docs("FR", chemicals=False, sds_requested=True)["doc_sds"], 0)


class ShipmentsFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "test.db"
        database.init_db()

    def tearDown(self):
        database.DB_PATH = self.original
        self.tmp.cleanup()

    def make_order(self, number, client, address, *, chemical=False, status="packed"):
        with database.connect() as db:
            now = database.now_iso()
            order_id = db.insert_returning_id(
                """INSERT INTO orders (order_number, client, delivery_address, order_date, status, created_at, updated_at)
                   VALUES (?, ?, ?, '2026-11-20', ?, ?, ?) RETURNING id""",
                (number, client, address, status, now, now),
            )
            product_id = db.insert_returning_id(
                """INSERT INTO products (sku, name, length_cm, width_cm, height_cm, weight_kg, rotation_allowed,
                       fragile, active, tsca_name, created_at)
                   VALUES (?, ?, 10, 10, 10, 1, 1, 0, 1, ?, ?) RETURNING id""",
                (f"SKU-{number}", f"Product {number}", "Glue" if chemical else "", now),
            )
            db.execute(
                """INSERT INTO order_items (order_id, product_id, sku_raw, description, quantity, unit_price)
                   VALUES (?, ?, 'X', 'X', 1, 1)""", (order_id, product_id))
            return order_id

    def test_create_from_order_fills_client_po_destination_and_docs(self):
        order_id = self.make_order("PO-US-1", "Fbrc8 LLC", "1 Main St\nAustin, TX 78701\nUnited States", chemical=True)
        shipment = create_shipment(ShipmentCreate(ship_date="2026-11-24", order_id=order_id), user=None)
        self.assertEqual(shipment["client"], "Fbrc8 LLC")
        self.assertEqual(shipment["po"], "PO-US-1")
        self.assertEqual(shipment["destination"], "US")
        self.assertIs(shipment["doc_tsca"], False)
        self.assertIs(shipment["doc_sec232"], False)
        self.assertIs(shipment["doc_sds"], False)
        self.assertIsNone(shipment["doc_stripe"])
        self.assertFalse(shipment["complete"])
        # The order is no longer offered, and a second shipment for it is refused.
        self.assertNotIn(order_id, [o["id"] for o in shipment_order_candidates()])
        with self.assertRaises(HTTPException) as caught:
            create_shipment(ShipmentCreate(ship_date="2026-11-24", order_id=order_id), user=None)
        self.assertEqual(caught.exception.status_code, 409)

    def test_manual_shipment_requires_destination(self):
        with self.assertRaises(HTTPException) as caught:
            create_shipment(ShipmentCreate(ship_date="2026-11-24", client="Shopify #1919"), user=None)
        self.assertEqual(caught.exception.status_code, 422)
        shipment = create_shipment(ShipmentCreate(ship_date="2026-11-24", client="Trade show", destination="es"), user=None)
        self.assertEqual(shipment["destination"], "ES")
        self.assertIsNone(shipment["doc_tsca"])
        self.assertIsNone(shipment["order_id"])

    def test_checklist_updates_and_progress(self):
        shipment = create_shipment(ShipmentCreate(ship_date="2026-11-24", client="Prusa", destination="CZ"), user=None)
        self.assertEqual(shipment["progress"], {"done": 0, "total": 6})
        updated = update_shipment(shipment["id"], ShipmentUpdate(
            doc_pl=True, doc_coo=True, invoice_number="3897", tracking_number="9566617281",
            email_client_sent=True, email_forwarder_sent=True))
        self.assertTrue(updated["complete"])
        self.assertIsNotNone(updated["email_client_sent_at"])
        # Switching Stripe on adds a step; switching the destination to the US adds TSCA and Section 232.
        updated = update_shipment(shipment["id"], ShipmentUpdate(doc_stripe=False, destination="US"))
        self.assertEqual(updated["progress"], {"done": 6, "total": 9})
        updated = update_shipment(shipment["id"], ShipmentUpdate(destination="DE"))
        self.assertIsNone(updated["doc_tsca"])
        self.assertEqual(updated["progress"], {"done": 6, "total": 7})
        # Unticking an e-mail clears its timestamp.
        updated = update_shipment(shipment["id"], ShipmentUpdate(email_client_sent=False))
        self.assertIsNone(updated["email_client_sent_at"])

    def test_list_filters(self):
        a = create_shipment(ShipmentCreate(ship_date="2026-11-20", client="UFP", destination="DE", forwarder="DHL",
                                           tracking_number="7527489690"), user=None)
        create_shipment(ShipmentCreate(ship_date="2026-11-24", client="niceshops", destination="AT", forwarder="TNT"), user=None)
        self.assertEqual([s["client"] for s in list_shipments(None, None, "", "", "", False)], ["niceshops", "UFP"])
        self.assertEqual([s["id"] for s in list_shipments(None, None, "75274", "", "", False)], [a["id"]])
        self.assertEqual([s["client"] for s in list_shipments(None, None, "", "TNT", "", False)], ["niceshops"])
        self.assertEqual([s["client"] for s in list_shipments("2026-11-21", None, "", "", "", False)], ["niceshops"])
        self.assertEqual([s["client"] for s in list_shipments(None, None, "", "", "de", False)], ["UFP"])
        self.assertEqual(len(list_shipments(None, None, "", "", "", True)), 2)

    def test_delete(self):
        shipment = create_shipment(ShipmentCreate(ship_date="2026-11-24", client="X", destination="IT"), user=None)
        delete_shipment(shipment["id"])
        self.assertEqual(list_shipments(None, None, "", "", "", False), [])
        with self.assertRaises(HTTPException):
            delete_shipment(shipment["id"])

    def test_deleting_order_keeps_shipment(self):
        order_id = self.make_order("PO-DE-1", "IGO3D", "Berlin\nGermany")
        shipment = create_shipment(ShipmentCreate(ship_date="2026-11-24", order_id=order_id), user=None)
        with database.connect() as db:
            db.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        remaining = list_shipments(None, None, "", "", "", False)
        self.assertEqual(remaining[0]["id"], shipment["id"])
        self.assertIsNone(remaining[0]["order_id"])
        self.assertEqual(remaining[0]["po"], "PO-DE-1")


if __name__ == "__main__":
    unittest.main()
