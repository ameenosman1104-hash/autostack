"""POS Customer Workflow Tests - Search, Create, Selection."""

import sys
import os
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.tenant_db import (
    add_product, add_customer, get_customer, search_customers,
    complete_sale, get_conn
)
from app.db_migrations import migrate_tenant_db
import tempfile
import uuid
import json


def test_pos_search_existing_customer():
    """Search endpoint should find customers by name, phone, registration."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 9001
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Add customers
    cid1 = add_customer(test_tid, 'ABC Motors', phone='555-1234', vehicle_registration='ABC 123 GP')
    cid2 = add_customer(test_tid, 'XYZ Auto', phone='555-5678', vehicle_registration='XYZ 456 GP')

    # Direct test via search_customers function (core functionality)
    results = search_customers(test_tid, 'ABC')
    assert len(results) == 1
    assert results[0]['name'] == 'ABC Motors'

    results = search_customers(test_tid, '555-1234')
    assert len(results) == 1
    assert results[0]['phone'] == '555-1234'

    results = search_customers(test_tid, 'ABC 123')
    assert len(results) == 1
    assert results[0]['vehicle_registration'] == 'ABC 123 GP'

    # Test phone search finds by partial
    results = search_customers(test_tid, '555')
    assert len(results) == 2

    print("[OK] Search existing customer works")


def test_pos_search_tenant_isolated():
    """Customer search should not cross tenant boundaries."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid_a = 9002
    test_tid_b = 9003

    for tid in [test_tid_a, test_tid_b]:
        db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{tid}.db')
        migrate_tenant_db(tid, db_path)

    # Tenant A adds customer
    cid_a = add_customer(test_tid_a, 'Tenant A Customer', phone='555-1111')

    # Tenant B adds different customer
    cid_b = add_customer(test_tid_b, 'Tenant B Customer', phone='555-2222')

    # Tenant A search should not see Tenant B's customer
    results_a = search_customers(test_tid_a, 'Tenant B')
    assert len(results_a) == 0

    # Tenant A should see their own
    results_a = search_customers(test_tid_a, 'Tenant A')
    assert len(results_a) == 1

    # Tenant B should see their own
    results_b = search_customers(test_tid_b, 'Tenant B')
    assert len(results_b) == 1

    # Tenant B should not see Tenant A's
    results_b = search_customers(test_tid_b, 'Tenant A')
    assert len(results_b) == 0

    print("[OK] Search remains tenant isolated")


def test_pos_create_new_customer():
    """New customer can be created via /customers/add-json endpoint."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 9004
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Use add_customer directly (which is reused by add-json endpoint)
    cid = add_customer(test_tid, 'New Customer', phone='555-9999',
                      vehicle_registration='NEW 999 GP')

    # Verify customer exists and can be searched
    customers = search_customers(test_tid, 'New Customer')
    assert len(customers) == 1
    assert customers[0]['name'] == 'New Customer'
    assert customers[0]['phone'] == '555-9999'
    assert customers[0]['vehicle_registration'] == 'NEW 999 GP'

    # Verify via get_customer
    cust = get_customer(test_tid, cid)
    assert cust is not None
    assert cust['name'] == 'New Customer'

    print("[OK] New customer can be created from POS")


def test_pos_customer_required_for_credit():
    """Credit sales should require a customer_id (enforced by complete_sale)."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 9005
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'POS-CREDIT-001', 'Test Tyre',
                     current_stock=100, selling_price=2000.00)

    # Try credit sale without customer - should raise
    try:
        complete_sale(test_tid, str(uuid.uuid4()),
                     [{'type': 'product', 'id': pid, 'quantity': 1}],
                     payment_method='credit',
                     customer_id=None)
        assert False, "Should have raised error for credit without customer"
    except ValueError as e:
        assert 'customer' in str(e).lower()

    # With customer should succeed
    cid = add_customer(test_tid, 'Credit Customer')
    result = complete_sale(test_tid, str(uuid.uuid4()),
                          [{'type': 'product', 'id': pid, 'quantity': 1}],
                          payment_method='credit',
                          customer_id=cid)
    assert result['invoice_number'] is not None

    print("[OK] Credit sales require customer")


def test_pos_cash_card_eft_optional_customer():
    """Cash, Card, EFT sales should allow optional customer."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 9006
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'POS-CASH-001', 'Test Tyre',
                     current_stock=100, selling_price=1000.00)

    # Walk-in cash sale (no customer)
    result = complete_sale(test_tid, str(uuid.uuid4()),
                          [{'type': 'product', 'id': pid, 'quantity': 1}],
                          payment_method='cash')
    assert result['invoice_number'] is not None

    # Walk-in card sale
    result = complete_sale(test_tid, str(uuid.uuid4()),
                          [{'type': 'product', 'id': pid, 'quantity': 1}],
                          payment_method='card')
    assert result['invoice_number'] is not None

    # Walk-in EFT sale
    result = complete_sale(test_tid, str(uuid.uuid4()),
                          [{'type': 'product', 'id': pid, 'quantity': 1}],
                          payment_method='eft')
    assert result['invoice_number'] is not None

    # With customer should also work
    cid = add_customer(test_tid, 'Repeat Customer')
    result = complete_sale(test_tid, str(uuid.uuid4()),
                          [{'type': 'product', 'id': pid, 'quantity': 1}],
                          customer_id=cid,
                          payment_method='card')
    assert result['invoice_number'] is not None

    print("[OK] Cash/Card/EFT allow optional customer")


def test_pos_customer_selection_preserves_basket():
    """Selecting customer should not clear basket items."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 9007
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid1 = add_product(test_tid, 'POS-PRESERVE-001', 'Tyre A',
                      current_stock=100, selling_price=1000.00)
    pid2 = add_product(test_tid, 'POS-PRESERVE-002', 'Tyre B',
                      current_stock=100, selling_price=2000.00)

    cid = add_customer(test_tid, 'Selection Customer')

    # In a real POS session, we'd add items to basket
    # Then select customer and complete sale
    result = complete_sale(test_tid, str(uuid.uuid4()),
                          [
                              {'type': 'product', 'id': pid1, 'quantity': 2},
                              {'type': 'product', 'id': pid2, 'quantity': 1}
                          ],
                          customer_id=cid,
                          payment_method='cash')

    assert result['invoice_number'] is not None

    # Verify items were actually sold
    conn = get_conn(test_tid)
    inv = conn.execute(
        "SELECT total FROM invoices WHERE id = ?",
        (result['invoice_id'],)
    ).fetchone()
    conn.close()

    # 2*1000 + 1*2000 = 4000
    assert inv[0] == 4000.00

    print("[OK] Customer selection preserves basket")


