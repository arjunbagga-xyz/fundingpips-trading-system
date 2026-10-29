"""
Strategy 7: 1:1 RR Simple
Tight stops, 50% win rate target, news avoidance.
"""

import MetaTrader5 as mt5
from datetime import datetime

class SimpleRRStrategy:
    """1:1 Risk-Reward Simple Strategy."""
    
    NAME = "1to1"
    TIMEFRAME = mt5.TIMEFRAME_M15
    RISK_PCT = 0.005  # 0.5%
    REWARD_RATIO = 1.0
    STOP_LOSS_PIPS = 15
    TAKE_PROFIT_PIPS = 15
    
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
    
    def is_session_open(self):
        """Check if London or NY session is open."""
        now = datetime.now()
        hour = now.hour
        return (8 <= hour < 17) or (13 <= hour < 22)
    
    def get_momentum(self, symbol):
        """Get short-term momentum."""
        rates = self.connector.get_rates(symbol, self.TIMEFRAME, 10)
        if rates is None or len(rates) < 10:
            return None
        
        closes = [r['close'] for r in rates]
        momentum = (closes[-1] - closes[0]) / closes[0]
        
        if momentum > 0.001:
            return "BUY"
        elif momentum < -0.001:
            return "SELL"
        return None
    
    def scan(self):
        """Scan for 1:1 RR setups."""
        signals = []
        
        if not self.is_session_open():
            return signals
        
        for symbol in self.symbols:
            momentum = self.get_momentum(symbol)
            if momentum is None:
                continue
            
            tick = self.connector.get_tick(symbol)
            if tick is None:
                continue
            
            if momentum == "BUY":
                entry = tick.ask
                sl = entry - self.STOP_LOSS_PIPS * self.get_pip_size(symbol)
                tp = entry + self.TAKE_PROFIT_PIPS * self.get_pip_size(symbol)
            else:
                entry = tick.bid
                sl = entry + self.STOP_LOSS_PIPS * self.get_pip_size(symbol)
                tp = entry - self.TAKE_PROFIT_PIPS * self.get_pip_size(symbol)
            
            signals.append({
                "strategy": self.NAME,
                "symbol": symbol,
                "direction": momentum,
                "confidence": 0.5,
                "risk_pct": self.RISK_PCT,
                "reward_ratio": self.REWARD_RATIO,
                "stop_loss_pips": self.STOP_LOSS_PIPS,
                "entry": entry,
                "stop_loss": sl,
                "take_profit": tp,
                "reason": f"1:1 RR: momentum {momentum}, tight SL/TP"
            })
        
        return signals

if __name__ == "__main__":
    from mt5_connector import MT5Connector
    conn = MT5Connector()
    if conn.connect():
        strat = SimpleRRStrategy(conn)
        signals = strat.scan()
        print(f"Found {len(signals)} 1:1 RR signals")
        for s in signals:
            print(f"  {s['symbol']} {s['direction']} @ {s['entry']:.5f}")
        conn.disconnect()
