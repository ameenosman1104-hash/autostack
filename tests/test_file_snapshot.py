"""Read-only local-file connector tests; no application database or network."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from datetime import datetime

spec=importlib.util.spec_from_file_location("snapshot",Path(__file__).parents[1]/"app/services/file_snapshot.py")
snapshot=importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)

class FileSnapshot(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"debtors.csv"

    def test_email_only_change_and_blank_omission(self):
        self.path.write_text("Name,Invoice No.,email\nShop,INV-1,\n",encoding="utf-8")
        self.assertNotIn("email",snapshot.read_snapshot(self.path)[0])
        self.path.write_text("Name,Invoice No.,email\nShop,INV-1,owner@example.com\n",encoding="utf-8")
        self.assertEqual(snapshot.read_snapshot(self.path)[0]["email"],"owner@example.com")

    def test_invalid_rows_rejected(self):
        for text in ["Name,Invoice No.\nShop,INV-1\nShop,INV-1", "Name,Invoice No.,email\nShop,INV-1,bad", "Name,Invoice No.,Invoice Amount\nShop,INV-1,NaN"]:
            self.path.write_text(text,encoding="utf-8")
            with self.assertRaises(ValueError): snapshot.read_snapshot(self.path)

    def test_real_excel_dates_and_cached_amounts(self):
        from openpyxl import Workbook
        path=self.path.with_suffix(".xlsx")
        wb=Workbook()
        wb.active.append(["Name","Invoice No.","Invoice Date","Invoice Amount","Amount Paid","Email Address"])
        wb.active.append(["Shop","INV-1",datetime(2026,9,9),18500,8000,"owner@example.com"])
        wb.save(path)
        wb.close()
        row=snapshot.read_snapshot(path)[0]
        self.assertEqual(row["purchase_date"],"2026-09-09")
        self.assertEqual(row["balance"],"10500.00")

    def test_ambiguous_mapping_rejected(self):
        self.path.write_text("Name,Invoice No.,email,Email Address\nShop,INV-1,a@b.co,c@d.co",encoding="utf-8")
        with self.assertRaises(ValueError): snapshot.read_snapshot(self.path)
