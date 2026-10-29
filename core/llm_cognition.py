"""
LLM Cognition Engine — calls tradingbeast Hermes for full-state analysis.

Runs every 15 minutes (5 min after rule engine). Sends complete system
state to the LLM, receives structured JSON decisions, logs everything
to llm_cognition table for psychology monitoring.
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# tradingbeast Hermes CLI
HERMES_CLI = "/home/tradingbeast/.local/bin/hermes"
HERMES_HOST = "tradingbeast"

# LLM trigger types
TRIGGER_SCHEDULED = "scheduled"
TRIGGER_POSITION_AT_RISK = "position_at_risk"
TRIGGER_CONFLICT = "conflict"
TRIGGER_NEWS = "news"
TRIGGER_OPPORTUNITY = "opportunity"
TRIGGER_VOLATILITY_SHIFT = "volatility_shift"
TRIGGER_NEW_SETUP = "new_setup"
TRIGGER_LOW_CONFIDENCE = "low_confidence"


def build_context(silos, positions, recent_trades, market_data, news_events,
                  strategy_perf, prior_learnings, psych_history):
    """Build the full context bundle sent to the LLM."""
    return {
        "timestamp": datetime.now().isoformat(),
        "trigger": "scheduled",
        "silos": silos,
        "positions": positions,
        "recent_trades": recent_trades[-20:] if recent_trades else [],
        "market": market_data,
        "news": news_events[:10] if news_events else [],
        "strategy_performance": strategy_perf,
        "prior_learnings": prior_learnings[-20:] if prior_learnings else [],
        "psych_history": psych_history[-7:] if psych_history else [],
        "mandate": (
            "Trade FundingPips rules. Protect capital. Grow silos. "
            "You may propose trades, close positions, adjust SL/TP, "
            "and change strategy weights. You may NOT override risk limits "
            "or breach compliance. Risk manager is final authority."
        ),
    }


def call_hermes(prompt, timeout=120):
    """
    Call tradingbeast Hermes CLI with a prompt.
    Returns the LLM response text.
    """
    try:
        result = subprocess.run(
            [HERMES_CLI, "-z", prompt, "chat"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            return f"ERROR: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return "ERROR: LLM call timed out"
    except Exception as e:
        return f"ERROR: {str(e)}"


def parse_llm_response(raw):
    """
    Parse LLM response into structured JSON.
    Handles both pure JSON and JSON embedded in markdown.
    """
    if not raw or raw.startswith("ERROR"):
        return None, raw

    # Try direct JSON parse
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from markdown code block
    if "```json" in raw:
        try:
            json_str = raw.split("```json")[1].split("```")[0].strip()
            return json.loads(json_str), None
        except (IndexError, json.JSONDecodeError):
            pass

    if "```" in raw:
        try:
            json_str = raw.split("```")[1].split("```")[0].strip()
            return json.loads(json_str), None
        except (IndexError, json.JSONDecodeError):
            pass

    return None, raw


def extract_psych_markers(response_json, raw_text):
    """
    Extract psychology markers from the LLM response.
    These feed the daily psych audit.
    """
    markers = {
        "proposed_risk_pct": None,
        "stated_confidence": None,
        "decision_count": 0,
        "hedge_language_score": 0.0,
        "urgency_score": 0.0,
        "loser_attachment_flag": 0,
        "revenge_flag": 0,
        "rationale": "",
    }

    if not response_json:
        return markers

    actions = response_json.get("actions", [])
    markers["decision_count"] = len(actions)

    # Extract risk from first trade action
    for action in actions:
        if action.get("type") == "discretionary_trade":
            risk = action.get("risk_pct")
            if risk is not None:
                markers["proposed_risk_pct"] = risk
            break

    # Extract confidence
    markers["stated_confidence"] = response_json.get("assessment_confidence")

    # Self-report
    self_report = response_json.get("self_report", {})
    markers["rationale"] = self_report.get("bias_acknowledgment", "")

    # Language analysis on raw text
    if raw_text:
        text_lower = raw_text.lower()

        # Hedge language: maybe, possibly, could, might, perhaps
        hedge_words = ["maybe", "possibly", "could", "might", "perhaps",
                       "uncertain", "not sure", "hesitant"]
        hedge_count = sum(1 for w in hedge_words if w in text_lower)
        markers["hedge_language_score"] = min(1.0, hedge_count / 5.0)

        # Urgency language: now, must, immediately, urgent, asap
        urgency_words = ["now", "must", "immediately", "urgent", "asap",
                         "right away", "without delay"]
        urgency_count = sum(1 for w in urgency_words if w in text_lower)
        markers["urgency_score"] = min(1.0, urgency_count / 3.0)

        # Revenge: size increase after loss
        if "revenge" in text_lower or "make back" in text_lower or \
           "recover" in text_lower:
            markers["revenge_flag"] = 1

        # Loser attachment: holding losers
        if "hold" in text_lower and ("loss" in text_lower or "loser" in text_lower):
            markers["loser_attachment_flag"] = 1

    return markers


def run_cognition_pass(silos, positions, recent_trades, market_data,
                       news_events, strategy_perf, prior_learnings,
                       psych_history, trigger="scheduled"):
    """
    Full cognition pass: build context, call LLM, parse, log, return decisions.
    """
    from core.silo_db import get_db

    context = build_context(
        silos, positions, recent_trades, market_data,
        news_events, strategy_perf, prior_learnings, psych_history
    )
    context["trigger"] = trigger

    # Build prompt
    prompt = f"""You are the trading cognition engine for a FundingPips prop firm trading system.

