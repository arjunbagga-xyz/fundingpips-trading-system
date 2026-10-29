"""
Strategy 2: Minervini VCP — REWRITTEN.

PILLAR: trend (competes with SecondBreakout).

Previous version's defects, now fixed:
  1. Detected "contraction" as rolling stdev of returns. Real VCP is a
     sequence of PULLBACK DEPTHS that get progressively shallower
     (e.g. 18% -> 12% -> 6%). Now uses swing-point depth analysis.
  2. Had no Trend Template gate at all. Minervini's template is the
     inviolable filter — without it you are buying any old chart pattern.
  3. Was not a "second breakout". Now requires a prior breakout, a
     controlled pullback, and a SECOND breakout through the pivot.
  4. Hardcoded 4 symbols, no regime gate, no cache, no context tagging.

Kept as a SEPARATE strategy from SecondBreakout on purpose: two
implementations of the same pillar let us learn which formulation of
trend-following actually pays.
"""
try:
    from strategies.strategy_pillar_base import PillarStrategy
    from core import market_structure as ms
except ImportError:
    from strategy_pillar_base import PillarStrategy
    import market_structure as ms


class VCPStrategy(PillarStrategy):

    NAME = "VCP"
    PILLAR = "trend"

    TF_CONTEXT = "D1"
    TF_SIGNAL = "H1"
    TF_TRIGGER = "M15"

    RISK_PCT = 0.006
    REWARD_RATIO = 3.0
    STOP_LOSS_PIPS = 30

    ALLOWED_REGIMES = ("trending",)

    # Base / contraction requirements
    BASE_LOOKBACK = 50
    MIN_CONTRACTIONS = 3          # VCP wants 3+ tightening pullbacks
    MAX_FINAL_CONTRACTION_PCT = 8.0   # final contraction must be tight
    VOLUME_MULT = 1.3

    # Trend Template
    MIN_STAGE2_SCORE = 4

    def scan_symbol(self, symbol, tick):
        # --- 1. Trend Template (mandatory SMA stack) ---
        ctx = self.rates(symbol, self.TF_CONTEXT, 260)
        passes, detail = ms.trend_template(ctx, price=tick.bid)
        checks = detail.get("checks", {}) if isinstance(detail, dict) else {}
        mandatory = ("price_above_sma50", "sma50_above_sma150",
                     "sma150_above_sma200", "sma200_rising")
        if not all(checks.get(k) for k in mandatory):
            return None
        if detail.get("stage2_score", 0) < self.MIN_STAGE2_SCORE:
            return None

        # --- 2. Real contraction sequence on the signal TF ---
        sig = self.rates(symbol, self.TF_SIGNAL, self.BASE_LOOKBACK + 60)
        if sig is None or len(sig) < self.BASE_LOOKBACK:
            return None

        depths, contracting = ms.contraction_sequence(sig, lookback=self.BASE_LOOKBACK)
        if not contracting or len(depths) < self.MIN_CONTRACTIONS:
            return None
        # The final contraction must be genuinely tight, or the base is sloppy
        if depths[-1] > self.MAX_FINAL_CONTRACTION_PCT:
            return None

        window = sig[-self.BASE_LOOKBACK:]
        base_high = max(ms.highs(window))
        base_low = min(ms.lows(window))
        if base_high <= base_low:
            return None

        # --- 3. SECOND breakout: a prior break, a pullback, then this break ---
        closes_sig = ms.closes(sig)
        # Find whether price already broke the base high earlier in the window
        prior_break_idx = None
        for i in range(len(sig) - 25, len(sig) - 3):
            if i < 0:
                continue
            if ms.highs(sig)[i] > base_high:
                prior_break_idx = i
                break
        if prior_break_idx is None:
            return None  # no first breakout yet -> too early (this is the point)

        # There must have been a pullback AFTER that first break
        after = ms.lows(sig)[prior_break_idx:]
        if not after or min(after) >= base_high:
            return None  # never pulled back -> chasing the first leg

        # --- 4. TRIGGER: the second breakout on M15, on volume ---
        trig = self.rates(symbol, self.TF_TRIGGER, 40)
        if trig is None or len(trig) < 25:
            return None

        vols = ms.volumes(trig)
        avg_vol = sum(vols[-20:-1]) / 19 if len(vols) >= 20 else None
        vol_ratio = (vols[-1] / avg_vol) if (avg_vol and avg_vol > 0) else 0.0
        vol_ok = vol_ratio >= self.VOLUME_MULT

        last_high = ms.highs(trig)[-1]
        if last_high <= base_high:
            return None  # not breaking out on the trigger TF

        ext_pct = (tick.bid - base_high) / base_high * 100.0
        if ext_pct > 1.0:
            return None  # too extended, missed it

        # --- 5. MTF confluence: context + signal + trigger agree ---
        d_ctx = "BUY"
        d_sig = self.trend_dir(symbol, self.TF_SIGNAL, 10, 30)
        d_trig = self.trend_dir(symbol, self.TF_TRIGGER, 10, 30)
        if not self.stacked(symbol, [d_ctx, d_sig, d_trig]):
            return None
        if d_trig != "BUY":
            return None

        confluence = {
            "trend_template": checks,
            "stage2_score": detail.get("stage2_score"),
            "contraction_depths": [round(d, 2) for d in depths],
            "final_contraction_pct": round(depths[-1], 2),
            "base_high": round(base_high, 5),
            "first_break_index": prior_break_idx,
            "pullback_after_break": True,
            "volume_ratio": round(vol_ratio, 2),
            "extension_pct": round(ext_pct, 3),
            "stacked": {"context": d_ctx, "signal": d_sig, "trigger": d_trig},
        }

        pip = self.get_pip_size(symbol)
        entry = tick.ask
        structural_stop = min(base_low, entry - self.STOP_LOSS_PIPS * pip)
        risk = entry - structural_stop
        if risk <= 0:
            return None
        tp = entry + risk * self.REWARD_RATIO

        conf = 0.55 + 0.05 * detail.get("stage2_score", 0) + 0.05 * len(depths)
        if vol_ok:
            conf += 0.1

        return self.build_signal(
            symbol, "BUY", entry, structural_stop, tp, min(0.95, conf),
            f"VCP: 2nd breakout over {base_high:.5f}, {len(depths)} contractions "
            f"({[round(d,1) for d in depths[-3:]]}), final {depths[-1]:.1f}%, "
            f"vol x{vol_ratio:.1f}",
            self.TF_TRIGGER, confluence,
        )
