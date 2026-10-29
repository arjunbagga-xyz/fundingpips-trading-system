"""
Execution Engine
Aggregates signals, resolves conflicts, checks compliance, places orders.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from database import log_trade, log_signal, log_learning, log_compliance
from risk_manager import RiskManager, MAX_OPEN_POSITIONS, MAX_TRADES_PER_DAY

class ExecutionEngine:
    """Executes trades based on strategy signals."""
    
    def __init__(self, connector, risk_manager):
        self.connector = connector
        self.risk = risk_manager
        self.pending_signals = []
        
    def aggregate_signals(self, all_signals):
        """Aggregate and prioritize signals from all strategies."""
        if not all_signals:
            return []
        
        # Group by symbol
        by_symbol = {}
        for signal in all_signals:
            symbol = signal['symbol']
            if symbol not in by_symbol:
                by_symbol[symbol] = []
            by_symbol[symbol].append(signal)
        
        # Resolve conflicts per symbol
        final_signals = []
        for symbol, signals in by_symbol.items():
            # Count directions
            buys = [s for s in signals if s['direction'] == 'BUY']
            sells = [s for s in signals if s['direction'] == 'SELL']
            
            # Pick direction with more signals
            if len(buys) > len(sells):
                # Pick highest confidence buy
                best = max(buys, key=lambda x: x['confidence'])
                final_signals.append(best)
            elif len(sells) > len(buys):
                # Pick highest confidence sell
                best = max(sells, key=lambda x: x['confidence'])
                final_signals.append(best)
            else:
                # Tie: pick highest confidence overall
                best = max(signals, key=lambda x: x['confidence'])
                final_signals.append(best)
        
        # Sort by confidence
        final_signals.sort(key=lambda x: x['confidence'], reverse=True)
        
        return final_signals
    
    def check_compliance(self, signal, equity, open_positions):
        """Check if signal passes all compliance checks."""
        # Check basic trade permission
        can_trade, reasons = self.risk.can_trade(equity, open_positions)
        if not can_trade:
            return False, reasons
        
        # Check if we already have a position in this symbol
        for pos in open_positions:
            if pos.symbol == signal['symbol']:
                return False, [f"Already have position in {signal['symbol']}"]
        
        # Check risk per trade
        if signal['risk_pct'] > 0.02:
            return False, [f"Risk per trade too high: {signal['risk_pct']:.2%}"]
        
        return True, []
    
    def get_pip_size(self, symbol):
        """Get pip size based on symbol digits."""
        info = self.connector.get_symbol_info(symbol)
        if not info:
            return 0.0001
        digits = info.digits
        if digits >= 4:
            return 0.0001
        elif digits == 3:
            return 0.01
        elif digits == 2:
            return 0.1
        else:
            return 1.0
    
    def validate_stops(self, symbol, entry, sl, tp):
        """Validate stop distances against broker minimums."""
        info = self.connector.get_symbol_info(symbol)
        if not info:
            return False, "Symbol not found"
        
        pip_size = self.get_pip_size(symbol)
        min_stops = info.trade_stops_level * pip_size
        
        if sl and abs(entry - sl) < min_stops:
            return False, f"SL too close: {abs(entry - sl):.5f} < {min_stops:.5f}"
        if tp and abs(entry - tp) < min_stops:
            return False, f"TP too close: {abs(entry - tp):.5f} < {min_stops:.5f}"
        
        return True, "OK"
    
    def check_margin(self, symbol, volume):
        """Check if we have enough margin for a trade."""
        info = self.connector.get_account_info()
        if not info:
            return False, "No account info"
        
        symbol_info = self.connector.get_symbol_info(symbol)
        if not symbol_info:
            return False, "Symbol not found"
        
        # Calculate required margin
        tick = self.connector.get_tick(symbol)
        if not tick:
            return False, "No tick"
        
        price = tick.ask if volume > 0 else tick.bid
        margin = volume * symbol_info.trade_contract_size * price / info.leverage
        
        if margin > info.margin_free:
            return False, f"Insufficient margin: ${margin:.2f} > ${info.margin_free:.2f}"
        
        return True, "OK"
    
    def execute_signal(self, signal, equity, open_positions):
        """Execute a single signal."""
        # Check compliance
        compliant, reasons = self.check_compliance(signal, equity, open_positions)
        if not compliant:
            log_signal(
                signal['strategy'], signal['symbol'], signal['direction'],
                signal['confidence'], signal['risk_pct'], signal['reward_ratio'],
                notes=f"Blocked: {'; '.join(reasons)}"
            )
            return None
        
        # Validate stops
        valid, reason = self.validate_stops(
            signal['symbol'], signal['entry'], signal['stop_loss'], signal['take_profit']
        )
        if not valid:
            log_signal(
                signal['strategy'], signal['symbol'], signal['direction'],
                signal['confidence'], signal['risk_pct'], signal['reward_ratio'],
                notes=f"Blocked: {reason}"
            )
            return None
        
        # Calculate position size
        volume = self.risk.calculate_position_size(
            signal['symbol'], signal['risk_pct'], signal['stop_loss_pips'], self.connector
        )
        
        if volume <= 0:
            log_signal(
                signal['strategy'], signal['symbol'], signal['direction'],
                signal['confidence'], signal['risk_pct'], signal['reward_ratio'],
                notes="Blocked: position size calculation failed"
            )
            return None
        
        # Check margin
        has_margin, margin_reason = self.check_margin(signal['symbol'], volume)
        if not has_margin:
            log_signal(
                signal['strategy'], signal['symbol'], signal['direction'],
                signal['confidence'], signal['risk_pct'], signal['reward_ratio'],
                notes=f"Blocked: {margin_reason}"
            )
            return None
        
        # Place order
        result = self.connector.place_order(
            symbol=signal['symbol'],
            direction=signal['direction'],
            volume=volume,
            price=signal['entry'],
            stop_loss=signal['stop_loss'],
            take_profit=signal['take_profit'],
            magic=hash(signal['strategy']) % 10000,
            comment=str(hash(signal['strategy']) % 10000)
        )
        
        if result:
            # Log trade
            trade_id = log_trade(
                strategy=signal['strategy'],
                symbol=signal['symbol'],
                direction=signal['direction'],
                volume=volume,
                entry_price=signal['entry'],
                stop_loss=signal['stop_loss'],
                take_profit=signal['take_profit'],
                fundingpips_model=self.risk.model,
                notes=signal['reason']
            )
            
            # Log signal as executed
            log_signal(
                signal['strategy'], signal['symbol'], signal['direction'],
                signal['confidence'], signal['risk_pct'], signal['reward_ratio'],
                notes=f"Executed: trade_id={trade_id}"
            )
            
            self.risk.trades_today += 1
            
            return trade_id
        
        return None
    
    def run(self, all_signals):
        """Main execution loop."""
        equity = self.connector.get_equity()
        open_positions = self.connector.get_positions()
        
        # Update risk manager
        self.risk.update_equity(equity)
        
        # Aggregate signals
        signals = self.aggregate_signals(all_signals)
        
        executed = []
        for signal in signals:
            # Re-check compliance before each trade
            equity = self.connector.get_equity()
            open_positions = self.connector.get_positions()
            
            trade_id = self.execute_signal(signal, equity, open_positions)
            if trade_id:
                executed.append(trade_id)
        
        return executed

if __name__ == "__main__":
    from mt5_connector import MT5Connector
    from strategies.strategy_01_orb import ORBStrategy
    from strategies.strategy_02_vcp import VCPStrategy
    from strategies.strategy_03_barbell import BarbellStrategy
    from strategies.strategy_04_smc import SMCStrategy
    from strategies.strategy_05_trend import TrendStrategy
    from strategies.strategy_06_meanrev import MeanReversionStrategy
    from strategies.strategy_07_simple import SimpleRRStrategy
    from strategies.strategy_08_price_action import PriceActionStrategy
    
    conn = MT5Connector()
    if conn.connect():
        risk = RiskManager("2-Step Flex")
        engine = ExecutionEngine(conn, risk)
        
        # Collect all signals
        strategies = [
            ORBStrategy(conn),
            VCPStrategy(conn),
            BarbellStrategy(conn),
            SMCStrategy(conn),
            TrendStrategy(conn),
            MeanReversionStrategy(conn),
            SimpleRRStrategy(conn),
            PriceActionStrategy(conn),
        ]
        
        all_signals = []
        for strat in strategies:
            signals = strat.scan()
            all_signals.extend(signals)
        
        # Execute
        executed = engine.run(all_signals)
        print(f"Executed {len(executed)} trades")
        
        conn.disconnect()
