#!/usr/bin/env python3
"""
MT5 Health Diagnostic — replaces the dumb watchdog.

Instead of blindly killing MT5 when a connection fails, this script:
1. Attempts MT5 connection with proper timeout
2. Reads MT5 terminal logs for actual errors
3. Checks system resources (memory, disk, wine processes)
4. Reports exactly what's wrong
5. Only restarts MT5 as a last resort, with clear reasoning

Run every 5 minutes via cron. Logs everything.
"""

import os
import sys
import time
import json
# Pure stdlib — no psutil dependency
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

LOG_DIR = Path("/home/tradingbeast/fundingpips/logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "mt5_health.log"

MT5_DIR = Path("/home/tradingbeast/.mt5/drive_c/Program Files/MetaTrader 5")
WINE_PY = "/home/tradingbeast/.mt5/drive_c/users/tradingbeast/AppData/Local/Programs/Python/Python311/python.exe"
FRAMEWORK_DIR = Path("/home/tradingbeast/fundingpips/framework")

# Grace period: don't restart MT5 younger than this (seconds)
GRACE_SECONDS = 180

# Memory threshold: if available RAM below this, MT5 may crash
MIN_FREE_MB = 500


def log(msg):
    """Write to health log with timestamp."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def get_mt5_process():
    """Get MT5 process info if running — pure stdlib via /proc."""
    result = command(['pgrep', '-f', MT5_EXE], timeout=5)
    pid = int(result.stdout.strip().splitlines()[0]) if result.stdout.strip() else None
    if not pid:
        return None
    try:
        age = int(time.time() - os.stat(f'/proc/{pid}').st_ctime)
    except (FileNotFoundError, ProcessLookupError):
        return None
    rss = 0
    try:
        for line in Path(f'/proc/{pid}/status').read_text(errors='ignore').splitlines():
            if line.startswith('VmRSS:'):
                rss = int(line.split()[1]) // 1024
                break
    except (FileNotFoundError, ProcessLookupError):
        pass
    return {'pid': pid, 'age': age, 'rss_mb': rss}


def get_system_health():
    """Check system resources — pure stdlib via /proc/meminfo and df."""
    meminfo = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            meminfo[key.strip()] = int(value.strip().split()[0]) // 1024
    total = meminfo.get('MemTotal', 0)
    available = meminfo.get('MemAvailable', 0)
    used = total - available
    swap_used = max(0, meminfo.get('SwapTotal', 0) - meminfo.get('SwapFree', 0))
    disk = command(['df', '-m', '/home'], timeout=5).stdout.splitlines()
    free_gb, disk_pct = 0, 0
    if len(disk) >= 2:
        parts = disk[1].split()
        free_gb = int(parts[3]) // 1024
        disk_pct = int(parts[4].rstrip('%'))
    return {
        "memory_total_mb": total,
        "memory_used_mb": used,
        "memory_available_mb": available,
        "memory_percent": round(used / total * 100, 1) if total > 0 else 0,
        "swap_used_mb": swap_used,
        "disk_free_gb": free_gb,
        "disk_percent": disk_pct,
    }


def get_mt5_terminal_log():
    """Read the latest MT5 terminal log for errors."""
    log_dirs = [
        Path("/home/tradingbeast/.mt5/drive_c/users/tradingbeast/AppData/Roaming/MetaQuotes/Terminal"),
    ]
    errors = []
    for base in log_dirs:
        if not base.exists():
            continue
        for d in base.iterdir():
            logs_dir = d / "MQL5" / "Logs"
            if not logs_dir.exists():
                logs_dir = d / "logs"
            if logs_dir.exists():
                log_files = sorted(logs_dir.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
                for lf in log_files[:2]:
                    try:
                        with open(lf, 'r', errors='ignore') as f:
                            lines = f.readlines()
                        # Get last 50 lines with errors
                        for line in lines[-100:]:
                            if any(kw in line.lower() for kw in ['error', 'fatal', 'crash', 'exception', 'failed', 'c0000135', 'winerror']):
                                errors.append(f"{lf.name}: {line.strip()}")
                    except Exception:
                        pass
    return errors[-20:]  # Last 20 errors


def try_mt5_connect(timeout=20):
    """
    Try connecting to MT5 via wine Python.
    Returns (success, details_dict).
    """
    script = '''
import sys, json, traceback
sys.path.insert(0, "/home/tradingbeast/fundingpips/framework")
try:
    import MetaTrader5 as mt5
    r = mt5.initialize()
    if r:
        info = mt5.account_info()
        if info:
            result = {"status": "connected", "login": info.login, "server": info.server, "balance": info.balance, "equity": info.equity}
        else:
            result = {"status": "init_ok_no_account", "login": None}
        mt5.shutdown()
    else:
        err = mt5.last_error()
        result = {"status": "init_failed", "error": str(err)}
except Exception as e:
    result = {"status": "exception", "error": str(e), "trace": traceback.format_exc()}
print(json.dumps(result))
'''
    try:
        result = subprocess.run(
            ["wine", WINE_PY, "-c", script],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(FRAMEWORK_DIR),
            env={**os.environ, "DISPLAY": ":99"},
        )
        if result.returncode == 0:
            # Parse JSON from stdout (may have wine noise)
            for line in result.stdout.strip().split('\n'):
                line = line.strip()
                if line.startswith('{'):
                    try:
                        data = json.loads(line)
                        return data.get("status") == "connected", data
                    except json.JSONDecodeError:
                        pass
            return False, {"status": "no_json_output", "stdout": result.stdout[-200:]}
        else:
            return False, {"status": "wine_error", "stderr": result.stderr[-200:]}
    except subprocess.TimeoutExpired:
        return False, {"status": "timeout", "detail": f"connect took >{timeout}s"}
    except Exception as e:
        return False, {"status": "exception", "error": str(e)}


def restart_mt5():
    """Kill and restart MT5 cleanly."""
    log("Restarting MT5...")
    
    # Kill existing MT5
    subprocess.run(["pkill", "-f", "terminal64.exe"], capture_output=True)
    time.sleep(3)
    
    # Force kill if still alive
    subprocess.run(["pkill", "-9", "-f", "terminal64.exe"], capture_output=True)
    time.sleep(2)
    
    # Kill wineserver to clean up
    subprocess.run(["wineserver", "-k"], capture_output=True)
    time.sleep(3)
    
    # Start MT5
    env = {**os.environ, "DISPLAY": ":99"}
    subprocess.Popen(
        ["wine", str(MT5_DIR / "terminal64.exe")],
        cwd=str(MT5_DIR), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    log("MT5 restart initiated, waiting 45s for startup...")
    time.sleep(45)


def run_diagnostic():
    """Full diagnostic pass."""
    log("=" * 60)
    log("MT5 HEALTH CHECK")
    
    # 1. Check MT5 process
    mt5_proc = get_mt5_process()
    if mt5_proc:
        age = mt5_proc['age']
        rss_mb = mt5_proc['rss_mb']
        log(f"MT5 process: PID={mt5_proc['pid']}, age={age}s, RSS={rss_mb}MB")
    else:
        age = 0
        log(f"MT5: {mt5_proc or 'NOT RUNNING'}")

    
    # 2. System resources
    sys_health = get_system_health()
    log(f"Memory: {sys_health['memory_used_mb']}/{sys_health['memory_total_mb']}MB used, {sys_health['memory_available_mb']}MB free, swap={sys_health['swap_used_mb']}MB")
    log(f"Disk: {sys_health['disk_free_gb']}GB free ({sys_health['disk_percent']}% used)")
    
    # 3. If within grace period, skip connection test
    if mt5_proc and age < GRACE_SECONDS:
        log(f"MT5 is {age}s old — within {GRACE_SECONDS}s grace period, skipping connection test")
        return
    
    # 4. Try connecting
    success, details = try_mt5_connect(timeout=20)
    log(f"Connection test: {json.dumps(details, indent=2)}")
    
    if success:
        log("MT5: HEALTHY")
        return
    
    # 5. Connection failed — diagnose
    log("MT5 connection FAILED — diagnosing...")
    
    # Check memory
    if sys_health["memory_available_mb"] < MIN_FREE_MB:
        log(f"DIAGNOSIS: Low memory ({sys_health['memory_available_mb']}MB free < {MIN_FREE_MB}MB threshold)")
    
    # Check disk
    if sys_health["disk_percent"] > 90:
        log(f"DIAGNOSIS: Low disk ({sys_health['disk_percent']}% used)")
    
    # Read MT5 terminal logs
    errors = get_mt5_terminal_log()
    if errors:
        log(f"MT5 terminal errors ({len(errors)}):")
        for e in errors[-10:]:
            log(f"  {e}")
    else:
        log("No MT5 terminal errors found in logs")
    
    # Check for wine errors
    try:
        dmesg = subprocess.run(["dmesg", "|", "tail", "-20"], capture_output=True, text=True, timeout=5)
        wine_errors = [l for l in dmesg.stdout.split('\n') if 'wine' in l.lower() or 'segfault' in l.lower()]
        if wine_errors:
            log("Wine errors in dmesg:")
            for e in wine_errors[-5:]:
                log(f"  {e}")
    except Exception:
        pass
    
    # 6. Decision
    if not mt5_proc:
        log("MT5 not running — starting fresh")
        restart_mt5()
    elif age < GRACE_SECONDS:
        log(f"MT5 still in grace period ({age}s) — waiting before restart")
    else:
        log(f"MT5 unresponsive for {age}s — restarting")
        restart_mt5()
    
    # 7. Post-restart check
    time.sleep(10)
    success2, details2 = try_mt5_connect(timeout=15)
    if success2:
        log("Post-restart: MT5 CONNECTED")
    else:
        log(f"Post-restart: STILL FAILING — {details2}")
        log("MANUAL INTERVENTION MAY BE REQUIRED")


if __name__ == "__main__":
    run_diagnostic()
