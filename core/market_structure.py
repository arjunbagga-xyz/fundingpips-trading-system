"""
Shared market-structure indicators.

Every strategy needs ATR / ADX / SMA / Bollinger / RSI / swing detection.
Duplicating them per strategy was ~200 lines of copy-paste and a source of
silent divergence (two strategies computing ATR differently = unattributable
results). One implementation, used everywhere.

All functions take a `rates` array (MT5 structured array of OHLCV) and are
pure: no connector access, no side effects, no caching concerns.
"""
import math


def closes(rates):
    return [float(r["close"]) for r in rates]


def highs(rates):
    return [float(r["high"]) for r in rates]


def lows(rates):
    return [float(r["low"]) for r in rates]


def opens(rates):
    return [float(r["open"]) for r in rates]


def volumes(rates):
    return [float(r["tick_volume"]) for r in rates]


def sma(values, period):
    """Simple moving average of the last `period` values."""
    if values is None or len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def sma_series(values, period):
    """Rolling SMA series. Returns list shorter than input by period-1."""
    if values is None or len(values) < period or period <= 0:
        return []
    out = []
    running = sum(values[:period])
    out.append(running / period)
    for i in range(period, len(values)):
        running += values[i] - values[i - period]
        out.append(running / period)
    return out


def ema_series(values, period):
    """Exponential MA series."""
    if values is None or len(values) < period or period <= 0:
        return []
    k = 2.0 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def true_ranges(rates):
    """Wilder true range series (index 0 is None — needs a prior bar)."""
    h, l, c = highs(rates), lows(rates), closes(rates)
    out = []
    for i in range(len(rates)):
        if i == 0:
            out.append(h[i] - l[i])
        else:
            out.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return out


def atr(rates, period=14):
    """Average True Range (Wilder smoothing)."""
    if rates is None or len(rates) < period + 1:
        return None
    tr = true_ranges(rates)
    # Wilder: seed with simple mean, then smooth
    val = sum(tr[1:period + 1]) / period
    for i in range(period + 1, len(tr)):
        val = (val * (period - 1) + tr[i]) / period
    return val


def atr_pct(rates, period=14):
    """ATR as a percentage of current price. Comparable across instruments."""
    a = atr(rates, period)
    if a is None:
        return None
    price = closes(rates)[-1]
    if price <= 0:
        return None
    return (a / price) * 100.0


def atr_percentile(rates, period=14, lookback=100):
    """Percentile (0-100) of current ATR within its own recent history.

    This is the volatility regime signal: high percentile = volatile,
    low percentile = quiet. Needs period+lookback bars.
    """
    need = period + lookback + 1
    if rates is None or len(rates) < need:
        return None
    series = []
    for end in range(period + 1, len(rates) + 1):
        window = rates[:end]
        a = atr(window, period)
        if a is not None:
            series.append(a)
    if len(series) < 20:
        return None
    current = series[-1]
    below = sum(1 for v in series[-lookback:] if v <= current)
    return (below / len(series[-lookback:])) * 100.0


def adx(rates, period=14):
    """Average Directional Index — trend strength (0-100).

    >25 = trending, <20 = ranging. Standard Wilder implementation.
    """
    if rates is None or len(rates) < period * 2 + 1:
        return None
    h, l, c = highs(rates), lows(rates), closes(rates)
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(rates)):
        up = h[i] - h[i - 1]
        down = l[i - 1] - l[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))

    def wilder(vals, p):
        if len(vals) < p:
            return []
        out = [sum(vals[:p])]
        for v in vals[p:]:
            out.append(out[-1] - (out[-1] / p) + v)
        return out

    tr_s = wilder(trs, period)
    pdm_s = wilder(plus_dm, period)
    mdm_s = wilder(minus_dm, period)
    n = min(len(tr_s), len(pdm_s), len(mdm_s))
    if n < period:
        return None

    dxs = []
    for i in range(n):
        if tr_s[i] == 0:
            continue
        pdi = 100.0 * pdm_s[i] / tr_s[i]
        mdi = 100.0 * mdm_s[i] / tr_s[i]
        denom = pdi + mdi
        if denom == 0:
            continue
        dxs.append(100.0 * abs(pdi - mdi) / denom)
    if len(dxs) < period:
        return None
    return sum(dxs[-period:]) / period


