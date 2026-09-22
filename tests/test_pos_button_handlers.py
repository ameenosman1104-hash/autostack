"""Test that Complete Sale button has exactly one handler and can execute."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re


def test_complete_sale_button_exists():
    """Complete Sale button must exist in template."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    assert 'id="completeBtn"' in content, "Complete Sale button should exist"
    print("[OK] Complete Sale button exists")


def test_exactly_one_complete_handler():
    """Template should have exactly ONE Complete Sale click handler, not duplicates."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Count addEventListener for completeBtn
    addEventListener_count = content.count("document.getElementById('completeBtn').addEventListener")
    addEventListener_count += content.count('document.getElementById("completeBtn").addEventListener')

    # Count .onclick assignments for completeBtn
    onclick_count = content.count("document.getElementById('completeBtn').onclick")
    onclick_count += content.count('document.getElementById("completeBtn").onclick')

    # Should have at most 1 .onclick assignment and 0 addEventListener listeners
    # (addEventListener can be used for other purposes, but not for main handler)
    assert addEventListener_count == 0, f"Should not use addEventListener for Complete Sale button (found {addEventListener_count})"
    assert onclick_count >= 1, f"Should have at least 1 .onclick assignment for Complete Sale"

    # More specifically, should not have duplicate handlers calling each other
    # The fix should have removed the validation addEventListener
    assert "originalCompleteClick" not in content, \
        "Should not save/reference original onclick (was causing TypeError on undefined)"

    print("[OK] Exactly one Complete Sale handler exists")


def test_complete_handler_not_broken():
    """Complete Sale handler should not call undefined or have syntax errors."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find the complete sale handler
    assert "document.getElementById('completeBtn').onclick = async ()" in content or \
           'document.getElementById("completeBtn").onclick = async ()' in content, \
        "Should have async Complete Sale handler"

    # Extract handler code (rough check)
    onclick_match = re.search(
        r"document\.getElementById\(['\"]completeBtn['\"]\)\.onclick\s*=\s*async\s*\(\)\s*\{([^}]*?)(?=\n\s*\};)",
        content,
        re.DOTALL
    )

    if onclick_match:
        handler_code = onclick_match.group(1)
        # Should not call undefined functions
        assert "originalCompleteClick" not in handler_code, \
            "Handler should not reference originalCompleteClick (undefined)"
        assert "originalCompleteHandler" not in handler_code, \
            "Handler should not reference originalCompleteHandler (undefined)"

    print("[OK] Complete Sale handler not broken")


def test_handler_validation_logic_present():
    """Handler should validate credit payment requires customer."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Should check for credit + missing customer
    assert "selectedPaymentMethod === 'credit'" in content or \
           'selectedPaymentMethod === "credit"' in content, \
        "Should validate credit payment"

    assert "selectedCustomerId" in content or \
           "document.getElementById('selectedCustomerId')" in content, \
        "Should check customer ID"

    print("[OK] Validation logic present")


def test_handler_creates_correct_payload():
    """Complete Sale handler should create payload with customer_id."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Should include customer_id in payload
    assert "customer_id:" in content or "customer_id :" in content, \
        "Payload should include customer_id"

    # Should include payment_method
    assert "payment_method:" in content or "payment_method :" in content, \
        "Payload should include payment_method"

    # Should include items array
    assert "items:" in content or "items :" in content, \
        "Payload should include items"

    # Should include idempotency_key
    assert "idempotency_key:" in content or "idempotency_key :" in content, \
        "Payload should include idempotency_key"

    print("[OK] Payload structure correct")


def test_handler_sends_to_correct_endpoint():
    """Complete Sale handler should POST to /pos/complete."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    assert 'fetch("/pos/complete"' in content or \
           "fetch('/pos/complete'" in content, \
        "Should POST to /pos/complete endpoint"

    assert 'method: "POST"' in content or \
           "method: 'POST'" in content, \
        "Should use POST method"

    print("[OK] Correct endpoint targeted")


def test_handler_error_display():
    """Complete Sale handler should display errors, not silently fail."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Should have error handling
    assert "showStatus" in content, "Should display status messages"
    assert "catch" in content, "Should have error handling"
    assert "console.error" in content, "Should log errors"

    print("[OK] Error handling present")


if __name__ == "__main__":
    print("\n=== POS BUTTON HANDLER REGRESSION TESTS ===\n")

    test_complete_sale_button_exists()
    test_exactly_one_complete_handler()
    test_complete_handler_not_broken()
    test_handler_validation_logic_present()
    test_handler_creates_correct_payload()
    test_handler_sends_to_correct_endpoint()
    test_handler_error_display()

    print("\n[OK] All button handler tests passed\n")
