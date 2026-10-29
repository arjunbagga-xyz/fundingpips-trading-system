"""
Silo definitions — unified single-account FundingPips system.

One account, all rules. Zero's rule set is the superset (tightest on every
dimension). Non-Zero rules that still apply (news window, weekend, lot size)
are included at their tightest values. Striking System, Risk Per Trade Idea,
and Profit Concentration are KEPT because Zero still enforces them.

Starting balance: $5,000 (fresh demo account).
"""

# ---------------------------------------------------------------------------
# FundingPips unified rule set — tightest of all models, all rules included
# ---------------------------------------------------------------------------

MODEL_RULES = {
    "Unified": {
        "phases": ["Phase 1", "Phase 2", "Master"],
        "phase_targets": {"Phase 1": 0.12, "Phase 2": 0.08, "Master": None},
        "daily_loss_limit": 0.02,          # tightest daily (1-Step Flex 2%)
        "max_loss_limit": 0.05,             # Zero trailing (tightest max DD)
        "dd_type": "trailing",              # Zero trailing DD
        "trailing_lock_pct": 0.05,          # locks at +5% profit
        "min_trading_days": {"Phase 1": 3, "Phase 2": 3, "Master": 0},  # tightest: 2-Step Std 5% DD
        "per_idea_cap": 0.03,               # Zero: 3% below $50K
        "max_open_risk": 0.01,              # Zero: 1% combined floating
        "consistency_rule": 0.15,           # Zero: biggest day <= 15%
        "profit_concentration": 0.60,       # $25K+ only; n/a at $5K
        "striking_system": 0.01,            # Zero: 1% floating loss per idea
        "news_window_before": 10,           # Zero: 10 min before
        "news_window_after": 10,            # Zero: 10 min after
        "news_exempt_hours": None,           # Zero: no exemption
        "news_prohibited": True,            # Zero: hard breach
        "weekend_holding_master": False,    # non-Zero: auto-close
        "weekend_holding_zero": True,       # Zero: hard breach
        "retention_profitable_days": 7,     # Zero: 7 profitable days per 30d
        "retention_min_day_pct": 0.0025,    # Zero: 0.25% each
        "first_profit_locked": 0.03,        # Zero: first 3% can't be requested
        "reward_cycle_days": 14,              # Zero: bi-weekly reward cycle
        "reward_split": 0.95,                 # Zero: 95% profit split
        "min_reward_pct": 0.01,               # Zero: min 1% of account to request reward
        "swing_trading_addon": False,          # without add-on: auto-close at EOD/weekend
        "lot_size_max": 20,                 # all models: 20 per click
        "lot_size_crypto": 1,                # all models: 1 crypto per click
        "inactivity_days": 30,              # all models: 30 days
        "min_profitable_day_pct": 0.0025,   # Zero: 0.25% per day
    },
}

# ---------------------------------------------------------------------------
# Single unified silo
# ---------------------------------------------------------------------------

SILOS = [
    {
        "silo_id": "unified-5k-01",
        "model": "Unified",
        "starting_balance": 5000.0,
        "risk_per_trade": 0.0025,       # 0.25% — trailing DD + 1% open risk
        "magic_number": 1000,
    },
]

MAX_OPEN_POSITIONS_PER_SILO = 3
MAX_TRADES_PER_DAY_PER_SILO = 5


def get_silo(silo_id):
    for s in SILOS:
        if s["silo_id"] == silo_id:
            return s
    return None


def get_rules(model):
    return MODEL_RULES.get(model)


def silo_config(silo_id):
    silo = get_silo(silo_id)
    if not silo:
        return None
    rules = get_rules(silo["model"])
    return {**silo, **rules}


if __name__ == "__main__":
    for s in SILOS:
        r = get_rules(s["model"])
        print(f"{s['silo_id']:12s} {s['model']:18s} "
              f"risk={s['risk_per_trade']*100:.2f}% "
              f"DD={r['max_loss_limit']*100:.0f}%({r['dd_type']}) "
              f"daily={r['daily_loss_limit']*100:.0f}% "
              f"magic={s['magic_number']}")