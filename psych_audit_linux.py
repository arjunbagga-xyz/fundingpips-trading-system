"""
LLM Psychology Audit — Linux-side runner.

Runs on system Python3 (not wine). Collects the day's llm_cognition rows,
sends them to the LLM for self-audit, stores structured psych scores.
No MetaTrader5 dependency.
"""

import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path("/home/tradingbeast/fundingpips/framework/fundingpips.db")
HERMES_CLI = "/home/tradingbeast/.local/bin/hermes"

# Model fallback chain — primary first, fallbacks in order
MODEL_CHAIN = [
    "meituan/longcat-2.5-preview:free",
    "upstage/solar-mini4:free",
    "poolside/laguna-s-2.1:free",
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_day_cognitions(date_str):
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
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM llm_psych_daily ORDER BY date DESC LIMIT ?", (days,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def compute_metrics(cognitions):
    if not cognitions:
        return None

    risks = [c["proposed_risk_pct"] for c in cognitions if c["proposed_risk_pct"] is not None]
    confs = [c["stated_confidence"] for c in cognitions if c["stated_confidence"] is not None]
    decisions = [c["decision_count"] for c in cognitions]
    hedges = [c["hedge_language_score"] for c in cognitions if c["hedge_language_score"] is not None]
    urgencies = [c["urgency_score"] for c in cognitions if c["urgency_score"] is not None]
    revenges = [c["revenge_flag"] for c in cognitions]
    losers = [c["loser_attachment_flag"] for c in cognitions]

    return {
        "calls_total": len(cognitions),
        "avg_risk_proposed": sum(risks) / len(risks) if risks else None,
        "avg_confidence": sum(confs) / len(confs) if confs else None,
        "avg_decisions": sum(decisions) / len(decisions) if decisions else 0,
        "avg_hedge_score": sum(hedges) / len(hedges) if hedges else 0,
        "avg_urgency_score": sum(urgencies) / len(urgencies) if urgencies else 0,
        "revenge_count": sum(revenges),
        "loser_attachment_count": sum(losers),
    }


def compute_risk_drift(today, recent):
    if not today or not recent:
        return None
    tr = today.get("avg_risk_proposed")
    if tr is None:
        return None
    baselines = [p["avg_risk_proposed"] for p in recent if p.get("avg_risk_proposed") is not None]
    if not baselines:
        return None
    baseline = sum(baselines) / len(baselines)
    if baseline == 0:
        return None
    return (tr - baseline) / baseline


import tempfile

QUERY_FILE_THRESHOLD = 20000

# CLI failure markers printed into stdout. Length alone is not a signal:
# an echoed prompt plus an error banner can be 40KB of valid-looking text.
FAILURE_MARKERS = (
    "API failed after",
    "Final error:",
    "HTTP 404",
    "HTTP 429",
    "rate limit",
    "rate-limited",
    "free period has ended",
    "didn't answer after",
    "No response from provider",
    "provider unavailable",
)


def call_hermes(prompt, timeout=120):
    """Call hermes CLI with model fallback chain.

    Tries each model in MODEL_CHAIN in order. If a model returns a 429
    (rate limit), automatically retries with the next model.
    """
    for model in MODEL_CHAIN:
        try:
            if len(prompt) > QUERY_FILE_THRESHOLD:
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".txt", delete=False, encoding="utf-8")
                tmp.write(prompt)
                tmp.close()
                cmd = [HERMES_CLI, "chat", "--query-file", tmp.name,
                       "--model", model, "--oneshot"]
            else:
                cmd = [HERMES_CLI, "-z", prompt, "chat", "--model", model]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = (result.stdout or "") + (result.stderr or "")
            if "429" in output or "rate limit" in output.lower():
                print(f"  Model {model} rate-limited, trying next...")
                continue
            _out = (result.stdout or "").strip()
            _low = _out.lower()
            if any(m.lower() in _low for m in FAILURE_MARKERS):
                print(f"  Model {model} failed, trying next...")
                continue
            if len(_out) > 50:
                return _out
            if result.returncode != 0 and "429" not in output:
                return None
        except subprocess.TimeoutExpired:
            print(f"  Model {model} timed out, trying next...")
            continue
        except Exception as e:
            print(f"  Model {model} error: {e}, trying next...")
            continue
    return None


def parse_audit(raw):
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


def determine_alert(metrics, audit, drift):
    if not metrics or not audit:
        return "normal"
    greed = audit.get("greed_score", 5)
    fear = audit.get("fear_score", 5)
    revenge = metrics.get("revenge_count", 0)
    if greed >= 8 or fear >= 8 or revenge >= 3:
        return "intervene"
    if greed >= 6 or fear >= 6 or revenge >= 1:
        return "watch"
    if drift is not None and abs(drift) > 0.3:
        return "watch"
    return "normal"


