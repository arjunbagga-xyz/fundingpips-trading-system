"""
MT5 Connection Wrapper for tradingbeast
Delegates all operations to RobustMT5Connection for auto-retry and auto-reconnect.
"""

from robust_connection import RobustMT5Connection

SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "XAUUSD", "XAGUSD"
]

class MT5Connector:
    """Wrapper for robust MT5 connection."""
    
    def __init__(self):
        self._conn = RobustMT5Connection()
        
    def connect(self):
        return self._conn.connect()
    
    def disconnect(self):
        self._conn.disconnect()
    
    @property
    def connected(self):
        return self._conn.connected
    
    def get_account_info(self):
        return self._conn.get_account_info()
    
    def get_balance(self):
        return self._conn.get_balance()
    
    def get_equity(self):
        return self._conn.get_equity()
    
    def get_free_margin(self):
        return self._conn.get_free_margin()
    
    def get_positions(self):
        return self._conn.get_positions()
    
    def get_position_pnl(self):
        return self._conn.get_position_pnl()
    
    def get_symbol_info(self, symbol):
        return self._conn.get_symbol_info(symbol)
    
    def get_tick(self, symbol):
        return self._conn.get_tick(symbol)
    
    def get_rates(self, symbol, timeframe, count, start_pos=0):
        return self._conn.get_rates(symbol, timeframe, count, start_pos)
    
    def get_rates_range(self, symbol, timeframe, date_from, date_to):
        return self._conn.get_rates_range(symbol, timeframe, date_from, date_to)
    
    def place_order(self, symbol, direction, volume, price, stop_loss=None, 
                    take_profit=None, magic=0, comment=""):
        return self._conn.place_order(symbol, direction, volume, price, stop_loss, take_profit, magic, comment)
    
    def close_position(self, ticket):
        return self._conn.close_position(ticket)
    
    def modify_position(self, ticket, stop_loss=None, take_profit=None):
        return self._conn.modify_position(ticket, stop_loss, take_profit)
    
    def get_trade_history(self, date_from, date_to):
        return self._conn.get_trade_history(date_from, date_to)
    
    def calculate_position_size(self, symbol, risk_pct, stop_loss_pips):
        return self._conn.calculate_position_size(symbol, risk_pct, stop_loss_pips)

if __name__ == "__main__":
    connector = MT5Connector()
    if connector.connect():
        print(f"Balance: ${connector.get_balance():,.2f}")
        print(f"Equity: ${connector.get_equity():,.2f}")
        print(f"Free Margin: ${connector.get_free_margin():,.2f}")
        positions = connector.get_positions()
        print(f"Open positions: {len(positions)}")
        connector.disconnect()
