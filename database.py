"""
FundingPips Trading Framework - Core Infrastructure
Built for: tradingbeast (Ubuntu 24.04, Wine, MT5)
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "fundingpips.db"

def init_db():
    """Initialize SQLite database with all tables."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Trades table
    c.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            strategy TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            volume REAL NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL,
            take_profit REAL,
            exit_price REAL,
            exit_time TEXT,
            pnl REAL,
            pnl_r REAL,
            status TEXT DEFAULT 'open',
            fundingpips_model TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Signals table
    c.execute('''
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            strategy TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            confidence REAL,
            risk_pct REAL,
            reward_ratio REAL,
            status TEXT DEFAULT 'pending',
            executed_at TEXT,
            trade_id INTEGER,
            notes TEXT
        )
    ''')
    
    # Account state table
    c.execute('''
        CREATE TABLE IF NOT EXISTS account_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            balance REAL,
            equity REAL,
            open_pnl REAL,
            daily_pnl REAL,
            daily_loss_pct REAL,
            max_drawdown_pct REAL,
            total_trades INTEGER,
            winning_trades INTEGER,
            losing_trades INTEGER,
            fundingpips_model TEXT,
            compliance_status TEXT
        )
    ''')
    
    # Strategy performance table
    c.execute('''
        CREATE TABLE IF NOT EXISTS strategy_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            strategy TEXT NOT NULL,
            trades INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            win_rate REAL,
            total_pnl REAL,
            avg_pnl REAL,
            max_drawdown REAL,
            profit_factor REAL,
            sharpe REAL
        )
    ''')
    
    # Learnings table
    c.execute('''
        CREATE TABLE IF NOT EXISTS learnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            category TEXT NOT NULL,
            strategy TEXT,
            symbol TEXT,
            learning TEXT NOT NULL,
            action_taken TEXT,
            outcome TEXT
        )
    ''')
    
    # Compliance log
    c.execute('''
        CREATE TABLE IF NOT EXISTS compliance_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            rule_name TEXT NOT NULL,
            rule_value REAL,
            current_value REAL,
            status TEXT NOT NULL,
            action_taken TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")

def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def log_trade(strategy, symbol, direction, volume, entry_price, stop_loss=None, 
              take_profit=None, fundingpips_model=None, notes=None):
    """Log a new trade."""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO trades (timestamp, strategy, symbol, direction, volume, 
                           entry_price, stop_loss, take_profit, fundingpips_model, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (datetime.now().isoformat(), strategy, symbol, direction, volume,
          entry_price, stop_loss, take_profit, fundingpips_model, notes))
    conn.commit()
    trade_id = c.lastrowid
    conn.close()
    return trade_id

def close_trade(trade_id, exit_price, pnl, pnl_r, notes=None):
    """Close a trade and log the result."""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        UPDATE trades 
        SET exit_price = ?, exit_time = ?, pnl = ?, pnl_r = ?, status = 'closed', notes = ?
        WHERE id = ?
    ''', (exit_price, datetime.now().isoformat(), pnl, pnl_r, notes, trade_id))
    conn.commit()
    conn.close()

def log_signal(strategy, symbol, direction, confidence, risk_pct, reward_ratio, notes=None):
    """Log a signal."""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO signals (timestamp, strategy, symbol, direction, confidence, 
                            risk_pct, reward_ratio, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (datetime.now().isoformat(), strategy, symbol, direction, confidence,
          risk_pct, reward_ratio, notes))
    conn.commit()
    signal_id = c.lastrowid
    conn.close()
    return signal_id

def log_learning(category, learning, strategy=None, symbol=None, action_taken=None, outcome=None):
    """Log a learning."""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO learnings (timestamp, category, strategy, symbol, learning, action_taken, outcome)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (datetime.now().isoformat(), category, strategy, symbol, learning, action_taken, outcome))
    conn.commit()
    conn.close()

def log_compliance(rule_name, rule_value, current_value, status, action_taken=None):
    """Log a compliance check."""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO compliance_log (timestamp, rule_name, rule_value, current_value, status, action_taken)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (datetime.now().isoformat(), rule_name, rule_value, current_value, status, action_taken))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
