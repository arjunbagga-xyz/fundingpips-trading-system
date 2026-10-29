"""
Risk Budget Engine — enforces all FundingPips rules per silo.

Every trade must pass 6 independent budgets + 4 additional gates.
The LLM can propose trades; this engine is the final authority.
"""

import uuid
from datetime import datetime, timedelta
from core.silos import get_rules, silo_config
from core.silo_db import (
    get_silo_row, get_open_silo_trades, get_open_ideas,
    get_silo_trades_today, update_silo, open_idea, update_idea_risk,
    close_idea, platform_now, platform_date, get_db,
)

# 10-minute reopen window — trades reopened within this window
# of closing a loss count as the SAME trade idea
REOPEN_WINDOW_MINUTES = 10


class RiskBudgetResult:
    """Result of a risk budget check."""

    def __init__(self, allowed, reason="", clamped_risk=None, idea_id=None):
        self.allowed = allowed
        self.reason = reason
        self.clamped_risk = clamped_risk
        self.idea_id = idea_id

    def __bool__(self):
        return self.allowed

    def to_dict(self):
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "clamped_risk": self.clamped_risk,
            "idea_id": self.idea_id,
        }


def check_risk_budget(silo_id, symbol, direction, proposed_risk_pct,
                      entry_price, stop_loss, take_profit, strategy_name=""):
    """
    Full risk budget check for a proposed trade.

    Returns RiskBudgetResult with allowed/reason/clamped_risk/idea_id.
    """
    row = get_silo_row(silo_id)
    if not row:
        return RiskBudgetResult(False, "silo not found")

    if row["status"] != "active":
        return RiskBudgetResult(False, f"silo status: {row['status']}")

    rules = get_rules(row["model"])
    balance = row["current_balance"]
    equity = row["equity"] or balance

    # --- Gate 1: Per-trade risk ---
    max_trade_risk = balance * row["risk_per_trade"]
    proposed_risk = balance * proposed_risk_pct
    clamped_risk = min(proposed_risk, max_trade_risk)
    clamped_pct = clamped_risk / balance if balance > 0 else 0

    # The strategy's risk_pct is a PREFERENCE. The silo's risk_per_trade is
    # a HARD CAP. Clamp DOWN to the cap and trade the clamped size.
    #
    # Rejecting for being "too big" is wrong when shrinking is the answer.
    # The old rule (>1.5x cap -> reject) meant the Zero silo (0.25% cap,
    # 0.375% threshold) rejected EVERY signal and never traded at all.
    if entry_price is not None and stop_loss is not None:
        if abs(entry_price - stop_loss) < 1e-12:
            return RiskBudgetResult(
                False,
                "stop loss equals entry - risk undefined"
            )

    # --- Gate 2: Per-idea risk (same direction + same symbol) ---
    idea_id = _resolve_idea_id(silo_id, symbol, direction, rules)
    open_ideas = get_open_ideas(silo_id)
    current_idea_risk = 0.0
    for idea in open_ideas:
        if idea["idea_id"] == idea_id:
            current_idea_risk = idea["total_risk"]
            break

    per_idea_cap = rules.get("per_idea_cap")
    if per_idea_cap is not None:
        max_idea_risk = balance * per_idea_cap
        if current_idea_risk + clamped_risk > max_idea_risk:
            return RiskBudgetResult(
                False,
                f"per-idea cap: current ${current_idea_risk:.2f} + "
                f"new ${clamped_risk:.2f} > ${max_idea_risk:.2f}"
            )

    # --- Gate 3: Combined open risk (Zero's 1% hard breach) ---
    # Clamp DOWN rather than reject: the cap must never be exceeded, but a
    # trade that would push it over should be shrunk to fit, not dropped.
    max_open_risk = rules.get("max_open_risk")
    if max_open_risk is not None:
        current_combined = _compute_combined_open_risk(silo_id)
        max_combined = balance * max_open_risk
        remaining = max_combined - current_combined
        if remaining < 0:
            remaining = 0.0
        if clamped_risk > remaining:
            clamped_risk = remaining
            clamped_pct = clamped_risk / balance if balance > 0 else 0
            print(f"  RISK-CAP {silo_id}: combined ${current_combined:.2f}/"
                  f"${max_combined:.2f} -> size clamped to ${clamped_risk:.2f}")

    # --- Gate 4: Daily loss budget ---
    daily_limit = row["daily_loss_limit"]
    daily_baseline = row["daily_baseline"] or balance
    daily_loss_used = row["daily_loss_used"] or 0.0
    daily_loss_remaining = (daily_baseline * daily_limit) - daily_loss_used

    if daily_loss_remaining <= 0:
        return RiskBudgetResult(False, "daily loss budget exhausted")

    # Projected loss if this trade hits SL
    if clamped_risk > daily_loss_remaining:
        # Clamp to remaining daily budget
        clamped_risk = daily_loss_remaining * 0.95  # 5% buffer
        clamped_pct = clamped_risk / balance if balance > 0 else 0

    # --- Gate 5: Overall loss floor ---
    floor = _compute_floor(row)
    projected_equity = equity - clamped_risk
    if projected_equity < floor:
        return RiskBudgetResult(
            False,
            f"overall floor: projected ${projected_equity:.2f} < "
            f"floor ${floor:.2f}"
        )

    # --- Gate 6: Position count ---
    open_trades = get_open_silo_trades(silo_id)
    if len(open_trades) >= 3:  # MAX_OPEN_POSITIONS_PER_SILO
        return RiskBudgetResult(False, "max open positions reached (3)")

    # --- Gate 7: Daily trade count ---
    trades_today = get_silo_trades_today(silo_id)
    if len(trades_today) >= 5:  # MAX_TRADES_PER_DAY_PER_SILO
        return RiskBudgetResult(False, "max daily trades reached (5)")

    # --- Gate 8: Striking system (1sf, 2ss) ---
    striking_threshold = rules.get("striking_system")
    if striking_threshold is not None:
        # Check if this trade would push any idea past the striking threshold
        idea_peak = 0.0
        for idea in open_ideas:
            if idea["idea_id"] == idea_id:
                idea_peak = idea["peak_floating_loss"]
                break
        new_peak = idea_peak + clamped_risk
        striking_limit = balance * striking_threshold
        if new_peak >= striking_limit:
            # Don't reject — but flag for warning
            # The striking system logs a warning, doesn't force-close
            pass  # Allow but the warning is logged elsewhere

    # All gates passed
    return RiskBudgetResult(
        True,
        "all gates passed",
        clamped_risk=clamped_risk,
        idea_id=idea_id,
    )


