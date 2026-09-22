"""Dashboard POS Sales Overview Tests."""

import sys
import os
from datetime import datetime, date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.tenant_db import (
    add_product, add_customer, add_service,
    complete_sale, get_conn
)
from app.db_migrations import migrate_tenant_db
import tempfile
import uuid


def test_dashboard_todays_sales_total():
    """Today's sales should be calculated from completed invoices."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 8001
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Add product
    pid = add_product(test_tid, 'DASHBOARD-001', 'Test Tyre',
                     current_stock=100, selling_price=2000.00)

    # Make sales
    complete_sale(test_tid, str(uuid.uuid4()),
                 [{'type': 'product', 'id': pid, 'quantity': 1}],
                 payment_method='cash')
    complete_sale(test_tid, str(uuid.uuid4()),
                 [{'type': 'product', 'id': pid, 'quantity': 2}],
                 payment_method='card')

    # Query today's sales
    from app.routes.dashboard import _get_todays_sales
    total = _get_todays_sales(test_tid)

    # Should be 2000 + 4000 = 6000
    assert total == 6000.00, f"Expected 6000.00, got {total}"

    print("[OK] Today's sales total calculated correctly")


def test_dashboard_todays_transaction_count():
    """Transaction count should include only completed sales today."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 8002
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'DASHBOARD-002', 'Test Tyre',
                     current_stock=100, selling_price=1500.00)

    # Make 3 sales
    for i in range(3):
        complete_sale(test_tid, str(uuid.uuid4()),
                     [{'type': 'product', 'id': pid, 'quantity': 1}],
                     payment_method='cash')

    from app.routes.dashboard import _get_todays_transaction_count
    count = _get_todays_transaction_count(test_tid)

    assert count == 3, f"Expected 3 transactions, got {count}"

    print("[OK] Today's transaction count correct")


def test_dashboard_this_month_sales():
    """This month's sales should sum all sales in current month."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 8003
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'DASHBOARD-003', 'Test Tyre',
                     current_stock=100, selling_price=1000.00)

    # Make multiple sales
    for i in range(5):
        complete_sale(test_tid, str(uuid.uuid4()),
                     [{'type': 'product', 'id': pid, 'quantity': 1}],
                     payment_method='cash')

    from app.routes.dashboard import _get_this_month_sales
    total = _get_this_month_sales(test_tid)

    # Should be 5 * 1000 = 5000
    assert total == 5000.00, f"Expected 5000.00, got {total}"

    print("[OK] This month's sales total correct")


def test_dashboard_outstanding_debt():
    """Outstanding debt should use existing authoritative balance."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 8004
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'DASHBOARD-004', 'Test Tyre',
                     current_stock=100, selling_price=5000.00)
    cid = add_customer(test_tid, 'Test Customer', phone='555-1234')

    # Credit sale creates debtor
    complete_sale(test_tid, str(uuid.uuid4()),
                 [{'type': 'product', 'id': pid, 'quantity': 1}],
                 customer_id=cid, payment_method='credit')

    # Check stats which use the same authoritative calculation
    from app.tenant_db import get_stats
    stats = get_stats(test_tid)

    assert stats['total_owed'] == 5000.00, f"Expected 5000.00 owed, got {stats['total_owed']}"

    print("[OK] Outstanding debt uses authoritative calculation")


def test_dashboard_recent_sales():
    """Recent sales should show latest sales with customer names."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 8005
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'DASHBOARD-005', 'Test Tyre',
                     current_stock=100, selling_price=2000.00)
    cid = add_customer(test_tid, 'ABC Motors', phone='555-5555')

    # Walk-in sale
    complete_sale(test_tid, str(uuid.uuid4()),
                 [{'type': 'product', 'id': pid, 'quantity': 1}],
                 payment_method='cash')

    # Customer sale
    complete_sale(test_tid, str(uuid.uuid4()),
                 [{'type': 'product', 'id': pid, 'quantity': 2}],
                 customer_id=cid, payment_method='card')

    from app.routes.dashboard import _get_recent_sales
    recent = _get_recent_sales(test_tid, limit=5)

    assert len(recent) == 2, f"Expected 2 sales, got {len(recent)}"

    # Most recent should be customer sale
    assert recent[0]['customer_name'] == 'ABC Motors'
    assert recent[0]['total'] == 4000.00

    # Older should be walk-in
    assert recent[1]['customer_name'] == 'Walk-in'
    assert recent[1]['total'] == 2000.00

    print("[OK] Recent sales display correctly with customer names")


def test_dashboard_walkin_displays_correctly():
    """Walk-in sales should display 'Walk-in' as customer."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 8006
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'DASHBOARD-006', 'Test Tyre',
                     current_stock=100, selling_price=1000.00)

    # Walk-in sale (no customer_id)
    complete_sale(test_tid, str(uuid.uuid4()),
                 [{'type': 'product', 'id': pid, 'quantity': 1}],
                 payment_method='cash')

    from app.routes.dashboard import _get_recent_sales
    recent = _get_recent_sales(test_tid)

    assert recent[0]['customer_name'] == 'Walk-in'

    print("[OK] Walk-in sales display correctly")