def bollinger(values, period=20, std_mult=2.0):
    """Returns (lower, middle, upper, bandwidth_pct)."""
    if values is None or len(values) < period:
        return None
    window = values[-period:]
    mid = sum(window) / period
    var = sum((v - mid) ** 2 for v in window) / period
    sd = math.sqrt(var)
    upper = mid + std_mult * sd
    lower = mid - std_mult * sd
    bw = ((upper - lower) / mid * 100.0) if mid else 0.0
    return lower, mid, upper, bw


def rsi(values, period=14):
    """Relative Strength Index (Wilder)."""
    if values is None or len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - (100.0 / (1.0 + rs))


def swing_points(rates, left=2, right=2):
    """Find swing highs/lows. Returns (highs_idx, lows_idx).

    A swing high at i requires rates[i].high > the `left` bars before and
    the `right` bars after. Used for structure, contraction, and peak
    detection.
    """
    h, l = highs(rates), lows(rates)
    n = len(rates)
    sh, sl = [], []
    for i in range(left, n - right):
        window_h = h[i - left:i + right + 1]
        window_l = l[i - left:i + right + 1]
        if h[i] == max(window_h):
            sh.append(i)
        if l[i] == min(window_l):
            sl.append(i)
    return sh, sl


def contraction_sequence(rates, lookback=60):
    """Detect Minervini-style volatility contraction.

    Walks swing highs and measures the pullback depth into each successive
    low. A valid VCP shows *decreasing* pullback depth, e.g. 18% -> 12% -> 6%.

    Returns (depths, is_contracting) where depths is a list of percentages.
    """
    sh, sl = swing_points(rates, left=2, right=2)
    if len(sl) < 3:
        return [], False
    h, l = highs(rates), lows(rates)
    start = max(0, len(rates) - lookback)
    pts = [i for i in sl if i >= start]
    if len(pts) < 3:
        return [], False
    depths = []
    for i in range(1, len(pts)):
        prev_high = max(h[pts[i - 1]:pts[i] + 1]) if pts[i] > pts[i - 1] else h[pts[i]]
        cur_low = l[pts[i]]
        if prev_high > 0:
            depths.append((prev_high - cur_low) / prev_high * 100.0)
    if len(depths) < 2:
        return depths, False
    contracting = all(depths[i] > depths[i + 1] for i in range(len(depths) - 1))
    return depths, contracting


def highest(values, period):
    if values is None or len(values) < period:
        return None
    return max(values[-period:])


def lowest(values, period):
    if values is None or len(values) < period:
        return None
    return min(values[-period:])


def trend_template(rates, price=None):
    """Minervini Trend Template. Returns (passes, detail_dict).

    Requires >= 200 bars. Stages:
      - price > SMA50 > SMA150 > SMA200
      - SMA200 rising over the last 20 bars
      - price >= 30% above 52-week (250-bar) low
      - price within 25% of 52-week high
    """
    if rates is None or len(rates) < 200:
        return False, {"error": "insufficient bars", "bars": len(rates) if rates is not None else 0}
    c = closes(rates)
    px = price if price is not None else c[-1]
    s50 = sma(c, 50)
    s150 = sma(c, 150)
    s200 = sma(c, 200)
    s200_series = sma_series(c, 200)
    if not s50 or not s150 or not s200 or len(s200_series) < 21:
        return False, {"error": "indicator calculation failed"}

    s200_rising = s200_series[-1] > s200_series[-21]
    lookback = c[-250:] if len(c) >= 250 else c
    hi52 = max(lookback)
    lo52 = min(lookback)
    above_low = ((px - lo52) / lo52 * 100.0) if lo52 > 0 else 0.0
    from_high = ((hi52 - px) / hi52 * 100.0) if hi52 > 0 else 100.0

    checks = {
        "price_above_sma50": px > s50,
        "sma50_above_sma150": s50 > s150,
        "sma150_above_sma200": s150 > s200,
        "sma200_rising": s200_rising,
        "above_52w_low_30pct": above_low >= 30.0,
        "within_25pct_of_52w_high": from_high <= 25.0,
    }
    passes = all(checks.values())
    detail = {
        "checks": checks,
        "price": round(px, 5),
        "sma50": round(s50, 5),
        "sma150": round(s150, 5),
        "sma200": round(s200, 5),
        "pct_above_52w_low": round(above_low, 1),
        "pct_from_52w_high": round(from_high, 1),
        "stage2_score": sum(1 for v in checks.values() if v),
    }
    return passes, detail