def _resolve_idea_id(silo_id, symbol, direction, rules):
    """
    Resolve the trade idea ID for a new trade.

    A trade idea = same direction + same symbol, including any reopened
    within 10 minutes of closing a loss.

    Returns the existing idea_id if one is open or was recently closed,
    otherwise generates a new one.
    """
    open_ideas = get_open_ideas(silo_id)
    for idea in open_ideas:
        if idea["symbol"] == symbol and idea["direction"] == direction:
            return idea["idea_id"]

    # Check recently closed ideas (within 10 min of a loss)
    conn = get_db()
    c = conn.cursor()
    cutoff = (platform_now() - timedelta(minutes=REOPEN_WINDOW_MINUTES)).isoformat()
    c.execute("""
        SELECT idea_id FROM silo_ideas
        WHERE silo_id = ? AND symbol = ? AND direction = ?
          AND status = 'closed' AND closed_at > ?
        ORDER BY closed_at DESC LIMIT 1
    """, (silo_id, symbol, direction, cutoff))
    row = c.fetchone()
    conn.close()
    if row:
        return row["idea_id"]

    return str(uuid.uuid4())[:8]


def _compute_combined_open_risk(silo_id):
    """Combined open risk for the Zero 1% hard breach.

    FundingPips measures this as combined FLOATING LOSS, not the distance to
    stop. Summing risk_amount was wrong twice over:
      1. It counted the full stop distance even for positions in profit.
      2. It could breach the cap while every position is green.

    Uses live floating PnL from MT5 when available (refreshed onto the DB
    rows every cycle by refresh_open_floating); falls back to 0.0 for trades
    with no recorded PnL, which is the conservative direction for a
    *loss* metric.
    """
    open_trades = get_open_silo_trades(silo_id)
    total = 0.0
    for t in open_trades:
        pnl = t.get("pnl")
        if pnl is None:
            continue
        # Only losses count toward combined floating risk.
        if pnl < 0:
            total += abs(pnl)
    return total


