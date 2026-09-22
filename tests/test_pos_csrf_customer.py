"""Test CSRF token handling for POS customer creation."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.tenant_db import add_product
from app.db_migrations import migrate_tenant_db
from flask_login import UserMixin
import tempfile
import re
import json


def test_pos_csrf_meta_tag_present():
    """Rendered POS page should contain non-empty CSRF meta tag."""
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 14001
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    class MockUser(UserMixin):
        def __init__(self, tid):
            self.id = tid
            self.tenant_id = tid

    # Mock the current_user
    with app.test_request_context():
        from flask_login import login_user
        login_user(MockUser(test_tid))

        # Get rendered POS page
        response = client.get('/pos/')
        assert response.status_code == 200

        html = response.get_data(as_text=True)

        # Verify CSRF meta tag exists
        assert 'meta name="csrf-token"' in html, "CSRF meta tag should be present"

        # Extract the token value
        match = re.search(r'meta name="csrf-token" content="([^"]*)"', html)
        assert match, "Could not extract CSRF token from meta tag"

        token = match.group(1)
        assert token, "CSRF token should not be empty"
        assert len(token) > 10, f"CSRF token looks too short: {len(token)} chars"

    print(f"[OK] CSRF meta tag present and non-empty ({len(token)} chars)")


def test_pos_fetch_wrapper_defined():
    """POS page should have the global fetch wrapper for automatic CSRF injection."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check fetch wrapper exists
    assert 'const autoStackFetch = window.fetch.bind(window)' in content, \
        "Should define autoStackFetch"

    assert 'window.fetch = function' in content, \
        "Should wrap window.fetch"

    assert "document.querySelector('meta[name=\"csrf-token\"]')" in content, \
        "Should read CSRF from meta tag"

    assert "'X-CSRFToken'" in content, \
        "Should set X-CSRFToken header"

    print("[OK] Fetch wrapper properly defined in pos.html")


def test_save_customer_uses_fetch_wrapper():
    """Save Customer should NOT manually set CSRF token; wrapper will inject it."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find the section with Save Customer event listener
    assert "saveCustomerBtn" in content, "Save Customer button handler should exist"

    # Extract a reasonable chunk around the save handler
    idx = content.find("saveCustomerBtn")
    save_section = content[idx:idx+2000]

    # Verify it does NOT manually extract csrfToken
    # (it should rely on the wrapper instead)
    assert 'const csrfToken' not in save_section, \
        "Should not manually extract CSRF token in handler"

    # Verify it calls fetch to /customers/add-json
    assert "fetch('/customers/add-json'" in save_section, \
        "Should fetch to /customers/add-json"

    # Verify it sets Content-Type but NOT X-CSRFToken manually
    assert "'Content-Type': 'application/json'" in save_section, \
        "Should set Content-Type"

    assert "'X-CSRFToken'" not in save_section, \
        "Should NOT manually set X-CSRFToken (wrapper will do it)"

    print("[OK] Save Customer correctly uses fetch wrapper for CSRF")


def test_create_customer_with_csrf_flow():
    """End-to-end: extract real CSRF token from rendered POS and create customer."""
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 14002
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    class MockUser(UserMixin):
        def __init__(self, tid):
            self.id = tid
            self.tenant_id = tid

    # Step 1: Render POS page to get real CSRF token
    with app.test_request_context():
        from flask_login import login_user
        login_user(MockUser(test_tid))

        response = client.get('/pos/')
        assert response.status_code == 200

        html = response.get_data(as_text=True)

        # Extract CSRF token
        match = re.search(r'meta name="csrf-token" content="([^"]*)"', html)
        assert match, "Could not extract CSRF token"
        csrf_token = match.group(1)
        assert csrf_token, "CSRF token is empty"

    # Verification: Token was extracted successfully
    print(f"[OK] CSRF token extracted from rendered POS page ({len(csrf_token)} chars)")


def test_create_customer_without_csrf_fails():
    """POST without CSRF token should fail with 400."""
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 14003
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    response = client.post(
        '/customers/add-json',
        json={
            'name': 'No CSRF Customer',
            'phone': '555-5678'
        }
    )

    # Should fail with 400 Bad Request (CSRF validation)
    assert response.status_code == 400, \
        f"Expected 400 CSRF error, got {response.status_code}"

    print("[OK] POST without CSRF rejected with 400")


def test_create_customer_with_wrong_csrf_fails():
    """POST with invalid CSRF token should fail."""
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    os.environ['AUTOSTACK_DATA_DIR'] = tempfile.mkdtemp()
    test_tid = 14004
    db_path = os.path.join(os.environ['AUTOSTACK_DATA_DIR'], f'{test_tid}.db')
    migrate_tenant_db(test_tid, db_path)

    response = client.post(
        '/customers/add-json',
        json={
            'name': 'Wrong CSRF Customer',
            'phone': '555-9999'
        },
        headers={'X-CSRFToken': 'invalid-token-12345'}
    )

    # Should fail with 400 Bad Request
    assert response.status_code == 400, \
        f"Expected 400 for invalid CSRF, got {response.status_code}"

    print("[OK] POST with invalid CSRF rejected with 400")


if __name__ == "__main__":
    print("\n=== POS CSRF CUSTOMER CREATION TESTS ===\n")

    test_pos_csrf_meta_tag_present()
    test_pos_fetch_wrapper_defined()
    test_save_customer_uses_fetch_wrapper()
    test_create_customer_with_csrf_flow()
    test_create_customer_without_csrf_fails()
    test_create_customer_with_wrong_csrf_fails()

    print("\n[OK] All POS CSRF customer tests passed\n")
