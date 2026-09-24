"""ChartAsst browser UI and human-friendly hypothesis API.

The GUI deliberately sits above PlanMatcher/MT5 rather than replacing them.
JSON remains an internal transport/storage format; users interact with cards,
forms, and optional browser speech recognition.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from app.utils import utc_now_iso

VALID_STATUSES = {"active", "triggered", "disabled", "expired"}

def register_gui(app, matcher):
    bp = Blueprint("chartasst_gui", __name__, template_folder="templates", static_folder="static")

    @bp.get("/")
    def index():
        return render_template("index.html")

    @bp.get("/api/hypotheses")
    def hypotheses():
        plans = matcher.plans
        return jsonify({"hypotheses": plans})

    @bp.post("/api/hypotheses")
    def create_hypothesis():
        data = request.get_json(silent=True) or {}
        symbol = str(data.get("symbol", "")).strip().upper()
        bias = str(data.get("bias", "")).strip().lower()
        action = "buy" if bias in ("bullish", "buy", "long") else "sell" if bias in ("bearish", "sell", "short") else str(data.get("action", "")).lower()
        if not symbol:
            return jsonify({"error": "Choose an instrument."}), 400
        if action not in ("buy", "sell"):
            return jsonify({"error": "Choose bullish or bearish."}), 400

        plan = {
            "name": data.get("name") or f"{symbol} {bias.title()} hypothesis",
            "symbol": symbol,
            "timeframe": data.get("timeframe", ""),
            "condition": data.get("thesis") or data.get("condition", ""),
            "notes": data.get("notes", ""),
            "action": action,
            "object_name": data.get("object_name", ""),
            "volume": data.get("volume"),
            "sl_points": data.get("sl_points"),
            "tp_points": data.get("tp_points"),
            "hypothesis": {
                "thesis": data.get("thesis", ""),
                "trigger": data.get("trigger", ""),
                "confirmation": data.get("confirmation", ""),
                "invalidation": data.get("invalidation", ""),
                "target": data.get("target", ""),
                "time_window": data.get("time_window", "This week"),
            },
            "created_at": utc_now_iso(),
        }
        try:
            created = matcher.add_plan(plan)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"hypothesis": created}), 201

    @bp.patch("/api/hypotheses/<plan_id>/status")
    def hypothesis_status(plan_id):
        data = request.get_json(silent=True) or {}
        status = str(data.get("status", "")).strip().lower()
        if status not in VALID_STATUSES:
            return jsonify({"error": "Invalid status."}), 400
        if not matcher.update_plan_status(plan_id, status):
            return jsonify({"error": "Hypothesis not found."}), 404
        return jsonify({"ok": True, "status": status})

    @bp.get("/api/summary")
    def summary():
        plans = matcher.plans
        counts = {"watching": 0, "developing": 0, "confirmed": 0, "invalidated": 0, "expired": 0}
        for p in plans:
            status = p.get("status")
            if status == "active":
                counts["watching"] += 1
            elif status == "triggered":
                counts["confirmed"] += 1
            elif status == "expired":
                counts["expired"] += 1
        return jsonify(counts)

    app.register_blueprint(bp)
    return app
