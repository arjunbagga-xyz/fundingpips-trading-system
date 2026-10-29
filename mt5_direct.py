"""Direct MT5 adapter — bypasses robust_connection health-check cycle."""
import time


class MT5DirectAdapter:
    """Thin wrapper around the mt5 module, providing the connector interface."""

    def __init__(self, mt5_module):
        self.mt5 = mt5_module

    def get_pip_size(self, symbol):
        info = self.mt5.symbol_info(symbol)
        if not info:
            return 0.0001
        d = info.digits
        if d >= 4:
            return 0.0001
        elif d == 3:
            return 0.01
        elif d == 2:
            return 0.1
        else:
            return 1.0

    def get_symbol_info(self, symbol):
        return self.mt5.symbol_info(symbol)

    def get_tick(self, symbol):
        return self.mt5.symbol_info_tick(symbol)

    def get_rates(self, symbol, timeframe, count, start_pos=0):
        from datetime import datetime
        return self.mt5.copy_rates_from(symbol, timeframe, datetime.now(), count)

    def get_rates_range(self, symbol, timeframe, date_from, date_to):
        return self.mt5.copy_rates_range(symbol, timeframe, date_from, date_to)

    def get_equity(self):
        info = self.mt5.account_info()
        return info.equity if info else 0

    def get_balance(self):
        info = self.mt5.account_info()
        return info.balance if info else 0

    def get_positions(self):
        return self.mt5.positions_get() or []

    def place_order(self, symbol, direction, volume, price, stop_loss=None,
                    take_profit=None, magic=0, comment=""):
        if stop_loss is None or take_profit is None:
            return None
        tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            self.mt5.symbol_select(symbol, True)
            time.sleep(1)
            tick = self.mt5.symbol_info_tick(symbol)
            if tick is None:
                return None
        price = tick.ask if direction == "BUY" else tick.bid
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": self.mt5.ORDER_TYPE_BUY if direction == "BUY" else self.mt5.ORDER_TYPE_SELL,
            "price": price,
            "magic": magic,
            "sl": stop_loss,
            "tp": take_profit,
            "comment": comment,
            "type_filling": self.mt5.ORDER_FILLING_FOK,
        }
        result = self.mt5.order_send(request)
        if result and result.retcode == self.mt5.TRADE_RETCODE_DONE:
            return result.order
        return None
