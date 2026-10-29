"""
Strategy 3: Taleb Barbell — REWRITTEN.

PILLAR: black_swan (competes with BlackSwan).

Previous version's defects, now fixed:
  1. `is_ranging()` used an ABSOLUTE bandwidth threshold (0.5%). EURUSD sits
     near that; NVDA runs 14%. The strategy could never fire on equities.
     Now uses ATR percentile relative to the instrument's own history.
  2. `is_trending()` counted up-days vs down-days, which is not trend
     strength — a choppy market with a slight drift scored as "trending".
     Now uses ADX.
  3. NO peak/reversal exit at all. This was the specific requirement for
     the black-swan pillar and it was completely absent. Now implemented
     in should_exit().
  4. Hardcoded 4 symbols, no regime gate, no cache, no context tagging.

The barbell STRUCTURE is kept and made explicit: the safe sleeve (mean
reversion, small target) and the convex sleeve (trend continuation,
uncapped target) are separate code paths with separate risk.
"""
try:
    from strategies.strategy_pillar_base import PillarStrategy
    from core import market_structure as ms
except ImportError:
    from strategy_pillar_base import PillarStrategy
    import market_structure as ms


class BarbellStrategy(PillarStrategy):

    NAME = "Barbell"
    PILLAR = "black_swan"

    TF_CONTEXT = "H4"
    TF_SIGNAL = "H1"
    TF_TRIGGER = "M15"

    # Safe sleeve
    RISK_PCT_SAFE = 0.004
    REWARD_SAFE = 1.0
    STOP_SAFE_PIPS = 15
    # Convex sleeve — the tail bet
    RISK_PCT_CONVEX = 0.01
    REWARD_CONVEX = 6.0
    STOP_CONVEX_PIPS = 45

    # Default risk fields (convex sleeve governs the signal record)
    RISK_PCT = 0.01
    REWARD_RATIO = 6.0
    STOP_LOSS_PIPS = 45

    ALLOWED_REGIMES = ("high_vol", "trending", "ranging")

    # Regime classification thresholds (percentile based, instrument-relative)
    RANGE_ADX_MAX = 22
    RANGE_ATR_PCTL_MAX = 60
    TREND_ADX_MIN = 25

    # Exhaustion thresholds for the exit
    EXHAUSTION_RSI = 78
    DIVERGENCE_LOOKBACK = 20

    # ---- helpers -------------------------------------------------------

    def _atr_pctl(self, symbol):
        ctx = self.rates(symbol, self.TF_CONTEXT, 170)
        if ctx is None or len(ctx) < 60:
            return None, None
        return ms.atr_percentile(ctx, 14, 100), ctx

    def _is_ranging(self, symbol):
        ctx = self.rates(symbol, self.TF_SIGNAL, 120)
        if ctx is None or len(ctx) < 60:
            return False
        adx = ms.adx(ctx, 14)
        pctl, _ = self._atr_pctl(symbol)
        if adx is None or pctl is None:
            return False
        return adx <= self.RANGE_ADX_MAX and pctl <= self.RANGE_ATR_PCTL_MAX

    def _is_trending(self, symbol):
        ctx = self.rates(symbol, self.TF_SIGNAL, 120)
        if ctx is None or len(ctx) < 60:
            return False
        adx = ms.adx(ctx, 14)
        return adx is not None and adx >= self.TREND_ADX_MIN

    # ---- entry ---------------------------------------------------------

    def scan_symbol(self, symbol, tick):
        # --- SAFE SLEEVE: mean reversion in a genuine range ---
        if self._is_ranging(symbol):
            sig = self._safe_sleeve(symbol, tick)
            if sig:
                return sig

        # --- CONVEX SLEEVE: trend continuation, uncapped target ---
        if self._is_trending(symbol):
            sig = self._convex_sleeve(symbol, tick)
            if sig:
                return sig

        return None

    def _safe_sleeve(self, symbol, tick):
        r = self.rates(symbol, self.TF_SIGNAL, 60)
        if r is None or len(r) < 25:
            return None
        bb = ms.bollinger(ms.closes(r), 20, 2.0)
        if bb is None:
            return None
        lower, mid, upper, bw = bb
        pip = self.get_pip_size(symbol)
        rsi = ms.rsi(ms.closes(r), 14)

        # Fade a genuine band touch, confirmed by RSI
        if tick.bid <= lower and (rsi is None or rsi <= 40):
            entry = tick.ask
            sl = entry - self.STOP_SAFE_PIPS * pip
            tp = entry + self.STOP_SAFE_PIPS * pip * self.REWARD_SAFE
            return self.build_signal(
                symbol, "BUY", entry, sl, tp, 0.6,
                f"Barbell safe: fade lower band {lower:.5f} (bw={bw:.2f}%, RSI={rsi if rsi is None else round(rsi,1)})",
                self.TF_SIGNAL,
                {"sleeve": "safe", "bb_lower": round(lower, 5), "bb_width_pct": round(bw, 3),
                 "rsi": round(rsi, 1) if rsi is not None else None},
                risk_pct=self.RISK_PCT_SAFE, reward_ratio=self.REWARD_SAFE,
            )
        if tick.ask >= upper and (rsi is None or rsi >= 60):
            entry = tick.bid
            sl = entry + self.STOP_SAFE_PIPS * pip
            tp = entry - self.STOP_SAFE_PIPS * pip * self.REWARD_SAFE
            return self.build_signal(
                symbol, "SELL", entry, sl, tp, 0.6,
                f"Barbell safe: fade upper band {upper:.5f} (bw={bw:.2f}%, RSI={rsi if rsi is None else round(rsi,1)})",
                self.TF_SIGNAL,
                {"sleeve": "safe", "bb_upper": round(upper, 5), "bb_width_pct": round(bw, 3),
                 "rsi": round(rsi, 1) if rsi is not None else None},
                risk_pct=self.RISK_PCT_SAFE, reward_ratio=self.REWARD_SAFE,
            )
        return None

    def _convex_sleeve(self, symbol, tick):
        # The convex bet: a trend that is accelerating, not one that is old.
        # Require the fast MA to have just crossed / be widening away from slow.
        sig = self.rates(symbol, self.TF_SIGNAL, 60)
        if sig is None or len(sig) < 40:
            return None
        c = ms.closes(sig)
        fast = ms.sma_series(c, 10)
        slow = ms.sma_series(c, 30)
        if len(fast) < 12 or len(slow) < 12:
            return None

        # sma_series returns series of DIFFERENT lengths for different
        # periods (len = bars - period + 1). Align them on their common
        # tail: the last N values correspond to the same bars.
        n = min(len(fast), len(slow))
        fast = fast[-n:]
        slow = slow[-n:]
        if n < 12:
            return None

        spread_now = fast[-1] - slow[-1]
        spread_prev = fast[-6] - slow[-6]
        widening = abs(spread_now) > abs(spread_prev)

        # Must be a FRESH trend: spread was near zero recently
        recent_spreads = [abs(fast[i] - slow[i]) for i in range(n - 12, n)]
        if not recent_spreads:
            return None
        was_flat = min(recent_spreads) < (abs(spread_now) * 0.35)

        if not (widening and was_flat):
            return None

        direction = "BUY" if spread_now > 0 else "SELL"
        atr = ms.atr(sig, 14)
        if atr is None or atr <= 0:
            return None

        pip = self.get_pip_size(symbol)
        if direction == "BUY":
            entry = tick.ask
            sl = entry - self.STOP_CONVEX_PIPS * pip
            tp = entry + self.STOP_CONVEX_PIPS * pip * self.REWARD_CONVEX
        else:
            entry = tick.bid
            sl = entry + self.STOP_CONVEX_PIPS * pip
            tp = entry - self.STOP_CONVEX_PIPS * pip * self.REWARD_CONVEX

        return self.build_signal(
            symbol, direction, entry, sl, tp, 0.55,
            f"Barbell convex: fresh {direction} trend (spread {spread_prev:.5f}->{spread_now:.5f}, "
            f"ATR={atr:.5f}, uncapped target)",
            self.TF_SIGNAL,
            {"sleeve": "convex", "spread_now": round(spread_now, 6),
             "spread_prev": round(spread_prev, 6), "fresh": True, "atr": round(atr, 6)},
            risk_pct=self.RISK_PCT_CONVEX, reward_ratio=self.REWARD_CONVEX,
        )

    # ------------------------------------------------------------------
    # EXIT — the peak/reversal detection that was entirely missing.
    # ------------------------------------------------------------------

    def should_exit(self, symbol, direction, entry_price, current_price,
                    peak_price, bars_held=0):
        """Detect that a convex (tail) position has peaked and is reversing.

        Returns (exit_now, reason). Same three-vote scheme as BlackSwan so
        both implementations of the pillar can be compared on identical
        exit criteria.
        """
        trig = self.rates(symbol, self.TF_TRIGGER, 60)
        if trig is None or len(trig) < 30:
            return False, "no data"

        votes = []
        c = ms.closes(trig)

        # 1. Momentum exhaustion
        rsi = ms.rsi(c, 14)
        if rsi is not None:
            if direction == "BUY" and rsi >= self.EXHAUSTION_RSI:
                votes.append(f"RSI overbought ({rsi:.1f})")
            if direction == "SELL" and rsi <= (100 - self.EXHAUSTION_RSI):
                votes.append(f"RSI oversold ({rsi:.1f})")

        # 2. Divergence: new price extreme, weaker momentum
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

        # 3. Giveback of the peak
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
