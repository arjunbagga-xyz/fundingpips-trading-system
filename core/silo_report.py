"""
Per-Silo Report Sender — generates FundingPips-style reports per silo.

Reports reflect virtual silo balances, NOT the demo account total.
Sent to Telegram daily.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Module-level DB access. These were imported only inside main(), so
# format_silo_report() raised NameError: get_db on every run.
from core.silo_db import get_db, get_all_silos

# Telegram config from .env
ENV_PATHS = [
    Path(__file__).parent.parent / ".env",
    Path("/home/tradingbeast/fundingpips/.env"),
]

def load_env():
    for p in ENV_PATHS:
        if p.exists():
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ[k.strip()] = v.strip().strip('"').strip("'")
            break

load_env()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "610566144")
TELEGRAM_THREAD_ID = os.environ.get("TELEGRAM_TOPIC_REPORTS", "")
# Reports only. Ops/health alerts go to TELEGRAM_TOPIC_OPS_ALERTS.


def format_silo_report(silo, rules):
    """Format a single silo's status as a report section."""
    silo_id = silo["silo_id"]
    model = silo["model"]
    starting = silo["starting_balance"]
    # Calculate real balance from closed PnL
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COALESCE(SUM(pnl), 0) FROM silo_trades WHERE silo_id = ? AND status = 'closed'", (silo_id,))
    closed_pnl = c.fetchone()[0]
    conn.close()
    balance = starting + closed_pnl
    equity = silo["equity"] or balance
    phase = silo["phase"]
    target = silo["profit_target"]
    status = silo["status"]

    pnl = balance - starting
    pnl_pct = (pnl / starting) * 100 if starting > 0 else 0

    # Progress to target
    if target:
        target_amt = starting * (1 + target)
        distance = target_amt - balance
        progress = (balance - starting) / (target_amt - starting) * 100 if target_amt != starting else 0
    else:
        distance = 0
        progress = 100

    # Daily loss
    daily_baseline = silo["daily_baseline"] or balance
    daily_limit = daily_baseline * silo["daily_loss_limit"]
    daily_loss = max(0, daily_baseline - equity)
    daily_loss_pct = (daily_loss / daily_baseline) * 100 if daily_baseline > 0 else 0
    daily_limit_pct = silo["daily_loss_limit"] * 100

    # Overall DD
    if silo["dd_type"] == "trailing":
        peak = silo["peak_equity"] or starting
        if silo.get("trailing_locked"):
            floor = starting
        else:
            floor = peak * (1 - silo["max_loss_limit"])
        dd_type_str = "trailing"
    else:
        floor = starting * (1 - silo["max_loss_limit"])
        dd_type_str = "static"

    dd = max(0, equity - floor)
    dd_pct = (dd / starting) * 100 if starting > 0 else 0
    max_dd_pct = silo["max_loss_limit"] * 100

    # Blockers
    from core.risk_budget import get_blockers
    blockers = get_blockers(silo_id)

    # Status emoji
    status_icon = "OK" if status == "active" else ("PASS" if status == "passed" else "DEAD")

    lines = []
    lines.append(f"SILO: {silo_id} | {model} | {phase}")
    lines.append(f"  Status: {status_icon} | Resets: {silo['resets']}")
    lines.append(f"  Balance: ${balance:,.2f} ({pnl_pct:+.2f}%)")
    if target:
        lines.append(f"  Target: ${starting * (1 + target):,.2f} -> ${distance:,.2f} to pass ({progress:.0f}%)")
    lines.append(f"  Daily Loss: {daily_loss_pct:.1f}%/{daily_limit_pct:.0f}% {'WARN' if daily_loss_pct > daily_limit_pct * 0.7 else 'OK'}")
    lines.append(f"  Max DD ({dd_type_str}): {dd_pct:.1f}%/{max_dd_pct:.0f}% {'WARN' if dd_pct > max_dd_pct * 0.7 else 'OK'}")

    # Combined open risk (Zero)
    if silo.get("max_open_risk"):
        max_combined = balance * silo["max_open_risk"]
        lines.append(f"  Max Open Risk: ${max_combined:.2f} (1% hard breach)")

    # Striking warnings
    if silo.get("striking_warnings", 0) > 0:
        lines.append(f"  Striking Warnings: {silo['striking_warnings']}")

    # Blockers
    if blockers:
        lines.append(f"  Blockers: {', '.join(blockers)}")

    return "\n".join(lines)


