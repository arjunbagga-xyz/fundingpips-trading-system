"""
Strategy 6: Mean Reversion
Bollinger Bands + RSI, ranging markets only.
"""

import MetaTrader5 as mt5
from datetime import datetime

class MeanReversionStrategy:
    """Mean Reversion: Bollinger Bands + RSI."""
    
    NAME = "MeanRev"
    TIMEFRAME = mt5.TIMEFRAME_M15
    BB_PERIOD = 20
    BB_STD = 2.0
    RSI_PERIOD = 14
    RSI_OVERSOLD = 30
    RSI_OVERBOUGHT = 70
    RISK_PCT = 0.005  # 0.5%
    REWARD_RATIO = 1.0
    STOP_LOSS_PIPS = 15
    
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
    
    def get_bollinger(self, symbol):
        """Calculate Bollinger Bands."""
        rates = self.connector.get_rates(symbol, self.TIMEFRAME, self.BB_PERIOD)
        if rates is None or len(rates) < self.BB_PERIOD:
            return None
        
        closes = [r['close'] for r in rates]
        sma = sum(closes) / len(closes)
        std = (sum((c - sma)**2 for c in closes) / len(closes)) ** 0.5
        
        upper = sma + self.BB_STD * std
        lower = sma - self.BB_STD * std
        
        return {"upper": upper, "middle": sma, "lower": lower}
    
    def get_rsi(self, symbol):
        """Calculate RSI."""
        rates = self.connector.get_rates(symbol, self.TIMEFRAME, self.RSI_PERIOD + 1)
        if rates is None or len(rates) < self.RSI_PERIOD + 1:
            return None
        
        gains = []
        losses = []
        for i in range(1, len(rates)):
            change = rates[i]['close'] - rates[i-1]['close']
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        
        avg_gain = sum(gains) / len(gains)
        avg_loss = sum(losses) / len(losses)
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def is_ranging(self, symbol):
        """Check if market is ranging."""
        bb = self.get_bollinger(symbol)
        if bb is None:
            return False
        
        bandwidth = (bb["upper"] - bb["lower"]) / bb["middle"]
        return bandwidth < 0.01  # Less than 1% bandwidth = ranging
    
    def scan(self):
        """Scan for mean reversion setups."""
        signals = []
        
        for symbol in self.symbols:
            if not self.is_ranging(symbol):
                continue
            
            bb = self.get_bollinger(symbol)
            rsi = self.get_rsi(symbol)
            
            if bb is None or rsi is None:
                continue
            
            tick = self.connector.get_tick(symbol)
            if tick is None:
                continue
            
            # Buy at lower band + oversold
            if tick.bid <= bb["lower"] and rsi <= self.RSI_OVERSOLD:
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
                    "reason": f"MeanRev: price at lower BB + RSI oversold ({rsi:.1f})"
                })
            
            # Sell at upper band + overbought
            elif tick.ask >= bb["upper"] and rsi >= self.RSI_OVERBOUGHT:
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
                    "reason": f"MeanRev: price at upper BB + RSI overbought ({rsi:.1f})"
                })
        
        return signals

if __name__ == "__main__":
    from mt5_connector import MT5Connector
    conn = MT5Connector()
    if conn.connect():
        strat = MeanReversionStrategy(conn)
        signals = strat.scan()
        print(f"Found {len(signals)} MeanRev signals")
        for s in signals:
            print(f"  {s['symbol']} {s['direction']} @ {s['entry']:.5f}")
        conn.disconnect()
