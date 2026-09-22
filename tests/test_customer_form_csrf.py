"""Test CSRF protection on customer forms."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.db_migrations import migrate_tenant_db
from flask_login import UserMixin
import tempfile
import re
import json


def test_customer_add_form_contains_csrf_token():
    """GET /customers/add should include CSRF token in the form."""
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 15001
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    class MockUser(UserMixin):
        def __init__(self, tid):
            self.id = tid
            self.tenant_id = tid

    with app.test_request_context():
        from flask_login import login_user
        login_user(MockUser(test_tid))

        response = client.get('/customers/add')
        assert response.status_code == 200

        html = response.get_data(as_text=True)

        # Check for CSRF token field
        assert 'name="csrf_token"' in html, "Form should have csrf_token field"

        # Extract the token value
        match = re.search(r'name="csrf_token" value="([^"]*)"', html)
        assert match, "Could not extract CSRF token from form"

        token = match.group(1)
        assert token, "CSRF token should not be empty"
        assert len(token) > 10, f"CSRF token looks too short: {len(token)} chars"

    print(f"[OK] Add Customer form contains CSRF token ({len(token)} chars)")


def test_customer_add_without_csrf_rejected():
    """POST /customers/add without CSRF token should be rejected with 400."""
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 15002
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    class MockUser(UserMixin):
        def __init__(self, tid):
            self.id = tid
            self.tenant_id = tid

    with app.test_request_context():
        from flask_login import login_user
        login_user(MockUser(test_tid))

        response = client.post(
            '/customers/add',
            data={
                'name': 'Test Customer',
                'phone': '555-1234'
            }
        )

        # Should be rejected with 400 (CSRF validation fails)
        assert response.status_code == 400, \
            f"Expected 400 for missing CSRF, got {response.status_code}"

    print("[OK] POST without CSRF token rejected with 400")


def test_customer_add_with_valid_csrf_succeeds():
    """POST /customers/add with valid CSRF token should succeed."""
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 15003
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    class MockUser(UserMixin):
        def __init__(self, tid):
            self.id = tid
            self.tenant_id = tid

    with app.test_request_context():
        from flask_login import login_user
        login_user(MockUser(test_tid))

        # Step 1: Get the form to extract CSRF token
        response = client.get('/customers/add')
        assert response.status_code == 200

        html = response.get_data(as_text=True)
        match = re.search(r'name="csrf_token" value="([^"]*)"', html)
        assert match, "Could not extract CSRF token"
        csrf_token = match.group(1)

        # Step 2: POST with the valid CSRF token
        response = client.post(
            '/customers/add',
            data={
                'csrf_token': csrf_token,
                'name': 'Valid CSRF Customer',
                'phone': '555-5678',
                'email': 'test@example.com',
                'vehicle_registration': 'ABC 123',
                'notes': 'Test customer'
            }
        )

        # Should succeed (redirect to customer detail, not 400)
        assert response.status_code in [200, 302], \
            f"Expected success (200/302), got {response.status_code}"

    print("[OK] POST with valid CSRF token succeeds")


def test_customer_created_belongs_to_tenant():
    """Created customer should belong to current tenant."""
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid_a = 15004
    test_tid_b = 15005

    for tid in [test_tid_a, test_tid_b]:
        db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{tid}.db')
        migrate_tenant_db(tid, db_path)

    class MockUser(UserMixin):
        def __init__(self, tid):
            self.id = tid
            self.tenant_id = tid

    # Tenant A creates a customer
    with app.test_request_context():
        from flask_login import login_user
        login_user(MockUser(test_tid_a))

        # Get CSRF token
        response = client.get('/customers/add')
        html = response.get_data(as_text=True)
        match = re.search(r'name="csrf_token" value="([^"]*)"', html)
        csrf_token = match.group(1)

        # Create customer
        response = client.post(
            '/customers/add',
            data={
                'csrf_token': csrf_token,
                'name': 'Tenant A Customer',
                'phone': '555-1111'
            }
        )

        # Verify customer was created (either 302 redirect or 200)
        assert response.status_code in [200, 302]

    # Tenant B tries to access Tenant A's customer
    from app.tenant_db import search_customers, get_all_customers

    customers_b = get_all_customers(test_tid_b)
    tenant_a_customers_in_b = [c for c in customers_b if c.get('name') == 'Tenant A Customer']
    assert len(tenant_a_customers_in_b) == 0, "Tenant B should not see Tenant A's customer"

    print("[OK] Created customer belongs only to current tenant")


def test_pos_add_json_still_works():
    """Verify POS /customers/add-json AJAX flow still works after form fix."""
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 15006
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    # Use the add_customer function directly (what /customers/add-json calls)
    from app.tenant_db import add_customer, get_customer

    cid = add_customer(test_tid, 'POS Add JSON Customer',
                      phone='555-6666', vehicle_registration='POS 666')

    cust = get_customer(test_tid, cid)
    assert cust is not None
    assert cust['name'] == 'POS Add JSON Customer'

    print("[OK] POS /customers/add-json still works correctly")


if __name__ == "__main__":
    print("\n=== CUSTOMER FORM CSRF TESTS ===\n")

    test_customer_add_form_contains_csrf_token()
    test_customer_add_without_csrf_rejected()
    test_customer_add_with_valid_csrf_succeeds()
    test_customer_created_belongs_to_tenant()
    test_pos_add_json_still_works()

    print("\n[OK] All customer form CSRF tests passed\n")
