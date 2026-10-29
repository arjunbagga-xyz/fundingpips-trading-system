"""
Regime classifier — deterministic, no ML.

Classifies the current market regime per symbol and maps it to a timeframe
band. This is the mechanism that makes timeframes dynamic *and learnable*:
the regime is recorded on every trade, so we can later correlate
(strategy, regime, timeframe) -> outcome and let the LLM tune the mapping.

Regimes:
    trending    — directional, ADX strong
    ranging     — mean-reverting, ADX weak, vol normal/low
    high_vol    — volatility percentile extreme (event/shock)
    choppy      — weak trend + high whipsaw (bad for everything)

Volatility bands (independent axis):
    low | normal | high   (from ATR percentile)

Timeframe bands:
    fast    — M5/M15   (pocket change, ranging)
    medium  — M15/H1   (black swan, high vol)
    slow    — H1/H4/D1 (trend following)
"""
from datetime import datetime, timezone

try:
    from core import market_structure as ms
except ImportError:
    import market_structure as ms

REGIMES = ("trending", "ranging", "high_vol", "choppy")

# regime -> (context_tf, signal_tf, trigger_tf) hint, in MT5 timeframe names
TF_BANDS = {
    "trending":  ("slow",   "D1", "H4", "H1"),
    "ranging":   ("fast",   "H1", "M15", "M5"),
    "high_vol":  ("medium", "H4", "H1", "M15"),
    "choppy":    (None,     None, None, None),   # stand down
}


def session_of(now=None):
    """Trading session from UTC hour. Approximate, good enough for tagging."""
    now = now or datetime.now(timezone.utc)
    h = now.hour
    if 7 <= h < 12:
        return "london"
    if 12 <= h < 16:
        return "london_ny_overlap"
    if 16 <= h < 21:
        return "ny"
    if 0 <= h < 7:
        return "asia"
    return "off"


def classify(symbol, rates, trend_threshold=25.0, chop_threshold=18.0):
    """Classify regime for one symbol from its bars.

    Returns dict:
        regime, volatility, atr_pct, trend_strength, session, tf_band, details
    Never raises — returns regime='unknown' on insufficient data.
    """
    now = datetime.now(timezone.utc)
    sess = session_of(now)

    if rates is None or len(rates) < 60:
        return {
            "regime": "unknown", "volatility": "unknown",
            "atr_pct": None, "trend_strength": None,
            "session": sess, "tf_band": None,
            "details": {"reason": "insufficient bars",
                        "bars": len(rates) if rates is not None else 0},
        }

    a_pct = ms.atr_pct(rates, 14)
    a_pctl = ms.atr_percentile(rates, 14, 100)
    strength = ms.adx(rates, 14)

    # Volatility band from ATR percentile
    if a_pctl is None:
        vol_band = "normal"
    elif a_pctl >= 80:
        vol_band = "high"
    elif a_pctl <= 25:
        vol_band = "low"
    else:
        vol_band = "normal"

    # Regime
    if strength is None:
        regime = "choppy"
    elif vol_band == "high" and strength < trend_threshold:
        # violent but directionless = shock/event
        regime = "high_vol"
    elif strength >= trend_threshold:
        regime = "trending"
    elif strength <= chop_threshold and vol_band == "high":
        regime = "choppy"
    else:
        regime = "ranging"

    band, ctx_tf, sig_tf, trig_tf = TF_BANDS.get(regime, (None, None, None, None))

    return {
        "regime": regime,
        "volatility": vol_band,
        "atr_pct": round(a_pct, 4) if a_pct is not None else None,
        "atr_percentile": round(a_pctl, 1) if a_pctl is not None else None,
        "trend_strength": round(strength, 1) if strength is not None else None,
        "session": sess,
        "tf_band": band,
        "tf_context": ctx_tf,
        "tf_signal": sig_tf,
        "tf_trigger": trig_tf,
        "details": {
            "trend_threshold": trend_threshold,
            "chop_threshold": chop_threshold,
            "bars": len(rates),
        },
    }


def classify_many(symbols, cache, timeframe, count=250):
    """Classify a list of symbols. `cache` is a RateCache, `timeframe` a const."""
    out = {}
    for s in symbols:
        rates = cache.get(s, timeframe, count)
        out[s] = classify(s, rates)
    return out


def summary(results):
    """Aggregate counts for logging / prompt context."""
    counts = {}
    for sym, r in results.items():
        counts[r["regime"]] = counts.get(r["regime"], 0) + 1
    return counts
