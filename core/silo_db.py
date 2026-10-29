"""
Silo database layer — schema + CRUD for the virtual FundingPips accounts.
Extends the existing fundingpips.db with silo tables.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "fundingpips.db"

# Platform time = UTC+3 (FundingPips reset timezone)
PLATFORM_TZ = timezone(timedelta(hours=3))


def platform_now():
    """Current platform time (UTC+3)."""
    return datetime.now(PLATFORM_TZ)


def platform_date():
    """Current platform date string (UTC+3) — used for daily resets."""
    return platform_now().strftime("%Y-%m-%d")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_silo_schema():
    """Create all silo-related tables."""
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS silos (
            silo_id TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            starting_balance REAL NOT NULL,
            current_balance REAL NOT NULL,
            equity REAL,
            phase TEXT NOT NULL,
            peak_equity REAL,
            trailing_locked INTEGER DEFAULT 0,
            daily_baseline REAL,
            daily_start_date TEXT,
            daily_loss_used REAL DEFAULT 0,
            profit_target REAL,
            daily_loss_limit REAL,
            max_loss_limit REAL,
            dd_type TEXT,
            per_idea_cap REAL,
            max_open_risk REAL,
            risk_per_trade REAL,
            consistency_rule REAL,
            profit_concentration REAL,
            striking_warnings INTEGER DEFAULT 0,
            min_trading_days INTEGER DEFAULT 0,
            trading_days_done INTEGER DEFAULT 0,
            profitable_days_30d INTEGER DEFAULT 0,
            magic_number INTEGER,
            status TEXT DEFAULT 'active',
            breach_reason TEXT,
            resets INTEGER DEFAULT 0,
            lifetime_pnl_reset REAL DEFAULT 0.0,
            last_reset_equity REAL,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS silo_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            silo_id TEXT NOT NULL,
            ticket INTEGER,
            idea_id TEXT,
            strategy TEXT,
            symbol TEXT,
            direction TEXT,
            volume REAL,
            entry_price REAL,
            stop_loss REAL,
            take_profit REAL,
            risk_amount REAL,
            pnl REAL,
            pnl_r REAL,
            pillar TEXT,
            status TEXT DEFAULT 'open',
            source TEXT DEFAULT 'rule',
            strategy_weight REAL DEFAULT 1.0,
            opened_at TEXT,
            closed_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS silo_ideas (
            idea_id TEXT PRIMARY KEY,
            silo_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            opened_at TEXT,
            closed_at TEXT,
            peak_floating_loss REAL DEFAULT 0,
            total_risk REAL DEFAULT 0,
            status TEXT DEFAULT 'open'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS silo_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            silo_id TEXT NOT NULL,
            timestamp TEXT,
            balance REAL,
            equity REAL,
            open_pnl REAL,
            combined_open_risk REAL,
            daily_loss_used REAL,
            daily_loss_pct REAL,
            drawdown_pct REAL,
            distance_to_floor REAL,
            open_positions INTEGER,
            trades_today INTEGER,
            striking_warnings INTEGER,
            compliance TEXT,
            blockers TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS llm_cognition (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            silo_id TEXT,
            trigger TEXT,
            context_json TEXT,
            response_raw TEXT,
            decision_json TEXT,
            actions_taken TEXT,
            outcome TEXT,
            proposed_risk_pct REAL,
            stated_confidence REAL,
            decision_count INTEGER,
            hedge_language_score REAL,
            urgency_score REAL,
            loser_attachment_flag INTEGER,
            revenge_flag INTEGER,
            rationale TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS llm_psych_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            calls_total INTEGER,
            avg_risk_proposed REAL,
            risk_drift_pct REAL,
            avg_confidence REAL,
            realized_win_rate REAL,
            calibration_gap REAL,
            overconfidence_flag INTEGER,
            underconfidence_flag INTEGER,
            greed_score REAL,
            fear_score REAL,
            revenge_count INTEGER,
            disposition_ratio REAL,
            contradiction_count INTEGER,
            mandate_drift_score REAL,
            emotional_state TEXT,
            audit_notes TEXT,
            alert_level TEXT
        )
    """)

    c.execute("""CREATE TABLE IF NOT EXISTS strategy_weights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            silo_id TEXT NOT NULL DEFAULT '*',
            strategy TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            source TEXT DEFAULT 'default',
            updated_at TEXT,
            UNIQUE(silo_id, strategy)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS symbol_weights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            silo_id TEXT NOT NULL DEFAULT '*',
            symbol TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            source TEXT DEFAULT 'default',
            updated_at TEXT,
            UNIQUE(silo_id, symbol)
        )
    """)

    c.execute("""CREATE TABLE IF NOT EXISTS learnings_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            silo_id TEXT,
            category TEXT,
            insight TEXT NOT NULL,
            evidence TEXT,
            action TEXT,
            confidence REAL,
            applied INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS trigger_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            silo_id TEXT,
            trigger_type TEXT,
            priority INTEGER DEFAULT 5,
            payload_json TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS news_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time TEXT,
            currency TEXT,
            event_name TEXT,
            impact TEXT,
            actual TEXT,
            forecast TEXT,
            fetched_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS breach_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            silo_id TEXT NOT NULL,
            timestamp TEXT,
            breach_type TEXT,
            detail TEXT,
            equity_at_breach REAL,
            phase_at_breach TEXT,
            reset_performed INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Silo CRUD
# ---------------------------------------------------------------------------

def create_silo(silo_def, rules):
    """Insert a new silo from its definition + model rules."""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    phase = rules["phases"][0]
    target = rules["phase_targets"].get(phase)

    # INSERT ... ON CONFLICT DO NOTHING: create the row once and never
    # touch it again. INSERT OR REPLACE deleted and re-inserted the row on
    # every engine cycle, wiping phase progress, resets, and peak_equity —
    # which made advance_phase() re-fire every 15 minutes.
    c.execute("""
        INSERT INTO silos (
            silo_id, model, starting_balance, current_balance, equity, phase,
            peak_equity, daily_baseline, daily_start_date, profit_target,
            daily_loss_limit, max_loss_limit, dd_type, per_idea_cap,
            max_open_risk, risk_per_trade, consistency_rule,
            profit_concentration, striking_warnings, min_trading_days,
            magic_number, status, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(silo_id) DO NOTHING
    """, (
        silo_def["silo_id"], silo_def["model"], silo_def["starting_balance"],
        silo_def["starting_balance"], silo_def["starting_balance"], phase,
        silo_def["starting_balance"], silo_def["starting_balance"],
        platform_date(), target,
        rules["daily_loss_limit"], rules["max_loss_limit"], rules["dd_type"],
        rules.get("per_idea_cap"), rules.get("max_open_risk"),
        silo_def["risk_per_trade"], rules.get("consistency_rule"),
        rules.get("profit_concentration"), 0,
        rules["min_trading_days"].get(phase, 0),
        silo_def["magic_number"], "active", now, now,
    ))
    conn.commit()
    conn.close()


def get_silo_row(silo_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM silos WHERE silo_id = ?", (silo_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_silos():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM silos ORDER BY silo_id")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def update_silo(silo_id, **fields):
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat()
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [silo_id]
    conn = get_db()
    c = conn.cursor()
    c.execute(f"UPDATE silos SET {cols} WHERE silo_id = ?", vals)
    conn.commit()
    conn.close()


def reset_silo(silo_id, reason):
    """Reset a breached silo back to Phase 1 with starting balance.

    Idempotent: if the silo was already reset for this breach and equity
    has not recovered above the baseline, do nothing. Without this the
    engine reset the same silo ~100 times a day in an infinite loop.
    """
    row = get_silo_row(silo_id)
    if not row:
        return
    # Already reset for this breach? Then leave it alone.
    # breach_reason holds the breach TYPE (e.g. "daily_loss"), not a value.
    if row.get("breach_reason") == reason and row.get("status") == "active":
        base = row["daily_baseline"] or row["current_balance"]
        eq = row["equity"] or row["current_balance"]
        # Only re-breach when the silo has genuinely made back the ground
        # since the reset. Comparing against a baseline that still holds the
        # pre-reset loss made this fire forever.
        if eq < base:
            return

    row = get_silo_row(silo_id)
    if not row:
        return
    from core.silos import get_rules
    rules = get_rules(row["model"])
    phase = rules["phases"][0]
    target = rules["phase_targets"].get(phase)
    sb = row["starting_balance"]

    # Preserve the real result: everything lost before the reset.
    prior_equity = row["equity"] or row["current_balance"]
    prior_loss = (row["starting_balance"] - prior_equity)
    prev_lifetime = row.get("lifetime_pnl_reset") or 0.0
    new_lifetime = prev_lifetime + prior_loss

    conn = get_db()
    c = conn.cursor()
    # The risk baseline must reflect REALITY, not the nominal challenge
    # balance. On a shared demo account the loss is still in MT5, so
    # baseline = sb would make equity < baseline forever -> infinite reset.
    real_equity = prior_equity
    c.execute("""
        UPDATE silos SET
            current_balance = ?, equity = ?, phase = ?, peak_equity = ?,
            trailing_locked = 0, daily_baseline = ?, daily_start_date = ?,
            daily_loss_used = 0, profit_target = ?, striking_warnings = 0,
            trading_days_done = 0, min_trading_days = ?,
            status = 'active', breach_reason = ?,
            resets = resets + 1, lifetime_pnl_reset = ?,
            last_reset_equity = ?, updated_at = ?
        WHERE silo_id = ?
    """, (sb, real_equity, phase, real_equity, real_equity, platform_date(),
          target, rules["min_trading_days"].get(phase, 0),
          reason, new_lifetime, prior_equity,
          datetime.now().isoformat(), silo_id))
    conn.commit()
    conn.close()

    # Log the breach
    log_breach(silo_id, reason, row["equity"], row["phase"], reset_performed=1)


def log_breach(silo_id, breach_type, equity, phase, reset_performed=0, detail=""):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO breach_log (silo_id, timestamp, breach_type, detail,
                                equity_at_breach, phase_at_breach, reset_performed)
        VALUES (?,?,?,?,?,?,?)
    """, (silo_id, datetime.now().isoformat(), breach_type, detail,
          equity, phase, reset_performed))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Trade / idea tracking
# ---------------------------------------------------------------------------

def record_silo_trade(silo_id, ticket, idea_id, strategy, symbol, direction,
                      volume, entry, sl, tp, risk_amount, source="rule",
                      pillar=None, llm_notes=None, regime=None, atr_pct=None,
                      session=None, timeframe=None, confluence_json=None,
                      confidence=None):
    """Record an opened trade. Accepts optional pillar/context fields so
    discretionary ideas and regime data are captured for later learning."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO silo_trades (silo_id, ticket, idea_id, strategy, symbol,
                                 direction, volume, entry_price, stop_loss,
                                 take_profit, risk_amount, status, source, opened_at,
                                 pillar, llm_notes, regime, atr_pct, session,
                                 timeframe, confluence_json, confidence)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,'open',?,?,?,?,?,?,?,?,?,?)
    """, (silo_id, ticket, idea_id, strategy, symbol, direction, volume,
          entry, sl, tp, risk_amount, source, datetime.now().isoformat(),
          pillar, llm_notes, regime, atr_pct, session, timeframe,
          confluence_json, confidence))
    conn.commit()
    conn.close()