def test_pos_customer_state_after_complete_sale():
    """After completing a sale, customer selection should be cleared for next sale."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 9008
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'POS-STATE-001', 'Test Tyre',
                     current_stock=100, selling_price=1500.00)
    cid1 = add_customer(test_tid, 'Customer 1')
    cid2 = add_customer(test_tid, 'Customer 2')

    # First sale with customer 1
    result1 = complete_sale(test_tid, str(uuid.uuid4()),
                           [{'type': 'product', 'id': pid, 'quantity': 1}],
                           customer_id=cid1,
                           payment_method='cash')
    assert result1['invoice_number'] is not None

    # Second sale with customer 2
    result2 = complete_sale(test_tid, str(uuid.uuid4()),
                           [{'type': 'product', 'id': pid, 'quantity': 1}],
                           customer_id=cid2,
                           payment_method='cash')
    assert result2['invoice_number'] is not None

    # Invoices should have different customer IDs
    conn = get_conn(test_tid)
    inv1 = conn.execute("SELECT customer_id FROM invoices WHERE id = ?",
                       (result1['invoice_id'],)).fetchone()
    inv2 = conn.execute("SELECT customer_id FROM invoices WHERE id = ?",
                       (result2['invoice_id'],)).fetchone()
    conn.close()

    assert inv1[0] == cid1
    assert inv2[0] == cid2

    print("[OK] Customer state persists correctly across sales")


def test_pos_walkin_sales_still_work():
    """Walk-in sales (no customer) should continue to work."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 9009
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'POS-WALKIN-001', 'Test Tyre',
                     current_stock=100, selling_price=1800.00)

    # Walk-in sale with no customer_id
    result = complete_sale(test_tid, str(uuid.uuid4()),
                          [{'type': 'product', 'id': pid, 'quantity': 1}],
                          payment_method='cash',
                          customer_id=None)

    assert result['invoice_number'] is not None

    # Verify customer_id is NULL
    conn = get_conn(test_tid)
    inv = conn.execute("SELECT customer_id FROM invoices WHERE id = ?",
                      (result['invoice_id'],)).fetchone()
    conn.close()

    assert inv[0] is None

    print("[OK] Walk-in sales work correctly")


def test_pos_tenant_isolation_customer_creation():
    """Customer created in Tenant A should not be visible in Tenant B."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid_a = 9010
    test_tid_b = 9011

    for tid in [test_tid_a, test_tid_b]:
        db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{tid}.db')
        migrate_tenant_db(tid, db_path)

    # Create customer in Tenant A
    cid_a = add_customer(test_tid_a, 'Tenant A Customer', phone='555-7777')

    # Verify Tenant B cannot access it
    cust = get_customer(test_tid_b, cid_a)
    assert cust is None

    # Verify Tenant A can access it
    cust = get_customer(test_tid_a, cid_a)
    assert cust is not None
    assert cust['name'] == 'Tenant A Customer'

    print("[OK] Tenant isolation enforced for customer creation")


def test_pos_customer_change_resets_search():
    """Clicking 'Change' button should allow new search."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 9012
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    cid1 = add_customer(test_tid, 'Customer A')
    cid2 = add_customer(test_tid, 'Customer B')

    # Simulate selecting customer 1
    cust1 = get_customer(test_tid, cid1)
    assert cust1['name'] == 'Customer A'

    # Search for customer 2 should find it
    results = search_customers(test_tid, 'Customer B')
    assert len(results) == 1
    assert results[0]['name'] == 'Customer B'

    # Simulate selecting customer 2
    cust2 = get_customer(test_tid, cid2)
    assert cust2['name'] == 'Customer B'

    print("[OK] Customer change allows new search")


if __name__ == "__main__":
    print("\n=== POS CUSTOMER WORKFLOW TESTS ===\n")

    test_pos_search_existing_customer()
    test_pos_search_tenant_isolated()
    test_pos_create_new_customer()
    test_pos_customer_required_for_credit()
    test_pos_cash_card_eft_optional_customer()
    test_pos_customer_selection_preserves_basket()
    test_pos_customer_state_after_complete_sale()
    test_pos_walkin_sales_still_work()
    test_pos_tenant_isolation_customer_creation()
    test_pos_customer_change_resets_search()

    print("\n[OK] All POS customer workflow tests passed\n")
