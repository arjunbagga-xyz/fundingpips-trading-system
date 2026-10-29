"""
Strategy 5: Trend Following
Dual moving average crossover with momentum filter.
"""

import MetaTrader5 as mt5
from datetime import datetime

class TrendStrategy:
    """Trend Following: dual MA crossover."""
    
    NAME = "Trend"
    TIMEFRAME = mt5.TIMEFRAME_H1
    FAST_MA = 10
    SLOW_MA = 30
    RISK_PCT = 0.01  # 1%
    REWARD_RATIO = 5.0
    STOP_LOSS_PIPS = 50
    
    def __init__(self, connector):
        self.connector = connector
        self.symbols = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
    
    def get_pip_size(self, symbol):
        info = self.connector.get_symbol_info(symbol)
        if not info:
            return 0.0001
        d = info.digits
        if d >= 4: return 0.0001
        elif d == 3: return 0.01
        elif d == 2: return 0.1
        else: return 1.0
    
    def get_ma(self, symbol, period):
        """Calculate moving average."""
        rates = self.connector.get_rates(symbol, self.TIMEFRAME, period)
        if rates is None or len(rates) < period:
            return None
        closes = [r['close'] for r in rates]
        return sum(closes) / len(closes)
    
    def detect_crossover(self, symbol):
        """Detect MA crossover."""
        fast_ma = self.get_ma(symbol, self.FAST_MA)
        slow_ma = self.get_ma(symbol, self.SLOW_MA)
        
        if fast_ma is None or slow_ma is None:
            return None
        
        # Get previous MAs
        rates = self.connector.get_rates(symbol, self.TIMEFRAME, self.SLOW_MA + 1)
        if rates is None or len(rates) < self.SLOW_MA + 1:
            return None
        
        prev_closes = [r['close'] for r in rates[:-1]]
        prev_fast = sum(prev_closes[-self.FAST_MA:]) / self.FAST_MA
        prev_slow = sum(prev_closes) / len(prev_closes)
        
        # Bullish crossover
        if prev_fast <= prev_slow and fast_ma > slow_ma:
            return "BUY"
        
        # Bearish crossover
        if prev_fast >= prev_slow and fast_ma < slow_ma:
            return "SELL"
        
        return None
    
    def scan(self):
        """Scan for trend following setups."""
        signals = []
        
        for symbol in self.symbols:
            direction = self.detect_crossover(symbol)
            if direction is None:
                continue
            
            tick = self.connector.get_tick(symbol)
            if tick is None:
                continue
            
            if direction == "BUY":
                entry = tick.ask
                sl = entry - self.STOP_LOSS_PIPS * self.get_pip_size(symbol)
                tp = entry + self.STOP_LOSS_PIPS * self.get_pip_size(symbol) * self.REWARD_RATIO
            else:
                entry = tick.bid
                sl = entry + self.STOP_LOSS_PIPS * self.get_pip_size(symbol)
                tp = entry - self.STOP_LOSS_PIPS * self.get_pip_size(symbol) * self.REWARD_RATIO
            
            signals.append({
                "strategy": self.NAME,
                "symbol": symbol,
                "direction": direction,
                "confidence": 0.6,
                "risk_pct": self.RISK_PCT,
                "reward_ratio": self.REWARD_RATIO,
                "stop_loss_pips": self.STOP_LOSS_PIPS,
                "entry": entry,
                "stop_loss": sl,
                "take_profit": tp,
                "reason": f"Trend: MA crossover ({self.FAST_MA}/{self.SLOW_MA})"
            })
        
        return signals

if __name__ == "__main__":
    from mt5_connector import MT5Connector
    conn = MT5Connector()
    if conn.connect():
        strat = TrendStrategy(conn)
        signals = strat.scan()
        print(f"Found {len(signals)} Trend signals")
        for s in signals:
            print(f"  {s['symbol']} {s['direction']} @ {s['entry']:.5f}")
        conn.disconnect()
