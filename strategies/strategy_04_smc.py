"""
Strategy 4: SMC/ICT Filtered (Simplified)
Killzones + 4H bias + FVG entries.
"""

import MetaTrader5 as mt5
from datetime import datetime

class SMCStrategy:
    """Smart Money Concepts — simplified mechanical version."""
    
    NAME = "SMC"
    TIMEFRAME = mt5.TIMEFRAME_M15
    TREND_TIMEFRAME = mt5.TIMEFRAME_H4
    RISK_PCT = 0.005  # 0.5%
    REWARD_RATIO = 2.5
    STOP_LOSS_PIPS = 25
    
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
    
    def is_killzone(self):
        """Check if current time is in a killzone."""
        now = datetime.now()
        hour = now.hour
        # London killzone: 08:00-10:00 UTC
        # NY killzone: 13:00-15:00 UTC
        return (8 <= hour < 10) or (13 <= hour < 15)
    
    def get_htf_bias(self, symbol):
        """Get 4H trend bias."""
        rates = self.connector.get_rates(symbol, self.TREND_TIMEFRAME, 20)
        if rates is None or len(rates) < 20:
            return None
        
        closes = [r['close'] for r in rates]
        sma = sum(closes) / len(closes)
        current = closes[-1]
        
        if current > sma * 1.002:
            return "BUY"
        elif current < sma * 0.998:
            return "SELL"
        return None
    
    def detect_fvg(self, symbol):
        """Detect Fair Value Gap (3-candle imbalance)."""
        rates = self.connector.get_rates(symbol, self.TIMEFRAME, 3)
        if rates is None or len(rates) < 3:
            return None
        
        # Bullish FVG: candle 1 high < candle 3 low
        if rates[0]['high'] < rates[2]['low']:
            return "BUY"
        
        # Bearish FVG: candle 1 low > candle 3 high
        if rates[0]['low'] > rates[2]['high']:
            return "SELL"
        
        return None
    
    def scan(self):
        """Scan for SMC setups."""
        signals = []
        
        if not self.is_killzone():
            return signals
        
        for symbol in self.symbols:
            htf_bias = self.get_htf_bias(symbol)
            fvg = self.detect_fvg(symbol)
            
            if htf_bias is None or fvg is None:
                continue
            
            # Only trade in direction of HTF bias
            if fvg != htf_bias:
                continue
            
            tick = self.connector.get_tick(symbol)
            if tick is None:
                continue
            
            if fvg == "BUY":
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
                "direction": fvg,
                "confidence": 0.65,
                "risk_pct": self.RISK_PCT,
                "reward_ratio": self.REWARD_RATIO,
                "stop_loss_pips": self.STOP_LOSS_PIPS,
                "entry": entry,
                "stop_loss": sl,
                "take_profit": tp,
                "reason": f"SMC: FVG in direction of HTF bias ({htf_bias})"
            })
        
        return signals

if __name__ == "__main__":
    from mt5_connector import MT5Connector
    conn = MT5Connector()
    if conn.connect():
        strat = SMCStrategy(conn)
        signals = strat.scan()
        print(f"Found {len(signals)} SMC signals")
        for s in signals:
            print(f"  {s['symbol']} {s['direction']} @ {s['entry']:.5f}")
        conn.disconnect()
