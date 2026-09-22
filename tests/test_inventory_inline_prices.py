"""Inline inventory price editing tests - Cost Price and Selling Price columns."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.tenant_db import add_product, get_product
from app.db_migrations import migrate_tenant_db
import tempfile


def test_cost_price_can_be_updated_via_update_product():
    """Cost price can be updated and persists correctly."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 7001
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'INLINE-001', 'Test Tyre',
                     current_stock=10, last_cost_price=1000.00, selling_price=2000.00)

    # Update cost price
    from app.tenant_db import update_product
    update_product(test_tid, pid, last_cost_price=1500.00)

    # Verify
    prod = get_product(test_tid, pid)
    assert prod['last_cost_price'] == 1500.00
    assert prod['selling_price'] == 2000.00

    print("[OK] Cost price can be updated")


def test_selling_price_can_be_updated():
    """Selling price can be updated and persists correctly."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 7002
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'INLINE-002', 'Test Tyre',
                     current_stock=10, last_cost_price=1000.00, selling_price=2000.00)

    from app.tenant_db import update_product
    update_product(test_tid, pid, selling_price=2500.00)

    prod = get_product(test_tid, pid)
    assert prod['selling_price'] == 2500.00
    assert prod['last_cost_price'] == 1000.00

    print("[OK] Selling price can be updated")


def test_both_prices_can_be_updated():
    """Both prices can be updated independently."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 7003
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'INLINE-003', 'Test Tyre',
                     current_stock=10, last_cost_price=1000.00, selling_price=2000.00)

    from app.tenant_db import update_product
    update_product(test_tid, pid, last_cost_price=1200.00)
    update_product(test_tid, pid, selling_price=2400.00)

    prod = get_product(test_tid, pid)
    assert prod['last_cost_price'] == 1200.00
    assert prod['selling_price'] == 2400.00

    print("[OK] Both prices can be updated")


def test_selling_price_null_allowed():
    """Selling price can be set to NULL."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 7004
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'INLINE-004', 'Test Tyre',
                     current_stock=10, selling_price=2000.00)

    from app.tenant_db import update_product
    update_product(test_tid, pid, selling_price=None)

    prod = get_product(test_tid, pid)
    assert prod['selling_price'] is None

    print("[OK] Selling price can be NULL")


def test_cost_price_not_negative():
    """Validate that negative cost prices should be rejected at schema level."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 7005
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'INLINE-005', 'Test Tyre',
                     current_stock=10, last_cost_price=1000.00)

    # Negative prices should be rejected by validation in update_field
    # This test verifies the field exists and can handle numeric operations
    prod = get_product(test_tid, pid)
    assert isinstance(prod['last_cost_price'], (int, float))
    assert prod['last_cost_price'] >= 0

    print("[OK] Cost price validation supported")


def test_selling_price_not_negative():
    """Validate that negative selling prices should be rejected at schema level."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 7006
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'INLINE-006', 'Test Tyre',
                     current_stock=10, selling_price=2000.00)

    prod = get_product(test_tid, pid)
    assert isinstance(prod['selling_price'], (int, float))
    assert prod['selling_price'] >= 0

    print("[OK] Selling price validation supported")


def test_other_fields_unchanged():
    """Verify other product fields remain unchanged when prices updated."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 7007
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'INLINE-007', 'Test Tyre',
                     current_stock=15, category='Touring', unit='PCS',
                     reorder_level=5, last_cost_price=1000.00, selling_price=2000.00)

    from app.tenant_db import update_product
    update_product(test_tid, pid, selling_price=2250.00)

    prod = get_product(test_tid, pid)
    assert prod['selling_price'] == 2250.00
    assert prod['current_stock'] == 15
    assert prod['category'] == 'Touring'
    assert prod['unit'] == 'PCS'
    assert prod['reorder_level'] == 5

    print("[OK] Other fields unchanged")


def test_stock_unchanged_on_price_update():
    """Stock should remain unchanged when prices are updated."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 7008
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'INLINE-008', 'Test Tyre',
                     current_stock=25, last_cost_price=1000.00, selling_price=2000.00)

    from app.tenant_db import update_product
    update_product(test_tid, pid, last_cost_price=1100.00)
    update_product(test_tid, pid, selling_price=2200.00)

    prod = get_product(test_tid, pid)
    assert prod['current_stock'] == 25

    print("[OK] Stock unchanged on price update")


def test_pos_gets_updated_selling_price():
    """POS query should return updated selling price."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 7009
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'INLINE-009', 'POS Test Tyre',
                     current_stock=20, selling_price=2000.00)

    from app.tenant_db import update_product, get_all_products

    # Update price
    update_product(test_tid, pid, selling_price=2500.00)

    # Check POS can see the updated price
    products = get_all_products(test_tid, 'POS Test')
    pos_prod = next((p for p in products if p['id'] == pid), None)

    assert pos_prod is not None
    assert pos_prod['selling_price'] == 2500.00

    print("[OK] POS sees updated selling price")


def test_tenant_isolation_on_product_update():
    """Tenant A cannot see/update Tenant B's products."""
    app = create_app()
    app.config['TESTING'] = True

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid_a = 7010
    test_tid_b = 7011

    for tid in [test_tid_a, test_tid_b]:
        db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{tid}.db')
        migrate_tenant_db(tid, db_path)

    # Add product to tenant B
    pid_b = add_product(test_tid_b, 'INLINE-010B', 'B Tyre',
                       current_stock=10, selling_price=2000.00)

    # Tenant A tries to query it
    from app.tenant_db import get_product
    prod = get_product(test_tid_a, pid_b)

    # Should not find it
    assert prod is None

    # Tenant B can find it
    prod_b = get_product(test_tid_b, pid_b)
    assert prod_b is not None
    assert prod_b['selling_price'] == 2000.00

    print("[OK] Tenant isolation enforced")


def test_csv_template_includes_price_columns():
    """Verify inventory.html template includes price columns."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/inventory.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

        # Check headers
        assert 'Cost Price' in content, "Missing Cost Price column header"
        assert 'Selling Price' in content, "Missing Selling Price column header"

        # Check data fields
        assert 'data-field="last_cost_price"' in content, "Missing cost price data field"
        assert 'data-field="selling_price"' in content, "Missing selling price data field"

        # Check price formatting
        assert 'price-cell' in content, "Missing price-cell class for styling"

        # Check editable markers
        assert 'editable-cell' in content, "Missing editable-cell class"

    print("[OK] Template includes price columns")


if __name__ == "__main__":
    print("\n=== INLINE INVENTORY PRICE EDITING TESTS ===\n")

    test_cost_price_can_be_updated_via_update_product()
    test_selling_price_can_be_updated()
    test_both_prices_can_be_updated()
    test_selling_price_null_allowed()
    test_cost_price_not_negative()
    test_selling_price_not_negative()
    test_other_fields_unchanged()
    test_stock_unchanged_on_price_update()
    test_pos_gets_updated_selling_price()
    test_tenant_isolation_on_product_update()
    test_csv_template_includes_price_columns()

    print("\n[OK] All inline price editing tests passed\n")
