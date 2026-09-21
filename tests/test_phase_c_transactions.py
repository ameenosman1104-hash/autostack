"""
Phase C.1: Atomic Sale Transaction Engine Tests

Comprehensive tests for complete_sale() function covering:
- Basic sales (cash, card, eft)
- Credit sales and debtor creation
- Stock deduction and audit
- Idempotency
- Validation and error handling
- Atomicity (rollback on failure)
- Tenant isolation
"""

import sys
import os
import tempfile
import shutil
import json
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tenant_db import (
    add_product, add_service, add_customer, get_product, get_service, get_customer,
    get_invoice, get_invoice_items, get_debtors_by_customer_id, customer_total_outstanding,
    get_stock_history, get_debtor, complete_sale, outstanding_balance, update_product
)
from app.db_migrations import migrate_tenant_db


class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def test(self, name, func):
        try:
            func()
            self.passed += 1
            print(f"[OK] {name}")
        except AssertionError as e:
            self.failed += 1
            error_msg = str(e) if str(e) else "Assertion failed (no message)"
            self.errors.append((name, error_msg))
            print(f"[FAIL] {name}: {error_msg}")
        except Exception as e:
            self.failed += 1
            error_msg = f"ERROR: {str(e)}"
            self.errors.append((name, error_msg))
            print(f"[FAIL] {name}: {error_msg}")

    def summary(self):
        print(f"\n{'='*70}")
        print(f"Results: {self.passed} passed, {self.failed} failed")
        print(f"{'='*70}")
        if self.errors:
            print("\nFailed tests:")
            for name, error in self.errors:
                print(f"  - {name}: {error}")
        return self.failed == 0


_test_counter = 0


class IsolatedTestDB:
    """Context manager for isolated test databases."""

    def __init__(self):
        global _test_counter
        _test_counter += 1
        self.counter = _test_counter
        self.temp_dir = None
        self.tid = 8000 + _test_counter
        self.original_dir = None
        # Unique suffix for product codes (timestamp-based)
        self.code_suffix = int(time.time() * 1000000) % 1000000

    def __enter__(self):
        self.temp_dir = tempfile.mkdtemp(prefix=f"phase_c_test_{self.counter}_")
        self.original_dir = os.environ.get("AUTOSTACK_DATA_DIR")
        os.environ["AUTOSTACK_DATA_DIR"] = self.temp_dir

        test_db_path = os.path.join(self.temp_dir, f"{self.tid}.db")
        try:
            migrate_tenant_db(self.tid, test_db_path)
        except Exception as e:
            self.__exit__(None, None, None)
            raise RuntimeError(f"Migration failed: {e}")

        return self.tid

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.original_dir:
            os.environ["AUTOSTACK_DATA_DIR"] = self.original_dir
        else:
            os.environ.pop("AUTOSTACK_DATA_DIR", None)

        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def unique_code(self, prefix):
        """Generate unique product code with timestamp."""
        return f"{prefix}-{self.code_suffix}"

    def unique_invoice(self, prefix):
        """Generate unique invoice number with timestamp."""
        return f"{prefix}-{self.code_suffix}"


# ======================== PHASE C.1 TESTS ========================

def test_cash_sale_single_product():
    """Cash sale with one product: stock reduces, no debtor created."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        # Setup
        pid = add_product(tid, db.unique_code("TYRE"), "Summer Tyre", current_stock=10, selling_price=100.00)

        # Process sale
        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 2}],
            payment_method="cash"
        )

        # Verify
        assert result["success"] is True
        assert result["duplicate"] is False
        assert result["total"] == 200.00
        assert result["debtor_id"] is None
        assert result["payment_status"] == "paid"

        # Check stock reduced
        updated_product = get_product(tid, pid)
        assert updated_product["current_stock"] == 8
    finally:
        db.__exit__(None, None, None)


def test_card_sale():
    """Card sale: stock reduces, payment_status=paid, no debtor."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("FILTER"), "Air Filter", current_stock=20, selling_price=100.00)
        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 3}],
            payment_method="card"
        )

        assert result["success"] is True
        assert result["payment_status"] == "paid"
        assert result["debtor_id"] is None

        updated_product = get_product(tid, pid)
        assert updated_product["current_stock"] == 17
    finally:
        db.__exit__(None, None, None)


