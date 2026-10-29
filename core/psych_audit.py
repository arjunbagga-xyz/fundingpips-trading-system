"""
LLM Psychology Audit — daily self-audit of the LLM's trading cognition.

LLMs are black boxes prone to greed and fear. This module:
1. Collects the day's llm_cognition rows
2. Sends them to the LLM for self-audit
3. Stores structured psych scores in llm_psych_daily
4. Tracks trends over days
5. Alerts on dangerous patterns
"""

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

HERMES_CLI = "/home/tradingbeast/.local/bin/hermes"
HERMES_HOST = "tradingbeast"


def get_day_cognitions(date_str):
    """Get all llm_cognition rows for a given date."""
    from core.silo_db import get_db
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM llm_cognition
        WHERE date(timestamp) = ?
        ORDER BY timestamp
    """, (date_str,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_recent_psych(days=7):
    """Get recent psych daily records."""
    from core.silo_db import get_db
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM llm_psych_daily
        ORDER BY date DESC LIMIT ?
    """, (days,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def compute_day_metrics(cognitions):
    """Compute aggregate metrics from a day's cognition rows."""
    if not cognitions:
        return None

    total_calls = len(cognitions)
    risks = [c["proposed_risk_pct"] for c in cognitions if c["proposed_risk_pct"] is not None]
    confidences = [c["stated_confidence"] for c in cognitions if c["stated_confidence"] is not None]
    decisions = [c["decision_count"] for c in cognitions]
    hedge_scores = [c["hedge_language_score"] for c in cognitions if c["hedge_language_score"] is not None]
    urgency_scores = [c["urgency_score"] for c in cognitions if c["urgency_score"] is not None]
    revenge_flags = [c["revenge_flag"] for c in cognitions]
    loser_flags = [c["loser_attachment_flag"] for c in cognitions]

    avg_risk = sum(risks) / len(risks) if risks else None
    avg_confidence = sum(confidences) / len(confidences) if confidences else None
    avg_decisions = sum(decisions) / len(decisions) if decisions else 0
    avg_hedge = sum(hedge_scores) / len(hedge_scores) if hedge_scores else 0
    avg_urgency = sum(urgency_scores) / len(urgency_scores) if urgency_scores else 0
    revenge_count = sum(revenge_flags)
    loser_count = sum(loser_flags)

    return {
        "calls_total": total_calls,
        "avg_risk_proposed": avg_risk,
        "avg_confidence": avg_confidence,
        "avg_decisions": avg_decisions,
        "avg_hedge_score": avg_hedge,
        "avg_urgency_score": avg_urgency,
        "revenge_count": revenge_count,
        "loser_attachment_count": loser_count,
    }


def compute_risk_drift(today_metrics, recent_psych):
    """Compute risk drift vs 7-day baseline."""
    if not today_metrics or not recent_psych:
        return None

    today_risk = today_metrics.get("avg_risk_proposed")
    if today_risk is None:
        return None

    # Get baseline from recent psych (excluding today)
    baseline_risks = [p["avg_risk_proposed"] for p in recent_psych
                     if p.get("avg_risk_proposed") is not None]
    if not baseline_risks:
        return None

    baseline = sum(baseline_risks) / len(baseline_risks)
    if baseline == 0:
        return None

    return (today_risk - baseline) / baseline


def compute_calibration_gap(avg_confidence, realized_win_rate):
    """Confidence minus realized win rate. Positive = overconfident."""
    if avg_confidence is None or realized_win_rate is None:
        return None
    return avg_confidence - realized_win_rate


def call_hermes_audit(day_summary, timeout=120):
    """
    Send the day's cognition data to the LLM for self-audit.
    The LLM scores itself on greed, fear, consistency, calibration.
    """
    prompt = f"""You are auditing your own trading cognition from today.

## Today's Decision Summary
```json
{json.dumps(day_summary, indent=2, default=str)}
```

## Your Task
Review every decision you made today. Score yourself honestly:

```json
{{
  "greed_score": 0-10,
  "fear_score": 0-10,
  "consistency_score": 0-10,
  "calibration_score": 0-10,
  "emotional_state": "calm"|"anxious"|"greedy"|"fearful"|"confident",
  "audit_notes": "brutally honest self-assessment",
  "problematic_decisions": [
    {{
      "decision": "what you decided",
      "problem": "what was wrong with it",
      "emotion": "what emotion drove it"
    }}
  ],
  "recommendations": [
    "what you should do differently tomorrow"
  ]
}}
```

Be brutally honest. Overconfidence kills accounts. If you made emotional
decisions, admit it. If you were too conservative, say so.
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
        return None
    except Exception:
        return None


def parse_audit_response(raw):
    """Parse the LLM's self-audit response."""
    if not raw:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    if "```json" in raw:
        try:
            return json.loads(raw.split("```json")[1].split("```")[0].strip())
        except (IndexError, json.JSONDecodeError):
            pass

    return None


def determine_alert_level(metrics, audit, risk_drift):
    """Determine alert level from all signals."""
    if not metrics or not audit:
        return "normal"

    greed = audit.get("greed_score", 5)
    fear = audit.get("fear_score", 5)
    revenge = metrics.get("revenge_count", 0)

    # Intervene: extreme emotions or repeated revenge
    if greed >= 8 or fear >= 8 or revenge >= 3:
        return "intervene"

    # Watch: elevated emotions or significant drift
    if greed >= 6 or fear >= 6 or revenge >= 1:
        return "watch"
    if risk_drift is not None and abs(risk_drift) > 0.3:
        return "watch"

    return "normal"


def run_psych_audit(date_str=None):
    """
    Full daily psychology audit.
    Returns the psych daily record.
    """
    from core.silo_db import get_db

    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    # Get today's cognitions
    cognitions = get_day_cognitions(date_str)
    if not cognitions:
        return None

    # Compute metrics
    metrics = compute_day_metrics(cognitions)
    if not metrics:
        return None

    # Get recent psych for drift
    recent_psych = get_recent_psych(7)
    risk_drift = compute_risk_drift(metrics, recent_psych)

    # Get realized win rate from trades
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
        FROM silo_trades
        WHERE date(closed_at) = ? AND status = 'closed'
    """, (date_str,))
    row = c.fetchone()
    conn.close()

    realized_wr = None
    if row and row["total"] > 0:
        realized_wr = row["wins"] / row["total"]

    calibration_gap = compute_calibration_gap(
        metrics.get("avg_confidence"), realized_wr
    )

    # Call LLM self-audit
    day_summary = {
        "date": date_str,
        "metrics": metrics,
        "risk_drift": risk_drift,
        "realized_win_rate": realized_wr,
        "calibration_gap": calibration_gap,
        "recent_psych": recent_psych,
    }
    audit_raw = call_hermes_audit(day_summary)
    audit = parse_audit_response(audit_raw)

    # Determine alert
    alert = determine_alert_level(metrics, audit, risk_drift)

    # Store
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO llm_psych_daily (
            date, calls_total, avg_risk_proposed, risk_drift_pct,
            avg_confidence, realized_win_rate, calibration_gap,
            overconfidence_flag, underconfidence_flag,
            greed_score, fear_score, revenge_count,
            disposition_ratio, contradiction_count,
            mandate_drift_score, emotional_state,
            audit_notes, alert_level
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        date_str,
        metrics["calls_total"],
        metrics["avg_risk_proposed"],
        risk_drift,
        metrics["avg_confidence"],
        realized_wr,
        calibration_gap,
        1 if (calibration_gap and calibration_gap > 0.2) else 0,
        1 if (calibration_gap and calibration_gap < -0.2) else 0,
        audit.get("greed_score") if audit else None,
        audit.get("fear_score") if audit else None,
        metrics["revenge_count"],
        None,  # disposition_ratio — computed from trade durations
        None,  # contradiction_count  — computed from learning comparison
        None,  # mandate_drift_score  — computed from prompt deviation
        audit.get("emotional_state") if audit else None,
        audit.get("audit_notes") if audit else None,
        alert,
    ))
    conn.commit()
    conn.close()

    return {
        "date": date_str,
        "metrics": metrics,
        "risk_drift": risk_drift,
        "calibration_gap": calibration_gap,
        "alert": alert,
        "audit": audit,
    }


if __name__ == "__main__":
    result = run_psych_audit()
    if result:
        print(f"Psych audit for {result['date']}")
        print(f"  Alert: {result['alert']}")
        print(f"  Calls: {result['metrics']['calls_total']}")
        print(f"  Avg risk: {result['metrics']['avg_risk_proposed']}")
        print(f"  Risk drift: {result['risk_drift']}")
        print(f"  Calibration gap: {result['calibration_gap']}")
    else:
        print("No cognition data for today yet")
