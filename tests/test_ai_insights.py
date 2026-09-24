#!/usr/bin/env python3
"""Tests for AI Insights Service with OpenAI Responses API.

Tests:
- gpt-5.4-nano default model
- OPENAI_MODEL override
- Product ID validation
- Fallback behavior
- Cache isolation
- Tenant isolation
"""

import sys
import os
import tempfile
from unittest.mock import patch
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ai_insights import AIInsights, StockInsightResponse, StockInsightItem
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
        print(f"AI Insights Tests: {self.passed} passed, {self.failed} failed")
        print(f"{'='*80}")
        if self.errors:
            print("\nFailed tests:")
            for name, error in self.errors:
                print(f"  - {name}: {error}")
        return self.failed == 0


_test_counter = 0

def setup_test_db():
    global _test_counter
    _test_counter += 1
    temp_dir = tempfile.mkdtemp(prefix=f"ai_test_{_test_counter}_")
    tenant_id = 4000 + _test_counter
    test_db_path = os.path.join(temp_dir, f"{tenant_id}.db")
    os.environ["AUTOSTACK_DATA_DIR"] = temp_dir
    migrate_tenant_db(tenant_id, test_db_path)
    return tenant_id


class AIInsightsTests:
    def __init__(self, results: TestResults):
        self.results = results

    def test_gpt_5_4_nano_default(self):
        """Verify gpt-5.4-nano is the default model."""
        tenant_id = setup_test_db()
        os.environ.pop("OPENAI_MODEL", None)

        ai_service = AIInsights(tenant_id)
        assert ai_service.DEFAULT_MODEL == "gpt-5.4-nano", "Default should be gpt-5.4-nano"
        assert ai_service.model == "gpt-5.4-nano", "Model should default to gpt-5.4-nano"

    def test_openai_model_override(self):
        """Verify OPENAI_MODEL environment variable overrides default."""
        tenant_id = setup_test_db()
        os.environ["OPENAI_MODEL"] = "gpt-5.5-turbo"

        ai_service = AIInsights(tenant_id)
        assert ai_service.model == "gpt-5.5-turbo", "Should use OPENAI_MODEL override"

        # Cleanup
        os.environ.pop("OPENAI_MODEL", None)

    def test_missing_api_key_fallback(self):
        """Verify fallback works when OPENAI_API_KEY missing."""
        tenant_id = setup_test_db()
        original_key = os.environ.pop("OPENAI_API_KEY", None)

        try:
            ai_service = AIInsights(tenant_id)
            mock_analysis = {
                "summary": {
                    "total_products": 5,
                    "out_of_stock_count": 1,
                    "low_stock_count": 2,
                    "products_needing_attention": 2,
                    "status": "warning"
                },
                "attention_items": [{
                    "product_name": "Test",
                    "current_stock": 2,
                    "reorder_level": 5,
                    "units_sold_30d": 10,
                    "estimated_days_remaining_30d": 6,
                    "fast_moving_status": "fast_moving",
                    "flags": ["BELOW_REORDER_LEVEL"],
                    "days_since_last_sale": 2
                }]
            }

            result = ai_service.generate_stock_explanation(mock_analysis)
            assert result.get("fallback") is True, "Should use fallback"
            assert result.get("success") is True, "Should still be successful"
        finally:
            if original_key:
                os.environ["OPENAI_API_KEY"] = original_key

    def test_pydantic_validation(self):
        """Verify Pydantic response validation works."""
        valid = StockInsightResponse(
            headline="Test",
            summary="Summary",
            priority="high",
            items=[
                StockInsightItem(
                    product_id=1,
                    headline="H",
                    explanation="E",
                    suggested_action="A"
                )
            ]
        )
        assert valid.priority == "high"
        assert len(valid.items) == 1

    def test_cache_tenant_isolation(self):
        """Verify cache is tenant-isolated."""
        tenant_a = setup_test_db()
        tenant_b = setup_test_db()

        ai_a = AIInsights(tenant_a)
        ai_b = AIInsights(tenant_b)

        # Cache should be independent
        assert ai_a.cache is not ai_b.cache, "Each tenant should have separate cache"

    def test_fallback_no_real_api_call(self):
        """Verify fallback never calls real OpenAI API."""
        tenant_id = setup_test_db()
        os.environ.pop("OPENAI_API_KEY", None)

        ai_service = AIInsights(tenant_id)

        mock_analysis = {
            "summary": {
                "total_products": 1,
                "out_of_stock_count": 1,
                "low_stock_count": 0,
                "products_needing_attention": 1,
                "status": "critical"
            },
            "attention_items": [{
                "product_name": "Test",
                "current_stock": 0,
                "reorder_level": 5,
                "units_sold_30d": 10,
                "estimated_days_remaining_30d": 0,
                "fast_moving_status": "fast",
                "flags": ["OUT_OF_STOCK"],
                "days_since_last_sale": 1
            }]
        }

        result = ai_service.generate_stock_explanation(mock_analysis)

        assert result["fallback"] is True
        assert result["success"] is True
        assert len(result["items"]) > 0

    def test_max_attention_items_limit(self):
        """Verify maximum attention items enforced."""
        tenant_id = setup_test_db()

        ai_service = AIInsights(tenant_id)

        # Create 30 items (exceeds MAX_ATTENTION_ITEMS=15)
        items = []
        for i in range(30):
            items.append({
                "product_id": i + 1,
                "product_name": f"Product {i+1}",
                "current_stock": i,
                "reorder_level": 5,
                "units_sold_30d": 10,
                "estimated_days_remaining_30d": 6,
                "fast_moving_status": "normal",
                "flags": ["BELOW_REORDER_LEVEL"],
                "days_since_last_sale": i
            })

        mock_analysis = {
            "summary": {
                "total_products": 30,
                "out_of_stock_count": 0,
                "low_stock_count": 30,
                "products_needing_attention": 30,
                "status": "warning"
            },
            "attention_items": items
        }

        prepared, mapping = ai_service._prepare_minimal_data(
            mock_analysis["summary"],
            mock_analysis["attention_items"]
        )

        assert len(prepared["attention_items"]) <= ai_service.MAX_ATTENTION_ITEMS

    def test_extract_product_ids(self):
        """Verify product ID extraction works."""
        tenant_id = setup_test_db()
        ai_service = AIInsights(tenant_id)

        prepared = {
            "attention_items": [
                {"product_id": 1},
                {"product_id": 2},
                {"product_id": 3}
            ]
        }

        ids = ai_service._extract_product_ids(prepared)
        assert ids == {1, 2, 3}, "Should extract all product IDs"

    def test_generate_cache_key(self):
        """Verify cache key generation works."""
        tenant_id = setup_test_db()
        ai_service = AIInsights(tenant_id)

        summary = {
            "total_products": 10,
            "out_of_stock_count": 1,
            "low_stock_count": 2,
            "products_needing_attention": 3,
            "status": "warning"
        }
        items = [
            {"flags": ["OUT_OF_STOCK"]},
            {"flags": ["BELOW_REORDER_LEVEL"]}
        ]

        key1 = ai_service._generate_cache_key(summary, items)
        key2 = ai_service._generate_cache_key(summary, items)

        assert key1 == key2, "Same inputs should produce same cache key"
        assert isinstance(key1, str), "Cache key should be string"
        assert len(key1) > 0, "Cache key should not be empty"

    def run_all_tests(self):
        """Run all tests."""
        self.results.test("gpt-5.4-nano default model", self.test_gpt_5_4_nano_default)
        self.results.test("OPENAI_MODEL override", self.test_openai_model_override)
        self.results.test("missing API key fallback", self.test_missing_api_key_fallback)
        self.results.test("Pydantic validation", self.test_pydantic_validation)
        self.results.test("cache tenant isolation", self.test_cache_tenant_isolation)
        self.results.test("fallback no API call", self.test_fallback_no_real_api_call)
        self.results.test("max attention items limit", self.test_max_attention_items_limit)
        self.results.test("product ID extraction", self.test_extract_product_ids)
        self.results.test("cache key generation", self.test_generate_cache_key)


if __name__ == '__main__':
    print("\n" + "="*80)
    print("AI INSIGHTS TESTS (Responses API)")
    print("="*80 + "\n")

    results = TestResults()
    tests = AIInsightsTests(results)
    tests.run_all_tests()

    success = results.summary()
    sys.exit(0 if success else 1)
