"""
Import System Regression Test for Phase B
Verifies that Phase B customer management changes did not break existing import workflows.
"""

import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tenant_db import (
    add_product, get_all_products, get_setting, save_setting,
    add_debtor, get_all_debtors,
)
from app.db_migrations import migrate_tenant_db


class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def test(self, name, func):
        try:
            func()
            self.passed += 1
            print(f"[OK] {name}")
        except AssertionError as e:
            self.failed += 1
            self.errors.append((name, str(e)))
            print(f"[FAIL] {name}: {e}")
        except Exception as e:
            self.failed += 1
            self.errors.append((name, f"ERROR: {str(e)}"))
            print(f"[FAIL] {name}: ERROR: {e}")

    def summary(self):
        print(f"\n{'='*60}")
        print(f"Results: {self.passed} passed, {self.failed} failed")
        print(f"{'='*60}")
        if self.errors:
            print("\nFailed tests:")
            for name, error in self.errors:
                print(f"  - {name}: {error}")
        return self.failed == 0


_test_counter = 0

class IsolatedTestDB:
    """Context manager for isolated test databases."""

    def __init__(self):
        global _test_counter
        _test_counter += 1
        self.counter = _test_counter
        self.temp_dir = None
        self.tid = 6000 + _test_counter
        self.original_dir = None

    def __enter__(self):
        self.temp_dir = tempfile.mkdtemp(prefix=f"import_test_{self.counter}_")
        self.original_dir = os.environ.get("AUTOSTACK_DATA_DIR")
        os.environ["AUTOSTACK_DATA_DIR"] = self.temp_dir

        test_db_path = os.path.join(self.temp_dir, f"{self.tid}.db")
        try:
            migrate_tenant_db(self.tid, test_db_path)
        except Exception as e:
            self.__exit__(None, None, None)
            raise RuntimeError(f"Migration failed: {e}")

        return self.tid

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.original_dir:
            os.environ["AUTOSTACK_DATA_DIR"] = self.original_dir
        else:
            os.environ.pop("AUTOSTACK_DATA_DIR", None)

        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)


# ======================== IMPORT REGRESSION TESTS ========================

def test_product_import_workflow():
    """Verify product import workflow still works (CSV processing)."""
    with IsolatedTestDB() as tid:
        import time
        # Simulate adding products as if imported from CSV (use unique codes per test run)
        timestamp = str(int(time.time() * 1000) % 1000000)
        products_to_import = [
            (f"TYRE-{timestamp}-001", "Summer Tyre", "Tyres", 50, 100.00),
            (f"TYRE-{timestamp}-002", "Winter Tyre", "Tyres", 30, 120.00),
            (f"FILTER-{timestamp}-001", "Air Filter", "Filters", 100, 25.00),
        ]

        for code, name, category, stock, cost in products_to_import:
            pid = add_product(tid, code, name, category=category,
                            current_stock=stock, last_cost_price=cost)
            assert pid is not None, f"Failed to add imported product {code}"

        # Verify all products were added
        products = get_all_products(tid)
        assert len(products) >= 3, "Not all imported products added"

        codes = [p["code"] for p in products]
        for code, _, _, _, _ in products_to_import:
            assert code in codes, f"Imported product {code} not found"


def test_debtor_import_workflow():
    """Verify debtor import workflow still works."""
    with IsolatedTestDB() as tid:
        # Simulate adding debtors as if imported from CSV
        debtors_to_import = [
            ("Customer A", 5000.00, "2024-01-15"),
            ("Customer B", 3000.00, "2024-02-20"),
            ("Customer C", 7500.00, "2024-03-10"),
        ]

        for name, amount, purchase_date in debtors_to_import:
            did = add_debtor(tid, name, amount_owed=amount, date_of_purchase=purchase_date)
            assert did is not None, f"Failed to add imported debtor {name}"

        # Verify all debtors were added
        debtors = get_all_debtors(tid, show_paid=True)
        assert len(debtors) >= 3, "Not all imported debtors added"

        names = [d["name"] for d in debtors]
        for name, _, _ in debtors_to_import:
            assert name in names, f"Imported debtor {name} not found"


def test_import_settings_storage():
    """Verify import configuration storage still works."""
    with IsolatedTestDB() as tid:
        # Store import configuration (as if from a web form)
        import_config = '{"source": "google_sheet", "url": "https://docs.google.com/..."}'
        save_setting(tid, "inv_import_config", import_config)

        # Retrieve and verify
        retrieved = get_setting(tid, "inv_import_config")
        assert retrieved == import_config, "Import config not properly stored/retrieved"


def test_import_state_isolation():
    """Verify import state is properly isolated between tenants."""
    with IsolatedTestDB() as tid_a:
        with IsolatedTestDB() as tid_b:
            # Tenant A stores import config
            config_a = '{"source": "sheet_a"}'
            save_setting(tid_a, "inv_import_config", config_a)

            # Tenant B stores different config
            config_b = '{"source": "sheet_b"}'
            save_setting(tid_b, "inv_import_config", config_b)

            # Verify isolation
            retrieved_a = get_setting(tid_a, "inv_import_config")
            retrieved_b = get_setting(tid_b, "inv_import_config")

            assert retrieved_a == config_a, "Tenant A config corrupted"
            assert retrieved_b == config_b, "Tenant B config corrupted"
            assert retrieved_a != retrieved_b, "Import configs not isolated"


def test_bulk_product_add_still_works():
    """Verify bulk product operations still work."""
    with IsolatedTestDB() as tid:
        # Add multiple products in sequence (simulating bulk import)
        import time
        timestamp = str(int(time.time() * 1000) % 1000000)
        num_products = 20
        for i in range(num_products):
            pid = add_product(tid, f"BULK-{timestamp}-{i:03d}", f"Bulk Product {i}",
                            current_stock=100 + i)
            assert pid is not None, f"Failed to add bulk product {i}"

        # Verify all were added
        products = get_all_products(tid)
        assert len(products) >= num_products, f"Expected {num_products}+ products, got {len(products)}"


# ======================== MAIN ========================

def main():
    """Run import regression tests."""
    print("="*60)
    print("Import System Regression Tests")
    print("="*60 + "\n")

    results = TestResults()

    print("Running Import Workflow Tests...")
    results.test("Product import workflow", test_product_import_workflow)
    results.test("Debtor import workflow", test_debtor_import_workflow)
    results.test("Import settings storage", test_import_settings_storage)
    results.test("Import state isolation", test_import_state_isolation)
    results.test("Bulk product add", test_bulk_product_add_still_works)

    success = results.summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
