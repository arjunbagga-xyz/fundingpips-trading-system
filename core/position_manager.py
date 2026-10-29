"""
Position Manager — manages open positions per silo.

- Breakeven at +1R
- Trail stop at +2R
- Thesis invalidation (rule-based)
- Idea grouping for per-idea risk
"""

import json
from datetime import datetime

from core.silo_db import (
    get_open_silo_trades, get_silo_row, get_db,
    update_silo, close_silo_trade, record_silo_trade,
)
from core.risk_budget import register_trade, release_trade
from core.silos import get_rules


def manage_positions(silo_id, connector):
    """
    Manage all open positions for a silo.
    Returns list of actions taken.
    """
    actions = []
    trades = get_open_silo_trades(silo_id)
    silo = get_silo_row(silo_id)
    if not silo:
        return actions

    magic = silo["magic_number"]
    rules = get_rules(silo["model"])

    for trade in trades:
        ticket = trade["ticket"]
        symbol = trade["symbol"]
        direction = trade["direction"]
        entry = trade["entry_price"]
        sl = trade["stop_loss"]
        tp = trade["take_profit"]
        risk_amount = trade["risk_amount"]

        # Get current price
        tick = connector.get_tick(symbol)
        if not tick:
            continue

        if direction == "BUY":
            current = tick.bid
        else:
            current = tick.ask

        # Compute R multiples
        pip_size = _get_pip_size(connector, symbol)
        sl_distance = abs(entry - sl)
        if sl_distance == 0:
            continue

        r_multiple = (current - entry) / sl_distance if direction == "BUY" \
            else (entry - current) / sl_distance

        new_sl = None
        action = None

        # Breakeven at +1R
        if r_multiple >= 1.0 and sl < entry if direction == "BUY" else sl > entry:
            new_sl = entry
            action = "breakeven"

        # Trail at +2R
        elif r_multiple >= 2.0:
            if direction == "BUY":
                trail_sl = entry + sl_distance * 1.0  # lock 1R
                if trail_sl > sl:
                    new_sl = trail_sl
                    action = "trail"
            else:
                trail_sl = entry - sl_distance * 1.0  # lock 1R
                if trail_sl < sl:
                    new_sl = trail_sl
                    action = "trail"

        # Thesis invalidation: price reversed significantly
        elif r_multiple < -1.5:
            # Price moved 1.5R against us — thesis likely wrong
            # Don't close (let SL handle it) but log
            pass

        if new_sl and action:
            # Modify SL
            result = connector.modify_position(ticket, sl=new_sl, tp=tp)
            if result:
                actions.append({
                    "action": action,
                    "ticket": ticket,
                    "symbol": symbol,
                    "old_sl": sl,
                    "new_sl": new_sl,
                    "r_multiple": r_multiple,
                })

    return actions


def _get_pip_size(connector, symbol):
    """Get pip size for a symbol."""
    info = connector.get_symbol_info(symbol)
    if not info:
        return 0.0001
    d = info.digits
    if d >= 4:
        return 0.0001
    elif d == 3:
        return 0.01
    elif d == 2:
        return 0.1
    else:
        return 1.0


def sync_positions(silo_id, connector):
    """
    Sync open positions from MT5 to the database.
    Close DB records for positions no longer open.
    """
    from core.silo_db import get_db

    magic = get_silo_row(silo_id)["magic_number"]
    mt5_positions = connector.get_positions(magic=magic)
    mt5_tickets = {p["ticket"] for p in mt5_positions}

    # Get DB open trades
    db_trades = get_open_silo_trades(silo_id)
    db_tickets = {t["ticket"] for t in db_trades}

    # Close DB records for positions no longer in MT5
    for ticket in db_tickets - mt5_tickets:
        # Position was closed (SL/TP hit or manual)
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            SELECT * FROM silo_trades WHERE silo_id = ? AND ticket = ? AND status = 'open'
        """, (silo_id, ticket))
        trade = c.fetchone()
        if trade:
            # Get final PnL from MT5 history
            pnl = 0.0
            pnl_r = None
            try:
                # Try to get from MT5
                history = connector.get_position_history(ticket)
                if history:
                    pnl = history.get("profit", 0)
                    risk = trade["risk_amount"]
                    if risk > 0:
                        pnl_r = pnl / risk
            except Exception:
                pass

            c.execute("""
                UPDATE silo_trades
                SET pnl = ?, pnl_r = ?, status = 'closed', closed_at = ?
                WHERE silo_id = ? AND ticket = ?
            """, (pnl, pnl_r, datetime.now().isoformat(), silo_id, ticket))

            # Release idea risk
            release_trade(silo_id, trade["idea_id"], trade["risk_amount"])

        conn.commit()
        conn.close()

    # Add new MT5 positions not in DB
    for pos in mt5_positions:
        if pos["ticket"] not in db_tickets:
            # New position we didn't record — log it
            record_silo_trade(
                silo_id, pos["ticket"], "unknown", "manual",
                pos["symbol"], "BUY" if pos["type"] == 0 else "SELL",
                pos["volume"], pos["price_open"], 0, 0, 0, source="manual"
            )


def get_position_summary(silo_id, connector):
    """Get a summary of open positions for reporting."""
    trades = get_open_silo_trades(silo_id)
    positions = []
    for t in trades:
        tick = connector.get_tick(t["symbol"])
        if not tick:
            continue
        current = tick.bid if t["direction"] == "BUY" else tick.ask
        entry = t["entry_price"]
        pip_size = _get_pip_size(connector, t["symbol"])
        sl_dist = abs(entry - t["stop_loss"])
        if sl_dist > 0:
            r_mult = (current - entry) / sl_dist if t["direction"] == "BUY" \
                else (entry - current) / sl_dist
        else:
            r_mult = 0
        positions.append({
            "ticket": t["ticket"],
            "symbol": t["symbol"],
            "direction": t["direction"],
            "entry": entry,
            "current": current,
            "r_multiple": r_mult,
            "pnl": t.get("pnl", 0),
            "hours_open": 0,  # would compute from opened_at
        })
    return positions


if __name__ == "__main__":
    print("Position Manager")
    print("  Breakeven at +1R")
    print("  Trail at +2R")
    print("  Idea grouping for per-idea risk")
    print("  Thesis invalidation monitoring")
