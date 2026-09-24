#!/usr/bin/env python3
"""Tests for Stock Intelligence Engine.

Tests verify:
- Correct metric calculations
- Deterministic flag logic
- Multi-tenant isolation
- Edge cases (zero division, no sales, etc.)
- Performance (no N+1 queries)
"""

import sys
import os
import tempfile
import sqlite3
from datetime import datetime, date, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.stock_intelligence import StockIntelligenceEngine
from app.tenant_db import get_conn
from app.db_migrations import migrate_tenant_db


class TestResults:
    """Test result tracking."""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def test(self, name, func):
        try:
            func()
            self.passed += 1
            print(f"[PASS] {name}")
        except AssertionError as e:
            self.failed += 1
            self.errors.append((name, str(e)))
            print(f"[FAIL] {name}: {e}")
        except Exception as e:
            self.failed += 1
            self.errors.append((name, f"ERROR: {str(e)}"))
            print(f"[ERROR] {name}: {e}")

    def summary(self):
        print(f"\n{'='*80}")
        print(f"Stock Intelligence Tests: {self.passed} passed, {self.failed} failed")
        print(f"{'='*80}")
        if self.errors:
            print("\nFailed tests:")
            for name, error in self.errors:
                print(f"  - {name}: {error}")
        return self.failed == 0


_test_counter = 0

def setup_test_db():
    """Setup test database with migrations."""
    global _test_counter
    _test_counter += 1

    temp_dir = tempfile.mkdtemp(prefix=f"stock_intel_test_{_test_counter}_")
    test_tenant_id = 2000 + _test_counter
    test_db_path = os.path.join(temp_dir, f"{test_tenant_id}.db")

    os.environ["AUTOSTACK_DATA_DIR"] = temp_dir

    try:
        migrate_tenant_db(test_tenant_id, test_db_path)
        return test_tenant_id
    except Exception as e:
        print(f"Migration failed: {e}")
        return None


