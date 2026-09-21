"""POS CSRF Protection Tests - Verify CSRF token handling is correct."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
import json
import uuid


def test_csrf_required_without_token():
    """POST /pos/complete WITHOUT CSRF token is rejected with CSRF error."""
    app = create_app()
    app.config['TESTING'] = True

    with app.test_client() as client:
        payload = {
            "idempotency_key": str(uuid.uuid4()),
            "items": [{"product_id": 1, "quantity": 1}],
            "customer_id": None,
            "payment_method": "card",
            "discount": 0
        }

        # POST without CSRF token
        response = client.post('/pos/complete',
                              data=json.dumps(payload),
                              content_type='application/json')

        # Should be rejected with 400 Bad Request (CSRF token missing)
        assert response.status_code == 400, \
            f"Expected 400 CSRF rejection, got {response.status_code}"

        # Verify CSRF error message is in response
        response_text = response.get_data(as_text=True)
        assert 'csrf' in response_text.lower() or 'CSRF' in response_text, \
            "Error should mention CSRF token"

        print("[OK] POST /pos/complete without CSRF token: 400 Bad Request (CSRF protection working)")


def test_response_is_not_json_on_csrf_error():
    """CSRF error returns HTML error page (not JSON) - frontend must handle gracefully."""
    app = create_app()
    app.config['TESTING'] = True

    with app.test_client() as client:
        payload = {
            "idempotency_key": str(uuid.uuid4()),
            "items": [{"product_id": 1, "quantity": 1}],
            "customer_id": None,
            "payment_method": "card",
            "discount": 0
        }

        # POST without CSRF token
        response = client.post('/pos/complete',
                              data=json.dumps(payload),
                              content_type='application/json')

        # Check Content-Type
        content_type = response.headers.get('Content-Type', '')
        assert 'text/html' in content_type, \
            f"CSRF error should return HTML, got {content_type}"

        # Verify it's not JSON
        response_text = response.get_data(as_text=True)
        assert response_text.startswith('<!doctype') or response_text.startswith('<!DOCTYPE'), \
            "CSRF error should return HTML document"

        print("[OK] CSRF error returns HTML (not JSON) - frontend Content-Type check will catch this")


def test_pos_template_includes_csrf_token():
    """Verify pos.html template includes CSRF token meta tag."""
    template_path = os.path.join(os.path.dirname(__file__),
                                '../app/templates/pos.html')

    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

        # Check for CSRF meta tag
        assert '<meta name="csrf-token"' in content, \
            "pos.html missing CSRF meta tag"

        # Check for CSRF token function call
        assert 'csrf_token()' in content, \
            "pos.html missing csrf_token() call in meta tag"

        print("[OK] pos.html includes CSRF meta tag with csrf_token()")


def test_pos_template_includes_fetch_wrapper():
    """Verify pos.html includes automatic CSRF token fetch wrapper."""
    template_path = os.path.join(os.path.dirname(__file__),
                                '../app/templates/pos.html')

    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

        # Check for fetch wrapper
        assert 'autoStackFetch' in content, \
            "pos.html missing fetch wrapper"

        # Check for X-CSRFToken header
        assert 'X-CSRFToken' in content, \
            "pos.html missing X-CSRFToken header in fetch wrapper"

        # Check for csrf-token meta extraction
        assert 'csrf-token' in content, \
            "pos.html not extracting csrf-token from meta tag"

        print("[OK] pos.html includes automatic CSRF token fetch wrapper")


def test_pos_response_handling_checks_content_type():
    """Verify pos.html checks Content-Type before parsing JSON."""
    template_path = os.path.join(os.path.dirname(__file__),
                                '../app/templates/pos.html')

    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

        # Check for Content-Type check
        assert "res.headers.get('content-type')" in content, \
            "pos.html not checking Content-Type header"

        # Check for JSON validation
        assert 'application/json' in content, \
            "pos.html not validating JSON content type"

        # Check for error handling on non-JSON
        assert 'Unable to complete sale' in content, \
            "pos.html not showing friendly error on non-JSON response"

        print("[OK] pos.html checks Content-Type and handles non-JSON responses")


if __name__ == "__main__":
    print("\n=== POS CSRF PROTECTION TESTS ===\n")

    test_csrf_required_without_token()
    test_response_is_not_json_on_csrf_error()
    test_pos_template_includes_csrf_token()
    test_pos_template_includes_fetch_wrapper()
    test_pos_response_handling_checks_content_type()

    print("\n[OK] All CSRF protection tests passed\n")
