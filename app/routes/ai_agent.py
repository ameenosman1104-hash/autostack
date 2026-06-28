from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
import requests
import json

ai_agent_bp = Blueprint("ai_agent", __name__)

# Next.js AI Agent API endpoint
AI_AGENT_API = "http://localhost:3000/api/ai-agent"

@ai_agent_bp.route("/")
@login_required
def chat():
    """Main AI agent chat interface"""
    return render_template("ai_agent.html")

@ai_agent_bp.route("/send", methods=["POST"])
@login_required
def send_message():
    """Send message to AI agent"""
    try:
        data = request.get_json()
        message = data.get("message", "").strip()

        if not message:
            return jsonify({"error": "Empty message"}), 400

        # Forward to Next.js AI Agent API
        response = requests.post(
            AI_AGENT_API,
            json={"message": message},
            timeout=15
        )

        if response.status_code == 200:
            ai_response = response.json()
            return jsonify({
                "success": True,
                "message": ai_response.get("message", "No response"),
                "history": ai_response.get("history", [])
            })
        else:
            return jsonify({
                "success": False,
                "error": f"AI Agent error: {response.status_code}"
            }), response.status_code

    except requests.exceptions.Timeout:
        return jsonify({
            "success": False,
            "error": "Request timeout. Please try again."
        }), 504

    except requests.exceptions.ConnectionError:
        return jsonify({
            "success": False,
            "error": "Cannot connect to AI Agent. Make sure Next.js server is running on port 3000."
        }), 503

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@ai_agent_bp.route("/book-appointment", methods=["POST"])
@login_required
def book_appointment():
    """Book appointment via AI Agent"""
    try:
        data = request.get_json()

        response = requests.post(
            AI_AGENT_API,
            json={
                "action": "book_appointment",
                "data": {
                    "customerName": data.get("customerName"),
                    "phone": data.get("phone"),
                    "date": data.get("date"),
                    "service": data.get("service", "Tyre Installation")
                }
            },
            timeout=15
        )

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({"success": False, "error": "Booking failed"}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@ai_agent_bp.route("/check-stock", methods=["POST"])
@login_required
def check_stock():
    """Check stock via AI Agent"""
    try:
        data = request.get_json()

        response = requests.post(
            AI_AGENT_API,
            json={
                "action": "check_stock",
                "data": {
                    "brand": data.get("brand"),
                    "size": data.get("size")
                }
            },
            timeout=15
        )

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({"success": False, "error": "Stock check failed"}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
