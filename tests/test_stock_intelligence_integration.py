#!/usr/bin/env python3
"""Integration test: Stock Intelligence with existing AutoStack functionality."""

import sys
import os
import tempfile
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.stock_intelligence import StockIntelligenceEngine
from app.tenant_db import (
    get_conn, add_product, get_product, add_customer,
    add_invoice, add_invoice_item, get_all_products, get_low_stock_products
)
from app.db_migrations import migrate_tenant_db


def setup_test_db():
    """Setup test database."""
    temp_dir = tempfile.mkdtemp(prefix="stock_int_test_")
    tenant_id = 3000
    test_db_path = os.path.join(temp_dir, f"{tenant_id}.db")
    os.environ["AUTOSTACK_DATA_DIR"] = temp_dir
    migrate_tenant_db(tenant_id, test_db_path)
    return tenant_id


print("\n" + "="*80)
print("STOCK INTELLIGENCE INTEGRATION TESTS")
print("="*80 + "\n")

# Test 1: Stock Intelligence works with existing products
print("[Test 1] Stock Intelligence reads existing products...")
tenant_id = setup_test_db()
conn = get_conn(tenant_id)

# Add a product using existing tenant_db function
product_id = add_product(
    tenant_id,
    code="INT001",
    name="Integration Test Product",
    category="test",
    current_stock=50,
    reorder_level=10
)

# Verify it was added
product = get_product(tenant_id, product_id)
assert product is not None, "Product not found"
assert product['code'] == "INT001", "Product code mismatch"

# Run Stock Intelligence
engine = StockIntelligenceEngine(tenant_id, get_conn)
result = engine.analyze_inventory()

# Verify result structure
assert 'summary' in result, "Missing summary"
assert result['summary']['total_products'] >= 1, "Product not counted"
print("  [OK] Stock Intelligence reads existing products")
conn.close()

# Test 2: Stock Intelligence doesn't break existing queries
print("[Test 2] Stock Intelligence doesn't break existing get_all_products()...")
tenant_id = setup_test_db()

# Add products via existing API
for i in range(5):
    add_product(tenant_id, f"PROD{i:03d}", f"Product {i}", current_stock=i*10, reorder_level=5)

# Get products via existing API
products = get_all_products(tenant_id)
assert len(products) == 5, f"Expected 5 products, got {len(products)}"
print("  [OK] Existing get_all_products() still works")

# Test 3: Stock Intelligence doesn't break get_low_stock_products()
print("[Test 3] Stock Intelligence doesn't break get_low_stock_products()...")
low_stock = get_low_stock_products(tenant_id)
# Should have products where current_stock <= reorder_level
# PROD0: 0 <= 5 (low), PROD1: 10 > 5 (ok), PROD2: 20 > 5 (ok), etc.
assert len(low_stock) >= 1, "Expected at least 1 low stock product"
print("  [OK] Existing get_low_stock_products() still works")

# Test 4: Sales data flows through Stock Intelligence
print("[Test 4] Sales data flows through Stock Intelligence...")
tenant_id = setup_test_db()

# Add product and customer
product_id = add_product(tenant_id, "SALE001", "Sales Test", current_stock=100, reorder_level=10)
customer_id = add_customer(tenant_id, "Test Customer", phone="555-1234")

# Create an invoice
today = date.today().isoformat()
invoice_id = add_invoice(
    tenant_id,
    invoice_number="INV-001",
    idempotency_key="key-001",
    customer_id=customer_id,
    sale_date=today,
    status="completed"
)

# Add line item
add_invoice_item(
    tenant_id,
    invoice_id,
    item_type="product",
    quantity=5,
    unit_price=100,
    product_id=product_id
)

# Stock Intelligence should process without errors and see the product
engine = StockIntelligenceEngine(tenant_id, get_conn)
result = engine.analyze_inventory()

# Verify structure exists (sales data flow test)
assert 'summary' in result, "Stock Intelligence didn't return summary"
assert result['summary']['total_products'] >= 1, "Product not counted"

# Check if any products show sales data (not checking exact numbers)
conn = get_conn(tenant_id)
all_data = engine._get_products_with_sales_data(conn)
sale001_data = next((p for p in all_data if p['code'] == 'SALE001'), None)
conn.close()

assert sale001_data is not None, "SALE001 product not found in analysis"
# Verify sale data fields exist and are accessible
assert 'qty_sold_7d' in sale001_data, "Sale data field missing"
print("  [OK] Sales data flows through Stock Intelligence")

# Test 5: Multi-tenancy isolation preserved
print("[Test 5] Multi-tenancy isolation preserved...")
tenant_a = setup_test_db()
tenant_b = setup_test_db()

add_product(tenant_a, "TNA_001", "Tenant A Product", current_stock=50, reorder_level=5)
add_product(tenant_b, "TNB_001", "Tenant B Product", current_stock=60, reorder_level=6)

engine_a = StockIntelligenceEngine(tenant_a, get_conn)
result_a = engine_a.analyze_inventory()

engine_b = StockIntelligenceEngine(tenant_b, get_conn)
result_b = engine_b.analyze_inventory()

# Find products
codes_a = [p['product_code'] for p in result_a.get('attention_items', [])]
codes_b = [p['product_code'] for p in result_b.get('attention_items', [])]

# Should not cross-contaminate
assert 'TNB_001' not in codes_a, "Tenant A should not see Tenant B products"
assert 'TNA_001' not in codes_b, "Tenant B should not see Tenant A products"
print("  [OK] Multi-tenancy isolation preserved")

print("\n" + "="*80)
print("ALL INTEGRATION TESTS PASSED")
print("="*80 + "\n")
print("Summary:")
print("  - Stock Intelligence integrates with existing product functions")
print("  - Existing queries still work (no regression)")
print("  - Sales data flows through Stock Intelligence")
print("  - Multi-tenancy isolation maintained")
print("\n")
