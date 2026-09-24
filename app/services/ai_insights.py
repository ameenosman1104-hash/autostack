"""AI Insights Service - OpenAI Responses API for AutoStack stock intelligence.

Architecture: Provider-independent service abstraction using Responses API

AutoStack Backend
    ↓
StockIntelligenceEngine (deterministic facts: quantities, dates, velocities)
    ↓
AIInsights.generate_stock_explanation() (verified data to AI)
    ↓
OpenAI Responses API + gpt-5.4-nano (explanation layer only)
    ↓
Structured Pydantic response (validated)
    ↓
AutoStack Frontend (AI output + deterministic metrics)

CRITICAL: AI NEVER calculates business metrics - only explains deterministic facts.
Numeric data (stock, sales, dates) comes from StockIntelligenceEngine, not AI.
"""

import os
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import hashlib
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# PYDANTIC STRUCTURED OUTPUT MODELS
# ══════════════════════════════════════════════════════════════════════════

class StockInsightItem(BaseModel):
    """Single product insight from AI."""
    product_id: int = Field(..., description="AutoStack product ID")
    headline: str = Field(..., description="Short product summary")
    explanation: str = Field(..., description="Explanation of stock condition")
    suggested_action: str = Field(..., description="Safe advisory action")
    priority: str = Field(default="medium", description="Item-level priority: high|medium|low")


class StockInsightResponse(BaseModel):
    """Structured response from OpenAI Responses API."""
    headline: str = Field(..., description="Overall stock situation summary")
    summary: str = Field(..., description="Brief explanation of key issues")
    priority: str = Field(..., description="high|medium|low")
    items: List[StockInsightItem] = Field(default_factory=list, description="Product insights")


class AIInsightsException(Exception):
    """Base exception for AI insights operations."""
    pass


# ══════════════════════════════════════════════════════════════════════════
# AI INSIGHTS SERVICE
# ══════════════════════════════════════════════════════════════════════════

