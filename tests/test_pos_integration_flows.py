"""Integrated POS flow tests - customer selection, creation, and sales."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.tenant_db import (
    add_product, add_customer, get_customer, get_all_debtors,
    complete_sale, get_conn
)
from app.db_migrations import migrate_tenant_db
import tempfile
import uuid


def test_existing_customer_credit_flow():
    """Flow: Search existing customer → Select → Credit sale → Debtor created."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 11001
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Setup
    pid = add_product(test_tid, 'INT-EXISTING-001', 'Test Tyre',
                     current_stock=100, selling_price=2000.00)
    cid = add_customer(test_tid, 'ABC Motors', phone='555-1111')

    # Search existing customer
    from app.tenant_db import search_customers
    results = search_customers(test_tid, 'ABC')
    assert len(results) == 1
    selected_customer = results[0]
    assert selected_customer['id'] == cid

    # Sale with selected customer and credit payment
    result = complete_sale(test_tid, str(uuid.uuid4()),
                          [{'type': 'product', 'id': pid, 'quantity': 1}],
                          customer_id=selected_customer['id'],
                          payment_method='credit')

    assert result['invoice_number'] is not None

    # Verify invoice was created with correct customer
    conn = get_conn(test_tid)
    inv = conn.execute("SELECT customer_id, total FROM invoices WHERE id = ?",
                      (result['invoice_id'],)).fetchone()
    conn.close()

    assert inv[0] == cid, f"Expected customer_id {cid}, got {inv[0]}"
    assert inv[1] == 2000.00

    # Verify debtor was created
    debtors = get_all_debtors(test_tid)
    assert len(debtors) == 1
    assert debtors[0]['customer_id'] == cid

    print("[OK] Existing customer credit flow works")


def test_new_customer_credit_flow():
    """Flow: Create new customer in POS → Select → Credit sale → Debtor created."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 11002
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Setup
    pid = add_product(test_tid, 'INT-NEW-001', 'Test Tyre',
                     current_stock=100, selling_price=1500.00)

    # Create new customer (simulating POS modal)
    cid = add_customer(test_tid, 'New Customer POS', phone='555-2222',
                      vehicle_registration='NEW 222 GP')

    # Verify customer was created and can be retrieved
    cust = get_customer(test_tid, cid)
    assert cust is not None
    assert cust['name'] == 'New Customer POS'

    # Sale with newly created customer and credit payment
    result = complete_sale(test_tid, str(uuid.uuid4()),
                          [{'type': 'product', 'id': pid, 'quantity': 1}],
                          customer_id=cid,
                          payment_method='credit')

    assert result['invoice_number'] is not None

    # Verify invoice linked to correct customer
    conn = get_conn(test_tid)
    inv = conn.execute("SELECT customer_id, total FROM invoices WHERE id = ?",
                      (result['invoice_id'],)).fetchone()
    conn.close()

    assert inv[0] == cid
    assert inv[1] == 1500.00

    # Verify debtor created for new customer
    debtors = get_all_debtors(test_tid)
    assert len(debtors) == 1
    assert debtors[0]['customer_id'] == cid

    print("[OK] New customer credit flow works")


def test_walkin_cash_flow():
    """Flow: No customer selected → Cash payment → No debtor created."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 11003
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Setup
    pid = add_product(test_tid, 'INT-WALKIN-001', 'Test Tyre',
                     current_stock=100, selling_price=1800.00)

    # Walk-in sale (no customer, no customer_id)
    result = complete_sale(test_tid, str(uuid.uuid4()),
                          [{'type': 'product', 'id': pid, 'quantity': 2}],
                          payment_method='cash',
                          customer_id=None)

    assert result['invoice_number'] is not None

    # Verify invoice has NULL customer
    conn = get_conn(test_tid)
    inv = conn.execute("SELECT customer_id, total FROM invoices WHERE id = ?",
                      (result['invoice_id'],)).fetchone()
    conn.close()

    assert inv[0] is None, "Walk-in invoice should have NULL customer_id"
    assert inv[1] == 3600.00  # 2 * 1800

    # Verify no debtor created
    debtors = get_all_debtors(test_tid)
    assert len(debtors) == 0

    print("[OK] Walk-in cash flow works")


