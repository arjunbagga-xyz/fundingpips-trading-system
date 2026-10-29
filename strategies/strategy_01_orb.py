"""
Strategy 1: Opening Range Breakout (ORB) + Trend Filter
Adapted for FundingPips: 15-min ORB, 4H trend, London/NY sessions only.
"""

import MetaTrader5 as mt5
from datetime import datetime, timedelta

class ORBStrategy:
    """Opening Range Breakout with trend filter."""
    
    NAME = "ORB"
    TIMEFRAME = mt5.TIMEFRAME_M15
    TREND_TIMEFRAME = mt5.TIMEFRAME_H4
    ORB_PERIOD = 15  # minutes
    RISK_PCT = 0.005  # 0.5%
    REWARD_RATIO = 1.5
    STOP_LOSS_PIPS = 20
    
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
        # London: 08:00-17:00 UTC, NY: 13:00-22:00 UTC
        return (8 <= hour < 17) or (13 <= hour < 22)
    
    def get_trend(self, symbol):
        """Get 4H trend direction."""
        rates = self.connector.get_rates(symbol, self.TREND_TIMEFRAME, 20)
        if rates is None or len(rates) < 20:
            return None
        
        # Simple trend: price above/below 20-period SMA
        closes = [r['close'] for r in rates]
        sma = sum(closes) / len(closes)
        current = closes[-1]
        
        if current > sma * 1.001:
            return "BUY"
        elif current < sma * 0.999:
            return "SELL"
        return None
    
    def get_opening_range(self, symbol):
        """Get today's opening range (first 15 minutes of session)."""
        now = datetime.now()
        
        # Determine session start
        if 8 <= now.hour < 17:
            session_start = now.replace(hour=8, minute=0, second=0, microsecond=0)
        elif 13 <= now.hour < 22:
            session_start = now.replace(hour=13, minute=0, second=0, microsecond=0)
        else:
            return None
        
        # Get rates from session start
        rates = self.connector.get_rates_range(
            symbol, self.TIMEFRAME, session_start, now
        )
        
        if rates is None or len(rates) == 0:
            return None
        
        # Filter to first ORB_PERIOD minutes
        orb_rates = []
        for r in rates:
            if r['time'] <= session_start.timestamp() + self.ORB_PERIOD * 60:
                orb_rates.append(r)
            else:
                break
        
        if len(orb_rates) == 0:
            return None
        
        high = max(r['high'] for r in orb_rates)
        low = min(r['low'] for r in orb_rates)
        
        return {"high": high, "low": low, "range": high - low}
    
    def scan(self):
        """Scan for ORB setups."""
        signals = []
        
        if not self.is_session_open():
            return signals
        
        for symbol in self.symbols:
            trend = self.get_trend(symbol)
            orb = self.get_opening_range(symbol)
            
            if trend is None or orb is None:
                continue
            
            tick = self.connector.get_tick(symbol)
            if tick is None:
                continue
            
            # Check for breakout
            if trend == "BUY" and tick.ask > orb["high"]:
                signals.append({
                    "strategy": self.NAME,
                    "symbol": symbol,
                    "direction": "BUY",
                    "confidence": 0.7,
                    "risk_pct": self.RISK_PCT,
                    "reward_ratio": self.REWARD_RATIO,
                    "stop_loss_pips": self.STOP_LOSS_PIPS,
                    "entry": tick.ask,
                    "stop_loss": tick.ask - self.STOP_LOSS_PIPS * self.get_pip_size(symbol),
                    "take_profit": tick.ask + self.STOP_LOSS_PIPS * self.get_pip_size(symbol) * self.REWARD_RATIO,
                    "reason": f"ORB breakout above {orb['high']:.5f}, trend {trend}"
                })
            
            elif trend == "SELL" and tick.bid < orb["low"]:
                signals.append({
                    "strategy": self.NAME,
                    "symbol": symbol,
                    "direction": "SELL",
                    "confidence": 0.7,
                    "risk_pct": self.RISK_PCT,
                    "reward_ratio": self.REWARD_RATIO,
                    "stop_loss_pips": self.STOP_LOSS_PIPS,
                    "entry": tick.bid,
                    "stop_loss": tick.bid + self.STOP_LOSS_PIPS * self.get_pip_size(symbol),
                    "take_profit": tick.bid - self.STOP_LOSS_PIPS * self.get_pip_size(symbol) * self.REWARD_RATIO,
                    "reason": f"ORB breakout below {orb['low']:.5f}, trend {trend}"
                })
        
        return signals

if __name__ == "__main__":
    from mt5_connector import MT5Connector
    conn = MT5Connector()
    if conn.connect():
        strat = ORBStrategy(conn)
        signals = strat.scan()
        print(f"Found {len(signals)} ORB signals")
        for s in signals:
            print(f"  {s['symbol']} {s['direction']} @ {s['entry']:.5f}")
        conn.disconnect()
