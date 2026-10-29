"""
Test runner for all 8 strategies.
Runs on tradingbeast via wine Python.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5_connector import MT5Connector
from strategies.strategy_01_orb import ORBStrategy
from strategies.strategy_02_vcp import VCPStrategy
from strategies.strategy_03_barbell import BarbellStrategy
from strategies.strategy_04_smc import SMCStrategy
from strategies.strategy_05_trend import TrendStrategy
from strategies.strategy_06_meanrev import MeanReversionStrategy
from strategies.strategy_07_simple import SimpleRRStrategy
from strategies.strategy_08_price_action import PriceActionStrategy

def main():
    print("=" * 60)
    print("FUNDINGPIPS STRATEGY TEST RUNNER")
    print("=" * 60)
    
    # Connect to MT5
    conn = MT5Connector()
    if not conn.connect():
        print("Failed to connect to MT5")
        return
    
    print(f"\nAccount: {conn.account_info.login}")
    print(f"Server: {conn.account_info.server}")
    print(f"Balance: ${conn.get_balance():,.2f}")
    print(f"Equity: ${conn.get_equity():,.2f}")
    
    # Initialize all strategies
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
    
    # Run all strategies
    all_signals = []
    for strat in strategies:
        print(f"\n--- {strat.NAME} ---")
        try:
            signals = strat.scan()
            print(f"  Found {len(signals)} signals")
            for s in signals:
                print(f"  {s['symbol']} {s['direction']} @ {s['entry']:.5f} ({s['reason']})")
            all_signals.extend(signals)
        except Exception as e:
            print(f"  Error: {e}")
    
    # Summary
    print("\n" + "=" * 60)
    print(f"TOTAL SIGNALS: {len(all_signals)}")
    print("=" * 60)
    
    for s in all_signals:
        print(f"  {s['strategy']:15s} {s['symbol']:8s} {s['direction']:5s} @ {s['entry']:.5f}")
    
    conn.disconnect()
    print("\nDone.")

if __name__ == "__main__":
    main()