def _compute_floor(row):
    """Compute the drawdown floor for a silo."""
    dd_type = row["dd_type"]
    starting = row["starting_balance"]
    max_loss = row["max_loss_limit"]

    if dd_type == "static":
        return starting * (1 - max_loss)
    elif dd_type == "trailing":
        peak = row["peak_equity"] or starting
        if row.get("trailing_locked"):
            return starting  # locked at starting balance
        return peak * (1 - max_loss)
    return starting * (1 - max_loss)


def register_trade(silo_id, idea_id, risk_amount, symbol=None, direction=None):
    """Register a trade's risk against its idea and silo budgets.

    The idea row is created on first use, so the caller must supply
    symbol/direction for a new idea. (Looking the row up first is
    circular: it does not exist yet on the first trade of an idea.)
    """
    open_ideas = get_open_ideas(silo_id)
    for idea in open_ideas:
        if idea["idea_id"] == idea_id:
            new_total = idea["total_risk"] + risk_amount
            update_idea_risk(idea_id, new_total, idea["peak_floating_loss"])
            return
    # New idea — create it if we know what it is
    if symbol is None or direction is None:
        return
    open_idea(idea_id, silo_id, symbol, direction, risk_amount)


def release_trade(silo_id, idea_id, risk_amount):
    """Release a trade's risk when it closes."""
    open_ideas = get_open_ideas(silo_id)
    for idea in open_ideas:
        if idea["idea_id"] == idea_id:
            new_total = max(0, idea["total_risk"] - risk_amount)
            update_idea_risk(idea_id, new_total, idea["peak_floating_loss"])
            if new_total <= 0:
                close_idea(idea_id)
            return


