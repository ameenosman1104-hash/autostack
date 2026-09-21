"""
Phase B Tests: Customer Management
Tests customer CRUD, search, debtor linking, tenant isolation, and regressions.
"""

import sys
import os
import tempfile
import shutil
from datetime import datetime, date

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tenant_db import (
    add_customer, get_customer, get_all_customers, update_customer, search_customers,
    get_debtor_by_customer_id, add_debtor, update_debtor, outstanding_balance, get_debtor,
    add_product, get_product, add_payment, get_payment_history,
    get_all_debtors, get_all_products, log_stock_change, get_stock_history,
    add_sale, get_all_sales, add_invoice, get_invoice, list_invoices_by_customer,
    get_setting, save_setting, init_tenant_db,
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
        self.tid = 3000 + _test_counter
        self.original_dir = None

    def __enter__(self):
        self.temp_dir = tempfile.mkdtemp(prefix=f"phaseB_test_{self.counter}_")
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
        # Restore original environment
        if self.original_dir:
            os.environ["AUTOSTACK_DATA_DIR"] = self.original_dir
        else:
            os.environ.pop("AUTOSTACK_DATA_DIR", None)

        # Clean up temp directory
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)


# ======================== CUSTOMER CRUD TESTS ========================

def test_add_customer_name_only():
    """Add customer with name field only (all others optional)."""
    with IsolatedTestDB() as tid:
        cid = add_customer(tid, "John Doe")
        assert cid is not None, "Failed to add customer"

        customer = get_customer(tid, cid)
        assert customer is not None, "Customer not found"
        assert customer["name"] == "John Doe"
        assert customer["phone"] == ""
        assert customer["email"] == ""
        assert customer["vehicle_registration"] == ""


def test_add_customer_full_fields():
    """Add customer with all optional fields."""
    with IsolatedTestDB() as tid:
        cid = add_customer(
            tid,
            name="ABC Transport",
            phone="0821234567",
            email="abc@example.com",
            vehicle_registration="CA-123-ABC",
            notes="Important client"
        )
        customer = get_customer(tid, cid)
        assert customer["name"] == "ABC Transport"
        assert customer["phone"] == "0821234567"
        assert customer["email"] == "abc@example.com"
        assert customer["vehicle_registration"] == "CA-123-ABC"
        assert customer["notes"] == "Important client"


def test_edit_customer():
    """Edit customer details."""
    with IsolatedTestDB() as tid:
        cid = add_customer(tid, "John Doe", phone="0821234567")

        update_customer(tid, cid, phone="0829999999", email="john@example.com")

        customer = get_customer(tid, cid)
        assert customer["phone"] == "0829999999"
        assert customer["email"] == "john@example.com"
        assert customer["name"] == "John Doe"  # Unchanged


def test_get_all_customers():
    """Get all customers."""
    with IsolatedTestDB() as tid:
        add_customer(tid, "Customer A")
        add_customer(tid, "Customer B")
        add_customer(tid, "Customer C")

        customers = get_all_customers(tid)
        assert len(customers) >= 3, f"Expected 3+ customers, got {len(customers)}"
        names = [c["name"] for c in customers]
        assert "Customer A" in names
        assert "Customer B" in names
        assert "Customer C" in names


# ======================== SEARCH TESTS ========================

def test_search_by_name():
    """Search customers by name."""
    with IsolatedTestDB() as tid:
        add_customer(tid, "Mohammed Khan", phone="0821111111")
        add_customer(tid, "Ahmed Khan", phone="0822222222")
        add_customer(tid, "John Smith", phone="0833333333")

        results = search_customers(tid, "Khan")
        assert len(results) == 2, f"Expected 2 results for 'Khan', got {len(results)}"
        names = [r["name"] for r in results]
        assert "Mohammed Khan" in names
        assert "Ahmed Khan" in names


def test_search_by_phone():
    """Search customers by phone number."""
    with IsolatedTestDB() as tid:
        add_customer(tid, "John Doe", phone="0821234567")
        add_customer(tid, "Jane Smith", phone="0829876543")

        results = search_customers(tid, "082123")
        assert len(results) == 1, f"Expected 1 result, got {len(results)}"
        assert results[0]["name"] == "John Doe"


def test_search_by_vehicle_registration():
    """Search customers by vehicle registration."""
    with IsolatedTestDB() as tid:
        add_customer(tid, "John Doe", vehicle_registration="CA-123-ABC")
        add_customer(tid, "Jane Smith", vehicle_registration="GP-456-XYZ")

        results = search_customers(tid, "CA-123")
        assert len(results) == 1, f"Expected 1 result, got {len(results)}"
        assert results[0]["name"] == "John Doe"


def test_search_case_insensitive():
    """Search is case-insensitive."""
    with IsolatedTestDB() as tid:
        add_customer(tid, "Mohammed Khan")

        results = search_customers(tid, "KHAN")
        assert len(results) == 1, "Search should be case-insensitive"


