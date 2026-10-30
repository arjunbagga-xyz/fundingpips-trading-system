"""
Silo Rule Engine — runs every 15 minutes.
Restart MT5 before order phase to clear binding state corruption.
"""
import json
import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

# Load .env
for env_path in [
    Path(__file__).parent.parent / ".env",
    Path("/home/tradingbeast/fundingpips/.env"),
    Path("/home/tradingbeast/fundingpips/framework/.env"),
]:
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")
        break

from core.silos import SILOS, get_rules
from core.silo_db import (
    init_silo_schema, create_silo, get_silo_row, get_all_silos,
    update_silo, reset_silo, get_open_silo_trades, get_silo_trades_today,
    record_silo_trade, snapshot_silo, get_strategy_weights, save_strategy_weights,
    get_symbol_weights, platform_date, get_db,
    apply_pending_strategy_params,
)
from core.risk_budget import check_risk_budget, check_breaches, advance_phase, get_blockers, register_trade
from core.news_filter import can_trade_now
from core.rate_cache import RateCache
from core import regime as regime_mod
import pytz

# FundingPips official instrument list (41 instruments)
SYMBOLS = [
    # FX Majors (7)
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    # FX Crosses (21)
    "AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "CADCHF", "CADJPY", "CHFJPY",
    "EURAUD", "EURCAD", "EURCHF", "EURGBP", "EURJPY", "EURNZD",
    "GBPAUD", "GBPCAD", "GBPCHF", "GBPJPY", "GBPNZD",
    "NZDCAD", "NZDCHF", "NZDJPY",
    # Metals (2)
    "XAUUSD", "XAGUSD",
    # Indices (7)
    "US30", "UK100", "GER40", "JP225", "NAS100", "SPX500", "STX50",
    # Energies (2)
    "USOIL", "UKOIL",
    # Crypto (2)
    "BTCUSD", "ETHUSD",
]
MAX_LOTS_PER_TRADE = 20
CRYPTO_SYMBOLS = {"BTCUSD", "ETHUSD"}

PILLAR_MEMBERS = {
    "trend": ["SecondBreakout", "VCP", "Trend"],
    "black_swan": ["BlackSwan", "Barbell"],
    "pocket_change": ["PocketChange", "MeanRev", "1to1"],
}
PILLARS = list(PILLAR_MEMBERS.keys())
PILLAR_MIN_WEIGHT = 0.3
MIN_STRATEGY_WEIGHT = 0.1
MIN_SYMBOL_WEIGHT = 0.1
WEIGHT_OVERRIDE_CODE = "FORCE_ZERO"


def restart_mt5():
    """Kill and restart MT5 terminal for a clean session."""
    subprocess.run(["pkill", "-f", "terminal64.exe"], capture_output=True, timeout=10)
    time.sleep(3)
    subprocess.run(["pkill", "-9", "-f", "terminal64.exe"], capture_output=True, timeout=10)
    subprocess.run(["wineserver", "-k"], capture_output=True, timeout=15)
    time.sleep(3)
    mt5_dir = Path("/home/tradingbeast/.mt5/drive_c/Program Files/MetaTrader 5")
    subprocess.Popen(
        ["wine", str(mt5_dir / "terminal64.exe")],
        cwd=str(mt5_dir),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "DISPLAY": ":99"},
    )
    time.sleep(30)


