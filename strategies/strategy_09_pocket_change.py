"""
PILLAR 3 — Pocket Change.

Goal: high win rate, small predictable deltas. The "always on" income stream
that fills the void while trend and black-swan setups are waiting.

Edge: mean reversion at the EDGE of an established range. We do not fade the
middle of a range (that is noise); we fade confirmed extremes, with the range
itself validated on a higher timeframe.

Confluence (per design):
    higher TF  -> must be RANGE-BOUND (not trending)
    signal TF  -> price must be AT a range extreme
    trigger TF -> reversal confirmation (RSI turn / rejection candle)

Regime gate: only fires in `ranging`. Stands down in trending/high_vol/choppy.
That is deliberate — fading a trend is how accounts die.
"""
try:
    from strategies.strategy_pillar_base import PillarStrategy
    from core import market_structure as ms
except ImportError:
    from strategy_pillar_base import PillarStrategy
    import market_structure as ms


class PocketChangeStrategy(PillarStrategy):

    NAME = "PocketChange"
    PILLAR = "pocket_change"

    TF_CONTEXT = "H1"
    TF_SIGNAL = "M15"
    TF_TRIGGER = "M5"

    RISK_PCT = 0.004          # 0.4% — small, this fires often
    REWARD_RATIO = 1.0        # 1:1 with a high win rate is the edge
    STOP_LOSS_PIPS = 12

    ALLOWED_REGIMES = ("ranging",)

    # Range validation
    RANGE_LOOKBACK = 50       # bars on context TF
    MAX_RANGE_TREND_ADX = 22  # above this the "range" is really a trend
    MIN_RANGE_WIDTH_PCT = 0.15  # too tight = no room to profit

    # Extreme thresholds
    EDGE_PCT = 0.20           # price must be within 20% of the band edge
    RSI_OVERSOLD = 35
    RSI_OVERBOUGHT = 65

    def scan_symbol(self, symbol, tick):
        # --- 1. CONTEXT: is the higher timeframe actually ranging? ---
        ctx = self.rates(symbol, self.TF_CONTEXT, self.RANGE_LOOKBACK + 20)
        if ctx is None or len(ctx) < self.RANGE_LOOKBACK:
            return None

        ctx_adx = ms.adx(ctx, 14)
        if ctx_adx is not None and ctx_adx > self.MAX_RANGE_TREND_ADX:
            return None  # trending, not a range — stand down

        ctx_window = ctx[-self.RANGE_LOOKBACK:]
        range_high = max(ms.highs(ctx_window))
        range_low = min(ms.lows(ctx_window))
        if range_high <= range_low:
            return None
        width_pct = (range_high - range_low) / range_low * 100.0
        if width_pct < self.MIN_RANGE_WIDTH_PCT:
            return None  # range too tight to bother

        # --- 2. SIGNAL: is price at an extreme of that range? ---
        price = tick.bid
        pos_in_range = (price - range_low) / (range_high - range_low)

        at_low = pos_in_range <= self.EDGE_PCT
        at_high = pos_in_range >= (1.0 - self.EDGE_PCT)
        if not (at_low or at_high):
            return None  # mid-range = noise, skip

        # --- 3. TRIGGER: reversal confirmation on the fast timeframe ---
        trig = self.rates(symbol, self.TF_TRIGGER, 40)
        if trig is None or len(trig) < 20:
            return None
        trig_rsi = ms.rsi(ms.closes(trig), 14)
        if trig_rsi is None:
            return None

        confluence = {
            "ctx_adx": round(ctx_adx, 1) if ctx_adx is not None else None,
            "range_high": round(range_high, 5),
            "range_low": round(range_low, 5),
            "range_width_pct": round(width_pct, 3),
            "pos_in_range": round(pos_in_range, 3),
            "trigger_rsi": round(trig_rsi, 1),
        }

        pip = self.get_pip_size(symbol)

        if at_low and trig_rsi <= self.RSI_OVERSOLD:
            # Fade the bottom of the range -> BUY back toward the middle
            entry = tick.ask
            sl = entry - self.STOP_LOSS_PIPS * pip
            tp = entry + self.STOP_LOSS_PIPS * pip * self.REWARD_RATIO
            # confidence scales with how extreme and how oversold
            conf = 0.55 + (self.EDGE_PCT - pos_in_range) * 1.5 + (self.RSI_OVERSOLD - trig_rsi) / 100.0
            return self.build_signal(
                symbol, "BUY", entry, sl, tp, min(0.9, conf),
                f"PocketChange: fade range low {range_low:.5f} (pos={pos_in_range:.2f}, "
                f"RSI={trig_rsi:.1f}, range={width_pct:.2f}%)",
                self.TF_TRIGGER, confluence,
            )

        if at_high and trig_rsi >= self.RSI_OVERBOUGHT:
            entry = tick.bid
            sl = entry + self.STOP_LOSS_PIPS * pip
            tp = entry - self.STOP_LOSS_PIPS * pip * self.REWARD_RATIO
            conf = 0.55 + (pos_in_range - (1.0 - self.EDGE_PCT)) * 1.5 + (trig_rsi - self.RSI_OVERBOUGHT) / 100.0
            return self.build_signal(
                symbol, "SELL", entry, sl, tp, min(0.9, conf),
                f"PocketChange: fade range high {range_high:.5f} (pos={pos_in_range:.2f}, "
                f"RSI={trig_rsi:.1f}, range={width_pct:.2f}%)",
                self.TF_TRIGGER, confluence,
            )

        return None