# ======================== DUPLICATE NAMES ========================

def test_duplicate_names_allowed():
    """Two customers can have the same name (not auto-merged)."""
    with IsolatedTestDB() as tid:
        cid1 = add_customer(tid, "Mohammed Khan", phone="0821111111")
        cid2 = add_customer(tid, "Mohammed Khan", phone="0822222222")

        assert cid1 != cid2, "Duplicate names should create separate records"

        cust1 = get_customer(tid, cid1)
        cust2 = get_customer(tid, cid2)

        assert cust1["name"] == cust2["name"] == "Mohammed Khan"
        assert cust1["phone"] == "0821111111"
        assert cust2["phone"] == "0822222222"


# ======================== DEBTOR LINKING ========================

def test_customer_linked_to_debtor():
    """Customer linked to debtor shows correct outstanding balance."""
    with IsolatedTestDB() as tid:
        cid = add_customer(tid, "John Doe")

        # Create debtor linked to customer
        did = add_debtor(tid, "John's Debt", amount_owed=5000.00, date_of_purchase=date.today().isoformat())
        update_debtor(tid, did, customer_id=cid)

        # Verify link works
        linked_debtor = get_debtor_by_customer_id(tid, cid)
        assert linked_debtor is not None, "Debtor should be linked to customer"
        assert linked_debtor["id"] == did

        # Verify balance
        balance = outstanding_balance(linked_debtor)
        assert balance == 5000.00, f"Expected balance 5000.00, got {balance}"


def test_customer_linked_to_paid_debtor():
    """Customer linked to a paid debtor should show zero balance."""
    with IsolatedTestDB() as tid:
        cid = add_customer(tid, "John Doe")

        # Create debtor, mark as paid
        did = add_debtor(tid, "John's Debt", amount_owed=5000.00, date_of_purchase=date.today().isoformat())
        update_debtor(tid, did, customer_id=cid, is_paid=True)

        # get_debtor_by_customer_id returns only active debtors (is_paid=0)
        linked_debtor = get_debtor_by_customer_id(tid, cid)
        assert linked_debtor is None, "Paid debtor should not be returned by get_debtor_by_customer_id"


def test_customer_without_debtor():
    """Customer without linked debtor works normally."""
    with IsolatedTestDB() as tid:
        cid = add_customer(tid, "New Customer")

        debtor = get_debtor_by_customer_id(tid, cid)
        assert debtor is None, "New customer should not have a debtor"


# ======================== INVOICE/TRANSACTION HISTORY ========================

def test_invoice_with_customer():
    """Invoice linked to customer shows in transaction history."""
    with IsolatedTestDB() as tid:
        cid = add_customer(tid, "John Doe")

        inv_id = add_invoice(
            tid,
            invoice_number="INV-001",
            idempotency_key="key-001",
            customer_id=cid,
            sale_date=datetime.now().isoformat(),
            total=1000.00
        )

        invoices = list_invoices_by_customer(tid, cid)
        assert len(invoices) == 1, "Invoice should be in customer history"
        assert invoices[0]["id"] == inv_id


def test_walk_in_invoice_null_customer():
    """Walk-in invoice (customer_id=NULL) is valid."""
    with IsolatedTestDB() as tid:
        # Invoice with no customer (walk-in)
        inv_id = add_invoice(
            tid,
            invoice_number="INV-WALKIN",
            idempotency_key="key-walkin",
            customer_id=None,  # ← Walk-in, no customer
            sale_date=datetime.now().isoformat(),
            total=500.00
        )

        invoice = get_invoice(tid, inv_id)
        assert invoice is not None
        assert invoice["customer_id"] is None, "Walk-in invoice should have NULL customer_id"


def test_customer_empty_transaction_history():
    """Customer with no invoices shows empty history."""
    with IsolatedTestDB() as tid:
        cid = add_customer(tid, "New Customer")

        invoices = list_invoices_by_customer(tid, cid)
        assert len(invoices) == 0, "New customer should have no invoices"


# ======================== TENANT ISOLATION ========================

def test_tenant_isolation_database_level():
    """Business A cannot access Business B's customers (database level)."""
    with IsolatedTestDB() as tid_a:
        with IsolatedTestDB() as tid_b:
            # Business A creates a customer
            cid_a = add_customer(tid_a, "Customer A")
            customer_a = get_customer(tid_a, cid_a)
            assert customer_a is not None, "Business A should see their customer"

            # Business B tries to access Business A's customer ID
            customer_b_sees_a = get_customer(tid_b, cid_a)
            assert customer_b_sees_a is None, "Business B should NOT see Business A's customer"