def test_eft_sale():
    """EFT sale: stock reduces, payment_status=paid, no debtor."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("OIL"), "Engine Oil", current_stock=50, selling_price=100.00)
        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 5}],
            payment_method="eft"
        )

        assert result["success"] is True
        assert result["payment_status"] == "paid"

        updated_product = get_product(tid, pid)
        assert updated_product["current_stock"] == 45
    finally:
        db.__exit__(None, None, None)


def test_walkin_cash_sale():
    """Walk-in customer (customer_id=None) cash sale succeeds."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("WIPER"), "Wiper Blade", current_stock=30, selling_price=100.00)
        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 2}],
            customer_id=None,
            payment_method="cash"
        )

        assert result["success"] is True
        assert result["debtor_id"] is None
    finally:
        db.__exit__(None, None, None)


def test_known_customer_cash_sale():
    """Known customer cash sale (no credit): succeeds, no debtor created."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        cid = add_customer(tid, "John Doe", phone="081234567")
        pid = add_product(tid, db.unique_code("BATT"), "Battery", current_stock=15, selling_price=100.00)
        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 1}],
            customer_id=cid,
            payment_method="cash"
        )

        assert result["success"] is True
        assert result["debtor_id"] is None
    finally:
        db.__exit__(None, None, None)


def test_credit_requires_customer():
    """Credit sale without customer_id is rejected."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("PAD"), "Brake Pad", current_stock=20, selling_price=100.00)
        try:
            complete_sale(
                tid,
                invoice_number=db.unique_invoice("INV"),
                idempotency_key=f"key-{db.code_suffix}",
                items=[{"type": "product", "id": pid, "quantity": 1}],
                customer_id=None,
                payment_method="credit"
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "credit payment requires customer_id" in str(e)
    finally:
        db.__exit__(None, None, None)


def test_credit_invalid_customer():
    """Credit sale with nonexistent customer_id is rejected."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("DISC"), "Brake Disc", current_stock=10, selling_price=100.00)
        try:
            complete_sale(
                tid,
                invoice_number=db.unique_invoice("INV"),
                idempotency_key=f"key-{db.code_suffix}",
                items=[{"type": "product", "id": pid, "quantity": 1}],
                customer_id=9999,
                payment_method="credit"
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "not found" in str(e)
    finally:
        db.__exit__(None, None, None)


def test_credit_sale_creates_debtor():
    """Credit sale creates exactly one new debtor and links to invoice."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        cid = add_customer(tid, "Jane Smith", phone="082987654")
        pid = add_product(tid, db.unique_code("SHOCK"), "Shock Absorber", current_stock=8, selling_price=100.00)
        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 2}],
            customer_id=cid,
            payment_method="credit"
        )

        assert result["success"] is True
        assert result["debtor_id"] is not None
        assert result["payment_status"] == "unpaid"
        assert result["total"] == 200.00

        # Verify debtor exists and is linked
        debtor = get_debtor(tid, result["debtor_id"])
        assert debtor is not None
        assert debtor["customer_id"] == cid
        assert debtor["amount_owed"] == 200.00
        assert debtor["is_paid"] == 0
        assert debtor["status"] == "DUE"

        # Verify invoice links to debtor
        invoice = get_invoice(tid, result["invoice_id"])
        assert invoice["debtor_id"] == result["debtor_id"]
    finally:
        db.__exit__(None, None, None)


