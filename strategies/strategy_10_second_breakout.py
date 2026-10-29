"""
PILLAR 1 — Second Breakout (Minervini-inspired).

Goal: capture sustained trends by entering on the SECOND breakout, not the
first. The first breakout out of a base is where amateurs get shaken out;
the second, after a controlled pullback, is where the move actually starts.

Rules (from Minervini's SEPA / VCP):
    1. Trend Template must pass (Stage 2 uptrend) — the inviolable gate.
    2. A base forms: price consolidates after a prior advance.
    3. FIRST breakout occurs (we do NOT trade this).
    4. Pullback/consolidation follows, with CONTRACTING volatility.
    5. SECOND breakout through the consolidation high, on volume.
    -> Enter here. Stop below the last contraction low. Target 20%+ or trail.

Confluence (per design): all timeframes stacked the same direction.
    context TF -> Stage 2 trend confirmed
    signal TF  -> base + contraction sequence
    trigger TF -> the breakout itself

Regime gate: `trending` only. In a range this strategy has no edge.
"""
try:
    from strategies.strategy_pillar_base import PillarStrategy
    from core import market_structure as ms
except ImportError:
    from strategy_pillar_base import PillarStrategy
    import market_structure as ms


class SecondBreakoutStrategy(PillarStrategy):

    NAME = "SecondBreakout"
    PILLAR = "trend"

    TF_CONTEXT = "D1"
    TF_SIGNAL = "H4"
    TF_TRIGGER = "H1"

    RISK_PCT = 0.0075         # 0.75% — lower frequency, higher conviction
    REWARD_RATIO = 3.0        # let winners run
    STOP_LOSS_PIPS = 40

    ALLOWED_REGIMES = ("trending",)

    BASE_LOOKBACK = 40        # bars on signal TF to define the base
    MIN_CONTRACTIONS = 2      # at least 2 pullbacks = a real base
    VOLUME_MULT = 1.4         # breakout bar volume vs average
    MAX_ENTRY_EXT_PCT = 0.5   # don't chase: price within 0.5% of pivot
    MIN_STAGE2_SCORE = 4      # of 6; SMA stack is mandatory regardless

    def scan_symbol(self, symbol, tick):
        # --- 1. CONTEXT: Trend Template on the daily ---
        # Mandatory: the SMA stack (50>150>200, price>50, 200 rising).
        # Scored: the 52-week position checks. Requiring all 6 is Minervini's
        # standard but it almost never triggers on FX, which teaches us nothing.
        ctx = self.rates(symbol, self.TF_CONTEXT, 260)
        passes, detail = ms.trend_template(ctx, price=tick.bid)
        checks = detail.get("checks", {}) if isinstance(detail, dict) else {}
        mandatory = (
            "price_above_sma50", "sma50_above_sma150",
            "sma150_above_sma200", "sma200_rising",
        )
        if not all(checks.get(k) for k in mandatory):
            return None  # not a Stage 2 instrument — no trend to ride
        if detail.get("stage2_score", 0) < self.MIN_STAGE2_SCORE:
            return None

        # --- 2. SIGNAL: base + contracting volatility on H4 ---
        sig = self.rates(symbol, self.TF_SIGNAL, self.BASE_LOOKBACK + 60)
        if sig is None or len(sig) < self.BASE_LOOKBACK:
            return None

        window = sig[-self.BASE_LOOKBACK:]
        base_high = max(ms.highs(window))
        base_low = min(ms.lows(window))
        if base_high <= base_low:
            return None

        depths, contracting = ms.contraction_sequence(sig, lookback=self.BASE_LOOKBACK)
        if not contracting or len(depths) < self.MIN_CONTRACTIONS:
            return None  # no VCP structure

        # --- 3. TRIGGER: the breakout on H1, on volume ---
        trig = self.rates(symbol, self.TF_TRIGGER, 30)
        if trig is None or len(trig) < 25:
            return None

        vols = ms.volumes(trig)
        avg_vol = sum(vols[-20:-1]) / 19 if len(vols) >= 20 else None
        last_vol = vols[-1]
        vol_ok = (avg_vol is not None and avg_vol > 0
                  and last_vol >= avg_vol * self.VOLUME_MULT)

        last_close = ms.closes(trig)[-1]
        last_high = ms.highs(trig)[-1]

        # Breakout = price clearing the base high
        broke_out = last_high > base_high or last_close > base_high
        if not broke_out:
            return None

        # Don't chase — must be near the pivot, not extended
        ext_pct = (tick.bid - base_high) / base_high * 100.0
        if ext_pct > self.MAX_ENTRY_EXT_PCT:
            return None

        # --- MTF confluence: all three stacked the same way ---
        d_ctx = "BUY" if detail.get("checks", {}).get("price_above_sma50") else "SELL"
        d_sig = self.trend_dir(symbol, self.TF_SIGNAL, 10, 30)
        d_trig = self.trend_dir(symbol, self.TF_TRIGGER, 10, 30)
        if not self.stacked(symbol, [d_ctx, d_sig, d_trig]):
            return None
        if d_trig != "BUY":
            return None  # this pillar is long-only by construction

        confluence = {
            "trend_template": detail.get("checks"),
            "stage2_score": detail.get("stage2_score"),
            "contraction_depths": [round(d, 2) for d in depths[-4:]],
            "base_high": round(base_high, 5),
            "base_low": round(base_low, 5),
            "volume_ratio": round(last_vol / avg_vol, 2) if avg_vol else None,
            "entry_extension_pct": round(ext_pct, 3),
            "stacked": {"context": d_ctx, "signal": d_sig, "trigger": d_trig},
        }

        pip = self.get_pip_size(symbol)
        entry = tick.ask
        # Stop below the last contraction low (structural), floored by pip stop
        structural_stop = min(base_low, entry - self.STOP_LOSS_PIPS * pip)
        risk = entry - structural_stop
        if risk <= 0:
            return None
        tp = entry + risk * self.REWARD_RATIO

        conf = 0.6 + 0.05 * detail.get("stage2_score", 0)
        if vol_ok:
            conf += 0.1
        if len(depths) >= 3:
            conf += 0.05

        return self.build_signal(
            symbol, "BUY", entry, structural_stop, tp, min(0.95, conf),
            f"SecondBreakout: 2nd breakout over {base_high:.5f} after "
            f"{len(depths)} contractions {[round(d,1) for d in depths[-3:]]}, "
            f"vol x{last_vol/avg_vol:.1f}" if avg_vol else
            f"SecondBreakout: 2nd breakout over {base_high:.5f}",
            self.TF_TRIGGER, confluence,
        )
