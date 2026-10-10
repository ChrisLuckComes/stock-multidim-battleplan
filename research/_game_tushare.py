# -*- coding: utf-8 -*-
"""Tushare 数据源：拉游戏板块日线 → bars_source 缓存 → 初筛指标。
token 从环境变量 TUSHARE_TOKEN 或文件 ~/.tushare_token 读取。
深度分析(stock_character/minesweep/battle_analyze)复用 bars_source 缓存，不触东财/新浪。
"""
import sys, json, os, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# 产物落 data/cache/（AGENTS.md 2b：运行产物不得落根目录）
CACHE = os.path.join(ROOT, "data", "cache")
PRESCAN = os.path.join(CACHE, "game_screen_prescan.json")
DEEP = os.path.join(CACHE, "game_deep.json")
MEMBERS = os.path.join(ROOT, "game_board_members.json")
os.makedirs(CACHE, exist_ok=True)
import bars_source as BS


def get_token():
    t = os.environ.get('TUSHARE_TOKEN')
    if t:
        return t
    for f in (os.path.expanduser('~/.tushare_token'),
              r'D:\code\stock-multidim-battleplan\.tushare_token'):
        if os.path.exists(f):
            return open(f, encoding='utf-8').read().strip()
    return None


import tushare as ts
token = get_token()
if not token:
    print('NO_TOKEN: 请提供 Tushare token（环境变量 TUSHARE_TOKEN 或 ~/.tushare_token）')
    sys.exit(2)
ts.set_token(token)
pro = ts.pro_api()

members = json.load(open(MEMBERS, encoding='utf-8'))


def tscode(code):
    return code + ('.SH' if code[0] == '6' else '.SZ')


def fetch_daily(code):
    df = pro.daily(ts_code=tscode(code), start_date='20250101', end_date='20261231')
    if df is None or len(df) == 0:
        return []
    df = df.sort_values('trade_date')
    bars = []
    for _, row in df.iterrows():
        bars.append({'d': str(row['trade_date']), 'o': float(row['open']),
                     'c': float(row['close']), 'h': float(row['high']),
                     'l': float(row['low']), 'v': float(row['vol'])})
    return bars


def ma(bars, n):
    if len(bars) < n:
        return None
    return sum(b['c'] for b in bars[-n:]) / n


def stage(bars, n):
    if len(bars) < n + 1:
        return None
    return (bars[-1]['c'] / bars[-1 - n]['c'] - 1) * 100


def zt_count(bars, since):
    c = 0
    for i, b in enumerate(bars):
        if b['d'] < since or i == 0:
            continue
        prev = bars[i - 1]['c']
        if prev > 0 and (b['c'] / prev - 1) * 100 >= 9.5:
            c += 1
    return c


def bigyang(bars, n, th=5.0):
    c = 0
    lo = max(0, len(bars) - n)
    for i in range(lo, len(bars)):
        if i == 0:
            continue
        prev = bars[i - 1]['c']
        if prev > 0 and (bars[i]['c'] / prev - 1) * 100 >= th:
            c += 1
    return c


def spike_ratio(bars, n):
    cnt = tot = 0
    lo = max(1, len(bars) - n)
    for i in range(lo, len(bars)):
        b = bars[i]
        hl = b['h'] - b['l']
        if hl <= 0:
            continue
        up = (b['h'] - max(b['o'], b['c'])) / hl
        dn = (min(b['o'], b['c']) - b['l']) / hl
        body = abs(b['c'] - b['o']) / hl
        tot += 1
        if max(up, dn) > 0.55 and body < 0.45:
            cnt += 1
    return cnt / tot if tot else 0.0


def rvol(bars):
    if len(bars) < 25:
        return None
    v5 = sum(b['v'] for b in bars[-5:]) / 5
    v20 = sum(b['v'] for b in bars[-25:-5]) / 20
    return v5 / v20 if v20 else None


rows = []
for code, name in members.items():
    try:
        bars = fetch_daily(code)
        if len(bars) < 60:
            print('skip(短)', code, name, len(bars))
            continue
        close = bars[-1]['c']
        ma5, ma10, ma20, ma50, ma200 = (ma(bars, k) for k in (5, 10, 20, 50, 200))
        bull = bool(ma5 and ma10 and ma20 and ma50 and ma5 > ma10 > ma20 > ma50 and close > ma5)
        s5, s10, s20, s60 = (stage(bars, k) for k in (5, 10, 20, 60))
        zt = zt_count(bars, '2026-01-01')
        by = bigyang(bars, 250, 5.0)
        sp = spike_ratio(bars, 120)
        rv = rvol(bars)
        last3 = sum((bars[-1 - i]['c'] / bars[-2 - i]['c'] - 1) * 100 for i in range(3) if len(bars) > i + 1)
        ma20_up = bool(ma20 and len(bars) > 21 and ma20 >= ma(bars[:-1], 20))
        rows.append({'code': code, 'name': name, 'close': round(close, 2),
                     'ma5': round(ma5, 2) if ma5 else None, 'ma10': round(ma10, 2) if ma10 else None,
                     'ma20': round(ma20, 2) if ma20 else None, 'ma50': round(ma50, 2) if ma50 else None,
                     'ma200': round(ma200, 2) if ma200 else None, 'bull': bull, 'ma20_up': ma20_up,
                     's5': round(s5, 2) if s5 is not None else None, 's10': round(s10, 2) if s10 is not None else None,
                     's20': round(s20, 2) if s20 is not None else None, 's60': round(s60, 2) if s60 is not None else None,
                     'zt': zt, 'bigyang': by, 'spike': round(sp, 3), 'rvol': round(rv, 2) if rv else None,
                     'last3': round(last3, 2), 'overheat': last3 >= 15})
        market = 'sh' if code[0] == '6' else 'sz'
        BS.cache_save(market, code, bars, 'tushare', min_len=60)
        print('OK', code, name, 'bars', len(bars))
    except Exception as e:
        print('ERR', code, name, repr(e)[:60])
    time.sleep(0.3)

for r in rows:
    score = (r['zt'] * 1.0 + r['bigyang'] * 0.4 + (1 - r['spike']) * 2.0
             + (6 if r['bull'] else 0) + (3 if r['ma20_up'] else 0))
    r['score'] = round(score, 2)
rows.sort(key=lambda r: r['score'], reverse=True)
json.dump(rows, open(PRESCAN, 'w', encoding='utf-8'),
          ensure_ascii=False, indent=2)
print('Tushare 初筛完成', len(rows), '只 -> game_screen_prescan.json')
for r in rows[:20]:
    print(f"{r['code']:8}{r['name']:10}{r['close']:>8}{('Y' if r['bull'] else '-'):>5}"
          f"{r['s20']:>7}{r['s60']:>7}{r['zt']:>5}{r['bigyang']:>5}{r['spike']:>6}{r['score']:>6}")
