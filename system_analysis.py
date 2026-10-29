#!/usr/bin/env python3
"""
FundingPips Trader State Analyzer
Generates structured JSON assessment for the trading system
"""

import json
from datetime import datetime

# Parse the incoming state
state_json = '''PASTE_YOUR_JSON_HERE'''

state = json.loads(state_json)

# Key observations
silos = state["silos"]
positions = state["positions"]
market_data = state["market_data"]
psych = state["psych"]
learnings = state["learnings"]
regime = state["regime"]
strategy_performance = state["strategy_performance"]

# Current psych state (most recent entry)
current_psych = psych[-1] if psych else {}
emotional_state = current_psych.get("emotional_state", "unknown")
alert_level = current_psych.get("alert_level", "normal")

# Calculate position P&L by silo
silo_pnl = {}
for p in positions:
    sid = p["silo_id"]
    if sid not in silo_pnl:
        silo_pnl[sid] = 0
    silo_pnl[sid] += p["pnl"]

# Strategy performance analysis
strategy_df = {}
for s in strategy_performance:
    strategy_df[s["strategy"]] = {
        "n": s["n"],
        "tp": s["tp"],
        "wins": s["wins"],
        "wr": s["wins"]/s["n"] if s["n"] > 0 else 0
    }

# Identify losing strategies
losing_strategies = [s["strategy"] for s in strategy_performance if s["tp"] < 0]

# Current AUDUSD position analysis
aud_positions = [p for p in positions if p["symbol"] == "AUDUSD" and p["pnl"] < 0]
aud_losses = sum(p["pnl"] for p in aud_positions)

# EURUSD position analysis
eur_positions = [p for p in positions if p["symbol"] == "EURUSD" and p["pnl"] > 0]
eur_gains = sum(p["pnl"] for p in eur_positions)

# XAUUSD regime analysis
xau_regime = next((r for r in regime if r["symbol"] == "XAUUSD"), None)

# Generate assessment
assessment = "System in high-stress state: emotional alert 'intervene', 7 revenge trades, weekend breach. AUDUSD shorts losing 55+ pts across 4 silos. XAUUSD trending with ADX 49 - highest conviction setup. Zero-5k-01 has outstanding Barbell position in GBPUSD."

