"""
Report Sender — generates daily report and delivers to Telegram + chat.
Runs on tradingbeast via wine Python, called by cron jobs.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import urllib.request
import urllib.parse
from datetime import datetime
from database import get_db

# Telegram settings
TELEGRAM_BOT_TOKEN = None
TELEGRAM_CHAT_ID = "610566144"
TELEGRAM_THREAD_ID = "19252"  # Reports topic

def load_telegram_config():
    """Load Telegram config from .env."""
    global TELEGRAM_BOT_TOKEN, TELEGRAM_THREAD_ID
    # Try multiple paths (Windows native, wine, Linux)
    paths = [
        "/home/tradingbeast/fundingpips/.env",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
        os.path.expanduser("~/AppData/Local/hermes/.env"),
        "/c/Users/MSI/AppData/Local/hermes/.env",
        "Z:/home/tradingbeast/AppData/Local/hermes/.env",
    ]
    for env_path in paths:
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("TELEGRAM_BOT_TOKEN="):
                        TELEGRAM_BOT_TOKEN = line.split("=", 1)[1].strip()
                    elif line.startswith("TELEGRAM_TOPIC_REPORTS="):
                        TELEGRAM_THREAD_ID = line.split("=", 1)[1].strip()
            if TELEGRAM_BOT_TOKEN:
                print(f"  Loaded Telegram config from {env_path}")
                return

def send_telegram_message(text):
    """Send message to Telegram topic."""
    if not TELEGRAM_BOT_TOKEN:
        print("  Telegram: no bot token, skipping")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "message_thread_id": TELEGRAM_THREAD_ID,
        "text": text
    }).encode("utf-8")
    
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            if result.get("ok"):
                print(f"  Telegram: sent to thread {TELEGRAM_THREAD_ID}")
                return True
            else:
                print(f"  Telegram: failed - {result.get('description')}")
                return False
    except Exception as e:
        print(f"  Telegram: error - {e}")
        return False

def generate_report():
    """Generate daily report from database."""
    conn = get_db()
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Account state
    c.execute("""
        SELECT balance, equity, open_pnl, daily_pnl, daily_loss_pct, max_drawdown_pct
        FROM account_state WHERE date(timestamp) = ?
        ORDER BY timestamp DESC LIMIT 1
    """, (today,))
    account = c.fetchone()
    
    # Strategy performance
    c.execute("""
        SELECT strategy, COUNT(*) as trades,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
               SUM(pnl) as total_pnl
        FROM trades
        WHERE date(timestamp) = ? AND status = 'closed'
        GROUP BY strategy
    """, (today,))
    strategies = c.fetchall()
    
    # Open positions
    c.execute("""
        SELECT symbol, direction, volume, entry_price, pnl
        FROM trades
        WHERE status = 'open'
    """, ())
    positions = c.fetchall()
    
    # Learnings
    c.execute("""
        SELECT category, learning, strategy
        FROM learnings
        WHERE date(timestamp) = ?
        ORDER BY timestamp DESC LIMIT 5
    """, (today,))
    learnings = c.fetchall()
    
    conn.close()
    
    # Build report
    lines = []
    lines.append(f"Daily Trading Report - {today}")
    lines.append("=" * 40)
    lines.append("")
    
    if account:
        balance, equity, open_pnl, daily_pnl, daily_loss, max_dd = account
        lines.append("ACCOUNT SUMMARY")
        lines.append(f"  Balance: ${balance:,.2f}")
        lines.append(f"  Equity:  ${equity:,.2f}")
        lines.append(f"  Open PnL: ${open_pnl:,.2f}")
        lines.append(f"  Daily PnL: ${daily_pnl:,.2f}")
        lines.append(f"  Daily Loss: {daily_loss:.2%}")
        lines.append(f"  Max DD: {max_dd:.2%}")
    else:
        lines.append("ACCOUNT SUMMARY")
        lines.append("  No data yet today")
    
    lines.append("")
    lines.append("STRATEGY PERFORMANCE")
    if strategies:
        for row in strategies:
            strategy, trades, wins, losses, pnl = row
            wr = (wins / trades * 100) if trades > 0 else 0
            lines.append(f"  {strategy:15s} | Trades: {trades:2d} | WR: {wr:5.1f}% | PnL: ${pnl:,.2f}")
    else:
        lines.append("  No trades yet today")
    
    lines.append("")
    lines.append("OPEN POSITIONS")
    if positions:
        for pos in positions:
            symbol, direction, volume, entry, pnl = pos
            pnl_str = f"${pnl:.2f}" if pnl is not None else "N/A"
            lines.append(f"  {symbol} {direction} {volume} lots @ {entry:.5f} | PnL: {pnl_str}")
    else:
        lines.append("  None")
    
    lines.append("")
    lines.append("LEARNINGS")
    if learnings:
        for i, row in enumerate(learnings, 1):
            category, learning, strategy = row
            lines.append(f"  {i}. [{category}] {learning}")
    else:
        lines.append("  None yet")
    
    lines.append("")
    lines.append("=" * 40)
    
    return "\n".join(lines)

def main():
    """Main entry point."""
    load_telegram_config()
    
    report = generate_report()
    
    # Send to Telegram
    send_telegram_message(report)
    
    # Print to stdout (for this chat)
    print(report)

if __name__ == "__main__":
    main()