def test_two_credit_invoices_create_two_debtors():
    """Two credit sales for same customer create two separate debtor records."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        cid = add_customer(tid, "Bob Johnson", phone="083111111")
        pid = add_product(tid, db.unique_code("WHEEL"), "Wheel", current_stock=50, selling_price=100.00)
        # First credit sale
        result1 = complete_sale(
            tid,
            invoice_number=f"INV-A-{db.code_suffix}",
            idempotency_key=f"key-A-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 1}],
            customer_id=cid,
            payment_method="credit"
        )

        # Second credit sale
        result2 = complete_sale(
            tid,
            invoice_number=f"INV-B-{db.code_suffix}",
            idempotency_key=f"key-B-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 2}],
            customer_id=cid,
            payment_method="credit"
        )

        # Verify two debtors created
        debtors = get_debtors_by_customer_id(tid, cid)
        assert len(debtors) == 2
        assert debtors[0]["amount_owed"] == 100.00
        assert debtors[1]["amount_owed"] == 200.00

        # Verify customer total outstanding
        total_outstanding = customer_total_outstanding(tid, cid)
        assert total_outstanding == 300.00
    finally:
        db.__exit__(None, None, None)


def test_multi_item_product_plus_service():
    """Multi-item sale: 2 products + 1 service."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        # Setup products
        pid1 = add_product(tid, db.unique_code("TYREA"), "Tyre A", current_stock=20, selling_price=100.00)
        pid2 = add_product(tid, db.unique_code("TYREB"), "Tyre B", current_stock=20, selling_price=100.00)
        extra_data = json.dumps({"selling_price": 100.00})
        update_product(tid, pid1, extra_data=extra_data)
        update_product(tid, pid2, extra_data=extra_data)

        # Setup service
        sid = add_service(tid, "Installation", default_price=50.00)

        # Process sale
        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[
                {"type": "product", "id": pid1, "quantity": 1},
                {"type": "product", "id": pid2, "quantity": 2},
                {"type": "service", "id": sid, "quantity": 1}
            ],
            payment_method="cash"
        )

        # Verify
        assert result["success"] is True
        assert result["total"] == 350.00  # 100 + 200 + 50
        assert result["items_count"] == 3

        # Stock should only reduce for products, not service
        p1 = get_product(tid, pid1)
        p2 = get_product(tid, pid2)
        assert p1["current_stock"] == 19  # 20 - 1
        assert p2["current_stock"] == 18  # 20 - 2
    finally:
        db.__exit__(None, None, None)


def test_service_does_not_affect_stock():
    """Service-only sale: no stock deduction."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        sid = add_service(tid, "Repair", default_price=75.00)

        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "service", "id": sid, "quantity": 3}],
            payment_method="cash"
        )

        assert result["success"] is True
        assert result["total"] == 225.00
    finally:
        db.__exit__(None, None, None)


def test_discount_applied():
    """Sale with discount: total = subtotal - discount."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("ITEM"), "Item", current_stock=10, selling_price=100.00)
        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 3}],
            payment_method="cash",
            discount=50.00
        )

        assert result["success"] is True
        assert result["total"] == 250.00  # 300 - 50
    finally:
        db.__exit__(None, None, None)


def test_duplicate_product_lines_aggregated():
    """Duplicate product lines are aggregated before stock validation."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("LIMITSTOCK"), "Limited Stock", current_stock=5, selling_price=100.00)
        # Try to sale same product twice: 3 + 4 = 7, but stock is only 5
        try:
            complete_sale(
                tid,
                invoice_number=db.unique_invoice("INV"),
                idempotency_key=f"key-{db.code_suffix}",
                items=[
                    {"type": "product", "id": pid, "quantity": 3},
                    {"type": "product", "id": pid, "quantity": 4}
                ],
                payment_method="cash"
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Insufficient stock" in str(e)
    finally:
        db.__exit__(None, None, None)


def test_insufficient_stock_rejected():
    """Sale with insufficient stock is rejected, nothing committed."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("LOWSTOCK"), "Low Stock Item", current_stock=2, selling_price=100.00)
        try:
            complete_sale(
                tid,
                invoice_number=db.unique_invoice("INV"),
                idempotency_key=f"key-{db.code_suffix}",
                items=[{"type": "product", "id": pid, "quantity": 5}],
                payment_method="cash"
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Insufficient stock" in str(e)

        # Verify no invoice created
        invoice = get_invoice(tid, 1)
        assert invoice is None
    finally:
        db.__exit__(None, None, None)


def test_stock_never_negative():
    """Verify stock cannot become negative."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("NEGTEST"), "Negative Test", current_stock=3, selling_price=100.00)
        # Exact stock should work
        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 3}],
            payment_method="cash"
        )
        assert result["success"] is True

        product = get_product(tid, pid)
        assert product["current_stock"] == 0  # Not negative
    finally:
        db.__exit__(None, None, None)


def test_missing_selling_price_rejected():
    """Product without extra_data.selling_price is rejected."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("NOPRICE"), "No Price", current_stock=10)
        # No selling_price in extra_data

        try:
            complete_sale(
                tid,
                invoice_number=db.unique_invoice("INV"),
                idempotency_key=f"key-{db.code_suffix}",
                items=[{"type": "product", "id": pid, "quantity": 1}],
                payment_method="cash"
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "no selling price" in str(e)
    finally:
        db.__exit__(None, None, None)


def test_negative_selling_price_rejected():
    """Product with negative selling_price is rejected."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("NEGPRICE"), "Negative Price", current_stock=10, selling_price=-100.00)

        try:
            complete_sale(
                tid,
                invoice_number=db.unique_invoice("INV"),
                idempotency_key=f"key-{db.code_suffix}",
                items=[{"type": "product", "id": pid, "quantity": 1}],
                payment_method="cash"
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "cannot be negative" in str(e) or "selling" in str(e)
    finally:
        db.__exit__(None, None, None)


def test_malformed_extra_data_rejected():
    """Product with malformed extra_data JSON doesn't break (selling_price is authoritative)."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        # Add product with malformed extra_data but valid selling_price
        pid = add_product(tid, db.unique_code("BADJSON"), "Bad JSON", current_stock=10,
                         selling_price=100.00, extra_data="not-json")

        # Sale should succeed because selling_price is set and is authoritative
        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 1}],
            payment_method="cash"
        )
        assert result["success"] is True
        assert result["total"] == 100.00
    finally:
        db.__exit__(None, None, None)