def test_idempotency_prevents_duplicate_on_retry():
    """Flow: Complete sale → Retry with same idempotency key → Same invoice, no duplicate."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 11004
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Setup
    pid = add_product(test_tid, 'INT-IDEM-001', 'Test Tyre',
                     current_stock=100, selling_price=1000.00)
    cid = add_customer(test_tid, 'Idem Customer')

    idempotency_key = str(uuid.uuid4())

    # First sale
    result1 = complete_sale(test_tid, idempotency_key,
                           [{'type': 'product', 'id': pid, 'quantity': 1}],
                           customer_id=cid,
                           payment_method='credit')

    invoice_id_1 = result1['invoice_id']
    invoice_num_1 = result1['invoice_number']

    # Retry with same idempotency key
    result2 = complete_sale(test_tid, idempotency_key,
                           [{'type': 'product', 'id': pid, 'quantity': 1}],
                           customer_id=cid,
                           payment_method='credit')

    invoice_id_2 = result2['invoice_id']
    invoice_num_2 = result2['invoice_number']

    # Should return same invoice
    assert invoice_id_1 == invoice_id_2, "Idempotent retry should return same invoice"
    assert invoice_num_1 == invoice_num_2

    # Verify only one invoice in DB
    conn = get_conn(test_tid)
    invoices = conn.execute(
        "SELECT COUNT(*) FROM invoices WHERE customer_id = ?",
        (cid,)
    ).fetchone()
    conn.close()

    assert invoices[0] == 1, "Should have exactly 1 invoice"

    # Verify only one debtor
    debtors = get_all_debtors(test_tid)
    assert len(debtors) == 1

    # Verify stock deducted only once
    from app.tenant_db import get_product
    prod = get_product(test_tid, pid)
    assert prod['current_stock'] == 99, "Stock should be deducted only once"

    print("[OK] Idempotency prevents duplicate on retry")


def test_multiple_sales_with_different_customers():
    """Flow: Sale 1 with customer A → Sale 2 with customer B → Both tracked correctly."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 11005
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Setup
    pid = add_product(test_tid, 'INT-MULTI-001', 'Test Tyre',
                     current_stock=100, selling_price=2000.00)
    cid_a = add_customer(test_tid, 'Customer A')
    cid_b = add_customer(test_tid, 'Customer B')

    # Sale 1 with Customer A
    result1 = complete_sale(test_tid, str(uuid.uuid4()),
                           [{'type': 'product', 'id': pid, 'quantity': 1}],
                           customer_id=cid_a,
                           payment_method='credit')

    # Sale 2 with Customer B
    result2 = complete_sale(test_tid, str(uuid.uuid4()),
                           [{'type': 'product', 'id': pid, 'quantity': 1}],
                           customer_id=cid_b,
                           payment_method='credit')

    # Verify different invoices
    assert result1['invoice_id'] != result2['invoice_id']
    assert result1['invoice_number'] != result2['invoice_number']

    # Verify correct customers linked
    conn = get_conn(test_tid)
    inv1 = conn.execute("SELECT customer_id FROM invoices WHERE id = ?",
                       (result1['invoice_id'],)).fetchone()
    inv2 = conn.execute("SELECT customer_id FROM invoices WHERE id = ?",
                       (result2['invoice_id'],)).fetchone()
    conn.close()

    assert inv1[0] == cid_a
    assert inv2[0] == cid_b

    # Verify two debtors
    debtors = get_all_debtors(test_tid)
    assert len(debtors) == 2

    print("[OK] Multiple sales with different customers work")


if __name__ == "__main__":
    print("\n=== POS INTEGRATION FLOW TESTS ===\n")

    test_existing_customer_credit_flow()
    test_new_customer_credit_flow()
    test_walkin_cash_flow()
    test_idempotency_prevents_duplicate_on_retry()
    test_multiple_sales_with_different_customers()

    print("\n[OK] All POS integration flow tests passed\n")
