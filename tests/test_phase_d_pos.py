"""Phase D POS integration tests - Fixed for invoice number allocation in transaction."""

import pytest
import json
import uuid
import time
from datetime import datetime
from decimal import Decimal

from app import create_app
from app.main_db import init_main_db, get_main_db_path
from app.tenant_db import (
    setup_tenant_db, get_conn, complete_sale,
    get_all_products, get_all_services, search_customers
)


class IsolatedTestDB:
    """Context manager for isolated test databases with unique tenant IDs."""

    def __init__(self, offset=8000):
        self.offset = offset
        self.tenant_id = None
        self.original_env = None
        self.code_suffix = int(time.time() * 1000000) % 1000000

    def __enter__(self):
        import os

        self.tenant_id = self.offset + int(time.time() * 1000) % 10000
        self.original_env = os.environ.get("TENANT_ID")
        os.environ["TENANT_ID"] = str(self.tenant_id)
        setup_tenant_db(self.tenant_id)
        return self.tenant_id

    def __exit__(self, exc_type, exc_val, exc_tb):
        import os
        if self.original_env is None:
            os.environ.pop("TENANT_ID", None)
        else:
            os.environ["TENANT_ID"] = self.original_env


@pytest.fixture
def app():
    """Create app with test config."""
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


class TestInvoiceNumberAllocation:
    """Tests for atomic invoice number allocation within transactions."""

    def test_invoice_allocated_first_call(self):
        """First sale should allocate INV-000001."""
        with IsolatedTestDB() as tid:
            conn = get_conn(tid)
            conn.execute("""
                INSERT INTO products (code, name, brand, tyre_size, condition, current_stock, selling_price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("TYRE001", "Test Tyre", "TestBrand", "195/65R15", "new", 10, 500.00))
            product_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
            conn.close()

            result = complete_sale(
                tid=tid,
                idempotency_key=str(uuid.uuid4()),
                items=[{"type": "product", "id": product_id, "quantity": 1}],
                payment_method="cash"
            )

            assert result["success"] is True
            assert result["invoice_number"] == "INV-000001"

    def test_invoice_allocated_increments(self):
        """Each new sale increments invoice number."""
        with IsolatedTestDB() as tid:
            conn = get_conn(tid)
            conn.execute("""
                INSERT INTO products (code, name, brand, tyre_size, condition, current_stock, selling_price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("TYRE002", "Test Tyre", "TestBrand", "195/65R15", "new", 100, 500.00))
            product_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
            conn.close()

            result1 = complete_sale(
                tid=tid,
                idempotency_key=str(uuid.uuid4()),
                items=[{"type": "product", "id": product_id, "quantity": 1}],
                payment_method="cash"
            )

            result2 = complete_sale(
                tid=tid,
                idempotency_key=str(uuid.uuid4()),
                items=[{"type": "product", "id": product_id, "quantity": 1}],
                payment_method="cash"
            )

            assert result1["invoice_number"] == "INV-000001"
            assert result2["invoice_number"] == "INV-000002"

    def test_failed_sale_does_not_consume_number(self):
        """Failed sale should not allocate sequence number."""
        with IsolatedTestDB() as tid:
            conn = get_conn(tid)
            conn.execute("""
                INSERT INTO products (code, name, brand, tyre_size, condition, current_stock, selling_price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("TYRE003", "Test Tyre", "TestBrand", "195/65R15", "new", 1, 500.00))
            product_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
            conn.close()

            # Try sale with insufficient stock (will fail)
            try:
                complete_sale(
                    tid=tid,
                    idempotency_key=str(uuid.uuid4()),
                    items=[{"type": "product", "id": product_id, "quantity": 10}],
                    payment_method="cash"
                )
            except ValueError:
                pass  # Expected to fail

            # Next successful sale should still get INV-000001 (not INV-000002)
            result = complete_sale(
                tid=tid,
                idempotency_key=str(uuid.uuid4()),
                items=[{"type": "product", "id": product_id, "quantity": 1}],
                payment_method="cash"
            )

            assert result["invoice_number"] == "INV-000001"

    def test_duplicate_retry_does_not_consume_number(self):
        """Retry with same idempotency key should return same invoice, not consume new number."""
        with IsolatedTestDB() as tid:
            conn = get_conn(tid)
            conn.execute("""
                INSERT INTO products (code, name, brand, tyre_size, condition, current_stock, selling_price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("TYRE004", "Test Tyre", "TestBrand", "195/65R15", "new", 100, 500.00))
            product_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
            conn.close()

            idempotency_key = str(uuid.uuid4())

            # First request
            result1 = complete_sale(
                tid=tid,
                idempotency_key=idempotency_key,
                items=[{"type": "product", "id": product_id, "quantity": 1}],
                payment_method="cash"
            )

            # Retry with same key
            result2 = complete_sale(
                tid=tid,
                idempotency_key=idempotency_key,
                items=[{"type": "product", "id": product_id, "quantity": 1}],
                payment_method="cash"
            )

            # Both should have same invoice number
            assert result1["invoice_number"] == "INV-000001"
            assert result2["invoice_number"] == "INV-000001"
            assert result2["duplicate"] is True

            # Next new sale should get INV-000002
            result3 = complete_sale(
                tid=tid,
                idempotency_key=str(uuid.uuid4()),
                items=[{"type": "product", "id": product_id, "quantity": 1}],
                payment_method="cash"
            )

            assert result3["invoice_number"] == "INV-000002"


class TestSellingPriceValidation:
    """Tests for selling_price validation and authorization."""

    def test_product_without_selling_price_rejected(self):
        """Sale fails if product has no selling_price set."""
        with IsolatedTestDB() as tid:
            conn = get_conn(tid)
            # Insert product WITHOUT selling_price
            conn.execute("""
                INSERT INTO products (code, name, brand, tyre_size, condition, current_stock, selling_price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("TYRE005", "No Price Tyre", "TestBrand", "195/65R15", "new", 10, None))
            product_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
            conn.close()

            # Sale should be rejected
            with pytest.raises(ValueError, match="no selling price"):
                complete_sale(
                    tid=tid,
                    idempotency_key=str(uuid.uuid4()),
                    items=[{"type": "product", "id": product_id, "quantity": 1}],
                    payment_method="cash"
                )

    def test_selling_price_from_column_only(self):
        """Uses selling_price column (not extra_data fallback)."""
        with IsolatedTestDB() as tid:
            conn = get_conn(tid)
            conn.execute("""
                INSERT INTO products (code, name, brand, tyre_size, condition, current_stock, selling_price, extra_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, ("TYRE006", "Test Tyre", "TestBrand", "195/65R15", "new", 10, 600.00,
                  json.dumps({"selling_price": 999.99})))  # Ignored extra_data price
            product_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
            conn.close()

            result = complete_sale(
                tid=tid,
                idempotency_key=str(uuid.uuid4()),
                items=[{"type": "product", "id": product_id, "quantity": 1}],
                payment_method="cash"
            )

            # Should use column price (600.00), NOT extra_data (999.99)
            assert result["success"] is True
            assert result["total"] == 600.00


class TestProductPlusSaleTransaction:
    """Tests for complete sales with products only, products+services."""

    def test_cash_walk_in_product_only(self):
        """Complete flow: walk-in, product, cash payment."""
        with IsolatedTestDB() as tid:
            conn = get_conn(tid)
            conn.execute("""
                INSERT INTO products (code, name, brand, tyre_size, condition, current_stock, selling_price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("TYRE007", "Premium Tyre", "Michelin", "205/55R16", "new", 20, 1500.00))
            product_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
            conn.close()

            result = complete_sale(
                tid=tid,
                idempotency_key=str(uuid.uuid4()),
                items=[{"type": "product", "id": product_id, "quantity": 2}],
                payment_method="cash"
            )

            assert result["success"] is True
            assert result["payment_status"] == "paid"
            assert result["total"] == 3000.00  # 2 × 1500
            assert result["debtor_id"] is None
            assert result["invoice_number"] == "INV-000001"

    def test_credit_creates_debtor(self):
        """Credit sale creates debtor record."""
        with IsolatedTestDB() as tid:
            conn = get_conn(tid)
            # Insert customer
            conn.execute("INSERT INTO customers (name, phone) VALUES (?, ?)",
                        ("Test Garage", "555-1234"))
            customer_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # Insert product
            conn.execute("""
                INSERT INTO products (code, name, brand, tyre_size, condition, current_stock, selling_price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("TYRE008", "Budget Tyre", "Generic", "175/70R13", "new", 30, 250.00))
            product_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
            conn.close()

            result = complete_sale(
                tid=tid,
                idempotency_key=str(uuid.uuid4()),
                items=[{"type": "product", "id": product_id, "quantity": 4}],
                customer_id=customer_id,
                payment_method="credit"
            )

            assert result["success"] is True
            assert result["payment_status"] == "unpaid"
            assert result["total"] == 1000.00  # 4 × 250
            assert result["debtor_id"] is not None


class TestStockDeduction:
    """Tests for stock deduction during sales."""

    def test_stock_deducted_on_sale(self):
        """Stock should be reduced after successful sale."""
        with IsolatedTestDB() as tid:
            conn = get_conn(tid)
            conn.execute("""
                INSERT INTO products (code, name, brand, tyre_size, condition, current_stock, selling_price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("TYRE009", "Test Tyre", "TestBrand", "195/65R15", "new", 50, 400.00))
            product_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
            conn.close()

            complete_sale(
                tid=tid,
                idempotency_key=str(uuid.uuid4()),
                items=[{"type": "product", "id": product_id, "quantity": 15}],
                payment_method="cash"
            )

            # Check stock reduced
            conn = get_conn(tid)
            product = conn.execute("SELECT current_stock FROM products WHERE id=?",
                                 (product_id,)).fetchone()
            conn.close()

            assert product[0] == 35  # 50 - 15


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
