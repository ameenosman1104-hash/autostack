"""POS Payload Fix Tests - Verify item structure (type + id + quantity)."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.tenant_db import add_product, add_service, add_customer, complete_sale
from app.db_migrations import migrate_tenant_db
import tempfile
import uuid


def test_product_only_sale_with_correct_payload():
    """Product-only sale with correct item structure (type + id + quantity)."""
    app = create_app()
    app.config['TESTING'] = True

    # Setup test database
    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 5000
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Add test product
    pid = add_product(test_tid, 'PAYLOADTEST-001', 'Cinturato P7 Test',
                     current_stock=12, selling_price=2000.00)

    # Test correct payload structure
    idem_key = str(uuid.uuid4())
    result = complete_sale(
        tid=test_tid,
        idempotency_key=idem_key,
        items=[
            {
                "type": "product",  # CORRECT: type field
                "id": pid,           # CORRECT: id field (not product_id)
                "quantity": 1
            }
        ],
        payment_method="cash"
    )

    assert result['success'] is True, f"Sale failed: {result}"
    assert result['total'] == 2000.00, f"Total should be 2000, got {result['total']}"
    assert 'invoice_number' in result, "Should have invoice_number"
    assert result['payment_status'] == 'paid', "Should be paid (cash)"

    print("[OK] Product-only sale with correct payload (type + id + quantity)")


def test_product_service_sale():
    """Product + Service sale with correct item structures."""
    app = create_app()
    app.config['TESTING'] = True

    # Setup test database
    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 5001
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Add test product and service
    pid = add_product(test_tid, 'PAYLOADTEST-002', 'Test Tyre',
                     current_stock=50, selling_price=1500.00)
    sid = add_service(test_tid, 'Fitting Test', default_price=100.00)

    # Test combined payload
    idem_key = str(uuid.uuid4())
    result = complete_sale(
        tid=test_tid,
        idempotency_key=idem_key,
        items=[
            {
                "type": "product",
                "id": pid,
                "quantity": 2
            },
            {
                "type": "service",
                "id": sid,
                "quantity": 2
            }
        ],
        payment_method="cash"
    )

    assert result['success'] is True, f"Sale failed: {result}"
    expected_total = (2 * 1500.00) + (2 * 100.00)  # 3200
    assert result['total'] == expected_total, f"Total should be {expected_total}, got {result['total']}"

    print("[OK] Product + Service sale with correct payload structures")


def test_stock_deduction_only_for_products():
    """Stock deduction happens for products, not for services."""
    app = create_app()
    app.config['TESTING'] = True

    # Setup test database
    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 5002
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Add test product and service
    pid = add_product(test_tid, 'PAYLOADTEST-003', 'Test Tyre',
                     current_stock=100, selling_price=1000.00)
    sid = add_service(test_tid, 'Service Test', default_price=50.00)

    from app.tenant_db import get_product

    # Check initial stock
    product_before = get_product(test_tid, pid)
    stock_before = product_before['current_stock']
    assert stock_before == 100, f"Initial stock should be 100, got {stock_before}"

    # Sell 5 products and 3 services
    idem_key = str(uuid.uuid4())
    result = complete_sale(
        tid=test_tid,
        idempotency_key=idem_key,
        items=[
            {"type": "product", "id": pid, "quantity": 5},
            {"type": "service", "id": sid, "quantity": 3}
        ],
        payment_method="cash"
    )

    assert result['success'] is True

    # Check stock after
    product_after = get_product(test_tid, pid)
    stock_after = product_after['current_stock']

    # Should be 100 - 5 = 95 (5 product sales)
    # Services should NOT reduce stock
    assert stock_after == 95, f"Stock should be 95 after 5 product sales, got {stock_after}"

    print("[OK] Stock deduction correct (products only, not services)")


def test_idempotency_with_correct_payload():
    """Idempotency works with correct item structure."""
    app = create_app()
    app.config['TESTING'] = True

    # Setup test database
    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 5003
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Add test product
    pid = add_product(test_tid, 'PAYLOADTEST-004', 'Test Tyre',
                     current_stock=20, selling_price=800.00)

    idem_key = str(uuid.uuid4())

    # First call
    result1 = complete_sale(
        tid=test_tid,
        idempotency_key=idem_key,
        items=[{"type": "product", "id": pid, "quantity": 3}],
        payment_method="cash"
    )

    assert result1['success'] is True
    invoice1 = result1['invoice_number']

    # Retry with same key
    result2 = complete_sale(
        tid=test_tid,
        idempotency_key=idem_key,
        items=[{"type": "product", "id": pid, "quantity": 3}],
        payment_method="cash"
    )

    assert result2['success'] is True
    assert result2['duplicate'] is True, "Should be marked as duplicate"
    assert result2['invoice_number'] == invoice1, "Should return same invoice"

    # Check stock wasn't deducted twice
    from app.tenant_db import get_product
    product = get_product(test_tid, pid)
    assert product['current_stock'] == 17, f"Stock should be 17 (20-3), got {product['current_stock']}"

    print("[OK] Idempotency works correctly with new payload structure")


def test_credit_sale_with_correct_payload():
    """Credit sale with customer works with correct item structure."""
    app = create_app()
    app.config['TESTING'] = True

    # Setup test database
    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 5004
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Add test customer and product
    cid = add_customer(test_tid, 'Test Shop', phone='555-1234')
    pid = add_product(test_tid, 'PAYLOADTEST-005', 'Test Tyre',
                     current_stock=30, selling_price=1200.00)

    # Credit sale
    idem_key = str(uuid.uuid4())
    result = complete_sale(
        tid=test_tid,
        idempotency_key=idem_key,
        items=[{"type": "product", "id": pid, "quantity": 2}],
        customer_id=cid,
        payment_method="credit"
    )

    assert result['success'] is True
    assert result['payment_status'] == 'unpaid', "Credit sale should be unpaid"
    assert result['debtor_id'] is not None, "Should have debtor"
    assert result['total'] == 2400.00, f"Total should be 2400, got {result['total']}"

    print("[OK] Credit sale with customer works with correct payload")


def test_invalid_payload_rejected():
    """Invalid payload structures are rejected."""
    app = create_app()
    app.config['TESTING'] = True

    # Setup test database
    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 5005
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    pid = add_product(test_tid, 'PAYLOADTEST-006', 'Test',
                     current_stock=10, selling_price=500.00)

    # Test: missing type field
    try:
        complete_sale(
            tid=test_tid,
            idempotency_key=str(uuid.uuid4()),
            items=[{"id": pid, "quantity": 1}],  # Missing type
            payment_method="cash"
        )
        assert False, "Should reject item without type"
    except ValueError as e:
        assert 'Invalid item type' in str(e), f"Error should mention item type: {e}"

    # Test: wrong type value
    try:
        complete_sale(
            tid=test_tid,
            idempotency_key=str(uuid.uuid4()),
            items=[{"type": "invalid", "id": pid, "quantity": 1}],
            payment_method="cash"
        )
        assert False, "Should reject invalid type value"
    except ValueError as e:
        assert 'Invalid item type' in str(e)

    print("[OK] Invalid payloads are correctly rejected")


if __name__ == "__main__":
    print("\n=== POS PAYLOAD FIX TESTS ===\n")

    test_product_only_sale_with_correct_payload()
    test_product_service_sale()
    test_stock_deduction_only_for_products()
    test_idempotency_with_correct_payload()
    test_credit_sale_with_correct_payload()
    test_invalid_payload_rejected()

    print("\n[OK] All payload fix tests passed\n")