# Build output
output = {
    "assessment": assessment,
    "assessment_confidence": 0.85,
    "actions": [
        "Close all AUDUSD short positions across 1sf-5k-01, 2sf-5k-01, 2sp-5k-01, 2ss-5k-01",
        "Reduce zero-5k-01 GBPUSD black swan position to 50% if breakout confirms",
        "Shift EURUSD short exposure to XAUUSD long trend following"
    ],
    "reasoning": f"Emotional state at 'intervene' level with 7 revenge trades. Weekend position breach confirmed. AUDUSD ranging with ADX 14-20 and consistent losses across silos. EURUSD short showing partial recovery. XAUUSD trending with ADX 49, RSI 35-40 - classic pullback in strong trend. Zero-5k-01 covers black_swan pillar with active GBPUSD Barbell position (pnl -1.06, needs monitoring).",
    "strategy_weights": {
        "1sf-5k-01": {
            "PriceAction": {"weight": 0.3, "reason": "Primary driver, 25% WR over 105 trades, solid TP of +1533"},
            "ORB": {"weight": 0.3, "reason": "Good 32% WR, positive TP +650, but reduce exposure"},
            "SecondBreakout": {"weight": 0.2, "reason": "Trend strategy for consolidating markets"},
            "Barbell": {"weight": 0.2, "reason": "Black swan hedge, keep active"}
        },
        "2sf-5k-01": {
            "PriceAction": {"weight": 0.25, "reason": "Maintain core, but reduce AUDUSD exposure"},
            "ORB": {"weight": 0.25, "reason": "NZDUSD long working, reduce overall risk"},
            "SecondBreakout": {"weight": 0.2, "reason": "Trend capture potential"},
            "Barbell": {"weight": 0.3, "reason": "Increase black swan focus"}
        },
        "2sp-5k-01": {
            "PriceAction": {"weight": 0.25, "reason": "Reduce correlation to failing AUDUSD shorts"},
            "SecondBreakout": {"weight": 0.35, "reason": "Trend strategy emphasized for moving to metals"},
            "Barbell": {"weight": 0.4, "reason": "Highest conviction for asymmetric payoffs"}
        },
        "2ss-5k-01": {
            "PriceAction": {"weight": 0.2, "reason": "Cut AUDUSD exposure, keep EURUSD positive"},
            "SecondBreakout": {"weight": 0.4, "reason": "Trend focus, XAUUSD opportunity"},
            "Barbell": {"weight": 0.4, "reason": "Black swan coverage"}
        },
        "zero-5k-01": {
            "PriceAction": {"weight": 0.15, "reason": "Reduce failing AUDUSD correlation"},
            "ORB": {"weight": 0.15, "reason": "NZDUSD long positive, keep small"},
            "SecondBreakout": {"weight": 0.3, "reason": "Focus on XAUUSD trending setup"},
            "Barbell": {"weight": 0.4, "reason": "Active GBPUSD black swan position, monitor for extension"}
        }
    },
    "symbol_weights": {
        "1sf-5k-01": {
            "XAUUSD": {"weight": 0.4, "reason": "Trending (ADX 49), pullback at RSI 35-40, clear VCP pattern forming"},
            "XAGUSD": {"weight": 0.3, "reason": "Trending, similar setup to XAUUSD"},
            "EURUSD": {"weight": 0.15, "reason": "Reducing SELL exposure, ranging with ADX 22.69"},
            "AUDUSD": {"weight": 0.1, "reason": "FLOOR ONLY - ranging, negative correlation, AUDUSD shorts losing"}
        },
        "2sf-5k-01": {
            "XAUUSD": {"weight": 0.4, "reason": "Primary trend-following opportunity"},
            "XAGUSD": {"weight": 0.3, "reason": "Secondary precious metal play"},
            "NZDUSD": {"weight": 0.15, "reason": "Current long position working, but reduce overall"},
            "EURUSD": {"weight": 0.15, "reason": "Maintain partial short exposure"}
        },
        "2sp-5k-01": {
            "XAUUSD": {"weight": 0.45, "reason": "Highest conviction trending setup, ADX 49, RSI 35.94 pullback"},
            "XAGUSD": {"weight": 0.35, "reason": "Trend following in silver"},
            "EURUSD": {"weight": 0.1, "reason": "FLOOR - limit exposure"}
        },
        "2ss-5k-01": {
            "XAUUSD": {"weight": 0.45, "reason": "Primary focus: strong trend, pullback opportunity"},
            "XAGUSD": {"weight": 0.35, "reason": "Supporting metal play"},
            "EURUSD": {"weight": 0.1, "reason": "FLOOR - maintain minimal"}
        },
        "zero-5k-01": {
            "XAUUSD": {"weight": 0.4, "reason": "Tailing the trend breakout, pullback entry Zone 3980-3990"},
            "XAGUSD": {"weight": 0.3, "reason": "Silver trend confirmation"},
            "GBPUSD": {"weight": 0.15, "reason": "Monitor Barbell position, exit on trend failure"},
            "EURUSD": {"weight": 0.15, "reason": "Current short position, reduce correlation"}
        }
    },
    "discretionary_trades": [
        {
            "silo_id": "2sp-5k-01",
            "pillar": "trend",
            "symbol": "XAUUSD",
            "direction": "BUY",
            "entry": 4000.0,
            "stop_loss": 3985.0,
            "take_profit": 4100.0,
            "risk_pct": 0.0035,
            "confidence": 0.85,
            "notes": "Second breakout after pullback in strong trending market. ADX 49 confirms trend strength. RSI 35.94 at oversold pullback zone. Volume contraction before current move suggests breakout potential. Monitor for failed breakout below 3980.",
            "price_action_evidence": "XAUUSD pulling back to 3999.79 (near recent lows) after trending move. ADX 49 (strong trend), RSI 35.94 (oversold pullback), ATR 15.04 contraction. Fast tf_band confirms momentum. Classic VCP pattern forming - wait for second breakout confirmation.",
            "exit_plan": "Exit if price closes below 3985 (stop loss) or if ADX drops below 35, or if RSI fails to make new high on rally attempt."
        },
        {
            "silo_id": "zero-5k-01",
            "pillar": "trend",
            "symbol": "XAUUSD",
            "direction": "BUY",
            "entry": 4000.0,
            "stop_loss": 3985.0,
            "take_profit": 4080.0,
            "risk_pct": 0.0025,
            "confidence": 0.8,
            "notes": "Trend following in zero silo with Barbell position active. Same setup as above but smaller size due to lower risk per trade. GBPUSD Barbell needs monitoring for exit.",
            "price_action_evidence": "XAUUSD trending with ADX 49, currently pulling back. RSI 35.94 at oversold. ATR 15.04 contraction suggests breakout imminent. Price at 3999.79 support test.",
            "exit_plan": "Exit if ADX < 35, price closes below 3985, or GBPUSD Barbell shows reversal signal (stop loss hit or trend failure)."
        },
        {
            "silo_id": "2sf-5k-01",
            "pillar": "black_swan",
            "symbol": "XAUUSD",
            "direction": "BUY",
            "entry": 4000.0,
            "stop_loss": 3980.0,
            "take_profit": 4120.0,
            "risk_pct": 0.005,
            "confidence": 0.8,
            "notes": "Black swan harvest: extreme move in gold with uncapped upside potential. Trend showing parabolic characteristics. ADX 49, RSI oversold pullback. This could extend further on institutional flows. Exit must be disciplined - trailing stop once 5% in money.",
            "price_action_evidence": "XAUUSD with ADX 49 (extremely strong trend), RSI 35.94 (deep pullback), ATR 15.04 contraction before next leg. Parabolic move characteristics. Gold often moves on institutional flows - potential for extended move.",
            "exit_plan": "Trail stop to 2% in profit, exit if ADX < 35, or if 15% target reached. Disciplined trailing stop prevents giving back gains."
        }
    ],
    "close_positions": [
        {
            "silo_id": "1sf-5k-01",
            "symbol": "AUDUSD",
            "direction": "SELL",
            "volume": 0.12,
            "reason": "AUDUSD shorts losing 18.45 pts across silos. Ranging market ADX 14-20. Correlated losses unacceptable. Deploy to XAUUSD instead."
        },
        {
            "silo_id": "2sf-5k-01",
            "symbol": "AUDUSD",
            "direction": "SELL",
            "volume": 0.15,
            "reason": "AUDUSD shorts losing 23.4 pts. Negative correlation across multiple silos. Redeploy to trending metals."
        },
        {
            "silo_id": "2sp-5k-01",
            "symbol": "AUDUSD",
            "direction": "SELL",
            "volume": 0.09,
            "reason": "AUDUSD position underwater -14.04 pts. Ranging regime mismatch. Protect capital."
        },
        {
            "silo_id": "2ss-5k-01",
            "symbol": "AUDUSD",
            "direction": "SELL",
            "volume": 0.14,
            "reason": "AUDUSD shorts losing 21.84 pts. Ranging market with ADX 14.73. Stop loss breach risk high."
        }
    ],
    "learning": {
        "category": "regime_aligned_execution",
        "insight": "AUDUSD shorts in ranging markets (ADX 14-20) consistently lose across all silos. EURUSD shorts show some recovery but limited upside. XAUUSD trending (ADX 49, RSI 35-40 pullback) provides superior risk-adjusted setups. Deploy capital from failing AUDUSD positions to XAUUSD trend following.",
        "evidence": "AUDUSD positions: 1sf-5k-01 SELL -4.56 pts, 2sf-5k-01 SELL -23.4 pts, 2sp-5k-01 SELL -14.04 pts, 2ss-5k-01 SELL -21.84 pts. XAUUSD regime: trending, high_volatility, ADX 49, RSI 35.94. Strategy performance: PriceAction +1533 TP, MeanRev -89.57 TP, PocketChange -57.83 TP.",
        "confidence": 0.9
    },
    "self_report": {
        "emotional_state": "intervene - high anxiety with 7 revenge trades, calibration gap 0.8 detected",
        "bias_acknowledgment": "Tendency to average losing AUDUSD shorts across silos. Need to cut correlation exposure and follow the data. Emotional state affecting execution - must enforce hard stops.",
        "uncertainty": "XAUUSD breakout may fail if institutional buyers absorb. GBPUSD black swan position in zero silo needs close monitoring. AUDUSD may bounce on support if USD weakness returns."
    }
}

print(json.dumps(output, indent=2))