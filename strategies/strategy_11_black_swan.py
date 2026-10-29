"""
PILLAR 2 — Black Swan Harvest (Taleb-inspired).

Goal: asymmetric payoff. Small, fixed risk. Uncapped upside. Rare by design.

This strategy will sit idle most cycles. That is correct behaviour, not a bug.
Taleb's point is that the *distribution* matters: many tiny losses, occasionally
a very large win. Forcing it to trade destroys the edge.

Two jobs:
    A. ENTRY  — detect the tail setup (volatility compression -> expansion,
                or a shock continuation with room to run).
    B. EXIT   — know when the upside has peaked and is coming back down.
                This is the part most systems get wrong: they ride the tail
                all the way back down. We detect exhaustion and leave.

Confluence (per design, INVERTED): higher TF shows compression/coiling,
lower TF shows the expansion. We want the squeeze, then the pop.

Regime gate: `high_vol` or `trending`. Never fires in `choppy`.
"""
try:
    from strategies.strategy_pillar_base import PillarStrategy
    from core import market_structure as ms
except ImportError:
    from strategy_pillar_base import PillarStrategy
    import market_structure as ms


class BlackSwanStrategy(PillarStrategy):

    NAME = "BlackSwan"
    PILLAR = "black_swan"

    TF_CONTEXT = "H4"
    TF_SIGNAL = "H1"
    TF_TRIGGER = "M15"

    RISK_PCT = 0.005          # 0.5% fixed — small because payoff is the point
    REWARD_RATIO = 8.0        # uncapped in spirit; 8R is the soft target
    STOP_LOSS_PIPS = 35

    ALLOWED_REGIMES = ("high_vol", "trending")

    # Compression detection (the "coil")
    COMPRESSION_LOOKBACK = 60
    COMPRESSION_PCTILE_MAX = 35   # ATR percentile must be low = coiled

    # Expansion detection (the "pop")
    EXPANSION_ATR_MULT = 1.8      # trigger bar range vs prior ATR

    # Peak / exhaustion detection (the exit)
    EXHAUSTION_RSI = 78           # overbought on trigger
    DIVERGENCE_LOOKBACK = 20

    def scan_symbol(self, symbol, tick):
        # --- 1. CONTEXT: was there a squeeze? (H4, low vol percentile) ---
        ctx = self.rates(symbol, self.TF_CONTEXT, self.COMPRESSION_LOOKBACK + 110)
        if ctx is None or len(ctx) < self.COMPRESSION_LOOKBACK + 20:
            return None

        ctx_pctl = ms.atr_percentile(ctx, 14, 100)
        ctx_bb = ms.bollinger(ms.closes(ctx), 20, 2.0)
        if ctx_pctl is None or ctx_bb is None:
            return None

        coiled = ctx_pctl <= self.COMPRESSION_PCTILE_MAX
        # BB width must be judged RELATIVE to the instrument: 1% is tight for
        # EURUSD but absurd for NVDA (which runs 9-14%). Use its own percentile.
        bbw_series = []
        ctx_closes = ms.closes(ctx)
        for end in range(20, len(ctx_closes) + 1):
            b = ms.bollinger(ctx_closes[:end], 20, 2.0)
            if b:
                bbw_series.append(b[3])
        bbw_pctl = None
        if len(bbw_series) >= 20:
            cur_bw = bbw_series[-1]
            bbw_pctl = sum(1 for v in bbw_series[-100:] if v <= cur_bw) / len(bbw_series[-100:]) * 100.0
        narrow_bands = bbw_pctl is not None and bbw_pctl <= self.COMPRESSION_PCTILE_MAX

        # --- 2. SIGNAL: is an expansion starting? (H1) ---
        sig = self.rates(symbol, self.TF_SIGNAL, 60)
        if sig is None or len(sig) < 30:
            return None

        sig_atr = ms.atr(sig, 14)
        if sig_atr is None or sig_atr <= 0:
            return None

        last_range = ms.highs(sig)[-1] - ms.lows(sig)[-1]
        last_close = ms.closes(sig)[-1]
        last_open = ms.opens(sig)[-1]
        expanding = last_range >= sig_atr * self.EXPANSION_ATR_MULT

        # Direction of the expansion
        direction = "BUY" if last_close > last_open else "SELL"

        # The setup: coiled context + expanding signal
        if not (coiled or narrow_bands):
            return None
        if not expanding:
            return None

        # --- 3. TRIGGER: confirm on M15, and check we are NOT already exhausted ---
        trig = self.rates(symbol, self.TF_TRIGGER, 40)
        if trig is None or len(trig) < 25:
            return None

        trig_rsi = ms.rsi(ms.closes(trig), 14)
        if trig_rsi is None:
            return None

        # Do not enter a long that is already blown off
        if direction == "BUY" and trig_rsi >= self.EXHAUSTION_RSI:
            return None
        if direction == "SELL" and trig_rsi <= (100 - self.EXHAUSTION_RSI):
            return None

        # Inverted confluence: context compressed, trigger expanded
        confluence = {
            "ctx_atr_percentile": round(ctx_pctl, 1),
            "ctx_bb_width_pct": round(ctx_bb[3], 3),
            "ctx_bb_width_percentile": round(bbw_pctl, 1) if bbw_pctl is not None else None,
            "coiled": coiled,
            "narrow_bands": narrow_bands,
            "expansion_ratio": round(last_range / sig_atr, 2),
            "trigger_rsi": round(trig_rsi, 1),
            "structure": "compression -> expansion",
        }

        pip = self.get_pip_size(symbol)
        if direction == "BUY":
            entry = tick.ask
            sl = entry - self.STOP_LOSS_PIPS * pip
            tp = entry + self.STOP_LOSS_PIPS * pip * self.REWARD_RATIO
        else:
            entry = tick.bid
            sl = entry + self.STOP_LOSS_PIPS * pip
            tp = entry - self.STOP_LOSS_PIPS * pip * self.REWARD_RATIO

        # Confidence is deliberately capped low: we EXPECT to be wrong often.
        # The edge is in the payoff, not the hit rate.
        conf = 0.45 + (self.COMPRESSION_PCTILE_MAX - ctx_pctl) / 200.0

        return self.build_signal(
            symbol, direction, entry, sl, tp, min(0.7, conf),
            f"BlackSwan: {direction} expansion x{last_range/sig_atr:.1f} out of "
            f"coil (H4 ATR pctl={ctx_pctl:.0f}, BB width={ctx_bb[3]:.2f}%)",
            self.TF_TRIGGER, confluence,
        )

    # ------------------------------------------------------------------
    # EXIT LOGIC — called by the engine on open black-swan positions.
    # ------------------------------------------------------------------

    def should_exit(self, symbol, direction, entry_price, current_price,
                    peak_price, bars_held=0):
        """Detect that the tail has peaked and is reversing.

        Returns (exit_now, reason). This is the 'know when the upside has
        reached and is coming back down' requirement.

        Three independent exhaustion signals; any two -> exit.
        """
        trig = self.rates(symbol, self.TF_TRIGGER, 60)
        if trig is None or len(trig) < 30:
            return False, "no data"

        votes = []

        # 1. Momentum exhaustion: RSI extreme in the trade's favour
        rsi = ms.rsi(ms.closes(trig), 14)
        if rsi is not None:
            if direction == "BUY" and rsi >= self.EXHAUSTION_RSI:
                votes.append(f"RSI overbought ({rsi:.1f})")
            if direction == "SELL" and rsi <= (100 - self.EXHAUSTION_RSI):
                votes.append(f"RSI oversold ({rsi:.1f})")

        # 2. Bearish/bullish divergence: price makes a new extreme, momentum does not
        c = ms.closes(trig)
        lb = min(self.DIVERGENCE_LOOKBACK, len(c) - 15)
        if lb >= 10:
            recent_high = max(c[-lb:])
            recent_low = min(c[-lb:])
            rsi_series = []
            for end in range(len(c) - lb, len(c) + 1):
                v = ms.rsi(c[:end], 14)
                if v is not None:
                    rsi_series.append(v)
            if len(rsi_series) >= 5:
                if direction == "BUY" and c[-1] >= recent_high and rsi_series[-1] < max(rsi_series):
                    votes.append("bearish divergence")
                if direction == "SELL" and c[-1] <= recent_low and rsi_series[-1] > min(rsi_series):
                    votes.append("bullish divergence")

        # 3. Giveback: we had a big run and have handed back a chunk of it
        if peak_price and entry_price:
            if direction == "BUY":
                peak_gain = peak_price - entry_price
                given_back = peak_price - current_price
            else:
                peak_gain = entry_price - peak_price
                given_back = current_price - peak_price
            if peak_gain > 0 and given_back > peak_gain * 0.5:
                votes.append(f"gave back {given_back/peak_gain*100:.0f}% of peak")

        if len(votes) >= 2:
            return True, "exhaustion: " + "; ".join(votes)
        return False, "still running"