def check_breaches(silo_id):
    """
    Check all breach conditions for a silo.
    Returns list of breach events (empty if clean).
    """
    row = get_silo_row(silo_id)
    if not row or row["status"] != "active":
        return []

    rules = get_rules(row["model"])
    breaches = []
    equity = row["equity"] or row["current_balance"]
    balance = row["current_balance"]

    # Daily loss breach
    daily_baseline = row["daily_baseline"] or balance
    daily_limit = daily_baseline * row["daily_loss_limit"]
    daily_loss = daily_baseline - equity
    # Only a POSITIVE loss breaches. Negative = above baseline (in profit).
    if daily_loss > 0 and daily_loss >= daily_limit:
        breaches.append({
            "type": "daily_loss",
            "detail": f"daily loss ${daily_loss:.2f} >= limit ${daily_limit:.2f}",
        })

    # Overall loss breach
    floor = _compute_floor(row)
    if equity < floor:
        breaches.append({
            "type": "overall_loss",
            "detail": f"equity ${equity:.2f} < floor ${floor:.2f}",
        })

    # Combined open risk breach (Zero)
    max_open_risk = rules.get("max_open_risk")
    if max_open_risk is not None:
        combined = _compute_combined_open_risk(silo_id)
        max_combined = balance * max_open_risk
        if combined > max_combined:
            breaches.append({
                "type": "combined_open_risk",
                "detail": f"combined ${combined:.2f} > max ${max_combined:.2f}",
            })

    # Striking system warning
    striking_threshold = rules.get("striking_system")
    if striking_threshold is not None:
        open_ideas = get_open_ideas(silo_id)
        striking_limit = balance * striking_threshold
        for idea in open_ideas:
            if idea["peak_floating_loss"] >= striking_limit:
                breaches.append({
                    "type": "striking_warning",
                    "detail": f"idea {idea['idea_id']} floating loss "
                              f"${idea['peak_floating_loss']:.2f} >= "
                              f"${striking_limit:.2f}",
                })

    # Zero: weekend holding
    if rules.get("weekend_holding_zero"):
        now = platform_now()
        if now.weekday() == 4 and now.hour >= 16:  # Friday 16:00 UTC+3
            open_trades = get_open_silo_trades(silo_id)
            if open_trades:
                breaches.append({
                    "type": "weekend_holding",
                    "detail": f"{len(open_trades)} positions open on Zero "
                              f"account approaching weekend",
                })

    # Zero: news window hard breach — no positions during restricted window
    if rules.get("news_prohibited"):
        try:
            from core.news_filter import is_in_news_window, get_mt5_news_events
            events = get_mt5_news_events(hours_ahead=1)
            open_trades = get_open_silo_trades(silo_id)
            if open_trades and events:
                for ev in events:
                    if is_in_news_window(ev["time"], "Zero", "Master"):
                        breaches.append({
                            "type": "news_window",
                            "detail": f"{len(open_trades)} positions open during "
                                      f"restricted news window ({ev['name']})",
                        })
                        break
        except Exception:
            pass

    # Zero: max risk per trade idea
    per_idea_cap = rules.get("per_idea_cap")
    if per_idea_cap is not None:
        open_ideas = get_open_ideas(silo_id)
        for idea in open_ideas:
            idea_limit = balance * per_idea_cap
            if idea["total_risk"] > idea_limit:
                breaches.append({
                    "type": "max_risk_per_idea",
                    "detail": f"idea {idea['idea_id']} risk ${idea['total_risk']:.2f} "
                              f"> limit ${idea_limit:.2f}",
                })

    # Zero: min profitable days (7 per rolling 30d)
    retention_days = rules.get("retention_profitable_days")
    if retention_days:
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute("""SELECT COUNT(DISTINCT date(closed_at))
                         FROM silo_trades
                         WHERE silo_id=? AND status='closed'
                           AND closed_at >= datetime('now', '-30 days')
                           AND pnl > 0
                           AND (pnl / ?) >= ?""",
                      (silo_id, balance, rules.get("retention_min_day_pct", 0.0025)))
            prof_days = c.fetchone()[0]
            conn.close()
            if prof_days < retention_days:
                breaches.append({
                    "type": "min_profitable_days",
                    "detail": f"{prof_days} profitable days < {retention_days} required",
                })
        except Exception:
            pass

    # Zero: consistency score (soft breach — biggest day <= 15% of total profit)
    consistency = rules.get("consistency_rule")
    if consistency is not None:
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute("""SELECT date(closed_at), SUM(pnl) as day_pnl
                         FROM silo_trades
                         WHERE silo_id=? AND status='closed'
                           AND closed_at IS NOT NULL
                         GROUP BY date(closed_at)""", (silo_id,))
            rows = c.fetchall()
            conn.close()
            if rows:
                day_pnls = [r[1] for r in rows if r[1] and r[1] > 0]
                if day_pnls:
                    total_profit = sum(day_pnls)
                    biggest_day = max(day_pnls)
                    if total_profit > 0:
                        score = biggest_day / total_profit
                        if score > consistency:
                            breaches.append({
                                "type": "consistency_score",
                                "detail": f"biggest day {score*100:.1f}% > "
                                          f"{consistency*100:.0f}% limit",
                            })
        except Exception:
            pass

    # All models: inactivity (30 days without completed trade)
    inactivity_days = rules.get("inactivity_days")
    if inactivity_days:
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute("""SELECT MAX(closed_at) FROM silo_trades
                         WHERE silo_id=? AND status='closed'""", (silo_id,))
            last_trade = c.fetchone()[0]
            conn.close()
            if last_trade:
                from datetime import datetime, timedelta
                last_dt = datetime.fromisoformat(last_trade.replace("Z", "+00:00"))
                if (datetime.now() - last_dt).days > inactivity_days:
                    breaches.append({
                        "type": "inactivity",
                        "detail": f"no completed trade in {(datetime.now() - last_dt).days} days",
                    })
        except Exception:
            pass

    return breaches


