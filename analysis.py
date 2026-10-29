import json

state = {
  "silos": [
    {"silo_id": "1sf-5k-01", "model": "1-Step Flex", "phase": "Phase 1", "status": "active", "current_balance": 4532.68, "equity": 4549.05, "daily_loss_used": 0.0, "daily_baseline": 4574.71, "risk_per_trade": 0.005, "daily_loss_limit": 0.03, "max_loss_limit": 0.12},
    {"silo_id": "2sf-5k-01", "model": "2-Step Flex", "phase": "Master", "status": "active", "current_balance": 6058.41, "equity": 6070.32, "daily_loss_used": 0.0, "daily_baseline": 6070.32, "risk_per_trade": 0.005, "daily_loss_limit": 0.04, "max_loss_limit": 0.12},
    {"silo_id": "2sp-5k-01", "model": "2-Step Pro", "phase": "Master", "status": "active", "current_balance": 5708.19, "equity": 5716.28, "daily_loss_used": 0.0, "daily_baseline": 5716.28, "risk_per_trade": 0.0035, "daily_loss_limit": 0.03, "max_loss_limit": 0.06},
    {"silo_id": "2ss-5k-01", "model": "2-Step Standard", "phase": "Master", "status": "active", "current_balance": 6032.96, "equity": 6045.12, "daily_loss_used": 0.0, "daily_baseline": 6045.12, "risk_per_trade": 0.005, "daily_loss_limit": 0.05, "max_loss_limit": 0.1},
    {"silo_id": "zero-5k-01", "model": "Zero", "phase": "Master", "status": "active", "current_balance": 4780.6, "equity": 4780.35, "daily_loss_used": 0.0, "daily_baseline": 4780.6, "risk_per_trade": 0.0025, "daily_loss_limit": 0.03, "max_loss_limit": 0.05}
  ],
  "positions": [
    {"silo_id": "1sf-5k-01", "symbol": "EURUSD", "direction": "SELL", "volume": 0.12, "entry_price": 1.12172, "pnl": 16.92, "strategy": "PriceAction"},
    {"silo_id": "1sf-5k-01", "symbol": "NZDUSD", "direction": "SELL", "volume": 0.12, "entry_price": 0.56974, "pnl": -4.56, "strategy": "PriceAction"},
    {"silo_id": "1sf-5k-01", "symbol": "USDCAD", "direction": "SELL", "volume": 0.17, "entry_price": 1.40072, "pnl": 4.01, "strategy": "PriceAction"},
    {"silo_id": "2sf-5k-01", "symbol": "AUDUSD", "direction": "SELL", "volume": 0.15, "entry_price": 0.70199, "pnl": -23.4, "strategy": "PriceAction"},
    {"silo_id": "2sf-5k-01", "symbol": "EURUSD", "direction": "SELL", "volume": 0.15, "entry_price": 1.12172, "pnl": 21.15, "strategy": "PriceAction"},
    {"silo_id": "2sf-5k-01", "symbol": "NZDUSD", "direction": "BUY", "volume": 0.12, "entry_price": 0.56853, "pnl": 14.16, "strategy": "ORB"},
    {"silo_id": "2sp-5k-01", "symbol": "AUDUSD", "direction": "SELL", "volume": 0.09, "entry_price": 0.70199, "pnl": -14.04, "strategy": "PriceAction"},
    {"silo_id": "2sp-5k-01", "symbol": "EURUSD", "direction": "SELL", "volume": 0.09, "entry_price": 1.12172, "pnl": 12.69, "strategy": "PriceAction"},
    {"silo_id": "2sp-5k-01", "symbol": "NZDUSD", "direction": "BUY", "volume": 0.08, "entry_price": 0.56853, "pnl": 9.44, "strategy": "ORB"},
    {"silo_id": "2ss-5k-01", "symbol": "AUDUSD", "direction": "SELL", "volume": 0.14, "entry_price": 0.70199, "pnl": -21.84, "strategy": "PriceAction"},
    {"silo_id": "2ss-5k-01", "symbol": "EURUSD", "direction": "SELL", "volume": 0.14, "entry_price": 1.12172, "pnl": 19.6, "strategy": "PriceAction"},
    {"silo_id": "2ss-5k-01", "symbol": "NZDUSD", "direction": "BUY", "volume": 0.12, "entry_price": 0.56853, "pnl": 14.4, "strategy": "ORB"},
    {"silo_id": "zero-5k-01", "symbol": "AUDUSD", "direction": "SELL", "volume": 0.06, "entry_price": 0.70199, "pnl": -9.36, "strategy": "PriceAction"},
    {"silo_id": "zero-5k-01", "symbol": "EURUSD", "direction": "SELL", "volume": 0.06, "entry_price": 1.12172, "pnl": 8.4, "strategy": "PriceAction"},
    {"silo_id": "zero-5k-01", "symbol": "GBPUSD", "direction": "BUY", "volume": 0.02, "entry_price": 1.33814, "pnl": -1.06, "strategy": "Barbell", "pillar": "black_swan"},
    {"silo_id": "zero-5k-01", "symbol": "USDCAD", "direction": "SELL", "volume": 0.08, "entry_price": 1.40072, "pnl": 1.77, "strategy": "PriceAction"}
  ],
  "market_data": {
    "XAUUSD": [{"close": 3999.79, "atr": 15.0448, "adx": 49.02, "rsi": 35.94}],
    "XAGUSD": [{"close": 53.958, "atr": 0.36759, "adx": 40.71, "rsi": 39.78}],
    "EURUSD": [{"close": 1.11947, "atr": 0.000997, "adx": 23.43, "rsi": 42.43}],
    "AUDUSD": [{"close": 0.70373, "atr": 0.000749, "adx": 14.42, "rsi": 64.05}],
    "GBPUSD": [{"close": 1.33726, "atr": 0.001119, "adx": 23.47, "rsi": 43.54}],
    "NZDUSD": [{"close": 0.57009, "atr": 0.000628, "adx": 17.7, "rsi": 61.22}],
    "USDCAD": [{"close": 1.40067, "atr": 0.001251, "adx": 19.33, "rsi": 50.58}],
    "NVDA": [{"close": 211.0, "atr": 1.67463, "adx": 45.24, "rsi": 33.28}],
    "TSLA": [{"close": 372.22, "atr": 4.18159, "adx": 31.77, "rsi": 43.2}]
  },
  "regime": {
    "XAUUSD": {"regime": "trending", "volatility": "normal"},
    "XAGUSD": {"regime": "trending", "volatility": "normal"},
    "EURUSD": {"regime": "ranging", "volatility": "low"},
    "AUDUSD": {"regime": "ranging", "volatility": "low"},
    "GBPUSD": {"regime": "ranging", "volatility": "low"},
    "NZDUSD": {"regime": "ranging", "volatility": "low"},
    "USDCAD": {"regime": "high_vol", "volatility": "high"},
    "NVDA": {"regime": "trending", "volatility": "normal"},
    "TSLA": {"regime": "trending", "volatility": "high"}
  },
  "strategy_performance": {
    "PriceAction": {"n": 105, "tp": 1533.03, "wins": 33, "win_rate": 31.4},
    "ORB": {"n": 62, "tp": 650.33, "wins": 20, "win_rate": 32.3},
    "Barbell": {"n": 4, "tp": 5.99, "wins": 4, "win_rate": 100.0},
    "PocketChange": {"n": 3, "tp": -57.83, "wins": 0, "win_rate": 0.0},
    "MeanRev": {"n": 4, "tp": -89.57, "wins": 0, "win_rate": 0.0}
  },
  "pillar_coverage": {
    "zero-5k-01": {
      "trend": {"covered": True, "strategies": ["SecondBreakout"], "open_positions": 0},
      "black_swan": {"covered": True, "strategies": ["Barbell"], "open_positions": 1},
      "pocket_change": {"covered": False, "strategies": [], "open_positions": 0}
    }
  }
}

# Calculate session P&L
silo_pnl = {}
for pos in state["positions"]:
    sid = pos["silo_id"]
    if sid not in silo_pnl:
        silo_pnl[sid] = 0
    silo_pnl[sid] += pos["pnl"]

print("Session P&L by Silo:")
for sid, pnl in silo_pnl.items():
    print(f"  {sid}: {pnl:.2f}")

# Analyze positions by symbol regime
print("\nPositions by Market Regime:")
for pos in state["positions"]:
    sym = pos["symbol"]
    reg = state["regime"].get(sym, {})
    print(f"  {pos['silo_id']} {sym} ({reg.get('regime', 'unknown')}): {pos['pnl']:+.2f}")

# Check SecondBreakout performance
print("\nSecondBreakout: Not in strategy_performance - needs review")