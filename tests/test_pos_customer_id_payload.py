"""Test that customer ID is properly included in complete sale payload for credit payments."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.tenant_db import add_product, add_customer, get_conn
from app.db_migrations import migrate_tenant_db
from flask_login import UserMixin
from flask_wtf.csrf import generate_csrf
import tempfile
import json
import uuid
import re


def test_existing_customer_id_in_credit_payload():
    """Existing customer ID must reach /pos/complete for credit payments."""
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 12001
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Setup: product and customer
    pid = add_product(test_tid, 'TEST-ID-001', 'Test Tyre',
                     current_stock=100, selling_price=3000.00)
    cid = add_customer(test_tid, 'ABC Tours', phone='0607788899')

    class MockUser(UserMixin):
        def __init__(self, tid):
            self.id = tid
            self.tenant_id = tid

    with app.test_request_context():
        from flask_login import login_user
        login_user(MockUser(test_tid))

        # Get CSRF token from POS page
        response = client.get('/pos/')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        match = re.search(r'meta name="csrf-token" content="([^"]*)"', html)
        assert match, "Could not extract CSRF token"
        csrf_token = match.group(1)

        # Simulate POS sale with existing customer and credit
        payload = {
            'idempotency_key': str(uuid.uuid4()),
            'items': [{'type': 'product', 'id': pid, 'quantity': 1}],
            'customer_id': cid,  # Explicit customer ID
            'payment_method': 'credit',
            'discount': 0
        }

        response = client.post(
            '/pos/complete',
            data=json.dumps(payload),
            content_type='application/json',
            headers={'X-CSRFToken': csrf_token}
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.get_data(as_text=True)}"

        data = response.get_json()
        assert data.get('success') is True, f"Expected success, got: {data}"
        assert data.get('invoice_number') is not None

        # Verify invoice has correct customer ID
        conn = get_conn(test_tid)
        inv = conn.execute("SELECT customer_id FROM invoices WHERE id = ?",
                          (data['invoice_id'],)).fetchone()
        conn.close()

        assert inv is not None
        assert inv[0] == cid, f"Expected customer_id {cid}, got {inv[0]}"

    print("[OK] Existing customer ID reaches credit payload correctly")


def test_new_customer_id_in_credit_payload():
    """Newly created customer ID must reach /pos/complete for credit payments."""
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 12002
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Setup: product and create new customer
    pid = add_product(test_tid, 'TEST-ID-002', 'Test Tyre',
                     current_stock=100, selling_price=2500.00)

    class MockUser(UserMixin):
        def __init__(self, tid):
            self.id = tid
            self.tenant_id = tid

    with app.test_request_context():
        from flask_login import login_user
        login_user(MockUser(test_tid))

        # Get CSRF token from POS page
        response = client.get('/pos/')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        match = re.search(r'meta name="csrf-token" content="([^"]*)"', html)
        assert match, "Could not extract CSRF token"
        csrf_token = match.group(1)

        # Step 1: Create customer via POS add-json endpoint
        response = client.post(
            '/customers/add-json',
            data=json.dumps({
                'name': 'New Customer POS',
                'phone': '555-9999',
                'vehicle_registration': 'NEW 999 GP'
            }),
            content_type='application/json',
            headers={'X-CSRFToken': csrf_token}
        )

        assert response.status_code == 201, f"Expected 201, got {response.status_code}"
        customer_data = response.get_json()
        assert customer_data.get('success') is True
        new_cid = customer_data['customer']['id']

        # Step 2: Complete sale with newly created customer and credit
        payload = {
            'idempotency_key': str(uuid.uuid4()),
            'items': [{'type': 'product', 'id': pid, 'quantity': 1}],
            'customer_id': new_cid,  # Newly created customer ID
            'payment_method': 'credit',
            'discount': 0
        }

        response = client.post(
            '/pos/complete',
            data=json.dumps(payload),
            content_type='application/json',
            headers={'X-CSRFToken': csrf_token}
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.get_data(as_text=True)}"

        data = response.get_json()
        assert data.get('success') is True, f"Expected success, got: {data}"
        assert data.get('invoice_number') is not None

        # Verify invoice has correct customer ID
        conn = get_conn(test_tid)
        inv = conn.execute("SELECT customer_id FROM invoices WHERE id = ?",
                          (data['invoice_id'],)).fetchone()
        conn.close()

        assert inv is not None
        assert inv[0] == new_cid, f"Expected customer_id {new_cid}, got {inv[0]}"

    print("[OK] New customer ID reaches credit payload correctly")


def test_null_customer_id_rejected_for_credit():
    """Null customer ID should be rejected for credit payments."""
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 12003
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Setup: product only
    pid = add_product(test_tid, 'TEST-ID-003', 'Test Tyre',
                     current_stock=100, selling_price=2000.00)

    class MockUser(UserMixin):
        def __init__(self, tid):
            self.id = tid
            self.tenant_id = tid

    with app.test_request_context():
        from flask_login import login_user
        login_user(MockUser(test_tid))

        # Get CSRF token from POS page
        response = client.get('/pos/')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        match = re.search(r'meta name="csrf-token" content="([^"]*)"', html)
        assert match, "Could not extract CSRF token"
        csrf_token = match.group(1)

        # Try credit without customer
        payload = {
            'idempotency_key': str(uuid.uuid4()),
            'items': [{'type': 'product', 'id': pid, 'quantity': 1}],
            'customer_id': None,  # No customer
            'payment_method': 'credit',
            'discount': 0
        }

        response = client.post(
            '/pos/complete',
            data=json.dumps(payload),
            content_type='application/json',
            headers={'X-CSRFToken': csrf_token}
        )

        # Should fail
        assert response.status_code == 400, f"Expected 400 error, got {response.status_code}"
        data = response.get_json()
        assert data.get('success') is False
        assert 'credit payment requires customer_id' in data.get('error', '').lower()

    print("[OK] Null customer ID rejected for credit payment")


def test_cash_payment_without_customer_succeeds():
    """Cash payment without customer should succeed."""
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 12004
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Setup: product only
    pid = add_product(test_tid, 'TEST-ID-004', 'Test Tyre',
                     current_stock=100, selling_price=2000.00)

    class MockUser(UserMixin):
        def __init__(self, tid):
            self.id = tid
            self.tenant_id = tid

    with app.test_request_context():
        from flask_login import login_user
        login_user(MockUser(test_tid))

        # Get CSRF token from POS page
        response = client.get('/pos/')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        match = re.search(r'meta name="csrf-token" content="([^"]*)"', html)
        assert match, "Could not extract CSRF token"
        csrf_token = match.group(1)

        # Cash payment without customer
        payload = {
            'idempotency_key': str(uuid.uuid4()),
            'items': [{'type': 'product', 'id': pid, 'quantity': 1}],
            'customer_id': None,
            'payment_method': 'cash',
            'discount': 0
        }

        response = client.post(
            '/pos/complete',
            data=json.dumps(payload),
            content_type='application/json',
            headers={'X-CSRFToken': csrf_token}
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.get_json()
        assert data.get('success') is True

    print("[OK] Cash payment without customer succeeds")


if __name__ == "__main__":
    print("\n=== POS CUSTOMER ID PAYLOAD TESTS ===\n")

    test_existing_customer_id_in_credit_payload()
    test_new_customer_id_in_credit_payload()
    test_null_customer_id_rejected_for_credit()
    test_cash_payment_without_customer_succeeds()

    print("\n[OK] All customer ID payload tests passed\n")
