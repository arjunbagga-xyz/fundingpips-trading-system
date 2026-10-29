"""
Shared per-cycle rate cache.

Why this exists: 8 strategies x 20 symbols x 3 timeframes = ~480 copy_rates
calls per cycle, each a Wine IPC round trip. At ~100ms each that is ~48s of
pure IPC overhead and a real corruption risk. Caching per (symbol, timeframe)
per cycle cuts it to ~60 calls.

Usage:
    cache = RateCache(connector)
    rates = cache.get("EURUSD", mt5.TIMEFRAME_H1, 200)
"""
import time


class RateCache:
    """Per-cycle cache of OHLCV bars, keyed by (symbol, timeframe)."""

    def __init__(self, connector, ttl_seconds=900):
        self.connector = connector
        self.ttl = ttl_seconds
        self._store = {}      # (symbol, tf) -> (fetched_at, count, rates)
        self.hits = 0
        self.misses = 0

    def get(self, symbol, timeframe, count):
        """Return >= count bars, fetching once per (symbol, tf) per cycle.

        If a cached entry has fewer bars than requested, it is refetched.
        """
        key = (symbol, timeframe)
        now = time.time()

        if key in self._store:
            fetched_at, cached_count, rates = self._store[key]
            fresh = (now - fetched_at) < self.ttl
            enough = cached_count >= count
            if fresh and enough:
                self.hits += 1
                return rates

        self.misses += 1
        rates = self.connector.get_rates(symbol, timeframe, count)
        if rates is not None:
            self._store[key] = (now, len(rates), rates)
        return rates

    def get_many(self, symbols, timeframe, count):
        """Fetch several symbols on one timeframe. Returns {symbol: rates}."""
        out = {}
        for s in symbols:
            r = self.get(s, timeframe, count)
            if r is not None:
                out[s] = r
        return out

    def stats(self):
        total = self.hits + self.misses
        rate = (self.hits / total * 100) if total else 0.0
        return {
            "keys": len(self._store),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_pct": round(rate, 1),
        }

    def clear(self):
        self._store.clear()
        self.hits = 0
        self.misses = 0
