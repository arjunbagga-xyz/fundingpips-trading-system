"""
Discretionary Monitor — runs every 15 minutes during market hours.
Scans for opportunities, manages open trades, adjusts SL/TP.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from database import get_db, log_trade, log_signal, log_learning, log_compliance
from mt5_connector import MT5Connector
from risk_manager import RiskManager
from execution_engine import ExecutionEngine
from strategies.strategy_08_price_action import PriceActionStrategy

class DiscretionaryMonitor:
    """Monitors market for discretionary opportunities and manages trades."""
    
    def __init__(self):
        self.connector = MT5Connector()
        self.risk = RiskManager("2-Step Flex")
        self.strategy = None
        self.engine = None
        
    def initialize(self):
        """Initialize monitor."""
        if not self.connector.connect():
            return False
        
        equity = self.connector.get_equity()
        self.risk.starting_balance = equity
        self.risk.peak_equity = equity
        self.risk.update_equity(equity)
        
        self.strategy = PriceActionStrategy(self.connector)
        self.engine = ExecutionEngine(self.connector, self.risk)
        return True
    
    def scan_opportunities(self):
        """Scan for discretionary opportunities."""
        return self.strategy.scan()
    
    def manage_positions(self):
        """Manage open positions — adjust SL/TP, close if thesis invalidated."""
        positions = self.connector.get_positions()
        equity = self.connector.get_equity()
        self.risk.update_equity(equity)
        
        for pos in positions:
            # Check if position is in profit — move SL to breakeven
            if pos.profit > 0:
                # Move SL to breakeven if not already there
                current_sl = pos.sl
                entry = pos.price_open
                if current_sl < entry and pos.type == 0:  # BUY
                    self.connector.modify_position(pos.ticket, stop_loss=entry)
                elif current_sl > entry and pos.type == 1:  # SELL
                    self.connector.modify_position(pos.ticket, stop_loss=entry)
            
            # Check if thesis invalidated (simplified)
            # In a real system, this would check market conditions
            pass
        
        return positions
    
    def run(self):
        """Run one monitoring cycle."""
        print(f"\n{'='*60}")
        print(f"  DISCRETIONARY MONITOR — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        
        # 1. Scan for opportunities
        print("\n[1] Scanning for opportunities...")
        signals = self.scan_opportunities()
        print(f"    Found {len(signals)} opportunities")
        
        # 2. Execute if any
        if signals:
            print("\n[2] Executing opportunities...")
            executed = self.engine.run(signals)
            print(f"    Executed {len(executed)} trades")
        else:
            print("\n[2] No opportunities to execute")
        
        # 3. Manage positions
        print("\n[3] Managing positions...")
        positions = self.manage_positions()
        print(f"    {len(positions)} open positions")
        for pos in positions:
            print(f"    {pos.symbol} {'BUY' if pos.type == 0 else 'SELL'} {pos.volume} @ {pos.price_open:.5f} | PnL: ${pos.profit:.2f}")
        
        # 4. Compliance check
        equity = self.connector.get_equity()
        compliance = self.risk.get_compliance_status(equity, positions)
        print(f"\n[4] Compliance: {'✅ PASS' if compliance['compliant'] else '❌ FAIL'}")
        if compliance['issues']:
            for issue in compliance['issues']:
                print(f"    ⚠️  {issue}")
        
        print(f"\n{'='*60}")
    
    def shutdown(self):
        """Clean shutdown."""
        self.connector.disconnect()

def main():
    """Main entry point."""
    monitor = DiscretionaryMonitor()
    
    if not monitor.initialize():
        print("Failed to initialize monitor")
        return
    
    try:
        monitor.run()
    except KeyboardInterrupt:
        print("\nInterrupted")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
    finally:
        monitor.shutdown()

if __name__ == "__main__":
    main()
