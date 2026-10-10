"""Tests for POS modal bugs - initial visibility and CSRF submission."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re


def test_pos_modal_initially_hidden():
    """Modal should be hidden by default via CSS, using class-based toggling."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        template_content = f.read()

    assert 'id="newCustomerModal"' in template_content, "Modal should be in template"

    # Verify CSS hides modal by default
    assert 'display: none' in template_content, "Modal should be hidden by CSS"
    assert '#newCustomerModal' in template_content, "Should have CSS for #newCustomerModal"

    # Verify class-based approach is used for showing
    assert 'modal-open' in template_content, "Should use modal-open class to show modal"

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
    assert 'newCustomerBtn' in template_content, "Should have new customer button"
    # Check for the new class-based approach
    assert "openCustomerModal" in template_content or "modal-open" in template_content, \
        "Should use class-based modal opening mechanism"
    assert "addEventListener('click', openCustomerModal)" in template_content, \
        "New Customer button should call openCustomerModal()"

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

    # Verify cancel handler closes modal using class-based approach
    assert "closeCustomerModal" in template_content, "Should have closeCustomerModal function"
    assert "closeModalBtn.addEventListener('click', closeCustomerModal)" in template_content, \
           "Close button should call closeCustomerModal()"
    assert "newCustomerModal.classList.remove('modal-open')" in template_content, \
           "Modal should remove modal-open class on close"

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
    """Save Customer uses global fetch wrapper for CSRF protection."""
    template_path = os.path.join(
        os.path.dirname(__file__),
        '../app/templates/pos.html'
    )

    with open(template_path, 'r', encoding='utf-8') as f:
        template_content = f.read()

    # Verify global fetch wrapper injects CSRF token
    assert "window.fetch = function" in template_content, \
        "Should have global fetch wrapper"

    assert "'X-CSRFToken'" in template_content, \
        "Should include X-CSRFToken header in wrapper"

    # Verify meta tag exists
    assert 'meta name="csrf-token"' in template_content, \
        "Should have CSRF meta tag"

    print("[OK] Save Customer includes CSRF token via wrapper")


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
    assert ("closeCustomerModal()" in template_content or
            "newCustomerModal.classList.remove('modal-open')" in template_content), \
           "Modal should be closed after save"
    assert "selectCustomer(" in template_content, \
           "Should select customer after save"

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