def queue_discretionary_trade(silo_id, symbol, direction, entry, stop_loss,
                              take_profit, risk_pct, pillar, notes,
                              confidence=None):
    """Queue an LLM-proposed trade idea for the engine to gate + execute."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO trigger_queue (created_at, silo_id, trigger_type, priority,
                                   payload_json, status)
        VALUES (?,?,?,?,?,'pending')
    """, (datetime.now().isoformat(), silo_id, "discretionary_trade", 2,
          json.dumps({
              "symbol": symbol, "direction": direction, "entry": entry,
              "stop_loss": stop_loss, "take_profit": take_profit,
              "risk_pct": risk_pct, "pillar": pillar, "notes": notes,
              "confidence": confidence,
          })))
    conn.commit()
    conn.close()


def close_silo_trade(silo_id, ticket, pnl, pnl_r=None):
    """Mark a trade closed. If pnl is None, leave it NULL rather than
    writing 0.0 — a missing value is honest, a fabricated zero is not."""
    conn = get_db()
    c = conn.cursor()
    if pnl is None:
        # No evidence: close the row but leave pnl/pnl_r NULL.
        c.execute("""
            UPDATE silo_trades
            SET status = 'closed', closed_at = ?
            WHERE silo_id = ? AND ticket = ?
        """, (datetime.now().isoformat(), silo_id, ticket))
    else:
        c.execute("""
            UPDATE silo_trades
            SET pnl = ?, pnl_r = ?, status = 'closed', closed_at = ?
            WHERE silo_id = ? AND ticket = ?
        """, (pnl, pnl_r, datetime.now().isoformat(), silo_id, ticket))
    conn.commit()
    conn.close()