class AIInsights:
    """Provider-independent AI insights using OpenAI Responses API."""

    # Configuration
    DEFAULT_MODEL = "gpt-5.4-nano"  # Current fastest model for structured explanations
    DEFAULT_TIMEOUT = 10  # seconds
    DEFAULT_MAX_TOKENS = 500  # token limit per response
    MAX_ATTENTION_ITEMS = 15  # maximum products to explain
    CACHE_DURATION = 600  # 10 minutes

    def __init__(self, tenant_id: str):
        """Initialize AI insights for a tenant.

        Args:
            tenant_id: Multi-tenant isolation key
        """
        self.tenant_id = tenant_id
        self.api_key = os.environ.get("OPENAI_API_KEY")
        self.model = os.environ.get("OPENAI_MODEL", self.DEFAULT_MODEL)
        self.cache = {}  # In-memory per-tenant cache

    def generate_stock_explanation(self, stock_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate AI explanation for stock intelligence.

        Args:
            stock_analysis: Output from StockIntelligenceEngine.analyze_inventory()

        Returns:
            Structured response with explanations
        """
        try:
            # Validate input
            if not stock_analysis or not isinstance(stock_analysis, dict):
                raise AIInsightsException("Invalid stock_analysis input")

            summary = stock_analysis.get("summary", {})
            attention_items = stock_analysis.get("attention_items", [])

            # Generate cache key from deterministic facts
            cache_key = self._generate_cache_key(summary, attention_items)

            # Check cache first
            if cache_key in self.cache:
                cached, timestamp = self.cache[cache_key]
                if datetime.now() - timestamp < timedelta(seconds=self.CACHE_DURATION):
                    logger.info(f"AI insights cache hit for tenant {self.tenant_id}")
                    return cached

            # Prepare minimal data for AI (with real product ID mapping)
            prepared_data, product_id_mapping = self._prepare_minimal_data(summary, attention_items)
            allowed_product_ids = self._extract_product_ids(prepared_data)

            # If API key missing, use fallback immediately
            if not self.api_key:
                logger.warning(f"OPENAI_API_KEY not set, using fallback")
                return self._generate_fallback_response(prepared_data, product_id_mapping)

            # Call OpenAI Responses API
            try:
                ai_response = self._call_openai_responses_api(prepared_data)

                # Validate product IDs and add deterministic summary
                validated = self._validate_product_ids(ai_response, allowed_product_ids, product_id_mapping, summary)

                # Cache result
                self.cache[cache_key] = (validated, datetime.now())

                return validated

            except Exception as e:
                logger.error(f"AI API error: {str(e)}", exc_info=True)
                # Fallback on any AI error
                return self._generate_fallback_response(prepared_data, product_id_mapping)

        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}", exc_info=True)
            return {
                "success": False,
                "headline": "Stock Analysis Unavailable",
                "summary": "Could not generate AI explanation.",
                "priority": "low",
                "items": [],
                "fallback": True,
                "error_reason": "internal_error"
            }

    def _generate_cache_key(self, summary: Dict, items: List) -> str:
        """Generate cache key from deterministic facts."""
        key_data = {
            "total": summary.get("total_products"),
            "out_of_stock": summary.get("out_of_stock_count"),
            "low_stock": summary.get("low_stock_count"),
            "attention": summary.get("products_needing_attention"),
            "status": summary.get("status"),
            "flags": tuple(tuple(sorted(item.get("flags", []))) for item in items[:self.MAX_ATTENTION_ITEMS])
        }
        key_str = str(key_data)
        return hashlib.md5(key_str.encode()).hexdigest()

    def _extract_product_ids(self, prepared_data: Dict) -> set:
        """Extract product IDs from prepared data for validation."""
        ids = set()
        for item in prepared_data.get("attention_items", []):
            if "product_id" in item:
                ids.add(item["product_id"])
        return ids

    def _prepare_minimal_data(self, summary: Dict, attention_items: List) -> tuple:
        """Prepare minimal privacy-focused data for AI.

        Returns:
            Tuple of (prepared_data, product_id_mapping)
            where product_id_mapping maps sequential_id -> real_product_id
        """
        limited_items = attention_items[:self.MAX_ATTENTION_ITEMS]

        prepared_items = []
        product_id_mapping = {}  # Maps sequential ID -> real product ID

        for i, item in enumerate(limited_items):
            sequential_id = i + 1
            real_id = item.get("product_id")

            if real_id:
                product_id_mapping[sequential_id] = real_id

            prepared_items.append({
                "product_id": sequential_id,  # Use safe sequential IDs for AI
                "product_name": item.get("product_name", "Unknown"),
                "current_stock": item.get("current_stock"),
                "reorder_level": item.get("reorder_level"),
                "units_sold_30d": item.get("units_sold_30d"),
                "days_remaining": item.get("estimated_days_remaining_30d"),
                "movement": item.get("fast_moving_status", "unknown"),
                "flags": item.get("flags", [])
            })

        prepared_data = {
            "summary": {
                "total": summary.get("total_products"),
                "out_of_stock": summary.get("out_of_stock_count"),
                "low_stock": summary.get("low_stock_count"),
                "attention": summary.get("products_needing_attention"),
                "status": summary.get("status")
            },
            "attention_items": prepared_items
        }

        return prepared_data, product_id_mapping

    def _call_openai_responses_api(self, prepared_data: Dict[str, Any]) -> StockInsightResponse:
        """Call OpenAI Responses API with structured output."""
        try:
            import openai
            from openai import APIConnectionError, APITimeoutError, RateLimitError, APIError

            client = openai.OpenAI(api_key=self.api_key)

            # System prompt grounding
            system_prompt = """You are AutoStack's Stock Insights assistant.

STRICT RULES:
1. Use ONLY supplied AutoStack facts.
2. Never invent: products, stock quantities, sales numbers, dates, monetary values.
3. Only explain and prioritize supplied stock conditions.
4. Suggested actions must be advisory only: "Review replenishment", "Check supplier status".
5. Do not make purchasing decisions.
6. Keep explanations concise and practical.

Your role: Transform verified AutoStack stock data into clear, actionable explanations."""

            user_prompt = f"""Analyze this stock data and provide insights for flagged products.

{json.dumps(prepared_data, indent=2)}

For each product, provide:
1. Why it's flagged (based on supplied flags)
2. What the metrics show
3. Suggested safe review action

Use ONLY the product IDs and data supplied."""

            # Call Responses API with Pydantic model
            response = client.responses.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format=StockInsightResponse,
                temperature=0.3,
                max_tokens=self.DEFAULT_MAX_TOKENS
            )

            # Extract parsed output
            return response.output_parsed

        except Exception as e:
            logger.error(f"OpenAI Responses API error: {str(e)}")
            raise AIInsightsException(f"OpenAI error: {str(e)}")

    def _validate_product_ids(self, response: StockInsightResponse, allowed_ids: set, product_id_mapping: Dict[int, int], deterministic_summary: Dict[str, Any] = None) -> Dict[str, Any]:
        """Validate that AI returned only allowed product IDs."""
        validated_items = []

        for item in response.items:
            # Reject unknown product IDs
            if item.product_id not in allowed_ids:
                logger.warning(f"AI returned unknown product_id {item.product_id}, rejecting")
                continue

            # Map sequential ID back to real product ID for frontend
            real_product_id = product_id_mapping.get(item.product_id, item.product_id)

            validated_items.append({
                "product_id": real_product_id,
                "headline": item.headline[:200],
                "explanation": item.explanation[:500],
                "suggested_action": item.suggested_action[:200],
                "priority": self._validate_priority(item.priority)
            })

        # Limit items
        validated_items = validated_items[:self.MAX_ATTENTION_ITEMS]

        return {
            "success": True,
            "headline": response.headline[:200],
            "summary": {
                "attention": deterministic_summary.get('products_needing_attention', 0) if deterministic_summary else 0,
                "out_of_stock": deterministic_summary.get('out_of_stock_count', 0) if deterministic_summary else 0,
                "low_stock": deterministic_summary.get('low_stock_count', 0) if deterministic_summary else 0,
                "status": deterministic_summary.get('status', 'healthy') if deterministic_summary else 'healthy'
            },
            "priority": self._validate_priority(response.priority),
            "items": validated_items,
            "fallback": False
        }

    def _validate_priority(self, priority: str) -> str:
        """Validate priority value."""
        p = str(priority).lower().strip()
        if p in ["high", "medium", "low"]:
            return p
        return "medium"

    def _derive_item_priority(self, flags: List[str]) -> str:
        """Derive item-level priority from deterministic flags."""
        if "OUT_OF_STOCK" in flags or "LOW_STOCK_FAST_MOVING" in flags:
            return "high"
        elif "BELOW_REORDER_LEVEL" in flags or "DORMANT" in flags:
            return "medium"
        else:
            return "low"

    def _generate_fallback_response(self, prepared_data: Dict[str, Any], product_id_mapping: Dict[int, int] = None) -> Dict[str, Any]:
        """Deterministic fallback using AutoStack flags only."""
        summary = prepared_data.get("summary", {})
        items = prepared_data.get("attention_items", [])

        if product_id_mapping is None:
            product_id_mapping = {}

        # Determine priority
        if summary.get("out_of_stock", 0) > 0:
            priority = "high"
            headline = f"{summary['out_of_stock']} products out of stock"
        elif summary.get("low_stock", 0) > 5:
            priority = "high"
            headline = f"Multiple low-stock products ({summary['low_stock']})"
        elif summary.get("low_stock", 0) > 0:
            priority = "medium"
            headline = f"Low stock on {summary['low_stock']} products"
        else:
            priority = "low"
            headline = "Stock levels healthy"

        # Generate explanations from flags only
        fallback_items = []
        for item in items[:self.MAX_ATTENTION_ITEMS]:
            flags = item.get("flags", [])
            sequential_id = item.get("product_id", 0)
            real_product_id = product_id_mapping.get(sequential_id, sequential_id)

            if "OUT_OF_STOCK" in flags:
                explanation = f"{item['product_name']} is out of stock."
                action = "Review replenishment immediately"
            elif "BELOW_REORDER_LEVEL" in flags:
                diff = item.get('reorder_level', 0) - item.get('current_stock', 0)
                explanation = f"{item['product_name']} is {diff:.0f} units below reorder level."
                action = "Review replenishment"
            elif "LOW_STOCK_FAST_MOVING" in flags:
                explanation = f"{item['product_name']} is moving fast but stock is low."
                action = "Review replenishment schedule"
            elif "DORMANT" in flags:
                days = item.get('days_since_last_sale', 0)
                explanation = f"{item['product_name']} has not sold in {days} days."
                action = "Review if product should remain in inventory"
            elif "NEVER_SOLD" in flags:
                explanation = f"{item['product_name']} has never been sold."
                action = "Review product utility"
            else:
                explanation = f"{item['product_name']} requires attention."
                action = "Review stock level"

            # Derive item-level priority from flags
            item_priority = self._derive_item_priority(flags)

            fallback_items.append({
                "product_id": real_product_id,
                "headline": item['product_name'],
                "explanation": explanation,
                "suggested_action": action,
                "priority": item_priority
            })

        return {
            "success": True,
            "headline": headline,
            "summary": {
                "attention": summary.get('attention', 0),
                "out_of_stock": summary.get('out_of_stock', 0),
                "low_stock": summary.get('low_stock', 0),
                "status": summary.get('status', 'healthy')
            },
            "priority": priority,
            "items": fallback_items,
            "fallback": True,
            "fallback_reason": "AI service unavailable"
        }


def get_ai_insights(tenant_id: str) -> AIInsights:
    """Factory function for AIInsights instances."""
    return AIInsights(tenant_id)