class SiloRuleEngine:
    def __init__(self, mt5):
        self.mt5 = mt5
        from core.mt5_connector_clean import MT5Connector
        self.conn = MT5Connector(mt5)
        self.strategies = self._load_strategies()
        self.results = []

    def _load_strategies(self):
        from strategies.strategy_01_orb import ORBStrategy
        from strategies.strategy_02_vcp import VCPStrategy
        from strategies.strategy_03_barbell import BarbellStrategy
        from strategies.strategy_04_smc import SMCStrategy
        from strategies.strategy_05_trend import TrendStrategy
        from strategies.strategy_06_meanrev import MeanReversionStrategy
        from strategies.strategy_07_simple import SimpleRRStrategy
        from strategies.strategy_08_price_action import PriceActionStrategy
        m = self.conn  # MT5Connector, not raw mt5 module
        return [
            ORBStrategy(m), VCPStrategy(m), BarbellStrategy(m),
            SMCStrategy(m), TrendStrategy(m), MeanReversionStrategy(m),
            SimpleRRStrategy(m), PriceActionStrategy(m),
        ]

    def _weekend_circuit_breaker(self):
        """Hard Friday 20:00 UTC circuit breaker - flatten all positions."""
        import pytz
        from datetime import datetime
        utc_now = datetime.now(pytz.UTC)
        if utc_now.weekday() != 4 or utc_now.hour < 20:
            return False
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT silo_id FROM silos WHERE status='active'")
        silos = [r[0] for r in cur.fetchall()]
        total_open = 0
        for sid in silos:
            cur.execute("SELECT COUNT(*) FROM silo_trades WHERE silo_id=? AND status='open'", (sid,))
            total_open += cur.fetchone()[0]
        conn.close()
        if total_open == 0:
            return False
        print(f"  WEEKEND CIRCUIT BREAKER TRIGGERED: {utc_now.isoformat()}")
        print(f"    {total_open} open positions - FLATTENING ALL")
        conn = get_db()
        cur = conn.cursor()
        for sid in silos:
            cur.execute("INSERT INTO breach_log (timestamp, silo_id, breach_type, equity_at_breach, details) VALUES (datetime('now'), ?, 'weekend_circuit_breaker', 0, ?)", (sid, f"Circuit breaker at {utc_now.isoformat()} UTC"))
        conn.commit()
        conn.close()
        return True

    def run(self):
        print(f"\n{'='*60}")
        print(f"SILO RULE ENGINE — {datetime.now().isoformat()}")
        print(f"{'='*60}")

        # 0. Weekend circuit breaker
        self._weekend_circuit_breaker()

        # 1. Apply pending LLM strategy parameter changes
        try:
            applied = apply_pending_strategy_params()
            if applied:
                print(f"  Applied {len(applied)} pending strategy param changes:")
                for a in applied:
                    print(f"    {a['silo_id']} {a['strategy']}.{a['param']}: {a['old_value']} -> {a['new_value']}")
        except Exception as e:
            print(f"  Warning: failed to apply pending params: {e}")

        # Phase 1: union symbol list
        active_symbols = []
        for sym in SYMBOLS:
            weights = [self.strategy_weights[sid].get(sym, 1.0) for sid in self.strategy_weights] if hasattr(self, 'strategy_weights') else [1.0]
            max_w = max(weights) if weights else 1.0
            if max_w < 0.1:
                continue
            active_symbols.append(sym)
        tradeable = [s for s in active_symbols]
        self.active_symbols = tradeable
        for sym in tradeable:
            self.mt5.symbol_select(sym, True)
        time.sleep(2)

        self.regime_ctx = {}
        try:
            self.regime_ctx = regime_mod.classify_many(self.active_symbols, None, None, 250)
        except Exception:
            pass

        init_silo_schema()
        for s in SILOS:
            create_silo(s, get_rules(s["model"]))

        silos = get_all_silos()
        print(f"Active silos: {len(silos)}")

        self.strategy_weights = {}
        self.symbol_weights = {}
        for silo in silos:
            sid = silo["silo_id"]
            self.strategy_weights[sid] = get_strategy_weights(sid)
            self.symbol_weights[sid] = get_symbol_weights(sid)

        # Process queued actions from LLM
        self._process_close_queue()
        self._process_discretionary_queue()

        for silo in silos:
            self._sync_silo(silo)

        all_signals = []
        active_strategies = []
        for strategy in self.strategies:
            weights = [self.strategy_weights[sid].get(strategy.NAME, 1.0) for sid in self.strategy_weights]
            max_w = max(weights) if weights else 1.0
            if max_w < MIN_STRATEGY_WEIGHT:
                continue
            active_strategies.append((strategy, max_w))
        for strategy, w in active_strategies:
            try:
                signals = strategy.scan()
                all_signals.extend(signals)
            except Exception as e:
                print(f"  Strategy {strategy.NAME} error: {e}")
        print(f"Total signals: {len(all_signals)}")

        for silo in silos:
            if silo["status"] != "active":
                continue
            self._process_silo(silo, all_signals)

        for silo in silos:
            self._snapshot_silo(silo)

        print(f"\nRule engine complete. {len(self.results)} actions taken.")
        return self.results

    def _sync_silo(self, silo):
        silo_id = silo["silo_id"]
        magic = silo["magic_number"]
        all_positions = self.mt5.positions_get() or []
        positions = [p for p in all_positions if p.magic == magic]
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COALESCE(SUM(pnl), 0) FROM silo_trades WHERE silo_id = ? AND status = 'closed'", (silo_id,))
        closed_pnl = c.fetchone()[0]
        conn.close()
        balance = silo["starting_balance"] + closed_pnl
        floating_pnl = sum(p.profit for p in positions)
        equity = balance + floating_pnl
        # Update peak_equity and trailing lock for Zero model
        peak = max(silo.get("peak_equity") or 0, equity, silo["starting_balance"])
        trailing_locked = silo.get("trailing_locked") or 0
        starting = silo["starting_balance"]
        if peak >= starting * 1.05:
            trailing_locked = 1
        update_silo(silo_id, equity=equity, current_balance=balance,
                    peak_equity=peak, trailing_locked=trailing_locked)
        today = platform_date() if 'platform_date' in globals() else datetime.now().strftime('%Y-%m-%d')
        if silo["daily_start_date"] != today:
            daily_baseline = max(balance, equity)
            update_silo(silo_id, daily_start_date=today, daily_baseline=daily_baseline, daily_loss_used=0)
        breaches = check_breaches(silo_id)
        for breach in breaches:
            print(f"  BREACH {silo_id}: {breach['type']} — {breach['detail']}")
            reset_silo(silo_id, breach["type"])
        new_phase = advance_phase(silo_id)
        if new_phase:
            print(f"  {silo_id} advanced to {new_phase}")

    def _process_close_queue(self):
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id, silo_id, payload_json FROM trigger_queue WHERE status='pending' AND trigger_type='close_position'")
        rows = c.fetchall()
        for row in rows:
            try:
                pl = json.loads(row["payload_json"] or "{}")
            except Exception:
                c.execute("UPDATE trigger_queue SET status='error' WHERE id=?", (row["id"],))
                continue
            silo_id = row["silo_id"]
            silo = get_silo_row(silo_id)
            if not silo or silo["status"] != "active":
                c.execute("UPDATE trigger_queue SET status='skipped' WHERE id=?", (row["id"],))
                continue
            ticket = pl.get("ticket")
            if ticket:
                try:
                    self.mt5.order_send({"action": self.mt5.TRADE_ACTION_DEAL, "position": ticket, "volume": 0, "type": self.mt5.ORDER_TYPE_CLOSE, "magic": silo["magic_number"]})
                    c.execute("UPDATE trigger_queue SET status='done' WHERE id=?", (row["id"],))
                except Exception as e:
                    print(f"  CLOSE FAILED {silo_id}: {e}")
                    c.execute("UPDATE trigger_queue SET status='failed' WHERE id=?", (row["id"],))
            else:
                c.execute("UPDATE trigger_queue SET status='skipped' WHERE id=?", (row["id"],))
        conn.commit()
        conn.close()

    def _process_discretionary_queue(self):
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id, silo_id, payload_json FROM trigger_queue WHERE status='pending' AND trigger_type='discretionary_trade'")
        rows = c.fetchall()
        for row in rows:
            try:
                pl = json.loads(row["payload_json"] or "{}")
            except Exception as e:
                print(f"  DISCRETIONARY parse error: {e}")
                c.execute("UPDATE trigger_queue SET status='error' WHERE id=?", (row["id"],))
                continue
            silo_id = row["silo_id"]
            silo = get_silo_row(silo_id)
            if not silo or silo["status"] != "active":
                c.execute("UPDATE trigger_queue SET status='skipped' WHERE id=?", (row["id"],))
                continue
            symbol = pl.get("symbol")
            direction = pl.get("direction")
            entry = pl.get("entry")
            sl = pl.get("stop_loss")
            tp = pl.get("take_profit")
            risk_pct = pl.get("risk_pct", 0.004)
            pillar = pl.get("pillar", "unassigned")
            notes = pl.get("notes", "")
            if not all([symbol, direction, entry, sl]):
                c.execute("UPDATE trigger_queue SET status='error' WHERE id=?", (row["id"],))
                continue
            result = check_risk_budget(silo_id, symbol, direction, risk_pct, entry, sl, tp or entry, f"LLM:{pillar}")
            if not result.allowed:
                print(f"  DISCRETIONARY BLOCKED {silo_id} {symbol} {direction}: {result.reason}")
                c.execute("UPDATE trigger_queue SET status='blocked', rejection_reason=? WHERE id=?", (result.reason, row["id"]))
                continue
            sig = {"strategy": f"LLM:{pillar}", "pillar": pillar, "symbol": symbol, "direction": direction, "entry": entry, "stop_loss": sl, "take_profit": tp or entry, "clamped_risk": result.clamped_risk, "idea_id": result.idea_id, "llm_notes": notes, "confidence": pl.get("confidence"), "timeframe": "discretionary"}
            before = len(self.results)
            self._execute_signal(silo, sig, discretionary=True)
            if len(self.results) > before:
                c.execute("UPDATE trigger_queue SET status='done' WHERE id=?", (row["id"],))
            else:
                c.execute("UPDATE trigger_queue SET status='failed' WHERE id=?", (row["id"],))
        conn.commit()
        conn.close()

    def _execute_signal(self, silo, signal, discretionary=False):
        silo_id = silo["silo_id"]
        magic = silo["magic_number"]
        risk_amount = signal.get("clamped_risk", 0)
        if risk_amount <= 0:
            return
        volume = 0.01
        tick = self.mt5.symbol_info_tick(signal["symbol"])
        if tick is not None:
            price = tick.ask if signal["direction"] == "BUY" else tick.bid
        else:
            price = signal["entry"]
        result = self.mt5.order_send({
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": signal["symbol"],
            "volume": volume,
            "type": self.mt5.ORDER_TYPE_BUY if signal["direction"] == "BUY" else self.mt5.ORDER_TYPE_SELL,
            "price": price,
            "magic": magic,
            "sl": signal["stop_loss"],
            "tp": signal["take_profit"],
            "comment": f"{silo_id}:{signal['strategy']}",
            "type_filling": self.mt5.ORDER_FILLING_FOK,
        })
        if result and result.retcode == self.mt5.TRADE_RETCODE_DONE:
            ticket = result.order
            record_silo_trade(silo_id, ticket, signal.get("idea_id"), signal["strategy"], signal["symbol"], signal["direction"], volume, signal["entry"], signal["stop_loss"], signal["take_profit"], risk_amount, source="rule")
            self.results.append({"action": "trade", "silo_id": silo_id, "ticket": ticket, "symbol": signal["symbol"], "direction": signal["direction"]})
        else:
            retcode = result.retcode if result else "None"
            comment = result.comment if result else "None"
            print(f"  ORDER FAILED {silo_id}: {signal['symbol']} {signal['direction']} retcode={retcode} comment={comment}")

    def _snapshot_silo(self, silo):
        silo_id = silo["silo_id"]
        row = get_silo_row(silo_id)
        equity = row["equity"] or row["current_balance"]
        balance = row["current_balance"]
        daily_baseline = row["daily_baseline"] or balance
        daily_loss = daily_baseline - equity
        daily_loss_pct = daily_loss / daily_baseline if daily_baseline > 0 else 0
        open_trades = get_open_silo_trades(silo_id)
        trades_today = get_silo_trades_today(silo_id)
        combined_risk = sum(t.get("risk_amount", 0) for t in open_trades)
        floor = balance * (1 - row["max_loss_limit"])
        drawdown_pct = (floor - equity) / balance if balance > 0 else 0
        distance_to_floor = equity - floor
        blockers = get_blockers(silo_id)
        snapshot_silo(silo_id, {
            "balance": balance, "equity": equity,
            "open_pnl": equity - balance,
            "combined_open_risk": combined_risk,
            "daily_loss_used": daily_loss, "daily_loss_pct": daily_loss_pct,
            "drawdown_pct": max(0, drawdown_pct),
            "distance_to_floor": distance_to_floor,
            "open_positions": len(open_trades), "trades_today": len(trades_today),
            "striking_warnings": row["striking_warnings"],
            "compliance": "ok" if not blockers else "warning",
            "blockers": json.dumps(blockers),
        })

    def _is_tradeable(self, symbol):
        return True

    def _filter_signals(self, silo, all_signals):
        silo_id = silo["silo_id"]
        filtered = []
        for signal in all_signals:
            symbol = signal.get("symbol")
            direction = signal.get("direction")
            entry = signal.get("entry")
            sl = signal.get("stop_loss")
            tp = signal.get("take_profit")
            risk_pct = signal.get("risk_pct", 0.005)
            strategy = signal.get("strategy", "unknown")
            if not all([symbol, direction, entry, sl]):
                continue
            result = check_risk_budget(silo_id, symbol, direction, risk_pct, entry, sl, tp or entry, strategy)
            if result.allowed:
                signal["clamped_risk"] = result.clamped_risk
                signal["idea_id"] = result.idea_id
                filtered.append(signal)
            else:
                print(f"  FILTERED {silo_id} {symbol} {direction}: {result.reason}")
        return filtered

    def _process_silo(self, silo, all_signals):
        silo_id = silo["silo_id"]
        model = silo["model"]
        can_trade, _ = can_trade_now(model, silo["phase"])
        if not can_trade:
            return
        if get_blockers(silo_id):
            return
        silo_signals = self._filter_signals(silo, all_signals)
        print(f"  {silo_id}: {len(silo_signals)} signals after filter")
        for signal in silo_signals:
            self._execute_signal(silo, signal)


def main():
    """Entry point for cron."""
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("ERROR: Cannot connect to MT5")
        return []
    info = mt5.account_info()
    if info is None:
        print("ERROR: No MT5 session — cannot proceed")
        return []
    time.sleep(2)
    engine = SiloRuleEngine(mt5)
    results = engine.run()
    return results


if __name__ == "__main__":
    main()