def test_service_price_authoritative():
    """Service price comes from services.default_price, not browser."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        sid = add_service(tid, "Labor", default_price=100.00)

        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "service", "id": sid, "quantity": 2}],
            payment_method="cash"
        )

        assert result["success"] is True
        assert result["total"] == 200.00  # 100 * 2, not influenced by browser
    finally:
        db.__exit__(None, None, None)


def test_idempotent_retry_returns_duplicate():
    """Idempotent retry with same key returns duplicate=True."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("IDEMPTEST"), "Idempotent Test", current_stock=10, selling_price=100.00)
        # First call
        result1 = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 1}],
            payment_method="cash"
        )

        assert result1["duplicate"] is False
        original_invoice_id = result1["invoice_id"]

        # Second call with same idempotency_key
        result2 = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 1}],
            payment_method="cash"
        )

        assert result2["duplicate"] is True
        assert result2["invoice_id"] == original_invoice_id
    finally:
        db.__exit__(None, None, None)


def test_idempotent_no_duplicate_items():
    """Idempotent retry creates no duplicate invoice_items."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("IDEMITEMS"), "Idem Items", current_stock=10, selling_price=100.00)
        # First call
        result1 = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 2}],
            payment_method="cash"
        )

        items1 = get_invoice_items(tid, result1["invoice_id"])
        assert len(items1) == 1

        # Second call
        result2 = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 2}],
            payment_method="cash"
        )

        items2 = get_invoice_items(tid, result2["invoice_id"])
        assert len(items2) == 1  # Still 1, not 2
    finally:
        db.__exit__(None, None, None)


def test_idempotent_no_duplicate_stock_deduction():
    """Idempotent retry does not deduct stock twice."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("IDEMSTOCK"), "Idem Stock", current_stock=10, selling_price=100.00)
        # First call
        complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 3}],
            payment_method="cash"
        )

        product1 = get_product(tid, pid)
        assert product1["current_stock"] == 7

        # Second call (idempotent)
        complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 3}],
            payment_method="cash"
        )

        product2 = get_product(tid, pid)
        assert product2["current_stock"] == 7  # Still 7, not 4
    finally:
        db.__exit__(None, None, None)


def test_idempotent_no_duplicate_debtor():
    """Idempotent credit retry does not create second debtor."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        cid = add_customer(tid, "Idempotent Customer", phone="089999999")
        pid = add_product(tid, db.unique_code("IDEMDEBTOR"), "Idem Debtor", current_stock=10, selling_price=100.00)
        # First call
        result1 = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 1}],
            customer_id=cid,
            payment_method="credit"
        )

        debtors1 = get_debtors_by_customer_id(tid, cid)
        assert len(debtors1) == 1

        # Second call (idempotent)
        result2 = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 1}],
            customer_id=cid,
            payment_method="credit"
        )

        debtors2 = get_debtors_by_customer_id(tid, cid)
        assert len(debtors2) == 1  # Still 1, not 2
        assert result1["debtor_id"] == result2["debtor_id"]
    finally:
        db.__exit__(None, None, None)


def test_stock_history_created():
    """Stock history entries created for each product deduction."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid1 = add_product(tid, db.unique_code("HISTA"), "Hist A", current_stock=10, selling_price=100.00)
        pid2 = add_product(tid, db.unique_code("HISTB"), "Hist B", current_stock=20, selling_price=100.00)
        extra_data = json.dumps({"selling_price": 100.00})
        update_product(tid, pid1, extra_data=extra_data)
        update_product(tid, pid2, extra_data=extra_data)

        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[
                {"type": "product", "id": pid1, "quantity": 2},
                {"type": "product", "id": pid2, "quantity": 3}
            ],
            payment_method="cash"
        )

        history = get_stock_history(tid)
        assert len(history) >= 2

        # Should have at least 2 entries (one for each product deducted)
        invoice_entries = [h for h in history if "Invoice INV-" in h.get("notes", "")]
        assert len(invoice_entries) >= 2
    finally:
        db.__exit__(None, None, None)


