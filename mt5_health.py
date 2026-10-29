#!/usr/bin/env python3
"""Pure-stdlib MT5 health diagnostic; no third-party dependencies."""
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

LOG = Path('/home/tradingbeast/fundingpips/logs/mt5_health.log')
MT5_EXE = 'terminal64.exe'
MT5_DIR = Path('/home/tradingbeast/.mt5/drive_c/Program Files/MetaTrader 5')
WINE_PY = '/home/tradingbeast/.mt5/drive_c/users/tradingbeast/AppData/Local/Programs/Python/Python311/python.exe'
FRAMEWORK = Path('/home/tradingbeast/fundingpips/framework')
GRACE_SECONDS = 180


def command(args, timeout=10):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def log(message):
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{stamp}] {message}'
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a') as f:
        f.write(line + '\n')


def mt5_process():
    result = command(['pgrep', '-f', MT5_EXE], timeout=5)
    pid = int(result.stdout.strip().splitlines()[0]) if result.stdout.strip() else None
    if not pid:
        return None
    age = int(time.time() - os.stat(f'/proc/{pid}').st_ctime)
    rss = 0
    try:
        for line in Path(f'/proc/{pid}/status').read_text(errors='ignore').splitlines():
            if line.startswith('VmRSS:'):
                rss = int(line.split()[1]) // 1024
                break
    except (FileNotFoundError, ProcessLookupError):
        pass
    return {'pid': pid, 'age': age, 'rss_mb': rss}


def resources():
    meminfo = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            meminfo[key.strip()] = int(value.strip().split()[0]) // 1024
    total = meminfo.get('MemTotal', 0)
    available = meminfo.get('MemAvailable', 0)
    swap_used = max(0, meminfo.get('SwapTotal', 0) - meminfo.get('SwapFree', 0))
    disk = command(['df', '-m', '/home'], timeout=5).stdout.splitlines()
    used, free, percent = 0, 0, 0
    if len(disk) >= 2:
        parts = disk[1].split()
        used, free = int(parts[2]), int(parts[3])
        percent = int(parts[4].rstrip('%'))
    return {'memory_total_mb': total, 'memory_available_mb': available,
            'swap_used_mb': swap_used, 'disk_free_gb': free // 1024,
            'disk_used_percent': percent}


def terminal_errors():
    found = []
    base = Path('/home/tradingbeast/.mt5/drive_c/users/tradingbeast/AppData/Roaming/MetaQuotes/Terminal')
    terms = ('error', 'fatal', 'crash', 'exception', 'failed', 'c0000135', 'winerror', 'invalid handle')
    for source in (base / 'MQL5' / 'Logs', base / 'logs'):
        if not source.exists():
            continue
        logs = sorted(source.glob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True)[:2]
        for path in logs:
            try:
                lines = path.read_text(errors='ignore').splitlines()[-200:]
            except OSError:
                continue
            found.extend(f'{path.name}: {line.strip()[:180]}' for line in lines
                         if any(term in line.lower() for term in terms))
    return found[-15:]


def connect(timeout=25):
    code = '''import json, traceback
try:
    import MetaTrader5 as mt5
    if mt5.initialize():
        info = mt5.account_info()
        result = {"status":"connected", **({"login":info.login,"server":info.server,"balance":info.balance,"equity":info.equity} if info else {})}
        mt5.shutdown()
    else:
        result = {"status":"initialize_failed","error":str(mt5.last_error())}
except Exception as e:
    result = {"status":"exception","error":str(e),"trace":traceback.format_exc()[-600:]}
print("MT5_STATUS:"+json.dumps(result))
'''
    try:
        proc = command(['wine', WINE_PY, '-c', code], timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, {'status': 'timeout', 'timeout_seconds': timeout}
    payloads = [line[11:] for line in (proc.stdout + proc.stderr).splitlines()
                if line.startswith('MT5_STATUS:')]
    if payloads:
        try:
            detail = json.loads(payloads[-1])
            return detail.get('status') == 'connected', detail
        except json.JSONDecodeError:
            pass
    return False, {'status': 'no_response', 'return_code': proc.returncode,
                   'stdout': proc.stdout[-500:], 'stderr': proc.stderr[-500:]}


def restart():
    log('ACTION: restarting MT5')
    command(['pkill', '-f', MT5_EXE])
    time.sleep(3)
    command(['pkill', '-9', '-f', MT5_EXE])
    command(['wineserver', '-k'])
    time.sleep(3)
    subprocess.Popen(['wine', str(MT5_DIR / MT5_EXE)], cwd=MT5_DIR,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     env={**os.environ, 'DISPLAY': ':99'})
    time.sleep(45)


def diagnostic():
    log('=' * 50 + '\nMT5 HEALTH CHECK')
    process = mt5_process()
    res = resources()
    log(f"MT5: {process or 'NOT RUNNING'}")
    log(f"Resources: {res}")
    if process and process['age'] < GRACE_SECONDS:
        log(f"Within {GRACE_SECONDS}s startup grace period; connection test deferred")
        return
    log('Attempting direct MetaTrader5 connection...')
    healthy, detail = connect()
    log(f'Connection result: {json.dumps(detail, indent=2)}')
    if healthy:
        log('VERDICT: healthy')
        return
    log('Diagnosing failure...')
    errors = terminal_errors()
    log('Recent terminal errors:\n' + ('\n'.join(errors) if errors else 'none found'))
    if not process:
        verdict = 'MT5 is absent; restarting'
    elif process['age'] < GRACE_SECONDS:
        verdict = 'still within startup grace period; not restarting'
    elif res['disk_used_percent'] > 90:
        verdict = 'unresponsive and disk nearly full; restart may not help'
    else:
        verdict = f"unresponsive for {process['age']}s; restarting"
    log('VERDICT: ' + verdict)
    if verdict.startswith(('MT5 is absent', 'unresponsive')):
        log(f'INTERVENTION NEEDED: {verdict}')
        log('NOT restarting — automated restart kills the user session (wineserver -k). Manual action required.')


if __name__ == '__main__':
    diagnostic()
