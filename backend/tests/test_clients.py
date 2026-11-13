import asyncio
import io
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException, UploadFile
from openpyxl import Workbook

from app import database
from app.extraction import parse_clients_excel
from app.main import (
    ClientPayload, delete_all_clients, delete_client, import_clients, list_clients, update_client,
)


def workbook_bytes(rows):
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


HEADER = ["Customer", "Phone Number", "Email", "Contact Name", "Address", "Country", "Shipping Address"]


class ClientsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "test.db"
        database.init_db()

    def tearDown(self):
        database.DB_PATH = self.original
        self.tmp.cleanup()

    def upload(self, content, name="clients.xlsx"):
        return asyncio.run(import_clients(UploadFile(io.BytesIO(content), filename=name)))

    def test_parser_matches_headers_and_skips_blank_customer(self):
        records, errors = parse_clients_excel(workbook_bytes([
            HEADER,
            ["Acme", 5551234, "a@acme.test", "Ana", "Main 1", "Mexico", "Dock 4"],
            [None, "1", None, None, None, None, None],
        ]))
        self.assertEqual(records[0]["phone_number"], "5551234")
        self.assertEqual(records[0]["shipping_address"], "Dock 4")
        self.assertEqual(len(errors), 1)

    def test_parser_finds_header_below_title_block_and_keeps_address_lines(self):
        records, errors = parse_clients_excel(workbook_bytes([
            ["Thought3D Ltd"],
            ["Customer Contact List"],
            [""],
            [],
            [None, *HEADER],
            [None, "3D Market", None, "luis@3dmarket.test", "Luis", "Street 121\nCol. Carretas", "Mexico", "Street 121\n\nMexico"],
            ["Thursday, Jan 07, 2021 05:06:05 PM GMT+1"],
        ]))
        self.assertEqual(errors, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["customer"], "3D Market")
        self.assertEqual(records[0]["address"], "Street 121\nCol. Carretas")
        self.assertEqual(records[0]["shipping_address"], "Street 121\nMexico")
        self.assertEqual(records[0]["phone_number"], "")

    def test_import_creates_then_updates_by_customer(self):
        result = self.upload(workbook_bytes([HEADER, ["Acme", "1", "a@acme.test", "Ana", "", "Mexico", ""]]))
        self.assertEqual(result["created"], 1)
        result = self.upload(workbook_bytes([HEADER, [" acme ", "2", "b@acme.test", "Bob", "", "USA", ""]]))
        self.assertEqual(result["updated"], 1)
        clients = list_clients()
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0]["email"], "b@acme.test")

    def test_update_delete_and_delete_all(self):
        self.upload(workbook_bytes([HEADER, ["Acme", "", "", "", "", "Mexico", ""], ["Beta", "", "", "", "", "USA", ""]]))
        acme, beta = list_clients()
        updated = update_client(acme["id"], ClientPayload(customer=" Acme Corp ", email="ops@acme.test", country="Mexico"))
        self.assertEqual(updated["customer"], "Acme Corp")
        self.assertEqual(updated["email"], "ops@acme.test")
        with self.assertRaises(HTTPException) as conflict:
            update_client(beta["id"], ClientPayload(customer="acme corp"))
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(delete_client(beta["id"])["customer"], "Beta")
        with self.assertRaises(HTTPException):
            delete_client(beta["id"])
        self.assertEqual(delete_all_clients(), {"deleted_clients": 1})
        self.assertEqual(list_clients(), [])

    def test_import_rejects_missing_customer_column_and_non_excel(self):
        with self.assertRaises(HTTPException):
            self.upload(workbook_bytes([["Email"], ["x@y.test"]]))
        with self.assertRaises(HTTPException):
            self.upload(b"x", name="clients.csv")


if __name__ == "__main__":
    unittest.main()
