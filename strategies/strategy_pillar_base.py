"""
Base class for pillar strategies.

Provides: pip sizing, signal construction, multi-timeframe confluence helpers,
and regime gating. Subclasses implement `scan_symbol()`.

Signal contract (consumed by silo_rule_engine._filter_signals / _execute_signal):
    strategy, symbol, direction, confidence, risk_pct, reward_ratio,
    entry, stop_loss, take_profit, reason
    + pillar, timeframe, confluence_json, regime, session, atr_pct  (context)
"""
import json

try:
    from core import market_structure as ms
except ImportError:
    import market_structure as ms


class PillarStrategy:
    """Shared machinery for pillar strategies."""

    NAME = "Base"
    PILLAR = "unassigned"

    # Timeframe band (set by subclass). Regime may override.
    TF_CONTEXT = "D1"
    TF_SIGNAL = "H4"
    TF_TRIGGER = "H1"

    RISK_PCT = 0.005
    REWARD_RATIO = 2.0
    STOP_LOSS_PIPS = 20

    # Regimes where this strategy is allowed to fire. None = all.
    ALLOWED_REGIMES = None

    def __init__(self, connector, cache=None, regime_ctx=None, symbols=None):
        self.connector = connector
        self.cache = cache
        self.regime_ctx = regime_ctx or {}
        self.symbols = symbols or ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]

    # ---- infrastructure -------------------------------------------------

    def tf(self, name):
        """Map a TF name to an MT5 constant."""
        import MetaTrader5 as mt5
        return {
            "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1,
        }.get(name)

    def rates(self, symbol, tf_name, count):
        """Fetch bars via the shared cache when available."""
        c = self.tf(tf_name)
        if c is None:
            return None
        if self.cache is not None:
            return self.cache.get(symbol, c, count)
        return self.connector.get_rates(symbol, c, count)

    def get_pip_size(self, symbol):
        info = self.connector.get_symbol_info(symbol)
        if not info:
            return 0.0001
        d = info.digits
        if d >= 4:
            return 0.0001
        if d == 3:
            return 0.01
        if d == 2:
            return 0.1
        return 1.0

    def regime_of(self, symbol):
        return (self.regime_ctx.get(symbol) or {}).get("regime", "unknown")

    def session_of(self, symbol):
        return (self.regime_ctx.get(symbol) or {}).get("session", "unknown")

    def atr_pct_of(self, symbol):
        return (self.regime_ctx.get(symbol) or {}).get("atr_pct")

    def regime_ok(self, symbol):
        if not self.ALLOWED_REGIMES:
            return True
        return self.regime_of(symbol) in self.ALLOWED_REGIMES

    def build_signal(self, symbol, direction, entry, stop_loss, take_profit,
                     confidence, reason, timeframe, confluence=None,
                     risk_pct=None, reward_ratio=None):
        return {
            "strategy": self.NAME,
            "pillar": self.PILLAR,
            "symbol": symbol,
            "direction": direction,
            "confidence": confidence,
            "risk_pct": risk_pct if risk_pct is not None else self.RISK_PCT,
            "reward_ratio": reward_ratio if reward_ratio is not None else self.REWARD_RATIO,
            "entry": entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "timeframe": timeframe,
            "regime": self.regime_of(symbol),
            "session": self.session_of(symbol),
            "atr_pct": self.atr_pct_of(symbol),
            "confluence_json": json.dumps(confluence) if confluence else None,
            "reason": reason,
        }

    # ---- confluence helpers --------------------------------------------

    def trend_dir(self, symbol, tf_name, fast=10, slow=30):
        """Direction from MA relationship on one timeframe."""
        r = self.rates(symbol, tf_name, slow + 5)
        if r is None or len(r) < slow:
            return None
        c = ms.closes(r)
        f, s = ms.sma(c, fast), ms.sma(c, slow)
        if f is None or s is None:
            return None
        if f > s:
            return "BUY"
        if f < s:
            return "SELL"
        return None

    def stacked(self, symbol, dirs):
        """True when all provided directions agree (ignoring None)."""
        known = [d for d in dirs if d]
        if len(known) < 2:
            return False
        return all(d == known[0] for d in known)

    # ---- entry point ---------------------------------------------------

    def scan(self):
        """Scan all symbols. Subclasses implement scan_symbol()."""
        signals = []
        for symbol in self.symbols:
            try:
                if not self.regime_ok(symbol):
                    continue
                tick = self.connector.get_tick(symbol)
                if tick is None:
                    continue
                sig = self.scan_symbol(symbol, tick)
                if sig:
                    signals.append(sig)
            except Exception as e:
                print(f"  [{self.NAME}] {symbol} error: {e}")
        return signals

    def scan_symbol(self, symbol, tick):
        raise NotImplementedError
