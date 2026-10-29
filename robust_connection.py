"""
Robust MT5 Connection Wrapper
Handles connection drops, auto-retry, auto-reconnect.
"""

import time
import MetaTrader5 as mt5
from datetime import datetime

LOGIN = 5058051765
SERVER = "MetaQuotes-Demo"
MAX_RETRIES = 5
RETRY_DELAY = 3  # seconds
HEALTH_CHECK_INTERVAL = 3600  # seconds

class RobustMT5Connection:
    """MT5 connection with auto-retry and auto-reconnect."""
    
    def __init__(self):
        self.connected = False
        self.last_health_check = 0
        self.connection_attempts = 0
        self.last_error = None
        
    def connect(self):
        """Connect to MT5 with retry logic."""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if mt5.initialize():
                    authorized = mt5.login(LOGIN, server=SERVER)
                    if authorized:
                        self.connected = True
                        self.connection_attempts = 0
                        self.last_error = None
                        self.last_health_check = time.time()
                        # Add required symbols to Market Watch
                        for sym in ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "CADCHF", "CADJPY", "CHFJPY", "EURAUD", "EURCAD", "EURCHF", "EURGBP", "EURJPY", "EURNZD", "GBPAUD", "GBPCAD", "GBPCHF", "GBPJPY", "GBPNZD", "NZDCAD", "NZDCHF", "NZDJPY", "XAUUSD", "XAGUSD", "US30", "UK100", "GER40", "JP225", "NAS100", "SPX500", "STX50", "USOIL", "UKOIL", "BTCUSD", "ETHUSD"]:
                            if mt5.symbol_info(sym) is None:
                                mt5.symbol_select(sym, True)
                        print(f"  MT5 connected (attempt {attempt})")
                        return True
                    else:
                        error = mt5.last_error()
                        print(f"  MT5 login failed (attempt {attempt}): {error}")
                        self.last_error = error
                else:
                    error = mt5.last_error()
                    print(f"  MT5 initialize failed (attempt {attempt}): {error}")
                    self.last_error = error
            except Exception as e:
                print(f"  MT5 connection error (attempt {attempt}): {e}")
                self.last_error = str(e)
            
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        
        self.connected = False
        self.connection_attempts += 1
        return False
    
    def disconnect(self):
        """Disconnect from MT5."""
        try:
            mt5.shutdown()
        except:
            pass
        self.connected = False
    
    def health_check(self):
        """Check if connection is alive. Does NOT disconnect."""
        now = time.time()
        if now - self.last_health_check < HEALTH_CHECK_INTERVAL:
            return self.connected
        
        self.last_health_check = now
        
        try:
            info = mt5.account_info()
            if info is None:
                print(f"  Health check: no account info (will reconnect on next call)")
                return False
            return True
        except Exception as e:
            print(f"  Health check error: {e} (will reconnect on next call)")
            return False
    
    def ensure_connected(self):
        """Ensure connection is alive, reconnect if needed."""
        if not self.connected:
            print("  Connection lost, reconnecting...")
            self.disconnect()
            time.sleep(2)
            result = self.connect()
            if result:
                # Re-add symbols after reconnect
                for sym in ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "CADCHF", "CADJPY", "CHFJPY", "EURAUD", "EURCAD", "EURCHF", "EURGBP", "EURJPY", "EURNZD", "GBPAUD", "GBPCAD", "GBPCHF", "GBPJPY", "GBPNZD", "NZDCAD", "NZDCHF", "NZDJPY", "XAUUSD", "XAGUSD", "US30", "UK100", "GER40", "JP225", "NAS100", "SPX500", "STX50", "USOIL", "UKOIL", "BTCUSD", "ETHUSD"]:
                    if mt5.symbol_info(sym) is None:
                        mt5.symbol_select(sym, True)
            return result
        
        if not self.health_check():
            print("  Health check failed, reconnecting...")
            self.disconnect()
            time.sleep(2)
            result = self.connect()
            if result:
                # Re-add symbols after reconnect
                for sym in ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "CADCHF", "CADJPY", "CHFJPY", "EURAUD", "EURCAD", "EURCHF", "EURGBP", "EURJPY", "EURNZD", "GBPAUD", "GBPCAD", "GBPCHF", "GBPJPY", "GBPNZD", "NZDCAD", "NZDCHF", "NZDJPY", "XAUUSD", "XAGUSD", "US30", "UK100", "GER40", "JP225", "NAS100", "SPX500", "STX50", "USOIL", "UKOIL", "BTCUSD", "ETHUSD"]:
                    if mt5.symbol_info(sym) is None:
                        mt5.symbol_select(sym, True)
            return result
        
        return True
    
    def get_account_info(self):
        """Get account info with auto-reconnect."""
        if not self.ensure_connected():
            return None
        if not self.ensure_connected():
            return None
        try:
            return mt5.account_info()
        except Exception as e:
            print(f"  get_account_info error: {e}")
            self.connected = False
            return None
    
    def get_balance(self):
        """Get balance with auto-reconnect."""
        info = self.get_account_info()
        return info.balance if info else 0
    
    def get_equity(self):
        """Get equity with auto-reconnect."""
        info = self.get_account_info()
        return info.equity if info else 0
    
    def get_free_margin(self):
        """Get free margin with auto-reconnect."""
        info = self.get_account_info()
        return info.margin_free if info else 0
    
    def get_positions(self):
        """Get positions with auto-reconnect."""
        if not self.ensure_connected():
            return []
        try:
            positions = mt5.positions_get()
            return positions if positions else []
        except Exception as e:
            print(f"  get_positions error: {e}")
            self.connected = False
            return []
    
    def get_position_pnl(self):
        """Get total floating PnL."""
        positions = self.get_positions()
        return sum(p.profit for p in positions)
    
    def get_symbol_info(self, symbol):
        """Get symbol info with auto-reconnect."""
        if not self.ensure_connected():
            return None
        try:
            return mt5.symbol_info(symbol)
        except Exception as e:
            print(f"  get_symbol_info error: {e}")
            self.connected = False
            return None
    
    def get_tick(self, symbol):
        """Get tick with auto-reconnect."""
        if not self.ensure_connected():
            return None
        try:
            return mt5.symbol_info_tick(symbol)
        except Exception as e:
            print(f"  get_tick error: {e}")
            self.connected = False
            return None
    
    def get_rates(self, symbol, timeframe, count, start_pos=0):
        """Get rates with auto-reconnect."""
        if not self.ensure_connected():
            return None
        if not self.ensure_connected():
            return None
        try:
            return mt5.copy_rates_from_pos(symbol, timeframe, start_pos, count)
        except Exception as e:
            print(f"  get_rates error: {e}")
            self.connected = False
            return None
    
    def get_rates_range(self, symbol, timeframe, date_from, date_to):
        """Get rates range with auto-reconnect."""
        if not self.ensure_connected():
            return None
        try:
            return mt5.copy_rates_range(symbol, timeframe, date_from, date_to)
        except Exception as e:
            print(f"  get_rates_range error: {e}")
            self.connected = False
            return None
    
    def place_order(self, symbol, direction, volume, price, stop_loss=None, 
                    take_profit=None, magic=0, comment=""):
        """Place order with auto-reconnect."""
        if not self.ensure_connected():
            return None
        
        # Try to get tick directly — if we have a price, we can trade
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            # Symbol not in Market Watch — try to add it
            mt5.symbol_select(symbol, True)
            time.sleep(1)
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                print(f"  No tick for {symbol}")
                return None
        

        # Update price
        if direction == "BUY":
            price = tick.ask
        else:
            price = tick.bid
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "magic": magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }
        
        if stop_loss:
            request["sl"] = stop_loss
        if take_profit:
            request["tp"] = take_profit
        
        try:
            result = mt5.order_send(request)
            if result is None:
                print(f"  Order failed: order_send returned None")
                self.connected = False
                return None
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                print(f"  Order failed: {result.retcode} - {result.comment}")
                return None
            
            print(f"  Order placed: {direction} {volume} {symbol} @ {price}")
            return result
        except Exception as e:
            print(f"  place_order error: {e}")
            self.connected = False
            return None
    
    def close_position(self, ticket):
        """Close position with auto-reconnect."""
        if not self.ensure_connected():
            return False
        
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            print(f"  Position {ticket} not found")
            return False
        
        position = positions[0]
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": mt5.ORDER_TYPE_SELL if position.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
            "position": ticket,
            "price": mt5.symbol_info_tick(position.symbol).bid if position.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(position.symbol).ask,
            "magic": position.magic,
            "comment": "Close position",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }
        
        try:
            result = mt5.order_send(request)
            if result is None:
                print(f"  Close failed: order_send returned None")
                self.connected = False
                return False
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                print(f"  Close failed: {result.retcode} - {result.comment}")
                return False
            
            print(f"  Position {ticket} closed")
            return True
        except Exception as e:
            print(f"  close_position error: {e}")
            self.connected = False
            return False
    
    def modify_position(self, ticket, stop_loss=None, take_profit=None):
        """Modify position with auto-reconnect."""
        if not self.ensure_connected():
            return False
        
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            print(f"  Position {ticket} not found")
            return False
        
        position = positions[0]
        
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": position.symbol,
        }
        
        if stop_loss:
            request["sl"] = stop_loss
        if take_profit:
            request["tp"] = take_profit
        
        try:
            result = mt5.order_send(request)
            if result is None:
                print(f"  Modify failed: order_send returned None")
                self.connected = False
                return False
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                print(f"  Modify failed: {result.retcode} - {result.comment}")
                return False
            
            print(f"  Position {ticket} modified")
            return True
        except Exception as e:
            print(f"  modify_position error: {e}")
            self.connected = False
            return False
    
    def get_trade_history(self, date_from, date_to):
        """Get trade history with auto-reconnect."""
        if not self.ensure_connected():
            return []
        try:
            return mt5.history_deals_get(date_from, date_to)
        except Exception as e:
            print(f"  get_trade_history error: {e}")
            self.connected = False
            return []
    
    def calculate_position_size(self, symbol, risk_pct, stop_loss_pips):
        """Calculate position size based on risk."""
        info = self.get_account_info()
        if not info:
            return 0
        
        symbol_info = self.get_symbol_info(symbol)
        if not symbol_info:
            return 0
        
        risk_amount = info.balance * risk_pct
        
        digits = symbol_info.digits
        if digits >= 4:
            pip_size = 0.0001
        elif digits == 3:
            pip_size = 0.01
        elif digits == 2:
            pip_size = 0.1
        else:
            pip_size = 1.0
        
        pip_value = symbol_info.trade_contract_size * pip_size
        
        position_size = risk_amount / (stop_loss_pips * pip_value)
        
        lot_step = symbol_info.volume_step
        position_size = round(position_size / lot_step) * lot_step
        
        position_size = max(symbol_info.volume_min, min(symbol_info.volume_max, position_size))
        
        return position_size

# Singleton instance
_connection = None

def get_connection():
    """Get or create singleton connection."""
    global _connection
    if _connection is None:
        _connection = RobustMT5Connection()
    return _connection

if __name__ == "__main__":
    conn = get_connection()
    if conn.connect():
        print(f"Balance: ${conn.get_balance():,.2f}")
        print(f"Equity: ${conn.get_equity():,.2f}")
        print(f"Free Margin: ${conn.get_free_margin():,.2f}")
        
        positions = conn.get_positions()
        print(f"Open positions: {len(positions)}")
        
        conn.disconnect()
    else:
        print("Failed to connect")
