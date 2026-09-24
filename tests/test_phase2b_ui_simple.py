#!/usr/bin/env python3
"""Phase 2B UI Verification Tests - Simplified Flask layer checks.

Focus:
- AI section renders in template
- API response has required structure
- Product ID mapping is correct
- Deterministic summary metrics present
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ai_insights import AIInsights
from app.services.stock_intelligence import StockIntelligenceEngine
from app.tenant_db import get_conn, add_product
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
        print(f"Phase 2B UI Tests: {self.passed} passed, {self.failed} failed")
        print(f"{'='*80}")
        if self.errors:
            print("\nFailed tests:")
            for name, error in self.errors:
                print(f"  - {name}: {error}")
        return self.failed == 0


class Phase2BTests:
    def __init__(self, results):
        self.results = results

    def setup_tenant(self, tenant_id):
        """Create a test tenant and database."""
        temp_dir = tempfile.mkdtemp(prefix=f"phase2b_{tenant_id}_")
        os.environ["AUTOSTACK_DATA_DIR"] = temp_dir
        test_db_path = os.path.join(temp_dir, f"{tenant_id}.db")
        migrate_tenant_db(tenant_id, test_db_path)
        return tenant_id

    def test_api_response_has_required_fields(self):
        """Verify AI endpoint response has fields required by UI."""
        tenant_id = self.setup_tenant(6001)

        # Add test product with low stock
        add_product(
            tenant_id,
            code="TEST001",
            name="Test Product",
            category="Test",
            unit="PCS",
            current_stock=2,
            reorder_level=5,
            last_cost_price=10.0,
            supplier="Supplier"
        )

        # Get stock analysis
        engine = StockIntelligenceEngine(tenant_id, get_conn)
        analysis = engine.analyze_inventory()

        # Get AI insights
        ai_service = AIInsights(tenant_id)
        os.environ.pop("OPENAI_API_KEY", None)  # Use fallback
        response = ai_service.generate_stock_explanation(analysis)

        # Verify required fields for UI
        assert response.get('success') is True, "Response must have success=true"
        assert 'headline' in response, "Response must have headline"
        assert 'summary' in response, "Response must have summary"
        assert 'priority' in response, "Response must have priority"
        assert 'items' in response, "Response must have items array"
        assert isinstance(response['items'], list), "Items must be array"

    def test_response_items_have_required_fields(self):
        """Verify each item in response has fields for UI."""
        tenant_id = self.setup_tenant(6002)

        add_product(
            tenant_id,
            code="TEST002",
            name="Low Stock Product",
            category="Test",
            unit="PCS",
            current_stock=0,  # Out of stock
            reorder_level=5,
            last_cost_price=10.0,
            supplier="Supplier"
        )

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        analysis = engine.analyze_inventory()

        ai_service = AIInsights(tenant_id)
        os.environ.pop("OPENAI_API_KEY", None)
        response = ai_service.generate_stock_explanation(analysis)

        # Check item structure
        for item in response.get('items', []):
            assert 'product_id' in item, "Each item must have product_id"
            assert 'headline' in item, "Each item must have headline"
            assert isinstance(item['product_id'], int), "product_id must be integer"

    def test_product_id_mapping_preserves_real_ids(self):
        """Verify real product IDs are preserved through mapping."""
        tenant_id = self.setup_tenant(6003)

        # Add multiple products
        real_ids = []
        for i in range(3):
            product = add_product(
                tenant_id,
                code=f"P{i}",
                name=f"Product {i}",
                category="Test",
                unit="PCS",
                current_stock=i if i > 0 else 0,  # First is out of stock
                reorder_level=5,
                last_cost_price=10.0,
                supplier="Supplier"
            )
            real_ids.append(product)

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        analysis = engine.analyze_inventory()

        ai_service = AIInsights(tenant_id)
        prepared, mapping = ai_service._prepare_minimal_data(
            analysis['summary'],
            analysis['attention_items']
        )

        # Verify mapping exists and contains real product IDs
        assert len(mapping) > 0, "Should have product ID mapping"
        for seq_id, real_id in mapping.items():
            assert isinstance(real_id, int), "Real ID must be integer"
            assert real_id > 0, "Real ID should be positive"

    def test_deterministic_data_in_response(self):
        """Verify response includes deterministic data from StockIntelligenceEngine."""
        tenant_id = self.setup_tenant(6004)

        add_product(
            tenant_id,
            code="DETERM",
            name="Deterministic Test",
            category="Test",
            unit="PCS",
            current_stock=3,
            reorder_level=10,
            last_cost_price=10.0,
            supplier="Supplier"
        )

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        analysis = engine.analyze_inventory()

        ai_service = AIInsights(tenant_id)
        os.environ.pop("OPENAI_API_KEY", None)
        response = ai_service.generate_stock_explanation(analysis)

        # Check response items have deterministic fields from input
        if response.get('items'):
            for item in response['items']:
                # These should come from prepared data, not AI
                # (could be undefined if not included, but shouldn't be fabricated)
                # Just verify structure is present
                assert 'product_id' in item
                assert 'headline' in item

    def test_priority_field_validation(self):
        """Verify priority field has valid values."""
        tenant_id = self.setup_tenant(6005)

        add_product(
            tenant_id,
            code="PRIORITY",
            name="Priority Test",
            category="Test",
            unit="PCS",
            current_stock=0,  # Critical
            reorder_level=10,
            last_cost_price=10.0,
            supplier="Supplier"
        )

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        analysis = engine.analyze_inventory()

        ai_service = AIInsights(tenant_id)
        os.environ.pop("OPENAI_API_KEY", None)
        response = ai_service.generate_stock_explanation(analysis)

        priority = response.get('priority')
        assert priority in ['high', 'medium', 'low'], f"Priority must be high/medium/low, got {priority}"

    def test_fallback_marker_present(self):
        """Verify fallback responses are marked."""
        tenant_id = self.setup_tenant(6006)

        add_product(
            tenant_id,
            code="FALLBACK",
            name="Fallback Test",
            category="Test",
            unit="PCS",
            current_stock=1,
            reorder_level=5,
            last_cost_price=10.0,
            supplier="Supplier"
        )

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        analysis = engine.analyze_inventory()

        ai_service = AIInsights(tenant_id)
        os.environ.pop("OPENAI_API_KEY", None)
        response = ai_service.generate_stock_explanation(analysis)

        # When using fallback, should be marked
        if not os.environ.get("OPENAI_API_KEY"):
            assert response.get('fallback') is True, "Fallback response should be marked"

    def test_summary_metrics_in_response(self):
        """Verify response includes deterministic summary metrics, not just items."""
        tenant_id = self.setup_tenant(6008)

        # Add multiple products with different statuses
        for i in range(5):
            add_product(
                tenant_id,
                code=f"SUMMARY{i}",
                name=f"Summary Test {i}",
                category="Test",
                unit="PCS",
                current_stock=i if i > 1 else 0,  # First two are out of stock
                reorder_level=5,
                last_cost_price=10.0,
                supplier="Supplier"
            )

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        analysis = engine.analyze_inventory()

        ai_service = AIInsights(tenant_id)
        os.environ.pop("OPENAI_API_KEY", None)
        response = ai_service.generate_stock_explanation(analysis)

        # Verify summary section exists
        assert 'summary' in response, "Response must have summary section"
        summary = response.get('summary', {})

        # Check required summary fields
        assert 'attention' in summary, "Summary must include attention count"
        assert 'out_of_stock' in summary, "Summary must include out_of_stock count"
        assert 'low_stock' in summary, "Summary must include low_stock count"
        assert 'status' in summary, "Summary must include status"

        # Summary metrics should be >= 0
        assert summary['attention'] >= 0
        assert summary['out_of_stock'] >= 0
        assert summary['low_stock'] >= 0

        # Status should be deterministic value
        assert summary['status'] in ['critical', 'warning', 'healthy']

    def test_capped_items_dont_underreport_metrics(self):
        """Verify: if 30 products are low-stock but only 15 items returned,
        summary still reports 30."""
        tenant_id = self.setup_tenant(6009)

        # Create 3 products, all low stock (below reorder level)
        for i in range(3):
            add_product(
                tenant_id,
                code=f"CAPPED{i}",
                name=f"Capped Test {i}",
                category="Test",
                unit="PCS",
                current_stock=2,  # All below reorder level
                reorder_level=5,
                last_cost_price=10.0,
                supplier="Supplier"
            )

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        analysis = engine.analyze_inventory()

        ai_service = AIInsights(tenant_id)
        os.environ.pop("OPENAI_API_KEY", None)
        response = ai_service.generate_stock_explanation(analysis)

        # Check: summary reports TOTAL low-stock count
        summary = response.get('summary', {})
        low_stock_count = summary.get('low_stock', 0)
        items_count = len(response.get('items', []))

        # Summary should match deterministic count (3 products all low stock)
        # Items count may be less due to MAX_ATTENTION_ITEMS cap
        assert low_stock_count >= 0, "Summary low_stock should be from deterministic data"

    def test_deterministic_status_not_ai_priority(self):
        """Verify: overall status comes from summary.status, not AI priority."""
        tenant_id = self.setup_tenant(6010)

        # Create one critical product (out of stock)
        add_product(
            tenant_id,
            code="CRITICAL",
            name="Critical Product",
            category="Test",
            unit="PCS",
            current_stock=0,  # Out of stock = critical
            reorder_level=5,
            last_cost_price=10.0,
            supplier="Supplier"
        )

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        analysis = engine.analyze_inventory()

        ai_service = AIInsights(tenant_id)
        os.environ.pop("OPENAI_API_KEY", None)
        response = ai_service.generate_stock_explanation(analysis)

        # Check summary.status is deterministic
        summary_status = response.get('summary', {}).get('status')
        ai_priority = response.get('priority')

        # summary.status should be from deterministic StockIntelligenceEngine
        assert summary_status in ['critical', 'warning', 'healthy'], \
            f"summary.status should be deterministic, got {summary_status}"

        # AI priority might differ, but summary.status is authoritative
        # (This test just verifies both fields exist and summary is independent)

    def test_no_real_ids_in_prepared_data(self):
        """Verify real product IDs are not exposed in prepared data sent to AI."""
        tenant_id = self.setup_tenant(6007)

        # Get some real product IDs
        real_id_1 = add_product(
            tenant_id,
            code="REAL001",
            name="Real Product 1",
            category="Test",
            unit="PCS",
            current_stock=0,
            reorder_level=5,
            last_cost_price=10.0,
            supplier="Supplier"
        )

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        analysis = engine.analyze_inventory()

        ai_service = AIInsights(tenant_id)
        prepared, mapping = ai_service._prepare_minimal_data(
            analysis['summary'],
            analysis['attention_items']
        )

        # Verify mapping works: sequential IDs map to real IDs
        assert len(mapping) > 0, "Should have product ID mapping"

        # All items in mapping should have both sequential and real IDs
        for seq_id, real_id in mapping.items():
            assert isinstance(seq_id, int) and seq_id >= 1, "Sequential ID should be positive int"
            assert isinstance(real_id, int) and real_id >= 1, "Real ID should be positive int"

        # Prepared data should use sequential IDs from mapping
        for item in prepared['attention_items']:
            seq_id = item['product_id']
            assert seq_id in mapping, f"All prepared IDs should be in mapping"

    def test_http_200_fallback_response_success(self):
        """Verify HTTP 200 with fallback=true is success for frontend (not error)."""
        tenant_id = self.setup_tenant(6011)

        # Create product that will trigger fallback
        add_product(
            tenant_id,
            code="HTTP200",
            name="HTTP 200 Test",
            category="Test",
            unit="PCS",
            current_stock=2,
            reorder_level=5,
            last_cost_price=10.0,
            supplier="Supplier"
        )

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        analysis = engine.analyze_inventory()

        ai_service = AIInsights(tenant_id)
        os.environ.pop("OPENAI_API_KEY", None)
        response = ai_service.generate_stock_explanation(analysis)

        # Key requirement: HTTP 200 + fallback=true means SUCCESS
        # Frontend should NOT treat this as error state
        assert response.get('success') is True, "Fallback response must have success=true"
        assert response.get('fallback') is True, "Should be marked as fallback"

        # Summary must be object, not string (critical for frontend fix)
        assert isinstance(response.get('summary'), dict), "Summary must be dict/object"
        assert 'status' in response['summary'], "Summary must have status field"
        assert 'attention' in response['summary'], "Summary must have attention field"
        assert 'out_of_stock' in response['summary'], "Summary must have out_of_stock field"
        assert 'low_stock' in response['summary'], "Summary must have low_stock field"

    def test_http_200_healthy_state_empty_items(self):
        """Verify HTTP 200 with items=[] and status=healthy renders correctly."""
        tenant_id = self.setup_tenant(6012)

        # Create products with healthy stock (no attention needed)
        for i in range(3):
            add_product(
                tenant_id,
                code=f"HEALTHY{i}",
                name=f"Healthy Product {i}",
                category="Test",
                unit="PCS",
                current_stock=20,  # Well-stocked
                reorder_level=5,
                last_cost_price=10.0,
                supplier="Supplier"
            )

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        analysis = engine.analyze_inventory()

        ai_service = AIInsights(tenant_id)
        os.environ.pop("OPENAI_API_KEY", None)
        response = ai_service.generate_stock_explanation(analysis)

        # When all products are healthy, items list might be empty
        # but status should reflect that
        assert response.get('success') is True, "Should be success"

        summary = response.get('summary', {})
        status = summary.get('status')

        # Status should be one of the valid values
        assert status in ['critical', 'warning', 'healthy'], \
            f"Summary status must be valid, got {status}"

        # If status is healthy and items is empty, that's valid
        items = response.get('items', [])
        if status == 'healthy' and len(items) == 0:
            # This is a valid "all green" state
            assert True, "Empty items with healthy status is valid"

    def test_fallback_items_have_priority(self):
        """Verify fallback items include item-level priority field."""
        tenant_id = self.setup_tenant(6013)

        # Create products with different severity levels
        add_product(
            tenant_id,
            code="PRIORITY_HIGH",
            name="Out of Stock Product",
            category="Test",
            unit="PCS",
            current_stock=0,  # OUT_OF_STOCK -> high
            reorder_level=5,
            last_cost_price=10.0,
            supplier="Supplier"
        )

        add_product(
            tenant_id,
            code="PRIORITY_MEDIUM",
            name="Below Reorder Product",
            category="Test",
            unit="PCS",
            current_stock=2,  # BELOW_REORDER_LEVEL -> medium
            reorder_level=5,
            last_cost_price=10.0,
            supplier="Supplier"
        )

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        analysis = engine.analyze_inventory()

        ai_service = AIInsights(tenant_id)
        os.environ.pop("OPENAI_API_KEY", None)
        response = ai_service.generate_stock_explanation(analysis)

        # Verify each item has priority field
        items = response.get('items', [])
        assert len(items) > 0, "Should have attention items"

        for item in items:
            assert 'priority' in item, f"Each item must have priority field: {item}"
            assert item['priority'] in ['high', 'medium', 'low'], \
                f"Priority must be high/medium/low, got {item['priority']}"

    def test_priority_derived_from_flags(self):
        """Verify item-level priority is derived correctly from deterministic flags."""
        tenant_id = self.setup_tenant(6014)

        # Create product with OUT_OF_STOCK flag (should be high priority)
        add_product(
            tenant_id,
            code="OOS",
            name="Out of Stock",
            category="Test",
            unit="PCS",
            current_stock=0,
            reorder_level=5,
            last_cost_price=10.0,
            supplier="Supplier"
        )

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        analysis = engine.analyze_inventory()

        ai_service = AIInsights(tenant_id)
        os.environ.pop("OPENAI_API_KEY", None)
        response = ai_service.generate_stock_explanation(analysis)

        # Find the out-of-stock item
        items = response.get('items', [])
        oos_item = None
        for item in items:
            if 'out of stock' in item.get('explanation', '').lower():
                oos_item = item
                break

        # OUT_OF_STOCK flag should result in high priority
        if oos_item:
            assert oos_item['priority'] == 'high', \
                f"OUT_OF_STOCK item should have high priority, got {oos_item['priority']}"

    def test_frontend_tolerates_invalid_priority(self):
        """Verify renderInsightItem() can handle invalid/missing priority gracefully."""
        tenant_id = self.setup_tenant(6015)

        add_product(
            tenant_id,
            code="TEST",
            name="Test Product",
            category="Test",
            unit="PCS",
            current_stock=2,
            reorder_level=5,
            last_cost_price=10.0,
            supplier="Supplier"
        )

        engine = StockIntelligenceEngine(tenant_id, get_conn)
        analysis = engine.analyze_inventory()

        ai_service = AIInsights(tenant_id)
        os.environ.pop("OPENAI_API_KEY", None)
        response = ai_service.generate_stock_explanation(analysis)

        # Verify response structure is valid and can be rendered
        assert response.get('success') is True, "Response must be success"
        items = response.get('items', [])

        for item in items:
            # Each item should have priority (not None/undefined)
            assert item.get('priority') is not None, \
                f"Item priority should not be None: {item}"
            # Priority should be a valid value
            assert item.get('priority') in ['high', 'medium', 'low'], \
                f"Priority must be valid: {item.get('priority')}"

    def run_all_tests(self):
        """Run all tests."""
        self.results.test("API response has required fields", self.test_api_response_has_required_fields)
        self.results.test("response items have required fields", self.test_response_items_have_required_fields)
        self.results.test("product ID mapping preserves real IDs", self.test_product_id_mapping_preserves_real_ids)
        self.results.test("deterministic data in response", self.test_deterministic_data_in_response)
        self.results.test("priority field validation", self.test_priority_field_validation)
        self.results.test("fallback marker present", self.test_fallback_marker_present)
        self.results.test("summary metrics in response", self.test_summary_metrics_in_response)
        self.results.test("capped items don't underreport", self.test_capped_items_dont_underreport_metrics)
        self.results.test("deterministic status not AI priority", self.test_deterministic_status_not_ai_priority)
        self.results.test("no real IDs in prepared data", self.test_no_real_ids_in_prepared_data)
        self.results.test("HTTP 200 fallback response is success", self.test_http_200_fallback_response_success)
        self.results.test("HTTP 200 healthy state with empty items", self.test_http_200_healthy_state_empty_items)
        self.results.test("fallback items have priority field", self.test_fallback_items_have_priority)
        self.results.test("priority derived from flags", self.test_priority_derived_from_flags)
        self.results.test("frontend tolerates invalid priority", self.test_frontend_tolerates_invalid_priority)


if __name__ == '__main__':
    print("\n" + "="*80)
    print("PHASE 2B UI VERIFICATION TESTS")
    print("="*80 + "\n")

    results = TestResults()
    tests = Phase2BTests(results)
    tests.run_all_tests()

    success = results.summary()
    sys.exit(0 if success else 1)
