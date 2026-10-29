"""
News Filter — integrates with MT5 built-in economic calendar.

FundingPips news rules:
- Evaluation: unrestricted (except Zero: prohibited)
- Master: 5 min before → 5 min after (Zero: 10 min before → 10 min after)
- Only red-folder (high-impact) events count
- Trades opened 5+ hours before are exempt (not on Zero)
- Trade closed during window → entire profit removed
- Zero: any position in window → hard breach
"""

from datetime import datetime, timedelta, timezone

# Platform time = UTC+3
PLATFORM_TZ = timezone(timedelta(hours=3))


def get_mt5_news_events(hours_ahead=24):
    """
    Fetch upcoming high-impact news events from MT5's built-in calendar.
    Returns list of event dicts.
    """
    try:
        import MetaTrader5 as mt5
        # Reuse the caller's live session. Never init or tear down here:
        # closing the shared IPC link makes every later order_send fail
        # with -10004 'No IPC connection'.
        if mt5.terminal_info() is None and not mt5.initialize():
            return []

        now = datetime.now(PLATFORM_TZ)
        end = now + timedelta(hours=hours_ahead)

        # MT5 calendar functions
        from_date = now.strftime("%Y-%m-%d")
        to_date = end.strftime("%Y-%m-%d")

        events = []
        try:
            calendar = mt5.calendar_get(from_date, to_date)
            for event in calendar:
                if event.impact == "High" or event.impact == 2:
                    events.append({
                        "time": event.time,
                        "currency": event.currency,
                        "name": event.name,
                        "impact": "High",
                        "actual": event.actual,
                        "forecast": event.forecast,
                    })
        except Exception:
            pass

        return events
    except Exception:
        return []


def is_in_news_window(event_time, model, phase="Master"):
    """
    Check if current time falls within the restricted news window.

    Returns (in_window, window_start, window_end, exempt)
    """
    now = datetime.now(PLATFORM_TZ)

    # Evaluation phase: unrestricted (except Zero)
    if phase != "Master":
        if model == "Zero":
            # Zero has no eval — always restricted
            pass
        else:
            return False, None, None, False

    # Zero: 10 min before → 10 min after
    if model == "Zero":
        before = 10
        after = 10
        exempt_hours = None
    else:
        # All other models: 5 min before → 5 min after
        before = 5
        after = 5
        exempt_hours = 5

    window_start = event_time - timedelta(minutes=before)
    window_end = event_time + timedelta(minutes=after)

    in_window = window_start <= now <= window_end

    return in_window, window_start, window_end, exempt_hours


def can_trade_now(model, phase="Master"):
    """
    Check if trading is allowed right now (news-wise).
    Returns (allowed, reason).
    """
    events = get_mt5_news_events(hours_ahead=1)

    for event in events:
        in_window, start, end, _ = is_in_news_window(
            event["time"], model, phase
        )
        if in_window:
            return False, f"news window: {event['name']} ({event['currency']})"

    return True, "no news restriction"


def can_close_for_profit(ticket_open_time, event_time, model, phase="Master"):
    """
    Check if closing a trade now would forfeit profit due to news rules.

    A trade closed during the restricted window → entire profit removed.
    Trades opened 5+ hours before are exempt (not on Zero).

    Returns (can_close, reason).
    """
    now = datetime.now(PLATFORM_TZ)

    # Evaluation: unrestricted (except Zero)
    if phase != "Master":
        if model == "Zero":
            pass  # Zero always restricted
        else:
            return True, "eval phase — unrestricted"

    # Check if in window
    in_window, start, end, exempt_hours = is_in_news_window(
        event_time, model, phase
    )

    if not in_window:
        return True, "outside news window"

    # In window — check exemption
    if exempt_hours is not None:
        hours_held = (now - ticket_open_time).total_seconds() / 3600
        if hours_held >= exempt_hours:
            return True, f"exempt (held {hours_held:.1f}h >= {exempt_hours}h)"

    return False, f"closing during news window would forfeit profit"


def get_news_block_minutes(model, phase="Master"):
    """
    Get the number of minutes to block before/after news.
    Returns (before_minutes, after_minutes) or None if unrestricted.
    """
    if phase != "Master":
        if model == "Zero":
            return 10, 10
        return None  # unrestricted

    if model == "Zero":
        return 10, 10
    return 5, 5


if __name__ == "__main__":
    events = get_mt5_news_events(24)
    print(f"Upcoming high-impact events: {len(events)}")
    for e in events[:5]:
        print(f"  {e['currency']} {e['name']} @ {e['time']}")

    for model in ["2-Step Flex", "2-Step Pro", "2-Step Standard",
                   "1-Step Flex", "Zero"]:
        allowed, reason = can_trade_now(model, "Master")
        print(f"  {model}: {'OK' if allowed else 'BLOCKED'} — {reason}")
