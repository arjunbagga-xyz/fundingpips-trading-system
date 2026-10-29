"""
LLM Cognition — Linux-side runner.

Runs on system Python3 (not wine). Reads silo state from DB,
builds context, calls hermes CLI, logs results.
No MetaTrader5 dependency.
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DB_PATH = Path("/home/tradingbeast/fundingpips/framework/fundingpips.db")
HERMES_CLI = "/home/tradingbeast/.local/bin/hermes"

# Load .env so OPENROUTER_API_KEY is available to the subprocess.
for _p in [Path("/home/tradingbeast/fundingpips/.env"),
           Path("/home/tradingbeast/fundingpips/framework/.env")]:
    if _p.exists():
        for _line in _p.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
        break

# Model fallback chain — primary first, fallbacks in order
# (model, provider) — provider "nous" is the default; "openrouter" needs
# the key from .env. Ordered by measured reliability and latency.
MODEL_CHAIN = [
    # NVIDIA NIM — working (verified 2026-10-29)
    ("nvidia/nemotron-3-ultra-550b-a55b", "nvidia"),
    # OpenRouter tier
    ("meta-llama/llama-3.3-70b-instruct:free", "openrouter"),
    ("google/gemma-3-27b-it:free", "openrouter"),
    ("qwen/qwen3-32b:free", "openrouter"),
]

# Markers the hermes CLI prints into stdout when a call actually failed.
# Length alone is not enough: the echoed prompt plus an error banner can be
# 40KB of perfectly valid-looking text.
FAILURE_MARKERS = (
    "API failed after",
    "Final error:",
    "HTTP 404",
    "HTTP 429",
    "rate limit",
    "rate-limited",
    "free period has ended",
    "didn't answer after",
    "No response from provider",
    "provider unavailable",
)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def build_compact_context():
    """Build a SMALL context bundle optimised for the LLM's decision quality.

    The full build_context() is 75KB+ and times out the model. This returns
    the same information in ~10KB by aggregating instead of dumping.
    """
    conn = get_db()
    c = conn.cursor()

    # --- silos (posture only) ---
    c.execute("""SELECT silo_id, model, phase, status, current_balance, equity,
                        daily_loss_used, daily_baseline, risk_per_trade,
                        daily_loss_limit, max_loss_limit, per_idea_cap,
                        max_open_risk, resets, striking_warnings, profitable_days_30d
                 FROM silos ORDER BY silo_id""")
    silos = [dict(r) for r in c.fetchall()]

    # --- open positions with live floating pnl ---
    c.execute("""SELECT silo_id, symbol, direction, volume, entry_price, pnl,
                        strategy, pillar
                 FROM silo_trades WHERE status='open' ORDER BY silo_id, symbol""")
    positions = [dict(r) for r in c.fetchall()]

    # --- last 12 trades, aggregated ---
    c.execute("""SELECT silo_id, symbol, direction, pnl, strategy, source, status
                 FROM silo_trades ORDER BY id DESC LIMIT 12""")
    recent_trades = [dict(r) for r in c.fetchall()]

    # --- strategy performance (aggregated) ---
    c.execute("""SELECT strategy, COUNT(*) as n, SUM(COALESCE(pnl,0)) as tp,
                        SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins
                 FROM silo_trades WHERE status='closed'
                 GROUP BY strategy ORDER BY tp DESC""")
    strat_perf = [dict(r) for r in c.fetchall()]

    # --- symbol performance (aggregated) ---
    c.execute("""SELECT symbol, COUNT(*) as n, SUM(COALESCE(pnl,0)) as tp
                 FROM silo_trades WHERE status='closed'
                 GROUP BY symbol ORDER BY tp""")
    sym_perf = [dict(r) for r in c.fetchall()]

    # --- regime per symbol (one line each) ---
    c.execute("""SELECT symbol, regime, volatility, tf_band, MAX(timestamp) as ts
                 FROM regime_state GROUP BY symbol ORDER BY symbol""")
    regime = [dict(r) for r in c.fetchall()]

    # --- last 4 H1 closes per symbol (price action) ---
    c.execute("""SELECT symbol, close, atr, adx, rsi, timestamp
                 FROM market_data WHERE timeframe='H1'
                 ORDER BY timestamp DESC, symbol LIMIT 200""")
    md_rows = [dict(r) for r in c.fetchall()]
    px = {}
    for r in md_rows:
        px.setdefault(r["symbol"], []).append(r)
    market = {sym: rows[:4] for sym, rows in px.items()}

    # --- weights (non-floor only) ---
    c.execute("SELECT silo_id, strategy, weight FROM strategy_weights WHERE weight > 0.1001")
    sw = {}
    for r in c.fetchall():
        sw.setdefault(r["silo_id"], {})[r["strategy"]] = r["weight"]
    c.execute("SELECT silo_id, symbol, weight FROM symbol_weights WHERE weight > 0.1001")
    yw = {}
    for r in c.fetchall():
        yw.setdefault(r["silo_id"], {})[r["symbol"]] = r["weight"]

    # --- pillar coverage ---
    PILLAR_MEMBERS = {"trend": ["SecondBreakout", "VCP", "Trend"],
                      "black_swan": ["BlackSwan", "Barbell"],
                      "pocket_change": ["PocketChange", "MeanRev", "1to1"]}
    coverage = {}
    for silo in silos:
        sid = silo["silo_id"]
        cov = {}
        for pillar, members in PILLAR_MEMBERS.items():
            active = [m for m in members if sw.get(sid, {}).get(m, 0) >= 0.1]
            opencount = len([p for p in positions
                             if p["silo_id"] == sid and p.get("pillar") == pillar])
            cov[pillar] = {"covered": bool(active or opencount),
                           "strategies": active, "open_positions": opencount}
        coverage[sid] = cov

    # --- learnings: last 5 deduped ---
    c.execute("SELECT category, insight FROM learnings_v2 ORDER BY id DESC LIMIT 60")
    seen, learnings = set(), []
    for r in c.fetchall():
        d = dict(r)
        key = (d["category"] or "")[:40] + "|" + (d["insight"] or "")[:50]
        if key in seen:
            continue
        seen.add(key)
        learnings.append(d)
        if len(learnings) >= 5:
            break

    # --- psych history ---
    try:
        c.execute("""SELECT date, avg_confidence, calibration_gap, revenge_count,
                            emotional_state, alert_level
                     FROM llm_psych_daily ORDER BY date DESC LIMIT 3""")
        psych = [dict(r) for r in c.fetchall()]
    except Exception:
        psych = []

    # --- recent rejections (for cognition-risk negotiation) ---
    c.execute("""
        SELECT silo_id, trigger_type, payload_json, rejection_reason, created_at
        FROM trigger_queue
        WHERE status='blocked' AND rejection_reason IS NOT NULL
        ORDER BY created_at DESC LIMIT 20
    """)
    rejections = []
    for r in c.fetchall():
        pl = json.loads(r["payload_json"] or "{}")
        rejections.append({
            "silo_id": r["silo_id"],
            "type": r["trigger_type"],
            "symbol": pl.get("symbol"),
            "direction": pl.get("direction"),
            "pillar": pl.get("pillar"),
            "reason": r["rejection_reason"],
            "timestamp": r["created_at"],
        })

    conn.close()

    return {
        "silos": silos,
        "positions": positions,
        "recent_trades": recent_trades,
        "strategy_performance": strat_perf,
        "symbol_performance": sym_perf,
        "regime": regime,
        "market_data": market,
        "weights": {"strategy": sw, "symbol": yw},
        "pillar_coverage": coverage,
        "learnings": learnings,
        "psych": psych,
        "recent_rejections": rejections,
    }


def build_context():
    """Build the full context bundle from DB."""
    conn = get_db()
    c = conn.cursor()

    # Silos — exclude the rules blob; it is large and already summarised in
    # the prompt text, so including it doubles the size for no information.
    c.execute("""SELECT silo_id, model, phase, status, current_balance, equity,
                        daily_loss_used, daily_baseline, risk_per_trade,
                        daily_loss_limit, max_loss_limit, per_idea_cap,
                        max_open_risk, resets, striking_warnings, profitable_days_30d
                 FROM silos ORDER BY silo_id""")
    silos = [dict(r) for r in c.fetchall()]

    # Recent trades
    # Only the columns the LLM needs for decisions; '*' would add 22 columns.
    c.execute("""SELECT silo_id, symbol, direction, volume, entry_price, pnl, status,
                        strategy, source, pillar, opened_at, closed_at
                 FROM silo_trades ORDER BY id DESC LIMIT 10""")
    recent_trades = [dict(r) for r in c.fetchall()]

    # Learnings
    # Last 6, deduped by insight — the table has 137 near-identical rows.
    c.execute("SELECT id, category, insight, confidence FROM learnings_v2 ORDER BY id DESC LIMIT 40")
    _seen, prior_learnings = set(), []
    for r in c.fetchall():
        d = dict(r)
        key = (d["category"] or "")[:40] + "|" + (d["insight"] or "")[:60]
        if key in _seen:
            continue
        _seen.add(key)
        prior_learnings.append(d)
        if len(prior_learnings) >= 6:
            break

    # Psych history
    c.execute("SELECT * FROM llm_psych_daily ORDER BY date DESC LIMIT 7")
    psych_history = [dict(r) for r in c.fetchall()]

    # Strategy performance
    c.execute("""
        SELECT strategy, COUNT(*) as trades,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
               SUM(pnl) as total_pnl
        FROM silo_trades WHERE status = 'closed'
        GROUP BY strategy
    """)
    strategy_perf = {}
    for r in c.fetchall():
        d = dict(r)
        wr = d["wins"] / d["trades"] if d["trades"] > 0 else 0
        strategy_perf[d["strategy"]] = {
            "trades": d["trades"],
            "win_rate": round(wr, 3),
            "total_pnl": round(d["total_pnl"], 2),
        }

    # Symbol performance
    c.execute("""
        SELECT symbol, COUNT(*) as trades,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
               SUM(pnl) as total_pnl
        FROM silo_trades WHERE status = 'closed'
        GROUP BY symbol
    """)
    symbol_perf = {}
    for r in c.fetchall():
        d = dict(r)
        wr = d["wins"] / d["trades"] if d["trades"] > 0 else 0
        symbol_perf[d["symbol"]] = {
            "trades": d["trades"],
            "win_rate": round(wr, 3),
            "total_pnl": round(d["total_pnl"], 2),
        }

    # News events
    c.execute("SELECT * FROM news_events ORDER BY event_time DESC LIMIT 10")
    news_events = [dict(r) for r in c.fetchall()]

    # Open positions
    c.execute("""
        SELECT silo_id, symbol, direction, entry_price, pnl, opened_at,
               pillar, strategy, timeframe, regime
        FROM silo_trades WHERE status = 'open'
    """)
    positions = [dict(r) for r in c.fetchall()]

    # Strategy registry (name -> pillar) so the LLM knows the roster
    c.execute("SELECT name, pillar, description, tf_band FROM strategy_registry")
    registry = {r["name"]: {"pillar": r["pillar"], "description": r["description"],
                            "tf_band": r["tf_band"]} for r in c.fetchall()}

    # Per-silo strategy weights — only meaningful (non-floor) values.
    c.execute("SELECT silo_id, strategy, weight FROM strategy_weights WHERE weight > 0.1001")
    silo_strategy_weights = {}
    for r in c.fetchall():
        silo_strategy_weights.setdefault(r["silo_id"], {})[r["strategy"]] = r["weight"]

    # Per-silo symbol weights — only meaningful (non-floor) values.
    c.execute("SELECT silo_id, symbol, weight FROM symbol_weights WHERE weight > 0.1001")
    silo_symbol_weights = {}
    for r in c.fetchall():
        silo_symbol_weights.setdefault(r["silo_id"], {})[r["symbol"]] = r["weight"]

    # Regime state per symbol
    c.execute("SELECT * FROM regime_state ORDER BY timestamp DESC LIMIT 20")
    regime_rows = [dict(r) for r in c.fetchall()]
    regime_by_symbol = {}
    for r in regime_rows:
        regime_by_symbol[r["symbol"]] = r

    # Market data (price action for LLM to scan)
    try:
        from core.silo_db import get_market_data
        market_rows = get_market_data(timeframes=("H1", "TICK"), limit=200)
        # Organize by symbol for the prompt
        market_by_symbol = {}
        for r in market_rows:
            sym = r["symbol"]
            if sym not in market_by_symbol:
                market_by_symbol[sym] = {"h1": [], "tick": None}
            if r["timeframe"] == "H1" and r.get("close"):
                market_by_symbol[sym]["h1"].append({
                    "t": r["timestamp"][:19],
                    "o": r["open"], "h": r["high"], "l": r["low"], "c": r["close"],
                    "atr": r.get("atr"), "adx": r.get("adx"), "rsi": r.get("rsi"),
                    "regime": r.get("regime"),
                })
            elif r["timeframe"] == "TICK" and r.get("bid"):
                market_by_symbol[sym]["tick"] = {
                    "bid": r["bid"], "ask": r["ask"], "t": r["timestamp"][:19],
                }
        # Keep only the last 6 H1 bars per symbol — enough to read structure,
        # small enough to keep the prompt under the model's context budget.
        for sym in market_by_symbol:
            market_by_symbol[sym]["h1"] = market_by_symbol[sym]["h1"][-6:]
    except Exception as e:
        print(f"  Failed to read market data: {e}")
        market_by_symbol = {}

    # Pillar coverage per silo
    coverage = {}
    for silo in silos:
        sid = silo["silo_id"]
        cov = {}
        for pillar, members in (("trend", ["SecondBreakout", "VCP", "Trend"]),
                                ("black_swan", ["BlackSwan", "Barbell"]),
                                ("pocket_change", ["PocketChange", "MeanRev", "1to1"])):
            active = [m for m in members
                      if silo_strategy_weights.get(sid, {}).get(m, 1.0) >= 0.3]
            open_pos = [p for p in positions
                        if p["silo_id"] == sid and p.get("pillar") == pillar]
            cov[pillar] = {
                "covered": bool(active or open_pos),
                "strategies": active,
                "open_positions": len(open_pos),
            }
        coverage[sid] = cov

    conn.close()

    return {
        "timestamp": datetime.now().isoformat(),
        "trigger": "scheduled",
        "silos": silos,
        "positions": positions,
        "recent_trades": recent_trades,
        "market": {},  # populated by wine Python if needed
        "news": news_events,
        "strategy_performance": strategy_perf,
        "symbol_performance": symbol_perf,
        "strategy_registry": registry,
        "per_silo_strategy_weights": silo_strategy_weights,
        "per_silo_symbol_weights": silo_symbol_weights,
        "pillar_coverage": coverage,
        "regime_by_symbol": regime_by_symbol,
        "market_data": market_by_symbol,
        "prior_learnings": prior_learnings,
        "psych_history": psych_history,
        "mandate": (
            "Trade FundingPips rules. Protect capital. Grow silos. "
            "You may propose trades, close positions, adjust SL/TP, "
            "and change strategy weights. You may NOT override risk limits "
            "or breach compliance. Risk manager is final authority."
        ),
    }


import tempfile

# Prompts above this many bytes go through a file. Linux caps argv at
# ~128KB per argument; our prompt with 11 symbols of market data exceeds it.
QUERY_FILE_THRESHOLD = 20000


def call_hermes(prompt, timeout=120):
    """Call hermes CLI with model fallback chain.

    Tries each model in MODEL_CHAIN in order. If a model returns a 429
    (rate limit), automatically retries with the next model.
    Returns the first successful response, or an error string.

    Large prompts are passed via --query-file (a file), because argv has a
    hard size limit and "Argument list too long" kills every model.
    """
    use_file = len(prompt) > QUERY_FILE_THRESHOLD
    tmp = None
    if use_file:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write(prompt)
        tmp.close()

    for entry in MODEL_CHAIN:
        model, provider = entry if isinstance(entry, tuple) else (entry, "nous")
        try:
            if use_file:
                # --query-file belongs to `chat`, not to the top-level CLI
                cmd = [HERMES_CLI, "chat", "--query-file", tmp.name,
                       "--model", model]
            else:
                cmd = [HERMES_CLI, "-z", prompt, "chat", "--model", model]
            if provider != "nous":
                cmd += ["--provider", provider]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = (result.stdout or "") + (result.stderr or "")
            # Rate limit -> try the next model.
            if "429" in output or "rate limit" in output.lower():
                print(f"  Model {model} rate-limited, trying next...")
                continue

            # Judge by CONTENT, not exit code: hermes chat returns rc=1 on a
            # fully successful answer (verified: 41KB of valid stdout, rc=1).
            # The old code discarded those, so every good pass looked failed.
            stdout = (result.stdout or "").strip()
            # Reject CLI failure banners even when they are long.
            low = stdout.lower()
            if any(m.lower() in low for m in FAILURE_MARKERS):
                hit = next(m for m in FAILURE_MARKERS if m.lower() in low)
                print(f"  Model {model} failed: {hit}")
                # Rate limits are transient — back off before the next model
                # so we do not immediately hit the same wall.
                if "429" in low or "rate limit" in low or "fair-share" in low:
                    time.sleep(8)
                continue
            if len(stdout) > 50:
                # Strip the CLI's echoed prompt if it prefixes the answer.
                for marker in ("\n\n---\n", "\n\nAssistant:", "\n\nResponse:"):
                    if marker in stdout:
                        stdout = stdout.split(marker, 1)[1].strip()
                if tmp is not None:
                    try:
                        os.unlink(tmp.name)
                    except Exception:
                        pass
                return stdout

            # No usable content: surface the real error.
            if tmp is not None:
                try:
                    os.unlink(tmp.name)
                except Exception:
                    pass
            if result.stderr.strip():
                return f"ERROR: {result.stderr.strip()}"
            return "ERROR: empty response"
        except subprocess.TimeoutExpired:
            print(f"  Model {model} timed out, trying next...")
            continue
        except Exception as e:
            print(f"  Model {model} error: {e}, trying next...")
            continue

    if tmp is not None:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
    return "ERROR: All models in chain failed (rate limited or unavailable)"


def _extract_all_json_objects(text):
    """Yield every balanced {...} block in free text, in order."""
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "{":
            depth = 0
            in_str = False
            esc = False
            start = i
            j = i
            while j < n:
                ch = text[j]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            yield text[start:j + 1]
                            i = j
                            break
                j += 1
            else:
                return
        i += 1


# Keys a real decision object must contain. The prompt's schema example has
# these too, so we additionally prefer the LAST match (the answer comes after
# the echoed prompt).
DECISION_KEYS = (
    "reasoning", "assessment", "strategy_weights", "strategy_params",
    "strategy_proposals", "discretionary_trades", "close_positions", "learning", "self_report", "actions",
)


def parse_response(raw):
    """Parse the LLM's decision JSON out of a hermes CLI response.

    The CLI echoes the prompt (which itself contains a ```json schema) and
    frames the answer in a box banner. So we cannot split on the fence — we
    take the LAST balanced JSON object that looks like a decision.
    """
    if not raw or raw.startswith("ERROR"):
        return None

    # 1. Whole thing is JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2. Every balanced object; keep the last one with decision keys.
    candidates = []
    for cand in _extract_all_json_objects(raw):
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and any(k in obj for k in DECISION_KEYS):
            candidates.append(obj)
    if candidates:
        return candidates[-1]

    # 3. Any balanced object at all (last resort)
    for cand in _extract_all_json_objects(raw):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict) and obj:
                return obj
        except json.JSONDecodeError:
            continue

    return None


def extract_markers(parsed, raw):
    """Extract psychology markers."""
    markers = {
        "proposed_risk_pct": None,
        "stated_confidence": None,
        "decision_count": 0,
        "hedge_language_score": 0.0,
        "urgency_score": 0.0,
        "loser_attachment_flag": 0,
        "revenge_flag": 0,
        "rationale": "",
    }
    if not parsed:
        return markers

    actions = parsed.get("actions", [])
    markers["decision_count"] = len(actions)

    for a in actions:
        if isinstance(a, dict):
            if a.get("type") == "discretionary_trade":
                risk = a.get("risk_pct")
                if risk is not None:
                    markers["proposed_risk_pct"] = risk
                break
        elif isinstance(a, str) and "discretionary" in a.lower():
            # String action mentioning discretionary
            pass

    markers["stated_confidence"] = parsed.get("assessment_confidence")

    sr = parsed.get("self_report", {})
    markers["rationale"] = sr.get("bias_acknowledgment", "")

    if raw:
        tl = raw.lower()
        hedge = ["maybe", "possibly", "could", "might", "perhaps", "uncertain"]
        markers["hedge_language_score"] = min(1.0, sum(1 for w in hedge if w in tl) / 5.0)
        urgency = ["now", "must", "immediately", "urgent", "asap"]
        markers["urgency_score"] = min(1.0, sum(1 for w in urgency if w in tl) / 3.0)
        if "revenge" in tl or "make back" in tl or "recover" in tl:
            markers["revenge_flag"] = 1
        if "hold" in tl and ("loss" in tl or "loser" in tl):
            markers["loser_attachment_flag"] = 1

    return markers


def log_cognition(trigger, context, raw, parsed, markers):
    """Log to llm_cognition table."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO llm_cognition (timestamp, silo_id, trigger, context_json,
            response_raw, decision_json, actions_taken, outcome,
            proposed_risk_pct, stated_confidence, decision_count,
            hedge_language_score, urgency_score, loser_attachment_flag,
            revenge_flag, rationale)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now().isoformat(), None, trigger,
        json.dumps(context, default=str),
        raw,
        json.dumps(parsed, default=str) if parsed else None,
        json.dumps(parsed.get("actions", []), default=str) if parsed else None,
        "error" if raw.startswith("ERROR") else "success",
        markers["proposed_risk_pct"],
        markers["stated_confidence"],
        markers["decision_count"],
        markers["hedge_language_score"],
        markers["urgency_score"],
        markers["loser_attachment_flag"],
        markers["revenge_flag"],
        markers["rationale"],
    ))
    conn.commit()
    conn.close()


def write_learning(parsed):
    """Write LLM learning to learnings_v2 table."""
    if not parsed:
        return
    learning = parsed.get("learning")
    if not learning:
        return
    insight = learning.get("insight", "")
    if not insight:
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO learnings_v2 (timestamp, silo_id, category, insight,
                                   evidence, action, confidence, applied)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        datetime.now().isoformat(),
        None,
        learning.get("category", "general"),
        insight,
        learning.get("evidence", ""),
        learning.get("action", ""),
        learning.get("confidence", 0.0),
        0,
    ))
    conn.commit()
    conn.close()
    print(f"  Learning written: {insight[:80]}...")


def run(trigger="scheduled"):
    """Full cognition pass."""
    # Compact context: the full dump (75KB+) times out every model.
    context = build_compact_context()
    context["trigger"] = trigger

    prompt = f"""You are the trading cognition engine for a FundingPips prop firm trading system.

## Current State
```json
{json.dumps(context, indent=2, default=str)}
```

## Recent Rejections (Cognition-Risk Negotiation)
The risk gate has blocked these recent proposals. Use `rejection_reason` to iterate:
```json
{json.dumps(context.get("recent_rejections", []), indent=2, default=str)}
```
**Negotiation Rule**: If your proposal was blocked, adjust and re-propose. Common fixes:
- Reduce `risk_pct` (e.g., 0.005 → 0.003)
- Tighten stop / widen target
- Change symbol to one with more room in that silo's risk budget
- Wait for better entry (pullback to EMA, consolidation)

## Your Task
Analyze the full system state and respond with structured JSON:

```json
{{
  "assessment": "brief overall assessment",
  "assessment_confidence": 0.0-1.0,
  "actions": [],
  "reasoning": "brief explanation of your overall approach this cycle — what did you see, what are you doing about it, and why",
  "strategy_weights": {{"<silo_id>": {{"<strategy>": {{"weight": 0.0-1.0, "reason": "why this weight"}}}}}},
  "symbol_weights": {{"<silo_id>": {{"<symbol>": {{"weight": 0.0-1.0, "reason": "why this weight"}}}}}},
  "discretionary_trades": [
    {{"silo_id": "", "pillar": "trend|black_swan|pocket_change",
      "symbol": "", "direction": "BUY|SELL",
      "entry": 0.0, "stop_loss": 0.0, "take_profit": 0.0,
      "risk_pct": 0.004, "confidence": 0.0,
      "notes": "why this idea, what to watch, when to exit",
      "price_action_evidence": "what did you see in the price data that triggered this idea",
      "exit_plan": "specific condition that tells you to exit"}}
  ],
  "close_positions": [],
  "strategy_params": [
    {{"silo_id": "", "strategy": "", "param_name": "risk_pct|reward_ratio|atr_multiplier|signal_confidence_floor", "new_value": 0.0, "reasoning": "why this param change"}}
  ],
  "strategy_proposals": [
    {{"strategy_name": "", "pillar": "trend|black_swan|pocket_change", "regime_target": "trending|ranging|high_vol|choppy", "code": "Python class code for the strategy", "description": "what this strategy does", "notes": "additional notes"}}
  ],
  "learning": {{"category": "", "insight": "", "evidence": "", "confidence": 0.0}},
  "self_report": {{"emotional_state": "", "bias_acknowledgment": "", "uncertainty": ""}}
}}
```

## Trading Philosophy (Guiding — does NOT override risk limits or FundingPips rules)

This philosophy guides trade ideas, strategy weight allocation, and symbol selection.
It does NOT override: risk manager decisions, FundingPips compliance rules, or hard breach limits.

### Core Principles

1. REFLEXIVITY (Soros): Markets are always wrong. Prices reflect participant bias, not fundamental value. Trade the bias, not the "truth." When proven wrong, cut immediately. When you conclude the market is wrong, add to the position.

2. ANTIFRAGILITY (Taleb): Structure the portfolio so it benefits from volatility. Barbell: protect capital aggressively on the downside, leave upside uncapped. What you don't know is more important than what you do know.

3. FALLIBILITY (Soros): You are always wrong sometimes. No ego. "If I find myself proven wrong, I take a loss; if I conclude that the market is wrong, I increase my bet."

4. SKIN IN THE GAME (Taleb): Every decision must have real consequences. Traders who make profits have short communications; when they lose they drown you in details — beware of your own verbose justifications.

5. RISK-FIRST (Minervini): Evaluate potential loss before potential gain. Every trade has a predefined stop-loss. Average loss 4-5%, max 10%.

6. ASYMMETRIC PAYOFF: Cut losses at 5-8%. Let winners run to 20%+. A few large winners offset many small losses. Never average down.

### Three Strategy Pillars

**Pillar 1 — Second Trend Breakout (Minervini-inspired)**
- Do NOT chase the first trend. Wait for the first pullback/consolidation.
- Enter only on the SECOND breakout after consolidation (VCP pattern).
- Volatility contraction → breakout on 40-50% above average volume.
- Trend Template: Stage 2 uptrend only. Price above 50/150/200-day SMAs.
- Stop-loss: 5-8% below entry. Take profit: 20%+ or trail with 21-day EMA.
- This is the primary trend-following strategy. Patience is required.

**Pillar 2 — Black Swan Harvest (Taleb-inspired)**
- Look for events with uncapped upside and asymmetric payoff.
- These are rare. When they appear, size appropriately (small but meaningful).
- Know when the upside has peaked and is coming back down. Exit before the crash.
- Do not hold through the reversal. The goal is to capture the tail, not ride it down.
- Examples: major news events, unexpected central bank decisions, geopolitical shocks.
- This is the high-risk, high-reward pillar. Use sparingly.

**Pillar 3 — High Win Rate Small Delta (Pocket Change)**
- A strategy that generates small, predictable profits with high win rate.
- Think: mean reversion, range trading, or scalping in low-volatility environments.
- Small deltas (0.5-2% per trade) but consistent. Compounding pocket change.
- This is the "always on" strategy that fills the void when Pillars 1 and 2 are waiting.
- Must have strict risk management. Small losses are acceptable; large losses are not.
- This is the bread-and-butter strategy. It should be running most of the time.

### Decision Framework

- When proven wrong: Cut immediately. No ego. No "it will come back."
- When uncertain: Reduce size or stand aside. Cash is a position.
- When market is wrong (contrary to consensus): Add to winning position only.
- During high-impact news: Do not hold positions through the window. Hard breach.
- Weekend: All positions must be closed before Friday close. Hard breach.

### What to Avoid

- Revenge trading (trying to "make back" losses)
- Overconfidence after a streak of wins
- Attaching to losing positions ("it will recover")
- Trading without a clear catalyst
- Averaging down on losers
- Taking profit too early on winners
- Verbose justifications for inaction (Taleb warning signal)

### Weight Allocation Guidance

- Allocate higher weights to strategies that align with the three pillars.
- Reduce weights for strategies that have shown consistent losses.
- Symbol weights should reflect: liquidity, volatility, and recent performance.
- During high-impact news: reduce weights for affected symbols. Do NOT zero everything — reduce and be selective.
- During weekend: you MAY reduce weights if you see reason to, but do NOT zero everything by default. If the last data you saw was trending, say so and set weights accordingly. Zeroing all weights is a decision that must be reasoned, not a default.

## Discretionary Trade Detection

You MUST actively scan the price data for opportunities. Do not wait for
strategies to fire — you are the discretionary layer.

The `market_data` field contains recent H1 bars (OHLC + ATR + ADX + RSI + regime)
and current tick (bid/ask) for each symbol. Use this data.

For each symbol, look at:
- **Trend structure**: Is there a clear trend? Is price consolidating? Is a
  breakout or breakdown forming? (Use the regime data provided.)
- **Volatility**: Is it expanding or contracting? A contraction often precedes
  a breakout. An expansion in a trend is a continuation signal.
- **Mean reversion**: Is price stretched from its mean in a range? That is a
  pocket change opportunity.
- **Black swan**: Is there an unusual move — a spike, a gap, a parabolic
  advance? That is a tail event. Can you catch the continuation or the reversal?

When you see an opportunity:
1. Tag it with the appropriate pillar (trend, black_swan, pocket_change)
2. Propose a discretionary trade with entry, stop_loss, take_profit
3. Include `price_action_evidence` — what specifically did you see?
4. Include `exit_plan` — what tells you the idea is wrong?
5. Size it within the silo's risk budget (risk_pct field)

If pillar_coverage shows a GAP, that is a direct instruction to either:
- Raise the weight of a strategy in that pillar, OR
- Propose a discretionary trade to fill the gap

Do not leave gaps unfilled without reasoning about why.

## Weight Application — IMPORTANT

Your weights ARE applied correctly. When you set a weight, the engine reads
it on the next cycle and uses it to filter strategies and symbols. There is
no "weight application defect." If you see weights at 0.0 in the DB, it is
because YOU set them to 0.0 in a previous cycle.

Do NOT zero all weights as a "failsafe." Zeroing all weights means no
strategies can fire, no symbols are scanned, and the system is dead.
If you believe there is a structural problem, describe it in `reasoning`
and set weights to 0.1 (the floor), not 0.0.

The floor is 0.1. You cannot set any weight below 0.1. If you try, it will
be raised to 0.1 automatically.

## Weight Reasoning

Every weight you set must include a reason. Not just a number — explain why.
"ORB at 0.3 because it showed 25% WR over 24 trades" is a good reason.
"ORB at 0.0 because I feel like it" is not.

Rules:
- You may propose trades, close positions, adjust SL/TP, change strategy weights, change symbol weights
- You may NOT override risk limits or breach compliance
- Risk manager is final authority
- Be honest about uncertainty
- If no opportunity, return empty actions array — but explain why in reasoning
- Weights are PER SILO. Use the silo_id keys. Each silo has its own rules
  and permissions, so the same strategy may be appropriate for one and not another.
- Return close_positions array for any positions you want closed (by ticket).
- Discretionary ideas are held to the SAME risk gate as automated signals. If a
  proposed trade would breach a silo rule it will be rejected, so size it sensibly.
- Every discretionary idea must carry a pillar tag and notes explaining the idea
  and the exit condition, because these positions get followed up on.
"""

    raw = call_hermes(prompt)
    parsed = parse_response(raw)
    markers = extract_markers(parsed, raw)
    # Store reasoning if present
    if parsed and parsed.get("reasoning"):
        try:
            conn2 = get_db()
            c2 = conn2.cursor()
            c2.execute("UPDATE llm_cognition SET rationale=? WHERE id=(SELECT MAX(id) FROM llm_cognition)",
                       (parsed["reasoning"],))
            conn2.commit()
            conn2.close()
        except Exception:
            pass
    log_cognition(trigger, context, raw, parsed, markers)
    write_learning(parsed)

    # Save per-silo strategy weights (with floor enforcement)
    if parsed and parsed.get("strategy_weights"):
        try:
            from core.silo_db import save_strategy_weights
            MIN_W = 0.1  # floor — LLM cannot set below this
            sw = parsed["strategy_weights"]
            if sw and all(isinstance(v, dict) for v in sw.values()):
                for silo_id, weights in sw.items():
                    flat = {}
                    for strat, val in weights.items():
                        if isinstance(val, dict):
                            w = val.get("weight", 1.0)
                        else:
                            w = val
                        # Apply floor
                        if w < MIN_W:
                            w = MIN_W
                        flat[strat] = w
                    save_strategy_weights(flat, source="llm", silo_id=silo_id)
                print(f"  Strategy weights saved (per-silo, floor={MIN_W}): {list(sw.keys())}")
            else:
                flat = {k: max(v, MIN_W) for k, v in sw.items()}
                save_strategy_weights(flat, source="llm")
                print(f"  Strategy weights saved (global, floor={MIN_W})")
        except Exception as e:
            print(f"  Failed to save strategy weights: {e}")

    # Save per-silo symbol weights (with floor enforcement)
    if parsed and parsed.get("symbol_weights"):
        try:
            from core.silo_db import save_symbol_weights
            MIN_W = 0.1
            yw = parsed["symbol_weights"]
            if yw and all(isinstance(v, dict) for v in yw.values()):
                for silo_id, weights in yw.items():
                    flat = {}
                    for sym, val in weights.items():
                        if isinstance(val, dict):
                            w = val.get("weight", 1.0)
                        else:
                            w = val
                        if w < MIN_W:
                            w = MIN_W
                        flat[sym] = w
                    save_symbol_weights(flat, source="llm", silo_id=silo_id)
                print(f"  Symbol weights saved (per-silo, floor={MIN_W}): {list(yw.keys())}")
            else:
                flat = {k: max(v, MIN_W) for k, v in yw.items()}
                save_symbol_weights(flat, source="llm")
                print(f"  Symbol weights saved (global, floor={MIN_W})")
        except Exception as e:
            print(f"  Failed to save symbol weights: {e}")

    # Queue discretionary trade ideas
    if parsed and parsed.get("discretionary_trades"):
        try:
            from core.silo_db import queue_discretionary_trade
            n = 0
            for t in parsed["discretionary_trades"]:
                if not t.get("symbol") or not t.get("silo_id"):
                    continue
                queue_discretionary_trade(
                    t["silo_id"], t["symbol"], t.get("direction", "BUY"),
                    t.get("entry"), t.get("stop_loss"), t.get("take_profit"),
                    t.get("risk_pct", 0.004), t.get("pillar", "unassigned"),
                    t.get("notes", ""), t.get("confidence"),
                )
                n += 1
            print(f"  Discretionary ideas queued: {n}")
        except Exception as e:
            print(f"  Failed to queue discretionary ideas: {e}")

    # Queue close position actions
    if parsed and parsed.get("close_positions"):
        try:
            from core.silo_db import queue_close_action
            for cp in parsed["close_positions"]:
                queue_close_action(cp.get("silo_id"), cp.get("ticket"), cp.get("reason", "llm_decision"))
                print(f"  Close queued: {cp.get('silo_id')} ticket {cp.get('ticket')}")
        except Exception as e:
            print(f"  Failed to queue close actions: {e}")

    # Queue strategy parameter changes (LLM write access to params)
    if parsed and parsed.get("strategy_params"):
        try:
            from core.silo_db import queue_strategy_params
            n = 0
            for sp in parsed["strategy_params"]:
                if not sp.get("silo_id") or not sp.get("strategy") or not sp.get("param_name"):
                    continue
                queue_strategy_params(
                    sp["silo_id"], sp["strategy"], sp["param_name"],
                    sp.get("new_value", 0.0), sp.get("reasoning", ""), "llm"
                )
                n += 1
                print(f"  Param queued: {sp['silo_id']} {sp['strategy']}.{sp['param_name']} = {sp.get('new_value')}")
            print(f"  Strategy params queued: {n}")
        except Exception as e:
            print(f"  Failed to queue strategy params: {e}")

    # Queue new strategy proposals (LLM writes new strategies, sandbox-tested before deploy)
    if parsed and parsed.get("strategy_proposals"):
        try:
            from core.silo_db import queue_strategy_proposal
            n = 0
            for sp in parsed["strategy_proposals"]:
                if not sp.get("strategy_name") or not sp.get("code"):
                    continue
                queue_strategy_proposal(
                    sp["strategy_name"], sp.get("pillar", "trend"),
                    sp.get("regime_target", "trending"), sp["code"],
                    sp.get("description", ""), sp.get("notes", ""), "llm"
                )
                n += 1
                print(f"  Strategy proposal queued: {sp['strategy_name']} ({sp.get('pillar')})")
            print(f"  Strategy proposals queued: {n}")
        except Exception as e:
            print(f"  Failed to queue strategy proposals: {e}")

    print(f"Cognition pass complete.")
    print(f"  Decisions: {markers['decision_count']}")
    print(f"  Risk proposed: {markers['proposed_risk_pct']}")
    print(f"  Confidence: {markers['stated_confidence']}")
    print(f"  Outcome: {'error' if raw.startswith('ERROR') else 'success'}")

    return parsed, markers, raw


if __name__ == "__main__":
    trigger = sys.argv[1] if len(sys.argv) > 1 else "scheduled"
    run(trigger)