def refresh_open_floating(silo_id, positions):
    """Write live floating PnL from MT5 onto open DB trades.

    FundingPips daily-loss rules count floating PnL. Without this the
    LLM and the risk manager are both blind to open exposure.
    """
    if not positions:
        return 0
    conn = get_db()
    c = conn.cursor()
    n = 0
    total = 0.0
    for p in positions:
        cur = c.execute("""UPDATE silo_trades SET pnl = ?
                           WHERE silo_id = ? AND ticket = ? AND status = 'open'""",
                        (round(p.profit, 2), silo_id, p.ticket))
        if cur.rowcount:
            n += cur.rowcount
            total += p.profit
    conn.commit()
    conn.close()
    if n:
        print(f"  FLOAT {silo_id}: {n} open trades updated, floating={total:+.2f}")
    return n


def get_open_silo_trades(silo_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT * FROM silo_trades
                 WHERE silo_id = ? AND status = 'open'""", (silo_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_silo_trades_today(silo_id):
    conn = get_db()
    c = conn.cursor()
    today = platform_date()
    c.execute("""SELECT * FROM silo_trades
                 WHERE silo_id = ? AND date(opened_at) = ?""",
              (silo_id, today))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def open_idea(idea_id, silo_id, symbol, direction, total_risk):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO silo_ideas
            (idea_id, silo_id, symbol, direction, opened_at, total_risk, status)
        VALUES (?,?,?,?,?,?,'open')
    """, (idea_id, silo_id, symbol, direction,
          datetime.now().isoformat(), total_risk))
    conn.commit()
    conn.close()


