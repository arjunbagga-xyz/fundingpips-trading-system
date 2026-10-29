"""System health check for FundingPips framework."""
import os
import sys
import sqlite3
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    print("=" * 60)
    print("  SYSTEM HEALTH CHECK")
    print("=" * 60)
    
    # 1. MT5 Connection
    print("\n[1] MT5 Connection")
    try:
        import MetaTrader5 as mt5
        if mt5.initialize():
            info = mt5.account_info()
            if info:
                print(f"  Connected: {info.login} @ {info.server}")
                print(f"  Balance: ${info.balance:,.2f}")
                print(f"  Equity: ${info.equity:,.2f}")
                print(f"  Free Margin: ${info.margin_free:,.2f}")
                print(f"  Leverage: 1:{info.leverage}")
            else:
                print("  No account logged in")
            mt5.shutdown()
        else:
            print(f"  FAILED: {mt5.last_error()}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # 2. Database
    print("\n[2] Database")
    db_path = "/home/tradingbeast/fundingpips/fundingpips.db"
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM trades")
        trades = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM signals")
        signals = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM learnings")
        learnings = c.fetchone()[0]
        conn.close()
        print(f"  Database: {db_path}")
        print(f"  Trades: {trades}")
        print(f"  Signals: {signals}")
        print(f"  Learnings: {learnings}")
    else:
        print("  Database not found")
    
    # 3. Cron Jobs
    print("\n[3] Cron Jobs")
    try:
        result = os.popen("crontab -l 2>/dev/null").read()
        if "fundingpips" in result:
            print("  FundingPips cron jobs: ACTIVE")
            for line in result.split("\n"):
                if "fundingpips" in line:
                    print(f"    {line}")
        else:
            print("  FundingPips cron jobs: NOT FOUND")
    except Exception as e:
        print(f"  Error checking cron: {e}")
    
    # 4. Files
    print("\n[4] Framework Files")
    files = [
        "database.py", "mt5_connector.py", "risk_manager.py",
        "execution_engine.py", "trading_loop.py", "discretionary_monitor.py",
        "report_sender.py", "close_all.py", "test_strategies.py",
        "cron_main.sh", "cron_discretionary.sh"
    ]
    for f in files:
        path = f"/home/tradingbeast/fundingpips/{f}"
        exists = "OK" if os.path.exists(path) else "MISSING"
        print(f"  {f:30s} {exists}")
    
    # 5. Strategies
    print("\n[5] Strategy Files")
    strat_dir = "/home/tradingbeast/fundingpips/strategies"
    if os.path.exists(strat_dir):
        for f in sorted(os.listdir(strat_dir)):
            if f.endswith(".py"):
                print(f"  {f:40s} OK")
    else:
        print("  Strategy directory not found")
    
    # 6. Logs
    print("\n[6] Recent Logs")
    log_dir = "/home/tradingbeast/fundingpips/logs"
    if os.path.exists(log_dir):
        logs = sorted(os.listdir(log_dir))[-5:]
        for log in logs:
            print(f"  {log}")
    else:
        print("  No logs yet")
    
    # 7. Telegram
    print("\n[7] Telegram")
    env_path = "/home/tradingbeast/fundingpips/.env"
    if os.path.exists(env_path):
        with open(env_path) as f:
            content = f.read()
            has_token = "TELEGRAM_BOT_TOKEN=" in content and "TELEGRAM_BOT_TOKEN=886544" not in content
            has_thread = "TELEGRAM_CRON_THREAD_ID=19252" in content
            token_status = "OK" if has_token else "MISSING"
            thread_status = "OK (19252)" if has_thread else "MISSING"
            print(f"  Bot token: {token_status}")
            print(f"  Thread ID: {thread_status}")
    else:
        print("  .env not found")
    
    print("\n" + "=" * 60)
    print("  HEALTH CHECK COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    main()