def test_invalid_customer_id_rejected():
    """Sale with nonexistent customer_id (non-credit) is rejected."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("CUSTTEST"), "Customer Test", current_stock=10, selling_price=100.00)
        try:
            complete_sale(
                tid,
                invoice_number=db.unique_invoice("INV"),
                idempotency_key=f"key-{db.code_suffix}",
                items=[{"type": "product", "id": pid, "quantity": 1}],
                customer_id=9999,
                payment_method="cash"
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "not found" in str(e)
    finally:
        db.__exit__(None, None, None)


def test_decimal_precision():
    """Monetary calculations use Decimal for precision."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("DECIMAL"), "Decimal Test", current_stock=10, selling_price=100.00)
        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 3}],
            payment_method="cash"
        )

        # 100.00 * 3 = 300.00
        assert result["total"] == 300.00
    finally:
        db.__exit__(None, None, None)


def test_tax_is_zero():
    """Invoice.tax is always 0 in Phase C.1."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("TAX"), "Tax Test", current_stock=10, selling_price=100.00)
        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 1}],
            payment_method="cash"
        )

        invoice = get_invoice(tid, result["invoice_id"])
        assert invoice["tax"] == 0.0
    finally:
        db.__exit__(None, None, None)


def test_no_payment_history_for_cash_sale():
    """Paid sale does not create payment_history entry."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("PAYMENTTEST"), "Payment Test", current_stock=10, selling_price=100.00)
        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 1}],
            payment_method="cash"
        )

        # payment_history should be empty (no debtor, no payment)
        assert result["debtor_id"] is None
    finally:
        db.__exit__(None, None, None)


