"""ChartAsst browser UI and human-friendly hypothesis API."""
from __future__ import annotations
from flask import Blueprint, jsonify, render_template, request
from app.hypothesis_state import STATES, normalize_status, transition, record_event
from app.condition_engine import evaluate_hypothesis
from app.utils import utc_now_iso

def _public(p):
    x = dict(p)
    x["hypothesis_status"] = normalize_status(x.get("hypothesis_status", p.get("status", "watching")))
    x["events"] = list(x.get("events", []))
    x["execution_mode"] = x.get("execution_mode", "alert")
    return x

def register_gui(app, matcher):
    bp = Blueprint("chartasst_gui", __name__, template_folder="templates", static_folder="static")
    @bp.get("/")
    def index(): return render_template("index.html")
    @bp.get("/api/hypotheses")
    def hypotheses(): return jsonify({"hypotheses": [_public(p) for p in matcher.plans]})
    @bp.get("/api/hypotheses/<plan_id>")
    def hypothesis(plan_id):
        p = matcher.get_plan(plan_id)
        if not p: return jsonify({"error": "Hypothesis not found."}), 404
        return jsonify({"hypothesis": _public(p)})
    @bp.post("/api/hypotheses")
    def create_hypothesis():
        data = request.get_json(silent=True) or {}
        symbol = str(data.get("symbol", "")).strip().upper()
        bias = str(data.get("bias", "")).strip().lower()
        action = "buy" if bias in ("bullish", "buy", "long") else "sell" if bias in ("bearish", "sell", "short") else str(data.get("action", "")).lower()
        if not symbol: return jsonify({"error": "Choose an instrument."}), 400
        if action not in ("buy", "sell"): return jsonify({"error": "Choose bullish or bearish."}), 400
        timestamp = utc_now_iso()
        h = {
            "name": data.get("name") or f"{symbol} {bias.title()} hypothesis",
            "symbol": symbol, "timeframe": data.get("timeframe", ""),
            "condition": data.get("thesis") or data.get("condition", ""),
            "notes": data.get("notes", ""), "action": action,
            "object_name": data.get("object_name", ""), "volume": data.get("volume"),
            "sl_points": data.get("sl_points"), "tp_points": data.get("tp_points"),
            "status": "active" if str(data.get("execution_mode", "alert")).strip().lower() == "auto_execute" else "disabled", "hypothesis_status": "watching", "execution_mode": data.get("execution_mode", "alert"),
            "hypothesis": {
                "thesis": data.get("thesis", ""), "trigger": data.get("trigger", ""),
                "confirmation": data.get("confirmation", ""), "invalidation": data.get("invalidation", ""),
                "target": data.get("target", ""), "time_window": data.get("time_window", "This week"),
            },
            "conditions": data.get("conditions") or {}, "events": [], "created_at": timestamp, "updated_at": timestamp,
        }
        record_event(h, "created", "Hypothesis created", "user")
        try: created = matcher.add_plan(h)
        except ValueError as exc: return jsonify({"error": str(exc)}), 400
        return jsonify({"hypothesis": _public(created)}), 201
    @bp.patch("/api/hypotheses/<plan_id>/status")
    def hypothesis_status(plan_id):
        data = request.get_json(silent=True) or {}
        target = normalize_status(data.get("status"))
        if target not in STATES: return jsonify({"error": "Invalid status."}), 400
        p = matcher.get_plan(plan_id)
        if not p: return jsonify({"error": "Hypothesis not found."}), 404
        try: transition(p, target, data.get("reason", ""), "user")
        except ValueError as exc: return jsonify({"error": str(exc)}), 409
        # Keep legacy matcher eligibility separate from hypothesis lifecycle.
        p["status"] = "active" if str(p.get("execution_mode", "alert")).strip().lower() == "auto_execute" and target in ("watching", "developing", "confirmed") else ("disabled" if target in ("watching", "developing", "confirmed", "paused") else target)
        matcher._persist()
        return jsonify({"hypothesis": _public(p)})
    @bp.post("/api/hypotheses/<plan_id>/events")
    def hypothesis_event(plan_id):
        p = matcher.get_plan(plan_id)
        if not p: return jsonify({"error": "Hypothesis not found."}), 404
        data = request.get_json(silent=True) or {}
        event = record_event(p, str(data.get("type", "note")), str(data.get("description", "")), str(data.get("source", "user")), data.get("metadata") or {})
        matcher._persist()
        return jsonify({"event": event, "hypothesis": _public(p)})
    @bp.post("/api/hypotheses/<plan_id>/market-event")
    def hypothesis_market_event(plan_id):
        data = request.get_json(silent=True) or {}
        result = matcher.process_hypothesis_event(plan_id, data)
        if not result:
            return jsonify({"error": "Hypothesis not found."}), 404
        return jsonify({
            "hypothesis": _public(result["plan"]),
            "event_type": result["event_type"],
            "hypothesis_status": result["hypothesis_status"],
        })

    @bp.post("/api/hypotheses/<plan_id>/evaluate")
    def evaluate_market(plan_id):
        p = matcher.get_plan(plan_id)
        if not p: return jsonify({"error": "Hypothesis not found."}), 404
        market = request.get_json(silent=True) or {}
        evaluation = evaluate_hypothesis(p, market)
        return jsonify({"plan_id": plan_id, "evaluation": evaluation})

    @bp.get("/api/summary")
    def summary():
        counts = {state: 0 for state in STATES}
        for p in matcher.plans: counts[normalize_status(p.get("hypothesis_status", p.get("status")))]+=1
        return jsonify(counts)
    app.register_blueprint(bp)
    return app