def format_cross_silo_summary(silos):
    """Format the cross-silo comparison table."""
    lines = []
    lines.append("ALL SILOS")
    lines.append(f"  {'Silo':<12s} {'Model':<18s} {'Balance':>10s} {'P&L%':>8s} {'Status':<8s}")
    lines.append(f"  {'-'*12} {'-'*18} {'-'*10} {'-'*8} {'-'*8}")

    for s in silos:
        sid = s["silo_id"]
        model = s["model"]
        bal = s["current_balance"]
        pnl_pct = ((bal - s["starting_balance"]) / s["starting_balance"]) * 100
        status = s["status"]
        lines.append(f"  {sid:<12s} {model:<18s} ${bal:>9,.2f} {pnl_pct:>+7.2f}% {status:<8s}")

    return "\n".join(lines)


def format_llm_audit(psych_record):
    """Format the LLM psychology audit section."""
    if not psych_record:
        return "LLM AUDIT: No data yet"

    lines = []
    lines.append("LLM COGNITION AUDIT")
    lines.append(f"  Alert: {psych_record.get('alert_level', 'unknown')}")
    lines.append(f"  Calls: {psych_record.get('calls_total', 0)}")

    greed = psych_record.get("greed_score")
    fear = psych_record.get("fear_score")
    if greed is not None:
        lines.append(f"  Greed: {greed}/10  Fear: {fear}/10")

    drift = psych_record.get("risk_drift_pct")
    if drift is not None:
        lines.append(f"  Risk Drift: {drift:+.1%}")

    gap = psych_record.get("calibration_gap")
    if gap is not None:
        lines.append(f"  Calibration Gap: {gap:+.2f}")

    emotion = psych_record.get("emotional_state")
    if emotion:
        lines.append(f"  Emotional State: {emotion}")

    return "\n".join(lines)


def format_learnings(learnings, max_items=5):
    """Format recent learnings."""
    if not learnings:
        return "LEARNINGS: None yet"

    lines = []
    lines.append("LEARNINGS")
    for i, l in enumerate(learnings[:max_items], 1):
        insight = l.get("insight", "")[:80]
        lines.append(f"  {i}. [{l.get('category', '?')}] {insight}")

    return "\n".join(lines)


def generate_full_report():
    """Generate the complete per-silo report."""
    from core.psych_audit import get_recent_psych

    silos = get_all_silos()

    # Get latest psych
    recent_psych = get_recent_psych(1)
    psych = recent_psych[0] if recent_psych else None

    # Get recent learnings
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM learnings_v2 ORDER BY id DESC LIMIT 5")
    learnings = [dict(r) for r in c.fetchall()]
    conn.close()

    # Build report
    date_str = datetime.now().strftime("%Y-%m-%d")
    parts = []
    parts.append(f"FUNDINGPIPS SILO REPORT - {date_str}")
    parts.append("=" * 50)

    # Per-silo sections
    for silo in silos:
        from core.silos import get_rules
        rules = get_rules(silo["model"])
        parts.append("")
        parts.append(format_silo_report(silo, rules))
        parts.append("-" * 40)

    # Cross-silo summary
    parts.append("")
    parts.append(format_cross_silo_summary(silos))
    parts.append("-" * 40)

    # LLM audit
    parts.append("")
    parts.append(format_llm_audit(psych))
    parts.append("-" * 40)

    # Learnings
    parts.append("")
    parts.append(format_learnings(learnings))

    return "\n".join(parts)


def send_telegram(message):
    """Send message to Telegram."""
    import urllib.request
    import urllib.parse

    if not TELEGRAM_BOT_TOKEN:
        print("No Telegram bot token — printing to stdout")
        print(message)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message[:4000],  # Telegram limit
        # Plain text. The report contains raw data (symbols, PnL, learnings)
        # that breaks Telegram's HTML parser -> HTTP 400.
    }
    if TELEGRAM_THREAD_ID:
        data["message_thread_id"] = TELEGRAM_THREAD_ID

    try:
        req = urllib.request.Request(
            url,
            data=urllib.parse.urlencode(data).encode(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"Telegram send failed: {e}")
        return False


def main():
    """Generate and send the report."""
    report = generate_full_report()
    print(report)
    print()
    send_telegram(report)
    print("Report sent.")


if __name__ == "__main__":
    main()