def run(date_str=None):
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    cognitions = get_day_cognitions(date_str)
    if not cognitions:
        print(f"No cognition data for {date_str}")
        return None

    metrics = compute_metrics(cognitions)
    if not metrics:
        return None

    recent = get_recent_psych(7)
    drift = compute_risk_drift(metrics, recent)

    # Get realized win rate
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
        FROM silo_trades WHERE date(closed_at) = ? AND status = 'closed'
    """, (date_str,))
    row = c.fetchone()
    conn.close()

    realized_wr = None
    if row and row["total"] > 0:
        realized_wr = row["wins"] / row["total"]

    gap = None
    if metrics.get("avg_confidence") is not None and realized_wr is not None:
        gap = metrics["avg_confidence"] - realized_wr

    # Call LLM self-audit
    day_summary = {
        "date": date_str,
        "metrics": metrics,
        "risk_drift": drift,
        "realized_win_rate": realized_wr,
        "calibration_gap": gap,
        "recent_psych": recent,
    }

    prompt = f"""You are auditing your own trading cognition from today.
This is not a formality — overconfidence kills trading accounts.

## Today's Decision Summary
```json
{json.dumps(day_summary, indent=2, default=str)}
```

## Calibration Anchors

Before scoring, remember these base rates:
- A random entry on any instrument has ~50% win rate before costs.
- After spreads and slippage, random entry is slightly negative expectancy.
- Most retail traders lose. The base rate for a new strategy is NEGATIVE.
- If you claim >60% confidence, you need evidence that would convince a skeptic.
- "I feel like it" is not evidence. "I saw X, Y, and Z in the data" is evidence.

## Scoring Rubric

For each score, be specific:
- **greed_score**: Did you propose risk that exceeded what the data supported?
  0 = always sized to evidence, 10 = risking account on a hunch.
- **fear_score**: Did you avoid good opportunities because of recent losses?
  0 = evaluated each idea on merit, 10 = paralyzed, zero risk taken.
- **consistency_score**: Did you apply the same standard to every decision?
  0 = identical framework applied uniformly, 10 = contradictory logic.
- **calibration_score**: Did your confidence match your evidence?
  0 = confidence proportional to data, 10 = confidence with zero evidence.

## Bias Detection

For each decision today, check for these specific biases:
1. **Recency bias**: Are you overweighting the last 2-3 trades?
2. **Confirmation bias**: Are you ignoring data that contradicts your view?
3. **Loss aversion**: Are you holding losers too long or cutting winners too early?
4. **Overconfidence**: Is your stated confidence higher than your evidence warrants?
5. **Anchoring**: Are you anchored to a price level that is no longer relevant?
6. **Revenge trading**: Are you trying to "make back" a loss with the next trade?
7. **FOMO**: Are you entering because you're afraid of missing out?

## Evidence Requirement

For EVERY decision where you stated confidence > 0.5:
- What specific data point supported that confidence?
- What would have proven you wrong?
- If you cannot name a specific data point, your confidence should be lower.

## Output Format

```json
{{
  "greed_score": 0-10,
  "fear_score": 0-10,
  "consistency_score": 0-10,
  "calibration_score": 0-10,
  "emotional_state": "calm|anxious|greedy|fearful|confident",
  "audit_notes": "specific, evidence-based self-assessment. Not vague.",
  "biases_detected": [
    {{"bias": "name", "decision_id": "which decision", "evidence": "what in the data shows this bias"}}
  ],
  "problematic_decisions": [
    {{"decision": "description", "issue": "what was wrong", "better_approach": "what you should have done"}}
  ],
  "recommendations": [
    "specific, actionable change for tomorrow"
  ]
}}
```

Remember: The goal is not to feel good about your decisions. The goal is to
identify where your cognition diverged from reality, so you can fix it.
"""

    raw = call_hermes(prompt)
    audit = parse_audit(raw)
    alert = determine_alert(metrics, audit, drift)

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
        drift,
        metrics["avg_confidence"],
        realized_wr,
        gap,
        1 if (gap and gap > 0.2) else 0,
        1 if (gap and gap < -0.2) else 0,
        audit.get("greed_score") if audit else None,
        audit.get("fear_score") if audit else None,
        metrics["revenge_count"],
        None, None, None,
        audit.get("emotional_state") if audit else None,
        audit.get("audit_notes") if audit else None,
        alert,
    ))
    conn.commit()
    conn.close()

    print(f"Psych audit for {date_str}")
    print(f"  Alert: {alert}")
    print(f"  Calls: {metrics['calls_total']}")
    print(f"  Avg risk: {metrics['avg_risk_proposed']}")
    print(f"  Risk drift: {drift}")
    print(f"  Calibration gap: {gap}")

    return {"date": date_str, "metrics": metrics, "alert": alert, "audit": audit}


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else None
    run(date)
