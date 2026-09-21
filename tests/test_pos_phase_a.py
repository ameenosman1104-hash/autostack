"""
Phase A Tests: POS Foundation
Tests database migrations, new tables, and POS functions without regression.
"""

import pytest
import sqlite3
import os
import tempfile
from datetime import datetime, date, timedelta
from app.tenant_db import (
    # Existing functions
    get_conn, get_product, add_product, update_product, get_all_products,
    get_debtor, add_debtor, get_all_debtors, outstanding_balance,
    add_payment, get_payment_history, log_stock_change, get_stock_history,
    get_low_stock_products, add_sale, get_all_sales, get_sales_stats,
    get_setting, save_setting,
    # New Phase A functions
    add_customer, get_customer, get_customer_by_name, get_all_customers, update_customer,
    add_invoice, get_invoice, get_invoice_by_idempotency_key, list_invoices,
    list_invoices_by_customer, update_invoice, get_invoice_items, add_invoice_item,
    add_service, get_service, get_all_services, update_service,
    get_debtor_by_customer_id,
)
from app.db_migrations import migrate_tenant_db


@pytest.fixture
def test_tenant_id():
    """Use a test tenant ID."""
    return 999


@pytest.fixture
def test_db_path(test_tenant_id):
    """Create a test database."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, f"{test_tenant_id}.db")
    yield db_path
    # Cleanup
    if os.path.exists(db_path):
        os.remove(db_path)
    os.rmdir(temp_dir)


@pytest.fixture
def setup_test_db(test_tenant_id, test_db_path, monkeypatch):
    """Setup test database with migrations."""
    # Patch the database path
    monkeypatch.setenv("AUTOSTACK_DATA_DIR", os.path.dirname(test_db_path))

    # Run migrations
    migrate_tenant_db(test_tenant_id, test_db_path)

    return test_tenant_id


# ==================== MIGRATION TESTS ====================

def test_migrations_run_successfully(setup_test_db, test_tenant_id, test_db_path):
    """Verify all Phase A migrations run without error."""
    conn = sqlite3.connect(test_db_path)

    # Check schema_migrations table exists
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    assert tables is not None, "schema_migrations table should exist"

    # Check all migrations applied
    versions = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    versions = [v[0] for v in versions]
    assert 11 in versions, "Migration 11 (customers) should be applied"
    assert 12 in versions, "Migration 12 (invoices) should be applied"
    assert 13 in versions, "Migration 13 (invoice_items) should be applied"
    assert 14 in versions, "Migration 14 (services) should be applied"
    assert 15 in versions, "Migration 15 (product columns) should be applied"
    assert 16 in versions, "Migration 16 (debtor columns) should be applied"

    conn.close()


# ==================== CUSTOMERS TABLE TESTS ====================

def test_add_customer(setup_test_db):
    """Test adding a customer."""
    tid = setup_test_db
    customer_id = add_customer(tid, "ABC Transport", phone="0123456789", email="abc@example.com")
    assert customer_id is not None

    customer = get_customer(tid, customer_id)
    assert customer["name"] == "ABC Transport"
    assert customer["phone"] == "0123456789"
    assert customer["customer_type"] == "regular"


def test_get_customer_by_name(setup_test_db):
    """Test searching for customer by name."""
    tid = setup_test_db
    add_customer(tid, "XYZ Distributors")

    found = get_customer_by_name(tid, "XYZ")
    assert found is not None
    assert found["name"] == "XYZ Distributors"


def test_update_customer(setup_test_db):
    """Test updating customer."""
    tid = setup_test_db
    customer_id = add_customer(tid, "Test Company")
    update_customer(tid, customer_id, phone="9876543210", notes="Updated")

    customer = get_customer(tid, customer_id)
    assert customer["phone"] == "9876543210"
    assert customer["notes"] == "Updated"


def test_list_customers(setup_test_db):
    """Test listing all customers."""
    tid = setup_test_db
    add_customer(tid, "Customer A")
    add_customer(tid, "Customer B")
    add_customer(tid, "Customer C")

    customers = get_all_customers(tid)
    assert len(customers) >= 3
    names = [c["name"] for c in customers]
    assert "Customer A" in names
    assert "Customer B" in names
    assert "Customer C" in names


# ==================== INVOICES TABLE TESTS ====================

def test_add_invoice(setup_test_db):
    """Test creating an invoice."""
    tid = setup_test_db
    customer_id = add_customer(tid, "Test Customer")

    invoice_id = add_invoice(
        tid,
        invoice_number="INV-001",
        idempotency_key="unique-key-001",
        customer_id=customer_id,
        sale_date=datetime.now().isoformat(),
        total=5000.00,
        payment_method='cash'
    )
    assert invoice_id is not None

    invoice = get_invoice(tid, invoice_id)
    assert invoice["invoice_number"] == "INV-001"
    assert invoice["total"] == 5000.00
    assert invoice["payment_method"] == "cash"


def test_invoice_idempotency_key_prevents_duplicate(setup_test_db):
    """Test that idempotency_key UNIQUE constraint prevents duplicates."""
    tid = setup_test_db
    customer_id = add_customer(tid, "Test Customer")
    idempotency_key = "unique-request-id"

    # First invoice
    invoice_id1 = add_invoice(
        tid,
        invoice_number="INV-002",
        idempotency_key=idempotency_key,
        customer_id=customer_id,
        sale_date=datetime.now().isoformat(),
        total=1000.00,
        payment_method='cash'
    )

    # Try to create with same idempotency_key
    with pytest.raises(ValueError, match="already processed"):
        add_invoice(
            tid,
            invoice_number="INV-003",  # Different invoice_number
            idempotency_key=idempotency_key,  # Same key
            customer_id=customer_id,
            sale_date=datetime.now().isoformat(),
            total=2000.00,
            payment_method='cash'
        )


def test_invoice_number_must_be_unique(setup_test_db):
    """Test that invoice_number UNIQUE constraint works."""
    tid = setup_test_db
    customer_id = add_customer(tid, "Test Customer")

    # First invoice
    add_invoice(
        tid,
        invoice_number="INV-UNIQUE",
        idempotency_key="key-1",
        customer_id=customer_id,
        sale_date=datetime.now().isoformat(),
        total=1000.00,
        payment_method='cash'
    )

    # Try to create with same invoice_number
    with pytest.raises(ValueError, match="already exists"):
        add_invoice(
            tid,
            invoice_number="INV-UNIQUE",  # Same number
            idempotency_key="key-2",  # Different key
            customer_id=customer_id,
            sale_date=datetime.now().isoformat(),
            total=2000.00,
            payment_method='cash'
        )


def test_get_invoice_by_idempotency_key(setup_test_db):
    """Test retrieving invoice by idempotency_key (for duplicate detection)."""
    tid = setup_test_db
    customer_id = add_customer(tid, "Test Customer")
    idempotency_key = "test-key-123"

    invoice_id = add_invoice(
        tid,
        invoice_number="INV-004",
        idempotency_key=idempotency_key,
        customer_id=customer_id,
        sale_date=datetime.now().isoformat(),
        total=3000.00,
        payment_method='card'
    )

    found = get_invoice_by_idempotency_key(tid, idempotency_key)
    assert found is not None
    assert found["id"] == invoice_id
    assert found["invoice_number"] == "INV-004"


def test_list_invoices(setup_test_db):
    """Test listing invoices."""
    tid = setup_test_db
    customer_id = add_customer(tid, "Test Customer")

    for i in range(3):
        add_invoice(
            tid,
            invoice_number=f"INV-{100+i}",
            idempotency_key=f"key-{100+i}",
            customer_id=customer_id,
            sale_date=datetime.now().isoformat(),
            total=1000.00 * (i + 1),
            payment_method='cash'
        )

    invoices = list_invoices(tid, limit=10)
    assert len(invoices) >= 3


def test_list_invoices_by_customer(setup_test_db):
    """Test listing invoices for a specific customer."""
    tid = setup_test_db
    cust1 = add_customer(tid, "Customer 1")
    cust2 = add_customer(tid, "Customer 2")

    add_invoice(tid, "INV-A1", "key-a1", cust1, datetime.now().isoformat(), 100, "cash")
    add_invoice(tid, "INV-A2", "key-a2", cust1, datetime.now().isoformat(), 200, "cash")
    add_invoice(tid, "INV-B1", "key-b1", cust2, datetime.now().isoformat(), 300, "cash")

    cust1_invoices = list_invoices_by_customer(tid, cust1)
    assert len(cust1_invoices) == 2

    cust2_invoices = list_invoices_by_customer(tid, cust2)
    assert len(cust2_invoices) == 1


# ==================== INVOICE ITEMS TESTS ====================

def test_add_invoice_item(setup_test_db):
    """Test adding a line item to an invoice."""
    tid = setup_test_db
    customer_id = add_customer(tid, "Test Customer")

    # Create product
    product_id = add_product(tid, "PROD-001", "Test Product", current_stock=100, last_cost_price=500)

    # Create invoice
    invoice_id = add_invoice(
        tid,
        invoice_number="INV-ITEM-001",
        idempotency_key="key-item-001",
        customer_id=customer_id,
        sale_date=datetime.now().isoformat(),
        total=2000.00,
        payment_method='cash'
    )

    # Add item
    item_id = add_invoice_item(tid, invoice_id, 'product', 4, 500, product_id=product_id)
    assert item_id is not None

    items = get_invoice_items(tid, invoice_id)
    assert len(items) == 1
    assert items[0]["quantity"] == 4
    assert items[0]["unit_price"] == 500
    assert items[0]["total"] == 2000.00


def test_invoice_item_with_service(setup_test_db):
    """Test adding a service item (no product_id)."""
    tid = setup_test_db
    customer_id = add_customer(tid, "Test Customer")

    # Create service
    service_id = add_service(tid, "Wheel Fitting", default_price=500)

    # Create invoice
    invoice_id = add_invoice(
        tid,
        invoice_number="INV-SVC-001",
        idempotency_key="key-svc-001",
        customer_id=customer_id,
        sale_date=datetime.now().isoformat(),
        total=500.00,
        payment_method='cash'
    )

    # Add service item
    item_id = add_invoice_item(tid, invoice_id, 'service', 1, 500, service_id=service_id)
    assert item_id is not None

    items = get_invoice_items(tid, invoice_id)
    assert len(items) == 1
    assert items[0]["item_type"] == "service"
    assert items[0]["service_id"] == service_id


# ==================== SERVICES TABLE TESTS ====================

def test_add_service(setup_test_db):
    """Test adding a service."""
    tid = setup_test_db
    service_id = add_service(tid, "Tyre Fitting", default_price=350, category="Fitting")
    assert service_id is not None

    service = get_service(tid, service_id)
    assert service["name"] == "Tyre Fitting"
    assert service["default_price"] == 350
    assert service["category"] == "Fitting"


def test_list_services(setup_test_db):
    """Test listing services."""
    tid = setup_test_db
    add_service(tid, "Service A", default_price=100)
    add_service(tid, "Service B", default_price=200)
    add_service(tid, "Service C", default_price=300)

    services = get_all_services(tid)
    assert len(services) >= 3


# ==================== REGRESSION TESTS ====================

def test_existing_products_still_work(setup_test_db):
    """Regression: Existing product system still works."""
    tid = setup_test_db
    product_id = add_product(tid, "OLD-001", "Existing Product", current_stock=50)

    product = get_product(tid, product_id)
    assert product["name"] == "Existing Product"
    assert product["current_stock"] == 50


def test_product_condition_field_has_default(setup_test_db):
    """Regression: New condition field defaults to 'new'."""
    tid = setup_test_db
    product_id = add_product(tid, "PROD-COND", "Test Product")

    product = get_product(tid, product_id)
    assert product["condition"] == "new"


def test_existing_debtors_still_work(setup_test_db):
    """Regression: Existing debtor system still works."""
    tid = setup_test_db
    debtor_id = add_debtor(tid, "Old Debtor", amount_owed=5000, date_of_purchase=date.today().isoformat())

    debtor = get_debtor(tid, debtor_id)
    assert debtor["name"] == "Old Debtor"
    assert debtor["amount_owed"] == 5000


def test_debtor_customer_id_nullable(setup_test_db):
    """Regression: customer_id on debtors is nullable (backwards compatible)."""
    tid = setup_test_db
    debtor_id = add_debtor(tid, "Existing Debtor", amount_owed=1000, date_of_purchase=date.today().isoformat())

    debtor = get_debtor(tid, debtor_id)
    assert debtor["customer_id"] is None


def test_existing_payments_still_work(setup_test_db):
    """Regression: Payment history still works."""
    tid = setup_test_db
    debtor_id = add_debtor(tid, "Paying Debtor", amount_owed=10000, date_of_purchase=date.today().isoformat())

    add_payment(tid, debtor_id, 4000, payment_method='cash')

    payments = get_payment_history(tid, debtor_id)
    assert len(payments) == 1
    assert payments[0]["amount_paid"] == 4000


def test_existing_stock_tracking_still_works(setup_test_db):
    """Regression: Stock tracking and audit trail still work."""
    tid = setup_test_db
    product_id = add_product(tid, "TRACK-001", "Track Product", current_stock=20)

    # Simulate stock change
    update_product(tid, product_id, current_stock=15)
    log_stock_change(tid, product_id, "TRACK-001", "Track Product",
                     "manual", 20, 15, "Test adjustment")

    history = get_stock_history(tid, limit=5)
    assert len(history) > 0
    assert any(h["product_code"] == "TRACK-001" for h in history)


def test_existing_sales_table_still_works(setup_test_db):
    """Regression: Old sales table (legacy) still works."""
    tid = setup_test_db
    product_id = add_product(tid, "LEGACY-001", "Legacy Product")

    # Old-style sale entry (one product)
    add_sale(tid, product_id, "LEGACY-001", "Legacy Product", 3, 500, "Legacy note")

    sales = get_all_sales(tid, limit=10)
    assert len(sales) > 0
    assert any(s["product_code"] == "LEGACY-001" for s in sales)


def test_existing_low_stock_alerts_still_work(setup_test_db):
    """Regression: Low-stock alert system still works."""
    tid = setup_test_db
    product_id = add_product(tid, "LOW-001", "Low Stock Product",
                            current_stock=2, reorder_level=5)

    low = get_low_stock_products(tid)
    assert any(p["id"] == product_id for p in low)


def test_existing_settings_still_work(setup_test_db):
    """Regression: Settings system still works."""
    tid = setup_test_db
    save_setting(tid, "test_key", "test_value")

    value = get_setting(tid, "test_key")
    assert value == "test_value"


def test_sales_stats_still_work(setup_test_db):
    """Regression: Sales statistics still calculate correctly."""
    tid = setup_test_db
    product_id = add_product(tid, "STAT-001", "Stat Product")

    add_sale(tid, product_id, "STAT-001", "Stat Product", 5, 1000, "")
    add_sale(tid, product_id, "STAT-001", "Stat Product", 3, 1000, "")

    stats = get_sales_stats(tid)
    assert stats["total_qty"] == 8
    assert stats["total_revenue"] == 8000
    assert stats["total_transactions"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