def test_dashboard_missing_selling_price_count():
    """Count products with no selling price."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 8007
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Product with selling price
    add_product(test_tid, 'DASHBOARD-007A', 'Tyre A',
               current_stock=100, selling_price=2000.00)

    # Product without selling price
    add_product(test_tid, 'DASHBOARD-007B', 'Tyre B',
               current_stock=100, selling_price=None)

    from app.routes.dashboard import _get_products_without_selling_price
    count = _get_products_without_selling_price(test_tid)

    assert count == 1, f"Expected 1 product without price, got {count}"

    print("[OK] Missing selling price count correct")


def test_dashboard_tenant_isolation_sales():
    """Tenant A should not see Tenant B's sales."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid_a = 8008
    test_tid_b = 8009

    for tid in [test_tid_a, test_tid_b]:
        db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{tid}.db')
        migrate_tenant_db(tid, db_path)

    # Tenant B makes sales
    pid_b = add_product(test_tid_b, 'DASHBOARD-008B', 'B Tyre',
                       current_stock=100, selling_price=3000.00)
    complete_sale(test_tid_b, str(uuid.uuid4()),
                 [{'type': 'product', 'id': pid_b, 'quantity': 1}],
                 payment_method='cash')
    complete_sale(test_tid_b, str(uuid.uuid4()),
                 [{'type': 'product', 'id': pid_b, 'quantity': 1}],
                 payment_method='card')

    # Tenant A checks their sales
    from app.routes.dashboard import _get_todays_sales, _get_todays_transaction_count
    sales_a = _get_todays_sales(test_tid_a)
    count_a = _get_todays_transaction_count(test_tid_a)

    # Tenant A should see 0
    assert sales_a == 0.0
    assert count_a == 0

    # Tenant B should see their sales
    sales_b = _get_todays_sales(test_tid_b)
    count_b = _get_todays_transaction_count(test_tid_b)

    assert sales_b == 6000.00
    assert count_b == 2

    print("[OK] Tenant isolation enforced for sales")


def test_dashboard_payment_methods_display():
    """Payment methods should display correctly (Cash/Card/EFT/Credit)."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 8010
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'DASHBOARD-010', 'Test Tyre',
                     current_stock=100, selling_price=1000.00)

    # Test each payment method
    for method in ['cash', 'card', 'eft']:
        complete_sale(test_tid, str(uuid.uuid4()),
                     [{'type': 'product', 'id': pid, 'quantity': 1}],
                     payment_method=method)

    from app.routes.dashboard import _get_recent_sales
    recent = _get_recent_sales(test_tid, limit=10)

    methods = [s['payment_method'].lower() for s in recent]
    assert 'eft' in methods
    assert 'card' in methods
    assert 'cash' in methods

    print("[OK] Payment methods display correctly")


def test_dashboard_empty_state():
    """Dashboard should handle empty state gracefully."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 8011
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    from app.routes.dashboard import (
        _get_todays_sales, _get_todays_transaction_count,
        _get_this_month_sales, _get_recent_sales
    )

    assert _get_todays_sales(test_tid) == 0.0
    assert _get_todays_transaction_count(test_tid) == 0
    assert _get_this_month_sales(test_tid) == 0.0
    assert _get_recent_sales(test_tid) == []

    print("[OK] Empty dashboard handled correctly")


if __name__ == "__main__":
    print("\n=== DASHBOARD POS OVERVIEW TESTS ===\n")

    test_dashboard_todays_sales_total()
    test_dashboard_todays_transaction_count()
    test_dashboard_this_month_sales()
    test_dashboard_outstanding_debt()
    test_dashboard_recent_sales()
    test_dashboard_walkin_displays_correctly()
    test_dashboard_missing_selling_price_count()
    test_dashboard_tenant_isolation_sales()
    test_dashboard_payment_methods_display()
    test_dashboard_empty_state()

    print("\n[OK] All dashboard tests passed\n")
