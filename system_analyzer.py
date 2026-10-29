#!/usr/bin/env python3
"""Trading system state analyzer - generates structured JSON response"""

import json
from datetime import datetime

def analyze_state():
    state = json.load(open('/home/tradingbeast/fundingpips/framework/system_state.json'))

    # Extract key data
    silos = state['silos']
    positions = state['positions']
    regime = {r['symbol']: r for r in state['regime']}
    market_data = {m['symbol']: m[-1] for m in state['market_data'].values()}

    # Identify negative-edge positions
    negative_edge_positions = []
    for p in positions:
        if p['symbol'] in ['XAGUSD'] or p['strategy'] == 'PriceAction':
            negative_edge_positions.append(p)

    # Identify trend opportunities in silos with position cap
    trend_opportunities = []
    for symbol, r in regime.items():
        if r['regime'] == 'trending' and r['adx'] > 30:
            trend_opportunities.append((symbol, r))

    # Calculate silo position counts
    silo_pos_count = {}
    for p in positions:
        sid = p['silo_id']
        silo_pos_count[sid] = silo_pos_count.get(sid, 0) + 1

    # Check for floor breaches
    floor_breached = []
    for s in silos:
        floor_threshold = s['current_balance'] * (1 - s['max_loss_limit'])
        if s['current_balance'] < floor_threshold * 1.2:  # Close to or below floor
            floor_breached.append(s['silo_id'])

    return {
        'silos': silos,
        'positions': positions,
        'regime': regime,
        'market_data': market_data,
        'negative_edge_positions': negative_edge_positions,
        'trend_opportunities': trend_opportunities,
        'silo_pos_count': silo_pos_count,
        'floor_breached': floor_breached,
        'strategy_performance': state['strategy_performance'],
        'rejection_reasons': set(r['reason'] for r in state['recent_rejections'])
    }

if __name__ == '__main__':
    result = analyze_state()
    print(json.dumps(result, indent=2, default=str))