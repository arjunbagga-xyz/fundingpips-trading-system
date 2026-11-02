#!/usr/bin/env python3
"""
MT5 Event Listener — receives trade events from the mt5_event_relay EA
via TCP socket and updates the SQLite DB in real time.

Runs as a background process. Started by the rule engine if not already running.
Listens on port 9876 (configurable via env MT5_RELAY_PORT).

Usage:
    python3 mt5_event_listener.py [--port 9876] [--db /path/to/fundingpips.db]
"""
import argparse
import json
import os
import socket
import sqlite3
import sys
import threading
import time
from datetime import datetime

# Add framework to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.silo_db import get_db, record_silo_trade, close_silo_trade, get_open_silo_trades
from core.silos import SILOS

# Magic number -> silo_id mapping
MAGIC_TO_SILO = {s["magic_number"]: s["silo_id"] for s in SILOS}

# Track which deals we've already processed (dedup)
_processed_deals = set()
_lock = threading.Lock()

import sys
sys.stdout.reconfigure(line_buffering=True)

DB_PATH = None


def get_db_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def handle_event(event):
    """Process a single trade event from the EA."""
    deal_ticket = event.get("deal_ticket")
    position = event.get("position")
    symbol = event.get("symbol")
    deal_type = event.get("type")  # 0=BUY, 1=SELL
    volume = event.get("volume", 0)
    price = event.get("price", 0)
    profit = event.get("profit", 0)
    sl = event.get("sl", 0)
    tp = event.get("tp", 0)
    entry = event.get("entry")  # 0=IN, 1=OUT, 2=INOUT, 3=OUT_BY
    magic = event.get("magic", 0)
    comment = event.get("comment", "")

    if not deal_ticket or not position:
        return

    # Dedup: skip if we've already processed this deal
    with _lock:
        if deal_ticket in _processed_deals:
            return
        _processed_deals.add(deal_ticket)

    # Only process our silo's trades
    silo_id = MAGIC_TO_SILO.get(magic)
    if not silo_id:
        return

    direction = "BUY" if deal_type == 0 else "SELL"

    if entry == 0:  # DEAL_ENTRY_IN — position opened
        # Check if already in DB
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM silo_trades WHERE ticket = ?", (position,))
        if c.fetchone()[0] > 0:
            conn.close()
            return  # already recorded
        conn.close()

        try:
            record_silo_trade(
                silo_id, position, None, "ea_relay", symbol, direction,
                volume, price, sl, tp, 0.0, source="ea_relay"
            )
            print(f"[LISTENER] RECORDED {silo_id} {symbol} {direction} ticket={position} (deal={deal_ticket})")
        except Exception as e:
            print(f"[LISTENER] ERROR recording {position}: {e}")

    elif entry in (1, 3):  # DEAL_ENTRY_OUT or OUT_BY — position closed
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT status, pnl FROM silo_trades WHERE silo_id = ? AND ticket = ?",
                  (silo_id, position))
        row = c.fetchone()
        if row and row["status"] == "open":
            conn.close()
            try:
                close_silo_trade(silo_id, position, profit)
                print(f"[LISTENER] CLOSED {silo_id} {symbol} ticket={position} pnl={profit:+.2f}")
            except Exception as e:
                print(f"[LISTENER] ERROR closing {position}: {e}")
        else:
            conn.close()

    elif entry == 2:  # DEAL_ENTRY_INOUT — position reversed
        # Treat as close of old + open of new
        # For simplicity, just close the old position
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT status FROM silo_trades WHERE silo_id = ? AND ticket = ?",
                  (silo_id, position))
        row = c.fetchone()
        if row and row["status"] == "open":
            conn.close()
            try:
                close_silo_trade(silo_id, position, profit)
                print(f"[LISTENER] REVERSED (closed) {silo_id} {symbol} ticket={position} pnl={profit:+.2f}")
            except Exception as e:
                print(f"[LISTENER] ERROR closing reversed {position}: {e}")
        else:
            conn.close()


def handle_client(conn, addr):
    """Handle a single EA connection."""
    print(f"[LISTENER] EA connected from {addr}")
    buffer = ""
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buffer += data.decode("utf-8", errors="replace")
            # Process complete JSON objects (newline-delimited)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    handle_event(event)
                except json.JSONDecodeError as e:
                    print(f"[LISTENER] JSON parse error: {e} — {line[:100]}")
    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f"[LISTENER] Connection lost: {e}")
    finally:
        conn.close()
        print(f"[LISTENER] EA disconnected from {addr}")


def main():
    global DB_PATH
    parser = argparse.ArgumentParser(description="MT5 Event Listener")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MT5_RELAY_PORT", "9876")))
    parser.add_argument("--db", type=str, default=os.environ.get("MT5_DB_PATH", "/home/tradingbeast/fundingpips/framework/fundingpips.db"))
    args = parser.parse_args()

    DB_PATH = args.db
    print(f"[LISTENER] Starting on port {args.port}, DB: {DB_PATH}")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", args.port))
    server.listen(5)
    print(f"[LISTENER] Listening on 127.0.0.1:{args.port}")

    while True:
        try:
            conn, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
        except (KeyboardInterrupt, SystemExit):
            print("[LISTENER] Shutting down")
            break
        except Exception as e:
            print(f"[LISTENER] Accept error: {e}")
            time.sleep(1)

    server.close()


if __name__ == "__main__":
    main()