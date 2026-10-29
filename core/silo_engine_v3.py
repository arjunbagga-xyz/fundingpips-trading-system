"""
Silo Rule Engine — runs every 15 minutes.
Restart MT5 before order phase to clear binding state corruption.
"""
import json
import os
import subprocess
import time
from datetime import datetime
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
    record_silo_trade, snapshot_silo,
    platform_date, get_db,
)
from core.risk_budget import check_risk_budget, check_breaches, advance_phase, get_blockers
from core.news_filter import can_trade_now

SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "USDCHF", "USDCAD", "AUDUSD",
    "NZDUSD", "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY",
    "EURAUD", "EURCAD", "EURCHF", "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD",
    "NZDCAD", "AUDCAD", "AUDNZD", "AUDCHF", "NZDCHF", "XAGUSD",
    "US30", "NAS100", "SPX500", "GER40", "UK100", "JP225", "AUS200",
    "FCHI", "HSI", "BTCUSD", "ETHUSD", "USOIL", "UKOIL",
]


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
        m = self.mt5
        return [
            ORBStrategy(m), VCPStrategy(m), BarbellStrategy(m),
            SMCStrategy(m), TrendStrategy(m), MeanReversionStrategy(m),
            SimpleRRStrategy(m), PriceActionStrategy(m),
        ]

    def run(self):
        print(f"\n{'='*60}")
        print(f"SILO RULE ENGINE — {datetime.now().isoformat()}")
        print(f"{'='*60}")

        # Phase 1: Scan signals with current session
        for sym in SYMBOLS:
            self.mt5.symbol_select(sym, True)
        time.sleep(2)

        init_silo_schema()
        for s in SILOS:
            create_silo(s, get_rules(s["model"]))

        silos = get_all_silos()
        print(f"Active silos: {len(silos)}")

        for silo in silos:
            self._sync_silo(silo)

        all_signals = []
        for strategy in self.strategies:
            try:
                signals = strategy.scan()
                all_signals.extend(signals)
            except Exception as e:
                print(f"  Strategy {strategy.NAME} error: {e}")
        print(f"Total signals: {len(all_signals)}")

        # Phase 2: Execute signals (MT5 already restarted by cron wrapper)
        if all_signals:
            for sym in SYMBOLS:
                self.mt5.symbol_select(sym, True)
            time.sleep(2)

        # Phase 3: Execute signals
        for silo in silos:
            if silo["status"] != "active":
                continue
            self._process_silo(silo, all_signals)

        # Phase 4: Snapshot
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
        update_silo(silo_id, equity=equity, current_balance=balance)
        today = platform_date()
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

    def _filter_signals(self, silo, all_signals):
        silo_id = silo["silo_id"]
        by_symbol = {}
        for signal in all_signals:
            sym = signal["symbol"]
            if sym not in by_symbol:
                by_symbol[sym] = []
            by_symbol[sym].append(signal)
        resolved = []
        for sym, signals in by_symbol.items():
            if len(signals) == 1:
                resolved.append(signals[0])
                continue
            best = max(signals, key=lambda s: s.get("confidence", 0))
            resolved.append(best)
        filtered = []
        for signal in resolved:
            result = check_risk_budget(
                silo_id, signal["symbol"], signal["direction"],
                signal["risk_pct"], signal["entry"],
                signal["stop_loss"], signal["take_profit"], signal["strategy"],
            )
            if not result.allowed:
                print(f"  FILTER {silo_id}: {signal['symbol']} {signal['direction']} — {result.reason}")
                continue
            signal = dict(signal)
            signal["clamped_risk"] = result.clamped_risk
            signal["idea_id"] = result.idea_id
            filtered.append(signal)
        return filtered

    def _execute_signal(self, silo, signal):
        silo_id = silo["silo_id"]
        magic = silo["magic_number"]
        risk_amount = signal.get("clamped_risk", 0)
        if risk_amount <= 0:
            return

        # Get fresh tick — retry with symbol_select
        tick = None
        for attempt in range(3):
            tick = self.mt5.symbol_info_tick(signal["symbol"])
            if tick is not None:
                break
            self.mt5.symbol_select(signal["symbol"], True)
            time.sleep(1.5)
        if tick is not None:
            price = tick.ask if signal["direction"] == "BUY" else tick.bid
        else:
            price = signal["entry"]

        result = self.mt5.order_send({
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": signal["symbol"],
            "volume": 0.01,
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
            record_silo_trade(
                silo_id, ticket, signal["idea_id"], signal["strategy"],
                signal["symbol"], signal["direction"], 0.01,
                signal["entry"], signal["stop_loss"],
                signal["take_profit"], risk_amount, source="rule",
            )
            self.results.append({
                "action": "trade", "silo_id": silo_id, "ticket": ticket,
                "symbol": signal["symbol"], "direction": signal["direction"],
            })
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
