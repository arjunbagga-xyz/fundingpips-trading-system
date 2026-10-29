#!/usr/bin/env python3
"""
STRATEGY SANDBOX - Test proposed strategies on historical data

Usage:
    from strategy_sandbox import test_strategy_proposal
    result = test_strategy_proposal(proposal_id)
    # returns dict with metrics
"""

import sqlite3
import json
import importlib.util
import sys
from datetime import datetime, timedelta

DB_PATH = '/home/tradingbeast/fundingpips/framework/fundingpips.db'

def get_historical_bars(symbol, timeframe, days=30):
    """Fetch historical bars from market_data table."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    cur.execute("""
        SELECT timestamp, open, high, low, close, volume
        FROM market_data
        WHERE symbol=? AND timeframe=? AND timestamp >= ?
        ORDER BY timestamp
    """, (symbol, timeframe, cutoff))
    rows = cur.fetchall()
    conn.close()
    return [
        {
            'timestamp': r['timestamp'],
            'open': r['open'],
            'high': r['high'],
            'low': r['low'],
            'close': r['close'],
            'tick_volume': r['volume'],
        }
        for r in rows
    ]

def load_strategy_class(code, class_name):
    """Dynamically load a strategy class from code string."""
    # Create a temporary module
    spec = importlib.util.spec_from_loader(class_name, loader=None)
    module = importlib.util.module_from_spec(spec)
    
    # Inject required imports
    exec_globals = {
        'MetaTrader5': __import__('MetaTrader5') if 'MetaTrader5' in sys.modules else None,
        'datetime': datetime,
    }
    
    # Execute the code
    exec(code, exec_globals)
    
    return exec_globals[class_name]

def run_sandbox_backtest(strategy_class, symbol, timeframe, bars, initial_balance=5000):
    """Run a backtest of the strategy on historical bars."""
    # This is a simplified backtest - in production would use proper event-driven backtest
    from core.market_structure import atr as ms_atr, adx as ms_adx, rsi as ms_rsi
    
    # Prepare data arrays
    highs = [b['high'] for b in bars]
    lows = [b['low'] for b in bars]
    closes = [b['close'] for b in bars]
    opens = [b['open'] for b in bars]
    
    # Instantiate strategy with mock connector
    class MockConnector:
        def get_rates(self, sym, tf, count):
            return bars[-count:] if len(bars) >= count else bars
        def get_tick(self, sym):
            class Tick:
                bid = bars[-1]['close']
                ask = bars[-1]['close']
            return Tick()
        def get_symbol_info(self, sym):
            class Info:
                digits = 5 if 'JPY' not in sym else 3
            return Info()
    
    connector = MockConnector()
    
    # Handle strategies that need all_silo_positions
    try:
        strat = strategy_class(connector, all_silo_positions={})
    except TypeError:
        try:
            strat = strategy_class(connector)
        except Exception as e:
            return {'error': f'Failed to instantiate: {e}'}
    
    trades = []
    balance = initial_balance
    
    # Scan at each bar (simplified - real backtest would be event-driven)
    for i in range(50, len(bars) - 1):  # Need 50 bars for indicators
        # Create a view of bars up to current point
        class LimitedConnector:
            def __init__(self, bars_up_to):
                self.bars_up_to = bars_up_to
            def get_rates(self, sym, tf, count):
                return self.bars_up_to[-count:] if len(self.bars_up_to) >= count else self.bars_up_to
            def get_tick(self, sym):
                class Tick:
                    bid = self.bars_up_to[-1]['close']
                    ask = self.bars_up_to[-1]['close']
                return Tick()
            def get_symbol_info(self, sym):
                class Info:
                    digits = 5 if 'JPY' not in sym else 3
                return Info()
        
        limited_conn = LimitedConnector(bars[:i+1])
        try:
            strat.connector = limited_conn
            signals = strat.scan()
        except Exception as e:
            continue
        
        if signals:
            # Take first signal
            sig = signals[0]
            direction = sig['direction']
            entry = sig['entry']
            sl = sig['stop_loss']
            tp = sig['take_profit']
            
            # Simulate forward until hit SL/TP or max 20 bars
            exit_price = None
            exit_reason = None
            for j in range(i+1, min(i+20, len(bars))):
                bar = bars[j]
                if direction == 'BUY':
                    if bar['low'] <= sl:
                        exit_price = sl
                        exit_reason = 'SL'
                        break
                    if bar['high'] >= tp:
                        exit_price = tp
                        exit_reason = 'TP'
                        break
                else:
                    if bar['high'] >= sl:
                        exit_price = sl
                        exit_reason = 'SL'
                        break
                    if bar['low'] <= tp:
                        exit_price = tp
                        exit_reason = 'TP'
                        break
            
            if exit_price is None:
                # Close at end of window
                exit_price = bars[min(i+19, len(bars)-1)]['close']
                exit_reason = 'TIME'
            
            # Calculate PnL
            pip_value = 10  # simplified
            if direction == 'BUY':
                pips = (exit_price - entry) / 0.0001
            else:
                pips = (entry - exit_price) / 0.0001
            
            pnl = pips * pip_value * 0.01  # 0.01 lots = $0.10/pip
            
            trades.append({
                'entry_idx': i,
                'exit_idx': min(i+19, len(bars)-1),
                'direction': direction,
                'entry': entry,
                'exit': exit_price,
                'sl': sl,
                'tp': tp,
                'pips': round(pips, 1),
                'pnl': round(pnl, 2),
                'reason': exit_reason,
            })
            
            balance += pnl
    
    # Calculate metrics
    if not trades:
        return {
            'trades': 0,
            'win_rate': 0,
            'total_pnl': 0,
            'profit_factor': 0,
            'max_drawdown': 0,
            'sharpe': 0,
        }
    
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    
    total_pnl = sum(t['pnl'] for t in trades)
    gross_profit = sum(t['pnl'] for t in wins)
    gross_loss = abs(sum(t['pnl'] for t in losses))
    
    # Max drawdown
    peak = initial_balance
    max_dd = 0
    running = initial_balance
    for t in trades:
        running += t['pnl']
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd
    
    return {
        'trades': len(trades),
        'win_rate': round(len(wins) / len(trades) * 100, 1),
        'total_pnl': round(total_pnl, 2),
        'profit_factor': round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999,
        'max_drawdown': round(max_dd, 2),
        'avg_win': round(sum(t['pnl'] for t in wins) / len(wins), 2) if wins else 0,
        'avg_loss': round(sum(t['pnl'] for t in losses) / len(losses), 2) if losses else 0,
        'trade_details': trades[:10],  # first 10
    }

def test_strategy_proposal(proposal_id):
    """Test a strategy proposal from the database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM strategy_proposals_v2 WHERE id=?", (proposal_id,))
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return {'error': 'Proposal not found'}
    
    # Load the strategy class
    code = row['code']
    class_name = row['strategy_name'].replace('-', '_').replace(' ', '_')
    
    try:
        StrategyClass = load_strategy_class(code, class_name)
    except Exception as e:
        return {'error': f'Failed to load class: {e}'}
    
    # Test on multiple symbols/timeframes
    symbols = ['EURUSD', 'GBPUSD', 'XAUUSD']
    timeframe = 'H1'  # Default test timeframe
    
    all_results = {}
    for sym in symbols:
        bars = get_historical_bars(sym, timeframe, days=60)
        if len(bars) < 100:
            all_results[sym] = {'error': f'Insufficient data: {len(bars)} bars'}
            continue
        
        result = run_sandbox_backtest(StrategyClass, sym, timeframe, bars)
        all_results[sym] = result
    
    # Aggregate
    total_trades = sum(r.get('trades', 0) for r in all_results.values() if 'error' not in r)
    total_pnl = sum(r.get('total_pnl', 0) for r in all_results.values() if 'error' not in r)
    avg_wr = sum(r.get('win_rate', 0) for r in all_results.values() if 'error' not in r) / max(1, len([r for r in all_results.values() if 'error' not in r]))
    
    summary = {
        'proposal_id': proposal_id,
        'strategy_name': row['strategy_name'],
        'total_trades': total_trades,
        'avg_win_rate': round(avg_wr, 1),
        'total_pnl': round(total_pnl, 2),
        'per_symbol': all_results,
        'tested_at': datetime.now().isoformat(),
    }
    
    # Update database
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        UPDATE strategy_proposals_v2
        SET status='tested', test_results_json=?, tested_at=?
        WHERE id=?
    """, (json.dumps(summary), datetime.now().isoformat(), proposal_id))
    conn.commit()
    conn.close()
    
    return summary

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        pid = int(sys.argv[1])
        result = test_strategy_proposal(pid)
        print(json.dumps(result, indent=2, default=str))
    else:
        print("Usage: python3 strategy_sandbox.py <proposal_id>")