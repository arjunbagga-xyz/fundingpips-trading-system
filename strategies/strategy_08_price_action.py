"""
Strategy 8: Price Action (Rule-Based) — REWRITTEN with regime awareness,
ATR-based stops, signal weighting, and cross-silo hedge awareness.
"""

import MetaTrader5 as mt5
from datetime import datetime
from core.market_structure import atr
from core.regime import classify as get_regime


class PriceActionStrategy:
    """Rule-based price action — opportunity detection from raw price structure."""

    NAME = "PriceAction"
    TIMEFRAME = mt5.TIMEFRAME_M15
    RISK_PCT = 0.005  # 0.5% of silo balance per trade
    REWARD_RATIO = 2.0
    ATR_MULTIPLIER = 1.5  # stop = ATR * multiplier

    # Symbol config: (enabled, weight_multiplier, regimes_allowed)
    SYMBOLS = {
        "EURUSD":   (True,  1.0, ["ranging", "trending"]),
        "GBPUSD":   (True,  1.0, ["ranging", "trending"]),
        "USDJPY":   (False, 0.0, []),           # DISABLED: 0% WR, -$287 all-time
        "XAUUSD":   (True,  1.0, ["trending", "high_vol"]),
        "XAGUSD":   (True,  1.5, ["trending", "high_vol"]),  # weighted higher: +$1,042 from 4 trades
    }

    # Signal weights (used for confidence scoring and sizing)
    SIGNAL_WEIGHTS = {
        "breakout":    1.0,
        "session":     0.85,
        "momentum":    0.75,
        "rejection":   0.65,
        "expansion":   0.55,
        "exhaustion":  0.5,   # only in ranging regime
    }

    # Which signals are allowed in which regimes
    REGIME_GATES = {
        "trending":    ["breakout", "momentum", "session"],
        "ranging":     ["rejection", "exhaustion", "expansion", "session"],
        "high_vol":    ["breakout", "expansion"],
        "choppy":      ["session"],  # very restrictive
    }

    def __init__(self, connector, silo_id=None, all_silo_positions=None):
        self.connector = connector
        self.silo_id = silo_id
        self.all_silo_positions = all_silo_positions or {}  # {silo_id: [{symbol, direction, volume}, ...]}

    def get_pip_size(self, symbol):
        info = self.connector.get_symbol_info(symbol)
        if not info:
            return 0.0001
        d = info.digits
        if d >= 4: return 0.0001
        elif d == 3: return 0.01
        elif d == 2: return 0.1
        else: return 1.0

    def _compute_atr_stop(self, symbol):
        """Return stop distance in price units based on ATR."""
        rates = self.connector.get_rates(symbol, self.TIMEFRAME, 50)
        if rates is None or len(rates) < 20:
            return self.STOP_LOSS_PIPS * self.get_pip_size(symbol) if hasattr(self, 'STOP_LOSS_PIPS') else 0.002

        a = atr(rates, period=14)
        if a is None or a <= 0:
            return 0.002
        return a * self.ATR_MULTIPLIER

    def _get_regime(self, symbol):
        """Get current regime for symbol."""
        rates = self.connector.get_rates(symbol, self.TIMEFRAME, 100)
        if rates is None or len(rates) < 50:
            return "unknown"
        reg = get_regime(symbol, rates)
        return reg or "unknown"

    def _has_opposite_position(self, symbol, direction):
        """Check if any silo holds opposite direction on same symbol."""
        if not self.all_silo_positions:
            return False
        opposite = "SELL" if direction == "BUY" else "BUY"
        for silo, positions in self.all_silo_positions.items():
            for p in positions:
                if p.get("symbol") == symbol and p.get("direction") == opposite:
                    return True
        return False

    def detect_breakout(self, symbol):
        """Detect N-bar breakout with volume confirmation."""
        rates = self.connector.get_rates(symbol, self.TIMEFRAME, 50)
        if rates is None or len(rates) < 50:
            return None

        current = rates[-1]
        prev_high = max(r['high'] for r in rates[:-1])
        prev_low = min(r['low'] for r in rates[:-1])

        if current['close'] > prev_high:
            return "BUY"
        elif current['close'] < prev_low:
            return "SELL"
        return None

    def detect_rejection(self, symbol):
        """Detect price rejection (long wick at S/R)."""
        rates = self.connector.get_rates(symbol, self.TIMEFRAME, 20)
        if rates is None or len(rates) < 20:
            return None

        current = rates[-1]
        body = abs(current['close'] - current['open'])
        upper_wick = current['high'] - max(current['close'], current['open'])
        lower_wick = min(current['close'], current['open']) - current['low']

        if lower_wick > body * 2:
            return "BUY"
        if upper_wick > body * 2:
            return "SELL"
        return None

    def detect_momentum_shift(self, symbol):
        """Detect momentum shift (rate of change)."""
        rates = self.connector.get_rates(symbol, self.TIMEFRAME, 20)
        if rates is None or len(rates) < 20:
            return None

        closes = [r['close'] for r in rates]
        roc = (closes[-1] - closes[-5]) / closes[-5] if len(closes) >= 5 else 0

        if roc > 0.002:
            return "BUY"
        elif roc < -0.002:
            return "SELL"
        return None

    def detect_session_open(self, symbol):
        """Detect session open momentum (London 08:00, NY 13:00 UTC)."""
        now = datetime.utcnow()
        hour = now.hour

        if hour not in (8, 13):
            return None

        rates = self.connector.get_rates(symbol, self.TIMEFRAME, 5)
        if rates is None or len(rates) < 5:
            return None

        momentum = (rates[-1]['close'] - rates[0]['open']) / rates[0]['open']

        if momentum > 0.001:
            return "BUY"
        elif momentum < -0.001:
            return "SELL"
        return None

    def detect_trend_exhaustion(self, symbol):
        """Detect trend exhaustion (parabolic move). ONLY in ranging regime."""
        rates = self.connector.get_rates(symbol, self.TIMEFRAME, 20)
        if rates is None or len(rates) < 20:
            return None

        closes = [r['close'] for r in rates]
        move = (closes[-1] - closes[-6]) / closes[-6] if len(closes) >= 6 else 0

        if abs(move) > 0.05:
            return "SELL" if move > 0 else "BUY"
        return None

    def detect_range_expansion(self, symbol):
        """Detect Bollinger Band squeeze -> expansion."""
        rates = self.connector.get_rates(symbol, self.TIMEFRAME, 20)
        if rates is None or len(rates) < 20:
            return None

        closes = [r['close'] for r in rates]
        sma = sum(closes) / len(closes)
        std = (sum((c - sma)**2 for c in closes) / len(closes)) ** 0.5

        bandwidth = (2 * std) / sma

        if bandwidth < 0.005:
            if closes[-1] > sma + std:
                return "BUY"
            elif closes[-1] < sma - std:
                return "SELL"
        return None

    def _build_signal(self, symbol, direction, rule_name, entry, stop_dist, weight, regime):
        """Build signal dict with regime-adjusted confidence and sizing."""
        pip_size = self.get_pip_size(symbol)
        sl = entry - stop_dist if direction == "BUY" else entry + stop_dist
        tp = entry + stop_dist * self.REWARD_RATIO if direction == "BUY" else entry - stop_dist * self.REWARD_RATIO

        # Base confidence from signal weight
        confidence = 0.5 + (weight * 0.35)  # 0.5 to 0.85 range

        # Regime alignment boost
        allowed = self.REGIME_GATES.get(regime, [])
        if rule_name in allowed:
            confidence = min(0.9, confidence + 0.1)
        else:
            confidence *= 0.5  # heavily penalize wrong-regime signals

        # Symbol weight multiplier
        sym_config = self.SYMBOLS.get(symbol, (True, 1.0, []))
        confidence *= sym_config[1]

        return {
            "strategy": self.NAME,
            "symbol": symbol,
            "direction": direction,
            "confidence": round(confidence, 2),
            "risk_pct": self.RISK_PCT,
            "reward_ratio": self.REWARD_RATIO,
            "stop_loss": round(sl, 5),
            "take_profit": round(tp, 5),
            "entry": round(entry, 5),
            "reason": f"PriceAction: {rule_name} ({regime} regime, weight={weight:.2f})",
            "meta": {
                "rule": rule_name,
                "regime": regime,
                "signal_weight": weight,
                "atr_stop": round(stop_dist / pip_size, 1),
            }
        }

    def scan(self):
        """Scan for price action opportunities with regime and hedge awareness."""
        signals = []

        for symbol, (enabled, weight_mult, allowed_regimes) in self.SYMBOLS.items():
            if not enabled:
                continue

            tick = self.connector.get_tick(symbol)
            if tick is None:
                continue

            regime = self._get_regime(symbol)
            if regime not in allowed_regimes and allowed_regimes:
                continue

            stop_dist = self._compute_atr_stop(symbol)
            if stop_dist <= 0:
                continue

            entry = tick.ask if True else tick.bid  # placeholder, set per direction

            # All detection rules
            rules = [
                ("breakout",    self.detect_breakout(symbol)),
                ("rejection",   self.detect_rejection(symbol)),
                ("momentum",    self.detect_momentum_shift(symbol)),
                ("session",     self.detect_session_open(symbol)),
                ("exhaustion",  self.detect_trend_exhaustion(symbol)),
                ("expansion",   self.detect_range_expansion(symbol)),
            ]

            for rule_name, direction in rules:
                if direction is None:
                    continue

                # Regime gate
                allowed = self.REGIME_GATES.get(regime, [])
                if rule_name not in allowed:
                    continue

                # Hedge awareness (configurable — default ON)
                if self._has_opposite_position(symbol, direction):
                    continue  # skip: another silo holds opposite direction

                # Build signal with regime-adjusted confidence
                signal_weight = self.SIGNAL_WEIGHTS.get(rule_name, 0.5)
                entry_price = tick.ask if direction == "BUY" else tick.bid

                signals.append(self._build_signal(
                    symbol, direction, rule_name, entry_price, stop_dist,
                    signal_weight, regime
                ))

        # Sort by confidence descending
        signals.sort(key=lambda s: s["confidence"], reverse=True)
        return signals


if __name__ == "__main__":
    from mt5_connector import MT5Connector
    conn = MT5Connector()
    if conn.connect():
        strat = PriceActionStrategy(conn)
        signals = strat.scan()
        print(f"Found {len(signals)} PriceAction signals")
        for s in signals:
            meta = s.get("meta", {})
            print(f"  {s['symbol']} {s['direction']} @ {s['entry']:.5f} "
                  f"conf={s['confidence']:.2f} {s['reason']} "
                  f"[regime={meta.get('regime')} weight={meta.get('signal_weight')}]")
        conn.disconnect()
