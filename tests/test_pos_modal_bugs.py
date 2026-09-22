"""Tests for POS modal bugs - initial visibility and CSRF submission."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re


def test_pos_modal_initially_hidden():
    """Modal should have display: none on initial page load, not display: flex."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        template_content = f.read()

    assert 'id="newCustomerModal"' in template_content, "Modal should be in template"

    # Find the modal element and check its style
    modal_pattern = r'<div id="newCustomerModal"[^>]*style="([^"]*)"'
    match = re.search(modal_pattern, template_content)
    assert match, "Could not find modal with style attribute"

    style = match.group(1)
    # Verify display: none is set at the start
    assert 'display: none' in style, f"Modal should have 'display: none', got: {style}"

    # Verify no duplicate display property in inline style that would override it
    display_count = style.count('display:')
    assert display_count == 1, f"Inline style should have exactly 1 'display' property, found {display_count} in: {style}"

    print("[OK] Modal initially hidden on page load")


def test_modal_opens_only_on_new_customer_button():
    """Modal should open only when + New Customer button is clicked."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        template_content = f.read()

    # Check that newCustomerBtn event listener opens modal
    assert 'newCustomerBtn.addEventListener' in template_content, "Should have new customer button listener"
    assert "document.getElementById('newCustomerModal').style.display = 'block'" in template_content, \
        "Button listener should set display to block"

    print("[OK] Modal opens only on + New Customer click")


def test_modal_cancel_closes_without_changes():
    """Clicking Cancel should close modal without affecting basket/payment."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        template_content = f.read()

    # Verify Cancel button has type="button" (not submit)
    assert 'id="closeModal"' in template_content
    close_pattern = r'<button[^>]*type="button"[^>]*id="closeModal"'
    match = re.search(close_pattern, template_content)
    assert match, "Close button should be type='button'"

    # Verify cancel handler sets display to none
    assert "document.getElementById('closeModal').addEventListener" in template_content
    assert "document.getElementById('newCustomerModal').style.display = 'none'" in template_content

    print("[OK] Cancel closes modal without side effects")


def test_save_customer_requests_add_json_endpoint():
    """Save Customer should POST to /customers/add-json, not /customers/add."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        template_content = f.read()

    # Verify Save Customer button exists and is type="button"
    assert 'id="saveCustomerBtn"' in template_content
    save_pattern = r'<button[^>]*type="button"[^>]*id="saveCustomerBtn"'
    match = re.search(save_pattern, template_content)
    assert match, "Save button should be type='button'"

    # Verify fetch targets /customers/add-json, NOT /customers/add
    assert "fetch('/customers/add-json'" in template_content, \
        "Should POST to /customers/add-json"

    print("[OK] Save Customer targets /customers/add-json")


def test_save_customer_includes_csrf_token():
    """Save Customer fetch should include X-CSRFToken header."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        template_content = f.read()

    # Verify CSRF token is fetched and included
    assert "'X-CSRFToken': csrfToken" in template_content or \
           "'X-CSRFToken': document.querySelector" in template_content, \
        "Should include X-CSRFToken header"

    # Verify meta tag exists
    assert 'meta name="csrf-token"' in template_content, \
        "Should have CSRF meta tag"

    print("[OK] Save Customer includes CSRF token")


def test_save_customer_prevents_default_submission():
    """Save Customer handler should call preventDefault()."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        template_content = f.read()

    # Check for preventDefault and stopPropagation
    assert 'preventDefault()' in template_content, "Should call preventDefault()"
    assert 'stopPropagation()' in template_content, "Should call stopPropagation()"

    print("[OK] Save Customer prevents default submission")


def test_cash_card_eft_do_not_require_customer():
    """Cash/Card/EFT payments should allow no customer selection."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        template_content = f.read()

    # Verify Complete Sale button is present
    assert 'id="completeBtn"' in template_content

    # Verify credit-only validation (not cash/card/eft)
    assert 'if (selectedPaymentMethod === \'credit\'' in template_content or \
           'selectedPaymentMethod === "credit"' in template_content, \
        "Should check for credit payment requirement only"

    print("[OK] Cash/Card/EFT do not require customer")


def test_modal_does_not_navigate_on_save():
    """Saving customer should remain on /pos/, not navigate."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        template_content = f.read()

    # Verify response doesn't close page or redirect on modal save
    # Modal save should just close modal and select customer
    assert "document.getElementById('newCustomerModal').style.display = 'none'" in template_content
    assert "selectCustomer(" in template_content

    # Should NOT have navigation/window.location in close modal
    # Find the save handler section
    save_pattern = r"document\.getElementById\('saveCustomerBtn'\)\.addEventListener\('click'[^}]*?\}\);"
    match = re.search(save_pattern, template_content, re.DOTALL)
    if match:
        handler = match.group(0)
        assert "window.location" not in handler and \
               "window.href" not in handler and \
               "document.location" not in handler, \
            "Should not navigate after customer save"

    print("[OK] Modal does not navigate on save")


if __name__ == "__main__":
    print("\n=== POS MODAL BUG FIXES TESTS ===\n")

    test_pos_modal_initially_hidden()
    test_modal_opens_only_on_new_customer_button()
    test_modal_cancel_closes_without_changes()
    test_save_customer_requests_add_json_endpoint()
    test_save_customer_includes_csrf_token()
    test_save_customer_prevents_default_submission()
    test_cash_card_eft_do_not_require_customer()
    test_modal_does_not_navigate_on_save()

    print("\n[OK] All modal bug tests passed\n")
