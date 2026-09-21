#!/usr/bin/env python3
"""
Phase A Tests: POS Foundation (Standalone)
Tests database migrations, new tables, and POS functions without pytest.
"""

import sys
import os
import tempfile
import sqlite3
from datetime import datetime, date, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tenant_db import (
    get_product, add_product, update_product, get_all_products,
    get_debtor, add_debtor, get_all_debtors, outstanding_balance,
    add_payment, get_payment_history, log_stock_change, get_stock_history,
    get_low_stock_products, add_sale, get_all_sales, get_sales_stats,
    get_setting, save_setting,
    add_customer, get_customer, get_customer_by_name, get_all_customers, update_customer,
    add_invoice, get_invoice, get_invoice_by_idempotency_key, list_invoices,
    list_invoices_by_customer, update_invoice, get_invoice_items, add_invoice_item,
    add_service, get_service, get_all_services, update_service,
    get_debtor_by_customer_id,
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
            print(f"✓ {name}")
        except AssertionError as e:
            self.failed += 1
            self.errors.append((name, str(e)))
            print(f"✗ {name}: {e}")
        except Exception as e:
            self.failed += 1
            self.errors.append((name, f"ERROR: {str(e)}"))
            print(f"✗ {name}: ERROR: {e}")

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

def setup_test_db():
    """Setup test database with migrations (fully isolated per test)."""
    global _test_counter
    _test_counter += 1

    # Create a truly unique temp directory for this test with unique tenant ID
    temp_dir = tempfile.mkdtemp(prefix=f"test_{_test_counter}_")
    test_tenant_id = 1000 + _test_counter  # Use very different tenant ID for each test
    test_db_path = os.path.join(temp_dir, f"{test_tenant_id}.db")

    # Set environment for this test database
    # NOTE: We do NOT restore it yet — tests will call get_conn() which needs this set
    os.environ["AUTOSTACK_DATA_DIR"] = temp_dir

    # Run migrations
    try:
        migrate_tenant_db(test_tenant_id, test_db_path)
        return test_tenant_id, test_db_path
    except Exception as e:
        print(f"Migration failed: {e}")
        return None


def test_migrations():
    """Test that migrations were applied."""
    test_id, test_path = setup_test_db()
    conn = sqlite3.connect(test_path)

    # Check migrations table
    try:
        versions = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        versions = [v[0] for v in versions]

        assert 11 in versions, "Migration 11 (customers) not applied"
        assert 12 in versions, "Migration 12 (invoices) not applied"
        assert 13 in versions, "Migration 13 (invoice_items) not applied"
        assert 14 in versions, "Migration 14 (services) not applied"
        assert 15 in versions, "Migration 15 (product columns) not applied"
        assert 16 in versions, "Migration 16 (debtor columns) not applied"
    finally:
        conn.close()


def test_customers():
    """Test customer functions."""
    test_id, _ = setup_test_db()

    # Add customer
    cust_id = add_customer(test_id, "ABC Transport", phone="123456", email="abc@test.com")
    assert cust_id is not None, "Failed to add customer"

    # Get customer
    customer = get_customer(test_id, cust_id)
    assert customer is not None, "Customer not found"
    assert customer["name"] == "ABC Transport", "Customer name mismatch"

    # Search by name
    found = get_customer_by_name(test_id, "ABC")
    assert found is not None, "Search by name failed"

    # Update customer
    update_customer(test_id, cust_id, phone="999999")
    customer = get_customer(test_id, cust_id)
    assert customer["phone"] == "999999", "Update failed"

    # List customers
    customers = get_all_customers(test_id)
    assert len(customers) >= 1, "List customers failed"


def test_invoices():
    """Test invoice functions."""
    test_id, _ = setup_test_db()
    cust_id = add_customer(test_id, "Test Cust")

    # Add invoice
    inv_id = add_invoice(
        test_id,
        invoice_number="INV-001",
        idempotency_key="key-001",
        customer_id=cust_id,
        sale_date=datetime.now().isoformat(),
        total=5000.00,
        payment_method='cash'
    )
    assert inv_id is not None, "Failed to add invoice"

    # Get invoice
    invoice = get_invoice(test_id, inv_id)
    assert invoice is not None, "Invoice not found"
    assert invoice["invoice_number"] == "INV-001", "Invoice number mismatch"

    # Get by idempotency key
    found = get_invoice_by_idempotency_key(test_id, "key-001")
    assert found is not None, "Get by idempotency_key failed"

    # List invoices
    invoices = list_invoices(test_id)
    assert len(invoices) >= 1, "List invoices failed"

    # List by customer
    invoices = list_invoices_by_customer(test_id, cust_id)
    assert len(invoices) >= 1, "List by customer failed"


def test_invoice_idempotency():
    """Test that idempotency_key prevents duplicates."""
    test_id, _ = setup_test_db()
    cust_id = add_customer(test_id, "Test Cust")

    # Create invoice
    add_invoice(
        test_id,
        invoice_number="INV-UNIQUE",
        idempotency_key="unique-key",
        customer_id=cust_id,
        sale_date=datetime.now().isoformat(),
        total=1000.00,
        payment_method='cash'
    )

    # Try to create with same idempotency_key
    try:
        add_invoice(
            test_id,
            invoice_number="INV-DIFFERENT",
            idempotency_key="unique-key",  # Same key
            customer_id=cust_id,
            sale_date=datetime.now().isoformat(),
            total=2000.00,
            payment_method='cash'
        )
        assert False, "Should have raised error for duplicate idempotency_key"
    except ValueError as e:
        assert "already processed" in str(e), f"Wrong error message: {e}"


def test_invoice_items():
    """Test invoice line items."""
    test_id, _ = setup_test_db()
    cust_id = add_customer(test_id, "Test Cust")

    # Create product and invoice
    prod_id = add_product(test_id, "PROD-001", "Test Product", current_stock=100)
    inv_id = add_invoice(
        test_id,
        invoice_number="INV-ITEMS",
        idempotency_key="key-items",
        customer_id=cust_id,
        sale_date=datetime.now().isoformat(),
        total=2000.00,
        payment_method='cash'
    )

    # Add item
    item_id = add_invoice_item(test_id, inv_id, 'product', 4, 500, product_id=prod_id)
    assert item_id is not None, "Failed to add invoice item"

    # Get items
    items = get_invoice_items(test_id, inv_id)
    assert len(items) == 1, "Item not found"
    assert items[0]["quantity"] == 4, "Quantity mismatch"


def test_services():
    """Test service functions."""
    test_id, _ = setup_test_db()

    # Add service
    svc_id = add_service(test_id, "Wheel Fitting", default_price=500, category="Fitting")
    assert svc_id is not None, "Failed to add service"

    # Get service
    service = get_service(test_id, svc_id)
    assert service is not None, "Service not found"
    assert service["name"] == "Wheel Fitting", "Service name mismatch"

    # List services
    services = get_all_services(test_id)
    assert len(services) >= 1, "List services failed"


def test_regression_products():
    """Regression: Existing product system still works."""
    test_id, _ = setup_test_db()

    # Add product
    prod_id = add_product(test_id, "LEGACY-001", "Legacy Product", current_stock=50)
    assert prod_id is not None, "Failed to add product"

    # Get product
    product = get_product(test_id, prod_id)
    assert product is not None, "Product not found"
    assert product["current_stock"] == 50, "Stock mismatch"

    # Product has condition field (defaults to NULL for unclassified products)
    actual_value = product["condition"]
    assert product["condition"] is None, f"Condition field should default to NULL, but got: {repr(actual_value)} (type: {type(actual_value).__name__})"


def test_regression_debtors():
    """Regression: Existing debtor system still works."""
    test_id, _ = setup_test_db()

    # Add debtor
    debt_id = add_debtor(test_id, "Old Debtor", amount_owed=5000, date_of_purchase=date.today().isoformat())
    assert debt_id is not None, "Failed to add debtor"

    # Get debtor
    debtor = get_debtor(test_id, debt_id)
    assert debtor is not None, "Debtor not found"
    assert debtor["amount_owed"] == 5000, "Amount owed mismatch"

    # customer_id should be nullable
    assert debtor["customer_id"] is None, "customer_id should be NULL for old debtors"


def test_regression_payments():
    """Regression: Payment history still works."""
    test_id, _ = setup_test_db()

    # Add debtor and payment
    debt_id = add_debtor(test_id, "Paying Debtor", amount_owed=10000, date_of_purchase=date.today().isoformat())
    add_payment(test_id, debt_id, 4000, payment_method='cash')

    # Get payments
    payments = get_payment_history(test_id, debt_id)
    assert len(payments) == 1, "Payment not recorded"
    assert payments[0]["amount_paid"] == 4000, "Payment amount mismatch"


def test_regression_sales():
    """Regression: Old sales table still works."""
    test_id, _ = setup_test_db()

    # Add product and sale
    prod_id = add_product(test_id, "LEGACY-SALES", "Legacy Product")
    add_sale(test_id, prod_id, "LEGACY-SALES", "Legacy Product", 3, 500, "Legacy note")

    # Get sales
    sales = get_all_sales(test_id, limit=10)
    assert len(sales) > 0, "Sale not recorded"
    assert any(s["product_code"] == "LEGACY-SALES" for s in sales), "Sale not found"


def test_regression_stock_tracking():
    """Regression: Stock tracking and audit trail still work."""
    test_id, _ = setup_test_db()

    # Add product and change stock
    prod_id = add_product(test_id, "TRACK-001", "Track Product", current_stock=20)
    update_product(test_id, prod_id, current_stock=15)
    log_stock_change(test_id, prod_id, "TRACK-001", "Track Product", "manual", 20, 15, "Test")

    # Get history
    history = get_stock_history(test_id, limit=5)
    assert len(history) > 0, "History not recorded"
    assert any(h["product_code"] == "TRACK-001" for h in history), "Stock change not found"


def test_regression_settings():
    """Regression: Settings system still works."""
    test_id, _ = setup_test_db()

    # Save and retrieve setting
    save_setting(test_id, "test_key", "test_value")
    value = get_setting(test_id, "test_key")
    assert value == "test_value", "Setting not saved/retrieved"


def test_product_condition_explicit_classification():
    """Test explicit classification of products as 'new' or 'used'."""
    test_id, _ = setup_test_db()

    # Add product without condition (should be NULL)
    prod1 = add_product(test_id, "UNCLASS-001", "Unclassified Product")
    product = get_product(test_id, prod1)
    assert product["condition"] is None, "Unclassified product should have NULL condition"

    # Add new tyre with explicit 'new'
    prod2 = add_product(test_id, "TYRE-NEW-001", "New Tyre", last_cost_price=500)
    update_product(test_id, prod2, condition='new')
    product = get_product(test_id, prod2)
    assert product["condition"] == "new", "New tyre should have condition='new'"

    # Add used tyre with explicit 'used'
    prod3 = add_product(test_id, "TYRE-USED-001", "Used Tyre", last_cost_price=300)
    update_product(test_id, prod3, condition='used')
    product = get_product(test_id, prod3)
    assert product["condition"] == "used", "Used tyre should have condition='used'"


def test_regression_sales_stats():
    """Regression: Sales statistics work."""
    test_id, _ = setup_test_db()

    # Add products and sales
    prod_id = add_product(test_id, "STAT-001", "Stat Product")
    add_sale(test_id, prod_id, "STAT-001", "Stat Product", 5, 1000, "")
    add_sale(test_id, prod_id, "STAT-001", "Stat Product", 3, 1000, "")

    # Get stats
    stats = get_sales_stats(test_id)
    assert stats["total_qty"] == 8, f"Total qty mismatch: {stats}"
    assert stats["total_revenue"] == 8000, f"Total revenue mismatch: {stats}"
    assert stats["total_transactions"] == 2, f"Total transactions mismatch: {stats}"


def main():
    """Run all tests."""
    global _test_counter
    _test_counter = 0  # Reset counter for this test run

    print("="*60)
    print("Phase A Tests: POS Foundation")
    print("="*60 + "\n")

    results = TestResults()

    print("Running Migration Tests...")
    results.test("Migrations applied", test_migrations)

    print("\nRunning Customer Tests...")
    results.test("Customer CRUD operations", test_customers)

    print("\nRunning Invoice Tests...")
    results.test("Invoice creation and retrieval", test_invoices)
    results.test("Idempotency key prevents duplicates", test_invoice_idempotency)

    print("\nRunning Invoice Item Tests...")
    results.test("Invoice line items", test_invoice_items)

    print("\nRunning Service Tests...")
    results.test("Service CRUD operations", test_services)

    print("\nRunning Regression Tests (Existing Functionality)...")
    results.test("Products still work", test_regression_products)
    results.test("Debtors still work", test_regression_debtors)
    results.test("Payments still work", test_regression_payments)
    results.test("Sales (legacy) still work", test_regression_sales)
    results.test("Stock tracking still works", test_regression_stock_tracking)
    results.test("Settings still work", test_regression_settings)
    results.test("Sales stats still work", test_regression_sales_stats)

    print("\nRunning Product Condition Classification Tests...")
    results.test("Product condition explicit classification (NULL/'new'/'used')", test_product_condition_explicit_classification)

    success = results.summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
