"""API routes for AutoStack AI Insights - Stock Intelligence explanations."""

from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from app.services.stock_intelligence import StockIntelligenceEngine
from app.services.ai_insights import get_ai_insights, AIInsightsException
from app.tenant_db import get_conn

ai_insights_bp = Blueprint("ai_insights", __name__, url_prefix="/api/ai")


@ai_insights_bp.route("/stock-insights", methods=["GET"])
@login_required
def get_stock_insights():
    """
    Get AI-powered stock insights based on StockIntelligenceEngine analysis.

    Returns:
        JSON with AI explanation of stock status and flagged products.
        Falls back to deterministic explanation if AI unavailable.
    """
    try:
        tenant_id = current_user.tenant_id

        # Step 1: Run Stock Intelligence Engine (deterministic)
        engine = StockIntelligenceEngine(tenant_id, get_conn)
        stock_analysis = engine.analyze_inventory()

        # Step 2: Get AI explanation (with fallback)
        ai_service = get_ai_insights(tenant_id)
        insights = ai_service.generate_stock_explanation(stock_analysis)

        # Step 3: Return to frontend
        return jsonify({
            "success": True,
            "data": insights
        }), 200

    except AIInsightsException as e:
        # AI-specific error
        return jsonify({
            "success": False,
            "error": str(e),
            "error_type": "ai_error"
        }), 500

    except Exception as e:
        # Unexpected error
        return jsonify({
            "success": False,
            "error": "Unexpected error generating stock insights",
            "error_type": "internal_error"
        }), 500


@ai_insights_bp.route("/health", methods=["GET"])
@login_required
def health_check():
    """Check if AI insights service is available."""
    try:
        import os
        api_key_present = bool(os.environ.get("OPENAI_API_KEY"))
        model = os.environ.get("OPENAI_MODEL", "gpt-5.4-nano")

        return jsonify({
            "success": True,
            "ai_service_available": api_key_present,
            "model": model,
            "tenant_id": current_user.tenant_id
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