def test_search_api_tenant_isolation():
    """Search API returns only current tenant's customers."""
    with IsolatedTestDB() as tid_a:
        with IsolatedTestDB() as tid_b:
            # Business A creates customers
            add_customer(tid_a, "Customer A", phone="0821111111")
            add_customer(tid_a, "Customer B", phone="0822222222")

            # Business B creates customers
            add_customer(tid_b, "Customer C", phone="0831111111")

            # Business B searches - should only see their customer
            results_b = search_customers(tid_b, "0831111111")
            assert len(results_b) == 1, "Business B should only see 1 result"
            assert results_b[0]["name"] == "Customer C"

            # Business B searches - should NOT see A's customers
            results_b_search = search_customers(tid_b, "Customer A")
            assert len(results_b_search) == 0, "Business B should not see Business A's customers"


# ======================== REGRESSION TESTS ========================

def test_regression_debtors_still_work():
    """Existing debtor system unaffected by Phase B."""
    with IsolatedTestDB() as tid:
        # Create debtor without customer (old style)
        did = add_debtor(tid, "Old Debtor", amount_owed=3000.00, date_of_purchase=date.today().isoformat())

        debtor = get_debtor(tid, did)
        assert debtor is not None
        assert debtor["name"] == "Old Debtor"
        assert debtor["customer_id"] is None, "Old debtor should have NULL customer_id"

        # Get all debtors should work
        all_debtors = get_all_debtors(tid, show_paid=True)
        assert len(all_debtors) > 0


def test_regression_stock_still_works():
    """Existing stock system unaffected by Phase B."""
    with IsolatedTestDB() as tid:
        pid = add_product(tid, "TYRE-001", "Test Tyre", current_stock=10)

        product = get_product(tid, pid)
        assert product is not None
        assert product["current_stock"] == 10

        all_products = get_all_products(tid)
        assert len(all_products) > 0


def test_regression_sales_still_work():
    """Existing sales system unaffected by Phase B."""
    with IsolatedTestDB() as tid:
        pid = add_product(tid, "LEGACY-001", "Legacy Product")
        add_sale(tid, pid, "LEGACY-001", "Legacy Product", 3, 500, "Legacy note")

        sales = get_all_sales(tid, limit=10)
        assert len(sales) > 0
        assert any(s["product_code"] == "LEGACY-001" for s in sales)


def test_regression_stock_history_still_works():
    """Existing stock history system unaffected by Phase B."""
    with IsolatedTestDB() as tid:
        pid = add_product(tid, "TRACK-001", "Track Product", current_stock=20)
        log_stock_change(tid, pid, "TRACK-001", "Track Product", "manual", 20, 15, "Test")

        history = get_stock_history(tid, limit=5)
        assert len(history) > 0
        assert any(h["product_code"] == "TRACK-001" for h in history)


def test_regression_settings_still_work():
    """Existing settings system unaffected by Phase B."""
    with IsolatedTestDB() as tid:
        save_setting(tid, "test_key", "test_value")

        value = get_setting(tid, "test_key")
        assert value == "test_value"


# ======================== MAIN ========================

def main():
    """Run all Phase B tests."""
    print("="*60)
    print("Phase B Tests: Customer Management")
    print("="*60 + "\n")

    results = TestResults()

    print("Running Customer CRUD Tests...")
    results.test("Add customer (name only)", test_add_customer_name_only)
    results.test("Add customer (full fields)", test_add_customer_full_fields)
    results.test("Edit customer", test_edit_customer)
    results.test("Get all customers", test_get_all_customers)

    print("\nRunning Customer Search Tests...")
    results.test("Search by name", test_search_by_name)
    results.test("Search by phone", test_search_by_phone)
    results.test("Search by vehicle registration", test_search_by_vehicle_registration)
    results.test("Search is case-insensitive", test_search_case_insensitive)

    print("\nRunning Duplicate Handling Tests...")
    results.test("Duplicate names allowed", test_duplicate_names_allowed)

    print("\nRunning Debtor Linking Tests...")
    results.test("Customer linked to debtor", test_customer_linked_to_debtor)
    results.test("Customer linked to paid debtor", test_customer_linked_to_paid_debtor)
    results.test("Customer without debtor", test_customer_without_debtor)

    print("\nRunning Invoice/Transaction History Tests...")
    results.test("Invoice with customer", test_invoice_with_customer)
    results.test("Walk-in invoice (NULL customer)", test_walk_in_invoice_null_customer)
    results.test("Customer empty transaction history", test_customer_empty_transaction_history)

    print("\nRunning Tenant Isolation Tests...")
    results.test("Tenant isolation (database level)", test_tenant_isolation_database_level)
    results.test("Search API tenant isolation", test_search_api_tenant_isolation)

    print("\nRunning Regression Tests...")
    results.test("Debtors still work", test_regression_debtors_still_work)
    results.test("Stock still works", test_regression_stock_still_works)
    results.test("Sales still work", test_regression_sales_still_work)
    results.test("Stock history still works", test_regression_stock_history_still_works)
    results.test("Settings still work", test_regression_settings_still_work)

    success = results.summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