## Current State
```json
{json.dumps(context, indent=2, default=str)}
```

## Your Task
Analyze the full system state and respond with structured JSON:

```json
{{
  "assessment": "brief overall assessment",
  "assessment_confidence": 0.0-1.0,
  "actions": [
    {{
      "type": "discretionary_trade"|"close"|"modify"|"reduce",
      "symbol": "EURUSD",
      "dir": "BUY"|"SELL",
      "entry": 1.23456,
      "sl": 1.23000,
      "tp": 1.24000,
      "risk_pct": 0.005,
      "target_silos": ["2sf-5k-01"],
      "confidence": 0.7,
      "reason": "why this trade"
    }}
  ],
  "strategy_weights": {{"ORB": 1.0, "VCP": 1.2}},
  "learning": {{
    "category": "risk"|"strategy"|"market"|"psychology",
    "insight": "what you learned",
    "evidence": "what evidence supports this",
    "confidence": 0.0-1.0
  }},
  "self_report": {{
    "emotional_state": "calm"|"anxious"|"greedy"|"fearful"|"confident",
    "bias_acknowledgment": "any biases you notice in yourself",
    "uncertainty": "what you are uncertain about"
  }}
}}
```

Rules:
- You may propose trades, close positions, adjust SL/TP, change strategy weights
- You may NOT override risk limits or breach compliance
- Risk manager is final authority — your proposals are gated
- Be honest about uncertainty — overconfidence kills accounts
- If no opportunity, return empty actions array
"""

    # Call LLM
    raw = call_hermes(prompt)
    parsed, error = parse_llm_response(raw)

    # Extract psych markers
    markers = extract_psych_markers(parsed, raw)

    # Log to DB
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO llm_cognition (timestamp, silo_id, trigger, context_json,
            response_raw, decision_json, actions_taken, outcome,
            proposed_risk_pct, stated_confidence, decision_count,
            hedge_language_score, urgency_score, loser_attachment_flag,
            revenge_flag, rationale)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now().isoformat(), None, trigger,
        json.dumps(context, default=str),
        raw,
        json.dumps(parsed, default=str) if parsed else None,
        json.dumps(parsed.get("actions", []), default=str) if parsed else None,
        "error" if error else "success",
        markers["proposed_risk_pct"],
        markers["stated_confidence"],
        markers["decision_count"],
        markers["hedge_language_score"],
        markers["urgency_score"],
        markers["loser_attachment_flag"],
        markers["revenge_flag"],
        markers["rationale"],
    ))
    conn.commit()
    conn.close()

    return parsed, markers, raw


def score_triggers(silos, positions, market_data, news_events):
    """
    Score whether the LLM should be triggered.
    Returns list of (trigger_type, priority, payload) tuples.
    """
    triggers = []

    for silo in silos:
        if silo.get("status") != "active":
            continue

        silo_positions = [p for p in positions if p.get("silo_id") == silo["silo_id"]]

        # Position at risk: floating loss > 50% of risk
        for pos in silo_positions:
            if pos.get("floating_loss", 0) > pos.get("risk_amount", 0) * 0.5:
                triggers.append((
                    TRIGGER_POSITION_AT_RISK, 1,
                    {"silo_id": silo["silo_id"], "ticket": pos["ticket"]}
                ))

        # Near daily loss limit
        daily_loss_pct = silo.get("daily_loss_pct", 0)
        if daily_loss_pct > 0.7:
            triggers.append((
                TRIGGER_LOW_CONFIDENCE, 2,
                {"silo_id": silo["silo_id"], "daily_loss_pct": daily_loss_pct}
            ))

        # Near overall floor
        distance_to_floor = silo.get("distance_to_floor", 999)
        if distance_to_floor < silo.get("starting_balance", 5000) * 0.02:
            triggers.append((
                TRIGGER_POSITION_AT_RISK, 1,
                {"silo_id": silo["silo_id"], "distance_to_floor": distance_to_floor}
            ))

    # News window approaching
    for event in news_events:
        if event.get("impact") == "High":
            triggers.append((
                TRIGGER_NEWS, 3,
                {"event": event}
            ))

    # Volatility regime shift (simplified)
    for symbol, data in market_data.items():
        atr = data.get("atr", 0)
        atr_avg = data.get("atr_avg", 0)
        if atr_avg > 0 and atr > atr_avg * 1.5:
            triggers.append((
                TRIGGER_VOLATILITY_SHIFT, 4,
                {"symbol": symbol, "atr": atr, "atr_avg": atr_avg}
            ))

    # Sort by priority
    triggers.sort(key=lambda x: x[1])
    return triggers


if __name__ == "__main__":
    print("LLM Cognition Engine")
    print(f"Hermes CLI: {HERMES_CLI}")
    print(f"Host: {HERMES_HOST}")
    print(f"Triggers: scheduled, position_at_risk, conflict, news, opportunity")
    print(f"Psych markers: risk drift, confidence, revenge, disposition, hedge, urgency")