def update_idea_risk(idea_id, total_risk, peak_loss):
    conn = get_db()
    c = conn.cursor()
    c.execute("""UPDATE silo_ideas
                 SET total_risk = ?, peak_floating_loss = ?
                 WHERE idea_id = ?""", (total_risk, peak_loss, idea_id))
    conn.commit()
    conn.close()


def close_idea(idea_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""UPDATE silo_ideas
                 SET status = 'closed', closed_at = ?
                 WHERE idea_id = ?""", (datetime.now().isoformat(), idea_id))
    conn.commit()
    conn.close()


def get_open_ideas(silo_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT * FROM silo_ideas
                 WHERE silo_id = ? AND status = 'open'""", (silo_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_strategy_weights(silo_id=None):
    """Return {strategy: weight}. Per-silo overrides global ('*')."""
    conn = get_db()
    c = conn.cursor()
    out = {}
    if silo_id is None:
        # Global view: prefer any row, per-silo wins over '*'
        c.execute("SELECT silo_id, strategy, weight FROM strategy_weights ORDER BY silo_id='*'")
        for r in c.fetchall():
            out[r["strategy"]] = r["weight"]
    else:
        c.execute("SELECT strategy, weight FROM strategy_weights WHERE silo_id='*'")
        for r in c.fetchall():
            out[r["strategy"]] = r["weight"]
        c.execute("SELECT strategy, weight FROM strategy_weights WHERE silo_id=?", (silo_id,))
        for r in c.fetchall():
            out[r["strategy"]] = r["weight"]
    conn.close()
    return out





def apply_pending_strategy_params():
    import sqlite3
    from datetime import datetime
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT id, silo_id, strategy, param_name, new_value, reasoning
        FROM strategy_params_v2
        WHERE status = 'pending'
        ORDER BY timestamp
    """)
    pending = cur.fetchall()
    applied = []
    for row in pending:
        if not _validate_param(row['strategy'], row['param_name'], row['new_value']):
            cur.execute("""
                UPDATE strategy_params_v2 SET status='rejected', applied_at=?, applied_by='validation'
                WHERE id=?
            """, (datetime.now().isoformat(), row['id']))
            continue
        old = _get_current_param(row['silo_id'], row['strategy'], row['param_name'])
        param_key = f"{row['strategy']}.{row['param_name']}"
        cur.execute("""
            INSERT OR REPLACE INTO strategy_weights (silo_id, strategy, weight, updated_at)
            VALUES (?, ?, ?, ?)
        """, (row['silo_id'], param_key, row['new_value'], datetime.now().isoformat()))
        cur.execute("""
            UPDATE strategy_params_v2 SET status='applied', applied_at=?, applied_by='engine'
            WHERE id=?
        """, (datetime.now().isoformat(), row['id']))
        applied.append({
            'silo_id': row['silo_id'], 'strategy': row['strategy'],
            'param': row['param_name'], 'old_value': old,
            'new_value': row['new_value'], 'reasoning': row['reasoning']
        })
    conn.commit()
    conn.close()
    return applied


def _validate_param(strategy, param, value):
    bounds = {
        'risk_pct': (0.001, 0.02), 'reward_ratio': (1.0, 5.0),
        'atr_multiplier': (0.5, 3.0), 'signal_confidence_floor': (0.3, 0.9),
    }
    if param not in bounds: return False
    lo, hi = bounds[param]
    return lo <= value <= hi


def _get_current_param(silo_id, strategy, param):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT weight FROM strategy_weights WHERE silo_id=? AND strategy=?',
                (silo_id, f'{strategy}.{param}'))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def save_strategy_weights(weights, source="llm", silo_id="*"):
    """Save strategy weights. weights = {strategy_name: weight}.

    silo_id='*' means a global default; otherwise per-silo.
    """
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    for strategy, weight in weights.items():
        c.execute("""
            INSERT OR REPLACE INTO strategy_weights (silo_id, strategy, weight, source, updated_at)
            VALUES (?,?,?,?,?)
        """, (silo_id, strategy, weight, source, now))
    conn.commit()
    conn.close()


def save_symbol_weights(weights, source="llm", silo_id="*"):
    """Save symbol weights. weights = {symbol: weight}. silo_id='*' = global."""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    for symbol, weight in weights.items():
        c.execute("""
            INSERT OR REPLACE INTO symbol_weights (silo_id, symbol, weight, source, updated_at)
            VALUES (?,?,?,?,?)
        """, (silo_id, symbol, weight, source, now))
    conn.commit()
    conn.close()


def get_symbol_weights(silo_id=None):
    """Return {symbol: weight}. Per-silo overrides global ('*')."""
    conn = get_db()
    c = conn.cursor()
    out = {}
    if silo_id is None:
        c.execute("SELECT silo_id, symbol, weight FROM symbol_weights ORDER BY silo_id='*'")
        for r in c.fetchall():
            out[r["symbol"]] = r["weight"]
    else:
        c.execute("SELECT symbol, weight FROM symbol_weights WHERE silo_id='*'")
        for r in c.fetchall():
            out[r["symbol"]] = r["weight"]
        c.execute("SELECT symbol, weight FROM symbol_weights WHERE silo_id=?", (silo_id,))
        for r in c.fetchall():
            out[r["symbol"]] = r["weight"]
    conn.close()
    return out


def queue_close_action(silo_id, ticket, reason="llm_decision"):
    """Queue a close action for the engine to execute."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO trigger_queue (created_at, silo_id, trigger_type, priority, payload_json, status)
        VALUES (?,?,?,?,?,'pending')
    """, (datetime.now().isoformat(), silo_id, "close_position", 1,
          json.dumps({"ticket": ticket, "reason": reason})))
    conn.commit()
    conn.close()


def snapshot_silo(silo_id, data):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO silo_state (silo_id, timestamp, balance, equity, open_pnl,
            combined_open_risk, daily_loss_used, daily_loss_pct, drawdown_pct,
            distance_to_floor, open_positions, trades_today, striking_warnings,
            compliance, blockers)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (silo_id, datetime.now().isoformat(), data.get("balance"),
          data.get("equity"), data.get("open_pnl"),
          data.get("combined_open_risk"), data.get("daily_loss_used"),
          data.get("daily_loss_pct"), data.get("drawdown_pct"),
          data.get("distance_to_floor"), data.get("open_positions"),
          data.get("trades_today"), data.get("striking_warnings"),
          data.get("compliance"), data.get("blockers")))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_silo_schema()
    print(f"Silo schema initialized at {DB_PATH}")


def save_regime_state(symbol, regime, volatility=None, atr_pct=None,
                      trend_strength=None, session=None, tf_band=None,
                      details_json=None):
    """Persist a per-symbol regime classification."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO regime_state (timestamp, symbol, regime, volatility,
                                  atr_pct, trend_strength, session, tf_band,
                                  details_json)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (datetime.now().isoformat(), symbol, regime, volatility, atr_pct,
          trend_strength, session, tf_band,
          json.dumps(details_json) if details_json else None))
    conn.commit()
    conn.close()