def count_trading_days(silo_id):
    """Distinct calendar days (UTC+3) on which this silo closed a trade.

    FundingPips requires a minimum number of trading days per phase. The
    counter was never incremented anywhere, so no silo could ever advance.
    Derived from the trade table instead of a mutable counter.
    """
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT DISTINCT date(closed_at)
                 FROM silo_trades
                 WHERE silo_id=? AND status='closed' AND closed_at IS NOT NULL""",
              (silo_id,))
    days = len(c.fetchall())
    conn.close()
    return days


def advance_phase(silo_id):
    """Check if a silo has met its phase target and advance.

    Minimum trading days are counted from the trade history rather than a
    counter that nothing ever incremented.
    """
    row = get_silo_row(silo_id)
    if not row or row["status"] != "active":
        return None

    rules = get_rules(row["model"])
    phase = row["phase"]
    target = row["profit_target"]

    if target is None:
        return None  # Master phase — no target

    balance = row["current_balance"]
    progress = (balance - row["starting_balance"]) / row["starting_balance"]

    if progress >= target:
        # Minimum trading days, counted from real trade history.
        min_days = row["min_trading_days"] or 0
        days_done = count_trading_days(silo_id)
        if days_done < min_days:
            print(f"  PHASE-HOLD {silo_id}: target met ({progress*100:+.1f}% "
                  f">= {target*100:.0f}%) but only {days_done}/{min_days} "
                  f"trading day(s)")
            return None  # Need more trading days

        # Advance
        phases = rules["phases"]
        idx = phases.index(phase)
        if idx + 1 < len(phases):
            new_phase = phases[idx + 1]
            new_target = rules["phase_targets"].get(new_phase)
            update_silo(silo_id, phase=new_phase, profit_target=new_target,
                        min_trading_days=rules["min_trading_days"].get(new_phase, 0))
            return new_phase
        else:
            # Passed all phases — Master
            update_silo(silo_id, phase="Master", profit_target=None, status="passed")
            return "Master"

    return None


def get_blockers(silo_id):
    """Get list of current blockers for a silo (for reporting)."""
    row = get_silo_row(silo_id)
    if not row:
        return ["silo not found"]

    blockers = []
    if row["status"] != "active":
        blockers.append(f"status={row['status']}")

    equity = row["equity"] or row["current_balance"]
    balance = row["current_balance"]

    # Daily loss
    daily_baseline = row["daily_baseline"] or balance
    daily_limit = daily_baseline * row["daily_loss_limit"]
    daily_loss = daily_baseline - equity
    if daily_loss >= daily_limit * 0.8:
        blockers.append(f"daily_loss_warning ({daily_loss/daily_limit*100:.0f}%)")

    # Overall
    floor = _compute_floor(row)
    if equity < floor * 1.02:
        blockers.append("near_overall_floor")

    # Positions
    open_trades = get_open_silo_trades(silo_id)
    if len(open_trades) >= 3:
        blockers.append("max_positions")

    # Daily trades
    trades_today = get_silo_trades_today(silo_id)
    if len(trades_today) >= 5:
        blockers.append("max_daily_trades")

    return blockers


if __name__ == "__main__":
    from core.silo_db import init_silo_schema, create_silo
    from core.silos import SILOS, get_rules

    init_silo_schema()

    # Create all 5 silos
    for s in SILOS:
        create_silo(s, get_rules(s["model"]))
        print(f"  {s['silo_id']}  {s['model']}")

    print("\nRisk budget engine ready.")
    print(f"Reopen window: {REOPEN_WINDOW_MINUTES} minutes")
    print(f"Max positions per silo: 3")
    print(f"Max trades per day per silo: 5")
