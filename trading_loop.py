"""
Main Trading Loop
Runs all strategies, executes signals, monitors positions, generates reports.
This is the entry point for both the 2x daily cron and the 15-min discretionary monitor.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from database import get_db, log_trade, log_signal, log_learning, log_compliance, init_db
from mt5_connector import MT5Connector
from risk_manager import RiskManager
from execution_engine import ExecutionEngine

# Import all strategies
from strategies.strategy_01_orb import ORBStrategy
from strategies.strategy_02_vcp import VCPStrategy
from strategies.strategy_03_barbell import BarbellStrategy
from strategies.strategy_04_smc import SMCStrategy
from strategies.strategy_05_trend import TrendStrategy
from strategies.strategy_06_meanrev import MeanReversionStrategy
from strategies.strategy_07_simple import SimpleRRStrategy
from strategies.strategy_08_price_action import PriceActionStrategy

class TradingLoop:
    """Main trading loop — strategies, execution, monitoring, reporting."""
    
    def __init__(self, model="2-Step Flex"):
        self.model = model
        self.connector = MT5Connector()
        self.risk = RiskManager(model)
        self.engine = None
        self.strategies = []
        
    def initialize(self):
        """Initialize all components."""
        # Connect to MT5
        if not self.connector.connect():
            print("ERROR: Failed to connect to MT5")
            return False
        
        # Initialize risk manager with current equity
        equity = self.connector.get_equity()
        self.risk.starting_balance = equity
        self.risk.peak_equity = equity
        self.risk.update_equity(equity)
        
        # Initialize execution engine
        self.engine = ExecutionEngine(self.connector, self.risk)
        
        # Initialize strategies
        self.strategies = [
            ORBStrategy(self.connector),
            VCPStrategy(self.connector),
            BarbellStrategy(self.connector),
            SMCStrategy(self.connector),
            TrendStrategy(self.connector),
            MeanReversionStrategy(self.connector),
            SimpleRRStrategy(self.connector),
            PriceActionStrategy(self.connector),
        ]
        
        return True
    
    def scan_all(self):
        """Run all strategies and collect signals."""
        all_signals = []
        for strat in self.strategies:
            try:
                signals = strat.scan()
                all_signals.extend(signals)
            except Exception as e:
                print(f"  Error in {strat.NAME}: {e}")
        return all_signals
    
    def execute_signals(self, all_signals):
        """Execute signals through the execution engine."""
        if not all_signals:
            return []
        return self.engine.run(all_signals)
    
    def monitor_positions(self):
        """Monitor open positions and update PnL."""
        positions = self.connector.get_positions()
        equity = self.connector.get_equity()
        self.risk.update_equity(equity)
        
        # Check for positions that hit SL/TP
        for pos in positions:
            # MT5 auto-closes at SL/TP, so we just track
            pass
        
        return positions
    
    def generate_report(self):
        """Generate daily report."""
        equity = self.connector.get_equity()
        balance = self.connector.get_balance()
        positions = self.connector.get_positions()
        open_pnl = self.connector.get_position_pnl()
        
        # Get today's trades
        conn = get_db()
        c = conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d")
        c.execute("""
            SELECT strategy, COUNT(*) as trades, 
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                   SUM(pnl) as total_pnl
            FROM trades 
            WHERE date(timestamp) = ? AND status = 'closed'
            GROUP BY strategy
        """, (today,))
        strategy_stats = c.fetchall()
        conn.close()
        
        # Compliance status
        compliance = self.risk.get_compliance_status(equity, positions)
        
        # Build report
        report = []
        report.append("=" * 60)
        report.append(f"  TRADING REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        report.append("=" * 60)
        report.append("")
        report.append("ACCOUNT SUMMARY")
        report.append(f"  Balance: ${balance:,.2f}")
        report.append(f"  Equity:  ${equity:,.2f}")
        report.append(f"  Open PnL: ${open_pnl:,.2f}")
        report.append(f"  Drawdown: {self.risk.get_drawdown_pct(equity):.2%}")
        report.append(f"  Daily Loss: {self.risk.get_daily_loss_pct(equity):.2%}")
        report.append("")
        report.append("STRATEGY PERFORMANCE (Today)")
        for row in strategy_stats:
            strategy, trades, wins, losses, pnl = row
            wr = (wins / trades * 100) if trades > 0 else 0
            report.append(f"  {strategy:15s} | Trades: {trades:2d} | WR: {wr:5.1f}% | PnL: ${pnl:,.2f}")
        report.append("")
        report.append("COMPLIANCE")
        report.append(f"  Model: {self.model}")
        report.append(f"  Compliant: {'YES' if compliance['compliant'] else 'NO'}")
        if compliance['issues']:
            for issue in compliance['issues']:
                report.append(f"  WARNING: {issue}")
        report.append("")
        report.append("OPEN POSITIONS")
        if positions:
            for pos in positions:
                report.append(f"  {pos.symbol} {'BUY' if pos.type == 0 else 'SELL'} {pos.volume} lots @ {pos.price_open:.5f} | PnL: ${pos.profit:.2f}")
        else:
            report.append("  None")
        report.append("=" * 60)
        
        return "\n".join(report)
    
    def run_once(self):
        """Run one complete cycle."""
        print(f"\n{'='*60}")
        print(f"  TRADING LOOP — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        
        # 1. Scan for signals
        print("\n[1] Scanning strategies...")
        all_signals = self.scan_all()
        print(f"    Found {len(all_signals)} signals")
        
        # 2. Execute signals
        print("\n[2] Executing signals...")
        executed = self.execute_signals(all_signals)
        print(f"    Executed {len(executed)} trades")
        
        # 3. Monitor positions
        print("\n[3] Monitoring positions...")
        positions = self.monitor_positions()
        print(f"    {len(positions)} open positions")
        
        # 4. Log account state
        print("\n[4] Logging account state...")
        self._log_account_state(positions)
        
        # 5. Log compliance checks
        print("\n[5] Logging compliance checks...")
        self._log_compliance(positions)
        
        # 6. Log learnings
        print("\n[6] Logging learnings...")
        self._log_learnings(all_signals, executed, positions)
        
        # 7. Generate report
        print("\n[7] Generating report...")
        report = self.generate_report()
        print(report)
        
        return report
    
    def _log_account_state(self, positions):
        """Log current account state to database."""
        equity = self.connector.get_equity()
        balance = self.connector.get_balance()
        open_pnl = self.connector.get_position_pnl()
        daily_pnl = self.risk.daily_pnl
        daily_loss_pct = self.risk.get_daily_loss_pct(equity)
        max_dd_pct = self.risk.get_drawdown_pct(equity)
        
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            INSERT INTO account_state (timestamp, balance, equity, open_pnl, daily_pnl, 
                                       daily_loss_pct, max_drawdown_pct, total_trades, 
                                       winning_trades, losing_trades, fundingpips_model, compliance_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(), balance, equity, open_pnl, daily_pnl,
              daily_loss_pct, max_dd_pct, self.risk.trades_today, 0, 0,
              self.model, "PASS" if daily_loss_pct < self.risk.config["daily_loss_limit"] else "FAIL"))
        conn.commit()
        conn.close()
        print(f"    Account state logged")
    
    def _log_compliance(self, positions):
        """Log compliance checks to database."""
        equity = self.connector.get_equity()
        
        # Daily loss check
        daily_loss_pct = self.risk.get_daily_loss_pct(equity)
        daily_limit = self.risk.config["daily_loss_limit"]
        log_compliance("daily_loss_limit", daily_limit, daily_loss_pct, 
                      "PASS" if daily_loss_pct < daily_limit else "FAIL")
        
        # Max drawdown check
        max_dd_pct = self.risk.get_drawdown_pct(equity)
        max_dd_limit = self.risk.config["max_loss_limit"]
        log_compliance("max_drawdown_limit", max_dd_limit, max_dd_pct,
                      "PASS" if max_dd_pct < max_dd_limit else "FAIL")
        
        # Open positions check
        log_compliance("max_open_positions", 3, len(positions),
                      "PASS" if len(positions) <= 3 else "FAIL")
        
        # Trades per day check
        log_compliance("max_trades_per_day", 5, self.risk.trades_today,
                      "PASS" if self.risk.trades_today <= 5 else "FAIL")
        
        print(f"    Compliance checks logged")
    
    def _log_learnings(self, all_signals, executed, positions):
        """Log learnings from this cycle."""
        # Learning: signal count
        if all_signals:
            log_learning("signal_generation", 
                        f"Generated {len(all_signals)} signals from {len(set(s['strategy'] for s in all_signals))} strategies")
        
        # Learning: execution success rate
        if all_signals:
            success_rate = len(executed) / len(all_signals) * 100
            log_learning("execution",
                        f"Executed {len(executed)}/{len(all_signals)} signals ({success_rate:.1f}% success rate)")
        
        # Learning: open positions
        if positions:
            total_pnl = sum(p.profit for p in positions)
            log_learning("position_management",
                        f"{len(positions)} open positions, total PnL: ${total_pnl:.2f}")
        
        # Learning: market conditions
        if all_signals:
            symbols = set(s['symbol'] for s in all_signals)
            log_learning("market_conditions",
                        f"Active symbols: {', '.join(sorted(symbols))}")
        
        print(f"    Learnings logged")
    
    def shutdown(self):
        """Clean shutdown."""
        self.connector.disconnect()
        print("\nTrading loop shutdown complete.")

def main():
    """Main entry point."""
    # Initialize database
    init_db()
    
    # Create and run trading loop
    loop = TradingLoop(model="2-Step Flex")
    
    if not loop.initialize():
        print("Failed to initialize trading loop")
        return
    
    try:
        loop.run_once()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
    finally:
        loop.shutdown()

if __name__ == "__main__":
    main()