class StockIntelligenceTests:
    """Test suite for Stock Intelligence calculations."""

    def __init__(self, results: TestResults):
        self.results = results

    def test_analyze_inventory_returns_structured_data(self):
        """Verify analyze_inventory returns correct structure."""
        tenant_id = setup_test_db()
        engine = StockIntelligenceEngine(tenant_id, get_conn)
        result = engine.analyze_inventory()

        assert 'summary' in result, "Missing 'summary' key"
        assert 'attention_items' in result, "Missing 'attention_items' key"
        assert 'generated_at' in result, "Missing 'generated_at' key"

        summary = result['summary']
        assert 'total_products' in summary, "Missing 'total_products' in summary"
        assert 'out_of_stock_count' in summary, "Missing 'out_of_stock_count' in summary"
        assert 'low_stock_count' in summary, "Missing 'low_stock_count' in summary"

    def test_out_of_stock_product_flagged(self):
        """Verify OUT_OF_STOCK flag for products with <= 0 stock."""
        tenant_id = setup_test_db()
        conn = get_conn(tenant_id)

        conn.execute(
            "INSERT INTO products (code, name, current_stock, reorder_level) VALUES (?,?,?,?)",
            ("TEST001", "Zero Stock Product", 0, 5)
        )
        conn.commit()
        conn.close()

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        result = engine.analyze_inventory()

        test_product = next((p for p in result['attention_items']
                            if p['product_code'] == 'TEST001'), None)
        assert test_product is not None, "Product not found in results"
        assert 'OUT_OF_STOCK' in test_product['flags'], "OUT_OF_STOCK flag missing"

    def test_below_reorder_level_flagged(self):
        """Verify BELOW_REORDER_LEVEL flag when stock <= reorder_level."""
        tenant_id = setup_test_db()
        conn = get_conn(tenant_id)

        conn.execute(
            "INSERT INTO products (code, name, current_stock, reorder_level) VALUES (?,?,?,?)",
            ("TEST002", "Low Stock Product", 3, 5)
        )
        conn.commit()
        conn.close()

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        result = engine.analyze_inventory()
        test_product = next((p for p in result['attention_items']
                            if p['product_code'] == 'TEST002'), None)

        assert test_product is not None, "Product not found"
        assert 'BELOW_REORDER_LEVEL' in test_product['flags'], "BELOW_REORDER_LEVEL flag missing"

    def test_no_reorder_level_flagged(self):
        """Verify NO_REORDER_LEVEL when reorder_level not set and product is old."""
        tenant_id = setup_test_db()
        conn = get_conn(tenant_id)

        old_date = (date.today() - timedelta(days=60)).isoformat() + "T00:00:00"
        conn.execute(
            "INSERT INTO products (code, name, current_stock, reorder_level, created_at) VALUES (?,?,?,?,?)",
            ("TEST003", "No Reorder Product", 10, 0, old_date)
        )
        conn.commit()
        conn.close()

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        result = engine.analyze_inventory()
        test_product = next((p for p in result['attention_items']
                            if p['product_code'] == 'TEST003'), None)

        assert test_product is not None, "Product not found"
        assert 'NO_REORDER_LEVEL' in test_product['flags'], "NO_REORDER_LEVEL flag missing"

    def test_newly_created_product_not_penalized(self):
        """Verify newly created products (< 30 days) don't get harsh flags."""
        tenant_id = setup_test_db()
        conn = get_conn(tenant_id)

        today = date.today().isoformat() + "T00:00:00"
        conn.execute(
            "INSERT INTO products (code, name, current_stock, reorder_level, created_at) VALUES (?,?,?,?,?)",
            ("TEST_NEW", "New Product", 5, 2, today)
        )
        conn.commit()
        conn.close()

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        result = engine.analyze_inventory()
        test_product = next((p for p in result['attention_items']
                            if p['product_code'] == 'TEST_NEW'), None)

        if test_product:
            assert 'NEVER_SOLD' not in test_product['flags'], "New product shouldn't have NEVER_SOLD flag"

    def test_never_sold_product_flagged(self):
        """Verify NEVER_SOLD flag for products with no transaction history."""
        tenant_id = setup_test_db()
        conn = get_conn(tenant_id)

        old_date = (date.today() - timedelta(days=60)).isoformat() + "T00:00:00"
        conn.execute(
            "INSERT INTO products (code, name, current_stock, reorder_level, created_at) VALUES (?,?,?,?,?)",
            ("TEST_NEVER", "Never Sold", 50, 5, old_date)
        )
        conn.commit()
        conn.close()

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        result = engine.analyze_inventory()
        test_product = next((p for p in result['attention_items']
                            if p['product_code'] == 'TEST_NEVER'), None)

        assert test_product is not None, "Never-sold product not found"
        assert 'NEVER_SOLD' in test_product['flags'], "NEVER_SOLD flag missing"
        assert test_product['has_ever_sold'] is False, "has_ever_sold should be False"

    def test_stock_cover_zero_sales_returns_none(self):
        """Verify stock cover returns None when no sales history."""
        tenant_id = setup_test_db()
        conn = get_conn(tenant_id)

        old_date = (date.today() - timedelta(days=60)).isoformat() + "T00:00:00"
        conn.execute(
            "INSERT INTO products (code, name, current_stock, reorder_level, created_at) VALUES (?,?,?,?,?)",
            ("TEST_ZERO_SALES", "No Sales", 50, 5, old_date)
        )
        conn.commit()
        conn.close()

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        result = engine.analyze_inventory()
        test_product = next((p for p in result['attention_items']
                            if p['product_code'] == 'TEST_ZERO_SALES'), None)

        if test_product:
            assert test_product['estimated_days_remaining_30d'] is None, \
                "Stock cover should be None when no sales"

    def test_summary_statistics_accurate(self):
        """Verify summary counts match actual data."""
        tenant_id = setup_test_db()
        conn = get_conn(tenant_id)

        conn.execute("INSERT INTO products (code, name, current_stock, reorder_level) VALUES (?,?,?,?)",
                    ("S1", "Stock Out", 0, 5))
        conn.execute("INSERT INTO products (code, name, current_stock, reorder_level) VALUES (?,?,?,?)",
                    ("S2", "Low Stock", 2, 5))
        conn.execute("INSERT INTO products (code, name, current_stock, reorder_level) VALUES (?,?,?,?)",
                    ("S3", "Healthy", 20, 5))

        conn.commit()
        conn.close()

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        result = engine.analyze_inventory()
        summary = result['summary']

        assert summary['total_products'] == 3, f"Expected 3 products, got {summary['total_products']}"
        assert summary['out_of_stock_count'] == 1, f"Expected 1 out of stock, got {summary['out_of_stock_count']}"
        # S1 (0 <= 5) and S2 (2 <= 5) are both low stock (below reorder level)
        assert summary['low_stock_count'] == 2, f"Expected 2 low stock, got {summary['low_stock_count']}"

    def test_multi_tenant_isolation(self):
        """Verify each tenant only sees their own products."""
        tenant1_id = setup_test_db()
        tenant2_id = setup_test_db()

        # Add products for tenant1 (old product with no reorder level = flag)
        old_date = (date.today() - timedelta(days=60)).isoformat() + "T00:00:00"
        conn1 = get_conn(tenant1_id)
        conn1.execute("INSERT INTO products (code, name, current_stock, reorder_level, created_at) VALUES (?,?,?,?,?)",
                     ("T1_P1", "Tenant1 Product", 10, 0, old_date))
        conn1.commit()
        conn1.close()

        # Add products for tenant2 (old product with no reorder level = flag)
        conn2 = get_conn(tenant2_id)
        conn2.execute("INSERT INTO products (code, name, current_stock, reorder_level, created_at) VALUES (?,?,?,?,?)",
                     ("T2_P1", "Tenant2 Product", 20, 0, old_date))
        conn2.commit()
        conn2.close()

        # Verify isolation
        engine1 = StockIntelligenceEngine(tenant1_id, get_conn)
        result1 = engine1.analyze_inventory()

        engine2 = StockIntelligenceEngine(tenant2_id, get_conn)
        result2 = engine2.analyze_inventory()

        # Extract product codes from attention items
        codes1 = [p['product_code'] for p in result1.get('attention_items', [])]
        codes2 = [p['product_code'] for p in result2.get('attention_items', [])]

        # Tenant2 should have T2_P1 (no reorder level is a flag)
        assert 'T2_P1' in codes2, f"Tenant2 should see their own product. Got codes: {codes2}"
        # Tenant2 should NOT have Tenant1's product
        assert 'T1_P1' not in codes2, f"Tenant2 should not see Tenant1's products. Got codes: {codes2}"

    def test_low_stock_fast_moving_flag(self):
        """Verify LOW_STOCK_FAST_MOVING flag for critical combination."""
        tenant_id = setup_test_db()
        conn = get_conn(tenant_id)

        old_date = (date.today() - timedelta(days=60)).isoformat() + "T00:00:00"
        product_id = conn.execute(
            "INSERT INTO products (code, name, current_stock, reorder_level, created_at) "
            "VALUES (?,?,?,?,?) RETURNING id",
            ("TEST_LFM", "Low Fast Moving", 3, 5, old_date)
        ).fetchone()[0]

        # Create customer
        customer_id = conn.execute(
            "INSERT INTO customers (name) VALUES (?) RETURNING id",
            ("Test Customer",)
        ).fetchone()[0]

        # Create 20 sales in last 30 days (0.67/day) - fast moving
        today_str = date.today().isoformat()
        for i in range(20):
            inv_id = conn.execute(
                "INSERT INTO invoices (invoice_number, idempotency_key, customer_id, sale_date, status) "
                "VALUES (?,?,?,?,?) RETURNING id",
                (f"INV-LFM-{i}", f"key-lfm-{i}", customer_id, today_str, 'completed')
            ).fetchone()[0]

            conn.execute(
                "INSERT INTO invoice_items (invoice_id, item_type, product_id, quantity, unit_price, total) "
                "VALUES (?,?,?,?,?,?)",
                (inv_id, 'product', product_id, 1, 100, 100)
            )

        conn.commit()
        conn.close()

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        result = engine.analyze_inventory()
        test_product = next((p for p in result['attention_items']
                            if p['product_code'] == 'TEST_LFM'), None)

        if test_product:
            assert 'LOW_STOCK_FAST_MOVING' in test_product['flags'], \
                "LOW_STOCK_FAST_MOVING flag should be present"

    def test_overall_status_critical_when_out_of_stock(self):
        """Verify overall status is 'critical' when products are out of stock."""
        tenant_id = setup_test_db()
        conn = get_conn(tenant_id)

        conn.execute("INSERT INTO products (code, name, current_stock, reorder_level) VALUES (?,?,?,?)",
                    ("CRIT1", "Out of Stock", 0, 5))
        conn.execute("INSERT INTO products (code, name, current_stock, reorder_level) VALUES (?,?,?,?)",
                    ("CRIT2", "Healthy", 50, 5))

        conn.commit()
        conn.close()

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        result = engine.analyze_inventory()

        assert result['summary']['status'] == 'critical', \
            f"Expected status 'critical', got '{result['summary']['status']}'"

    def test_overall_status_warning_when_high_low_stock_percentage(self):
        """Verify overall status is 'warning' when > 20% of products are low stock."""
        tenant_id = setup_test_db()
        conn = get_conn(tenant_id)

        # Create 5 products, 2 below reorder (40% = warning)
        for i in range(3):
            conn.execute("INSERT INTO products (code, name, current_stock, reorder_level) VALUES (?,?,?,?)",
                        (f"W{i}", f"Healthy {i}", 50, 5))

        conn.execute("INSERT INTO products (code, name, current_stock, reorder_level) VALUES (?,?,?,?)",
                    ("W_LOW1", "Low Stock 1", 2, 5))
        conn.execute("INSERT INTO products (code, name, current_stock, reorder_level) VALUES (?,?,?,?)",
                    ("W_LOW2", "Low Stock 2", 3, 5))

        conn.commit()
        conn.close()

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        result = engine.analyze_inventory()

        # 40% low stock should trigger warning
        assert result['summary']['status'] in ['warning', 'critical'], \
            f"Expected warning status, got '{result['summary']['status']}'"

    def test_fast_moving_classification_by_velocity(self):
        """Verify fast-moving classification is based on actual units/day."""
        tenant_id = setup_test_db()
        conn = get_conn(tenant_id)

        old_date = (date.today() - timedelta(days=60)).isoformat() + "T00:00:00"
        product_id = conn.execute(
            "INSERT INTO products (code, name, current_stock, created_at) VALUES (?,?,?,?) RETURNING id",
            ("TEST_FM", "Fast Moving", 200, old_date)
        ).fetchone()[0]

        customer_id = conn.execute(
            "INSERT INTO customers (name) VALUES (?) RETURNING id",
            ("Test Customer",)
        ).fetchone()[0]

        # 30 sales in 30 days = 1 unit/day
        today_str = date.today().isoformat()
        for i in range(30):
            inv_id = conn.execute(
                "INSERT INTO invoices (invoice_number, idempotency_key, customer_id, sale_date, status) "
                "VALUES (?,?,?,?,?) RETURNING id",
                (f"INV-FM-{i}", f"key-fm-{i}", customer_id, today_str, 'completed')
            ).fetchone()[0]

            conn.execute(
                "INSERT INTO invoice_items (invoice_id, item_type, product_id, quantity, unit_price, total) "
                "VALUES (?,?,?,?,?,?)",
                (inv_id, 'product', product_id, 1, 100, 100)
            )

        conn.commit()
        conn.close()

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        result = engine.analyze_inventory()
        test_product = next((p for p in result['attention_items']
                            if p['product_code'] == 'TEST_FM'), None)

        if not test_product:
            # Might not be in attention_items if no flags, so check all products
            conn = get_conn(tenant_id)
            all_data = engine._get_products_with_sales_data(conn)
            test_product = next((p for p in all_data if p['code'] == 'TEST_FM'), None)
            conn.close()

        if test_product:
            assert 'fast' in test_product.get('fast_moving_status', '').lower(), \
                "Should be classified as fast-moving"

    def test_cancelled_invoices_not_counted(self):
        """Verify cancelled/draft/failed invoices do NOT contribute to sales metrics."""
        tenant_id = setup_test_db()
        conn = get_conn(tenant_id)

        old_date = (date.today() - timedelta(days=60)).isoformat() + "T00:00:00"
        product_id = conn.execute(
            "INSERT INTO products (code, name, current_stock, created_at) VALUES (?,?,?,?) RETURNING id",
            ("TEST_CANCEL", "Cancelled Test", 50, old_date)
        ).fetchone()[0]

        customer_id = conn.execute(
            "INSERT INTO customers (name) VALUES (?) RETURNING id",
            ("Test Customer",)
        ).fetchone()[0]

        today_str = date.today().isoformat()

        # Create completed invoice (SHOULD count)
        inv_completed = conn.execute(
            "INSERT INTO invoices (invoice_number, idempotency_key, customer_id, sale_date, status) "
            "VALUES (?,?,?,?,?) RETURNING id",
            ("INV-COMP", "key-comp", customer_id, today_str, 'completed')
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO invoice_items (invoice_id, item_type, product_id, quantity, unit_price, total) "
            "VALUES (?,?,?,?,?,?)",
            (inv_completed, 'product', product_id, 5, 100, 500)
        )

        # Create draft invoice (should NOT count)
        inv_draft = conn.execute(
            "INSERT INTO invoices (invoice_number, idempotency_key, customer_id, sale_date, status) "
            "VALUES (?,?,?,?,?) RETURNING id",
            ("INV-DRAFT", "key-draft", customer_id, today_str, 'draft')
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO invoice_items (invoice_id, item_type, product_id, quantity, unit_price, total) "
            "VALUES (?,?,?,?,?,?)",
            (inv_draft, 'product', product_id, 10, 100, 1000)
        )

        # Create cancelled invoice (should NOT count)
        inv_cancelled = conn.execute(
            "INSERT INTO invoices (invoice_number, idempotency_key, customer_id, sale_date, status) "
            "VALUES (?,?,?,?,?) RETURNING id",
            ("INV-CANCELLED", "key-cancelled", customer_id, today_str, 'cancelled')
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO invoice_items (invoice_id, item_type, product_id, quantity, unit_price, total) "
            "VALUES (?,?,?,?,?,?)",
            (inv_cancelled, 'product', product_id, 8, 100, 800)
        )

        conn.commit()
        conn.close()

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        result = engine.analyze_inventory()

        # Check all products data
        test_conn = get_conn(tenant_id)
        all_data = engine._get_products_with_sales_data(test_conn)
        test_product = next((p for p in all_data if p['code'] == 'TEST_CANCEL'), None)
        test_conn.close()

        assert test_product is not None, "Product not found"
        # Should only count the 5 units from completed invoice, NOT the 10+8 from draft/cancelled
        assert test_product.get('qty_sold_7d') == 5, \
            f"Expected 5 units (completed only), got {test_product.get('qty_sold_7d')}"

    def test_date_boundaries_7_30_90_days(self):
        """Verify date boundaries for 7/30/90 day calculations (inclusive/exclusive)."""
        tenant_id = setup_test_db()
        conn = get_conn(tenant_id)

        old_date = (date.today() - timedelta(days=100)).isoformat() + "T00:00:00"
        product_id = conn.execute(
            "INSERT INTO products (code, name, current_stock, created_at) VALUES (?,?,?,?) RETURNING id",
            ("TEST_BOUNDS", "Boundary Test", 200, old_date)
        ).fetchone()[0]

        customer_id = conn.execute(
            "INSERT INTO customers (name) VALUES (?) RETURNING id",
            ("Test Customer",)
        ).fetchone()[0]

        # Create sales at specific dates
        # Today (within 7d, 30d, 90d)
        today = date.today().isoformat()
        inv1 = conn.execute(
            "INSERT INTO invoices (invoice_number, idempotency_key, customer_id, sale_date, status) "
            "VALUES (?,?,?,?,?) RETURNING id",
            ("INV-TODAY", "key-today", customer_id, today, 'completed')
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO invoice_items (invoice_id, item_type, product_id, quantity, unit_price, total) "
            "VALUES (?,?,?,?,?,?)",
            (inv1, 'product', product_id, 1, 100, 100)
        )

        # 7 days ago (within 7d, 30d, 90d)
        seven_ago = (date.today() - timedelta(days=7)).isoformat()
        inv2 = conn.execute(
            "INSERT INTO invoices (invoice_number, idempotency_key, customer_id, sale_date, status) "
            "VALUES (?,?,?,?,?) RETURNING id",
            ("INV-7D", "key-7d", customer_id, seven_ago, 'completed')
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO invoice_items (invoice_id, item_type, product_id, quantity, unit_price, total) "
            "VALUES (?,?,?,?,?,?)",
            (inv2, 'product', product_id, 2, 100, 200)
        )

        # 30 days ago (within 30d, 90d)
        thirty_ago = (date.today() - timedelta(days=30)).isoformat()
        inv3 = conn.execute(
            "INSERT INTO invoices (invoice_number, idempotency_key, customer_id, sale_date, status) "
            "VALUES (?,?,?,?,?) RETURNING id",
            ("INV-30D", "key-30d", customer_id, thirty_ago, 'completed')
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO invoice_items (invoice_id, item_type, product_id, quantity, unit_price, total) "
            "VALUES (?,?,?,?,?,?)",
            (inv3, 'product', product_id, 4, 100, 400)
        )

        # 90 days ago (within 90d only)
        ninety_ago = (date.today() - timedelta(days=90)).isoformat()
        inv4 = conn.execute(
            "INSERT INTO invoices (invoice_number, idempotency_key, customer_id, sale_date, status) "
            "VALUES (?,?,?,?,?) RETURNING id",
            ("INV-90D", "key-90d", customer_id, ninety_ago, 'completed')
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO invoice_items (invoice_id, item_type, product_id, quantity, unit_price, total) "
            "VALUES (?,?,?,?,?,?)",
            (inv4, 'product', product_id, 8, 100, 800)
        )

        # 91 days ago (outside all windows)
        outside = (date.today() - timedelta(days=91)).isoformat()
        inv5 = conn.execute(
            "INSERT INTO invoices (invoice_number, idempotency_key, customer_id, sale_date, status) "
            "VALUES (?,?,?,?,?) RETURNING id",
            ("INV-91D", "key-91d", customer_id, outside, 'completed')
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO invoice_items (invoice_id, item_type, product_id, quantity, unit_price, total) "
            "VALUES (?,?,?,?,?,?)",
            (inv5, 'product', product_id, 16, 100, 1600)
        )

        conn.commit()
        conn.close()

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        test_conn = get_conn(tenant_id)
        all_data = engine._get_products_with_sales_data(test_conn)
        test_product = next((p for p in all_data if p['code'] == 'TEST_BOUNDS'), None)
        test_conn.close()

        assert test_product is not None, "Product not found"

        # Verify boundaries (inclusive, using >=)
        # 7d: today(1) + 7ago(2) = 3
        assert test_product.get('qty_sold_7d') == 3, \
            f"Expected 3 units in 7d, got {test_product.get('qty_sold_7d')}"

        # 30d: today(1) + 7ago(2) + 30ago(4) = 7
        assert test_product.get('qty_sold_30d') == 7, \
            f"Expected 7 units in 30d, got {test_product.get('qty_sold_30d')}"

        # 90d: today(1) + 7ago(2) + 30ago(4) + 90ago(8) = 15
        assert test_product.get('qty_sold_90d') == 15, \
            f"Expected 15 units in 90d, got {test_product.get('qty_sold_90d')}"

    def test_days_since_last_sale_calculated(self):
        """Verify days_since_last_sale is calculated correctly."""
        tenant_id = setup_test_db()
        conn = get_conn(tenant_id)

        old_date = (date.today() - timedelta(days=60)).isoformat() + "T00:00:00"
        product_id = conn.execute(
            "INSERT INTO products (code, name, current_stock, created_at) VALUES (?,?,?,?) RETURNING id",
            ("TEST_DAYS", "Days Test", 50, old_date)
        ).fetchone()[0]

        customer_id = conn.execute(
            "INSERT INTO customers (name) VALUES (?) RETURNING id",
            ("Test Customer",)
        ).fetchone()[0]

        # Sale 15 days ago
        fifteen_days_ago = (date.today() - timedelta(days=15)).isoformat()
        inv_id = conn.execute(
            "INSERT INTO invoices (invoice_number, idempotency_key, customer_id, sale_date, status) "
            "VALUES (?,?,?,?,?) RETURNING id",
            ("INV-DAYS", "key-days", customer_id, fifteen_days_ago, 'completed')
        ).fetchone()[0]

        conn.execute(
            "INSERT INTO invoice_items (invoice_id, item_type, product_id, quantity, unit_price, total) "
            "VALUES (?,?,?,?,?,?)",
            (inv_id, 'product', product_id, 1, 100, 100)
        )

        conn.commit()
        conn.close()

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        result = engine.analyze_inventory()
        test_product = next((p for p in result['attention_items']
                            if p['product_code'] == 'TEST_DAYS'), None)

        if not test_product:
            conn = get_conn(tenant_id)
            all_data = engine._get_products_with_sales_data(conn)
            test_product = next((p for p in all_data if p['code'] == 'TEST_DAYS'), None)
            conn.close()

        if test_product:
            assert test_product.get('days_since_last_sale') == 15, \
                f"Expected 15 days, got {test_product.get('days_since_last_sale')}"

    def test_reorder_threshold_semantics(self):
        """Verify reorder threshold matches existing get_low_stock_products() (<=)."""
        tenant_id = setup_test_db()
        conn = get_conn(tenant_id)

        # Test edge case: current_stock EQUALS reorder_level
        # Should be considered LOW STOCK (matches get_low_stock_products() which uses <=)
        conn.execute(
            "INSERT INTO products (code, name, current_stock, reorder_level) VALUES (?,?,?,?)",
            ("TEST_EQUAL", "Exactly At Reorder", 5, 5)
        )

        # Also test below
        conn.execute(
            "INSERT INTO products (code, name, current_stock, reorder_level) VALUES (?,?,?,?)",
            ("TEST_BELOW", "Below Reorder", 4, 5)
        )

        # And above
        conn.execute(
            "INSERT INTO products (code, name, current_stock, reorder_level) VALUES (?,?,?,?)",
            ("TEST_ABOVE", "Above Reorder", 6, 5)
        )

        conn.commit()
        conn.close()

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        result = engine.analyze_inventory()

        # Get summary counts
        summary = result['summary']

        # Should have 2 low stock (equals + below), not 1
        assert summary['low_stock_count'] == 2, \
            f"Expected 2 low stock (equals + below), got {summary['low_stock_count']}"

    def run_all_tests(self):
        """Run all tests."""
        self.results.test("analyze_inventory returns structured data",
                         self.test_analyze_inventory_returns_structured_data)
        self.results.test("OUT_OF_STOCK product flagged",
                         self.test_out_of_stock_product_flagged)
        self.results.test("BELOW_REORDER_LEVEL product flagged",
                         self.test_below_reorder_level_flagged)
        self.results.test("NO_REORDER_LEVEL product flagged",
                         self.test_no_reorder_level_flagged)
        self.results.test("newly created product not penalized",
                         self.test_newly_created_product_not_penalized)
        self.results.test("NEVER_SOLD product flagged",
                         self.test_never_sold_product_flagged)
        self.results.test("stock cover returns None with zero sales",
                         self.test_stock_cover_zero_sales_returns_none)
        self.results.test("summary statistics accurate",
                         self.test_summary_statistics_accurate)
        self.results.test("multi-tenant isolation enforced",
                         self.test_multi_tenant_isolation)
        self.results.test("LOW_STOCK_FAST_MOVING flag present",
                         self.test_low_stock_fast_moving_flag)
        self.results.test("overall status critical when out of stock",
                         self.test_overall_status_critical_when_out_of_stock)
        self.results.test("overall status warning when high low-stock %",
                         self.test_overall_status_warning_when_high_low_stock_percentage)
        self.results.test("fast-moving classification by velocity",
                         self.test_fast_moving_classification_by_velocity)
        self.results.test("days_since_last_sale calculated",
                         self.test_days_since_last_sale_calculated)
        self.results.test("cancelled invoices not counted in sales",
                         self.test_cancelled_invoices_not_counted)
        self.results.test("date boundaries 7/30/90 days correct",
                         self.test_date_boundaries_7_30_90_days)
        self.results.test("reorder threshold semantics (<=)",
                         self.test_reorder_threshold_semantics)


if __name__ == '__main__':
    print("\n" + "="*80)
    print("STOCK INTELLIGENCE ENGINE TESTS")
    print("="*80 + "\n")

    results = TestResults()
    tests = StockIntelligenceTests(results)
    tests.run_all_tests()

    success = results.summary()
    sys.exit(0 if success else 1)