def save_market_data(timestamp, symbol, timeframe, open=None, high=None,
                     low=None, close=None, volume=None, bid=None, ask=None,
                     atr=None, adx=None, rsi=None, regime=None):
    """Write a market data point for the LLM cognition to read."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO market_data
        (timestamp, symbol, timeframe, open, high, low, close, volume,
         bid, ask, atr, adx, rsi, regime)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (timestamp, symbol, timeframe, open, high, low, close, volume,
          bid, ask, atr, adx, rsi, regime))
    conn.commit()
    conn.close()


def get_market_data(symbols=None, timeframes=("H1",), limit=30):
    """Read recent market data for the cognition context."""
    conn = get_db()
    c = conn.cursor()
    if symbols:
        placeholders = ",".join("?" * len(symbols))
        c.execute(f"""
            SELECT * FROM market_data
            WHERE symbol IN ({placeholders}) AND timeframe IN ({",".join("?" * len(timeframes))})
            ORDER BY timestamp DESC LIMIT ?
        """, (*symbols, *timeframes, limit))
    else:
        c.execute(f"""
            SELECT * FROM market_data
            WHERE timeframe IN ({",".join("?" * len(timeframes))})
            ORDER BY timestamp DESC LIMIT ?
        """, (*timeframes, limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def queue_strategy_params(silo_id, strategy, param_name, new_value, reasoning="", proposed_by="llm"):
    """Queue a strategy parameter change for the engine to apply."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO strategy_params_v2 (silo_id, strategy, param_name, new_value, proposed_by, reasoning, status)
        VALUES (?,?,?,?,?,?,'pending')
    """, (silo_id, strategy, param_name, new_value, proposed_by, reasoning))
    conn.commit()
    conn.close()


def queue_strategy_proposal(strategy_name, pillar, regime_target, code, description="", notes="", proposed_by="llm"):
    """Queue a new strategy proposal for sandbox testing."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO strategy_proposals_v2 (proposed_by, strategy_name, pillar, regime_target, code, description, notes, status)
        VALUES (?,?,?,?,?,?,?,'pending')
    """, (proposed_by, strategy_name, pillar, regime_target, code, description, notes))
    conn.commit()
    conn.close()