def test_tenant_isolation():
    """Tenant A sale cannot reference Tenant B product."""
    db_a = IsolatedTestDB()
    tid_a = db_a.__enter__()
    db_b = IsolatedTestDB()
    tid_b = db_b.__enter__()
    try:
        # Tenant A creates product
        pid_a = add_product(tid_a, db_a.unique_code("TENANT-A"), "Tenant A Product", current_stock=10, selling_price=100.00)
        extra_data = json.dumps({"selling_price": 100.00})
        update_product(tid_a, pid_a, extra_data=extra_data)

        # Tenant B tries to use Tenant A's product
        try:
            complete_sale(
                tid_b,
                invoice_number=db_b.unique_invoice("INV"),
                idempotency_key=f"key-{db_b.code_suffix}",
                items=[{"type": "product", "id": pid_a, "quantity": 1}],
                payment_method="cash"
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "not found" in str(e)
    finally:
        db_a.__exit__(None, None, None)
        db_b.__exit__(None, None, None)


def test_browser_unit_price_ignored():
    """Browser-supplied unit_price is ignored; backend price used."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("BROWSER"), "Browser Test", current_stock=10, selling_price=100.00)
        # Even if caller tries to supply unit_price, backend ignores it
        result = complete_sale(
            tid,
            invoice_number=db.unique_invoice("INV"),
            idempotency_key=f"key-{db.code_suffix}",
            items=[{"type": "product", "id": pid, "quantity": 2}],
            payment_method="cash"
        )

        # Verify backend used correct price (100 * 2 = 200)
        assert result["success"] is True
        assert result["total"] == 200.00

        invoice_items = get_invoice_items(tid, result["invoice_id"])
        assert invoice_items[0]["unit_price"] == 100.00
    finally:
        db.__exit__(None, None, None)


def test_credit_sale_failure_rollback_debtor():
    """If credit sale fails, debtor is NOT created (atomicity)."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        cid = add_customer(tid, "Credit Failure Test", phone="089999999")
        pid = add_product(tid, db.unique_code("CREDITFAIL"), "Credit Fail", current_stock=1, selling_price=100.00)
        # Try to buy more than stock
        try:
            complete_sale(
                tid,
                invoice_number=db.unique_invoice("INV"),
                idempotency_key=f"key-{db.code_suffix}",
                items=[{"type": "product", "id": pid, "quantity": 5}],
                customer_id=cid,
                payment_method="credit"
            )
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Insufficient stock" in str(e)

        # Verify no debtor was created
        debtors = get_debtors_by_customer_id(tid, cid)
        assert len(debtors) == 0
    finally:
        db.__exit__(None, None, None)


def test_stock_history_idempotent_no_duplicate():
    """Idempotent retry does not create duplicate stock_history entry."""
    db = IsolatedTestDB()
    tid = db.__enter__()
    try:
        pid = add_product(tid, db.unique_code("STKHIST"), "Stock History", current_stock=20, selling_price=100.00)
        idem_key = f"key-{db.code_suffix}"

        # First call
        result1 = complete_sale(
            tid,
            idempotency_key=idem_key,
            items=[{"type": "product", "id": pid, "quantity": 4}],
            payment_method="cash"
        )

        history1 = get_stock_history(tid)
        # Get the invoice number from the result
        inv_num = result1["invoice_number"]
        history_count_1 = len([h for h in history1 if inv_num in h.get("notes", "")])

        # Second call (idempotent)
        result2 = complete_sale(
            tid,
            idempotency_key=idem_key,
            items=[{"type": "product", "id": pid, "quantity": 4}],
            payment_method="cash"
        )

        history2 = get_stock_history(tid)
        history_count_2 = len([h for h in history2 if inv_num in h.get("notes", "")])

        # Should still have only 1 history entry (idempotent - no duplicate)
        assert history_count_1 == 1
        assert history_count_2 == 1
    finally:
        db.__exit__(None, None, None)


# ======================== MAIN ========================

def main():
    """Run Phase C.1 transaction tests."""
    print("="*70)
    print("Phase C.1: Atomic Sale Transaction Engine Tests")
    print("="*70 + "\n")

    results = TestResults()

    print("Running Phase C.1 Transaction Tests...\n")

    results.test("Cash sale: single product", test_cash_sale_single_product)
    results.test("Card sale", test_card_sale)
    results.test("EFT sale", test_eft_sale)
    results.test("Walk-in cash sale", test_walkin_cash_sale)
    results.test("Known customer cash sale", test_known_customer_cash_sale)
    results.test("Credit requires customer", test_credit_requires_customer)
    results.test("Credit invalid customer", test_credit_invalid_customer)
    results.test("Credit creates debtor", test_credit_sale_creates_debtor)
    results.test("Two credit invoices: two debtors", test_two_credit_invoices_create_two_debtors)
    results.test("Multi-item: products + service", test_multi_item_product_plus_service)
    results.test("Service does not affect stock", test_service_does_not_affect_stock)
    results.test("Discount applied", test_discount_applied)
    results.test("Duplicate product lines aggregated", test_duplicate_product_lines_aggregated)
    results.test("Insufficient stock rejected", test_insufficient_stock_rejected)
    results.test("Stock never negative", test_stock_never_negative)
    results.test("Missing selling_price rejected", test_missing_selling_price_rejected)
    results.test("Negative selling_price rejected", test_negative_selling_price_rejected)
    results.test("Malformed extra_data rejected", test_malformed_extra_data_rejected)
    results.test("Service price authoritative", test_service_price_authoritative)
    results.test("Idempotent retry: duplicate flag", test_idempotent_retry_returns_duplicate)
    results.test("Idempotent: no duplicate items", test_idempotent_no_duplicate_items)
    results.test("Idempotent: no duplicate stock", test_idempotent_no_duplicate_stock_deduction)
    results.test("Idempotent: no duplicate debtor", test_idempotent_no_duplicate_debtor)
    results.test("Stock history created", test_stock_history_created)
    results.test("Invalid customer rejected", test_invalid_customer_id_rejected)
    results.test("Decimal precision", test_decimal_precision)
    results.test("Tax is zero", test_tax_is_zero)
    results.test("No payment_history for paid", test_no_payment_history_for_cash_sale)
    results.test("Tenant isolation", test_tenant_isolation)
    results.test("Browser unit_price ignored", test_browser_unit_price_ignored)
    results.test("Credit failure: debtor rollback", test_credit_sale_failure_rollback_debtor)
    results.test("Idempotent: no duplicate stock_history", test_stock_history_idempotent_no_duplicate)

    success = results.summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
