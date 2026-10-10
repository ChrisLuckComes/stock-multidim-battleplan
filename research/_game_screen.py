# -*- coding: utf-8 -*-
"""游戏板块初筛：按老罗框架（股性优先）拉日线算指标，排序输 Top。
数据源：东财 push2his kline（避开不通的新浪）。
指标：阶段涨幅 / 年内涨停基因(YearZTDay) / 近一年大阳数(弹性) / 毛刺占比(干净度)
     / 均线多头结构 / RVOL / 过热标记。
初筛后只对 Top 票跑 stock_character + minesweep + rule123 深度分析。
"""
import os, sys, json, urllib.request, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# 产物落 data/cache/（AGENTS.md 2b：运行产物不得落根目录）
CACHE = os.path.join(ROOT, "data", "cache")
PRESCAN = os.path.join(CACHE, "game_screen_prescan.json")
DEEP = os.path.join(CACHE, "game_deep.json")
MEMBERS = os.path.join(ROOT, "game_board_members.json")
os.makedirs(CACHE, exist_ok=True)
import fetch_market as FM
import bars_source as BS

members = json.load(open(MEMBERS, encoding="utf-8"))

def secid(code):
    return ('1.' if code[0] == '6' else '0.') + code

def em_kline(code, lmt=320):
    sid = secid(code)
    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={sid}&klt=101&fqt=1&end=20500101&lmt={lmt}"
           f"&fields1=f1,f2,f3,f4,f5,f6"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
    for attempt in range(3):
        try:
            d = FM.fetch_json_fallback(url, timeout=20, retries=2)
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(1.5)
            else:
                raise
    kd = (d.get("data") or {}).get("klines") or []
    bars = []
    for line in kd:
        p = line.split(",")
        if len(p) < 7:
            continue
        bars.append({"d": p[0], "o": float(p[1]), "c": float(p[2]),
                     "h": float(p[3]), "l": float(p[4]), "v": float(p[5]),
                     "amt": float(p[6]) if len(p) > 6 else 0.0})
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
        bars = em_kline(code, 320)
        market = 'sh' if code[0] == '6' else 'sz'
        try:
            BS.cache_save(market, code, bars, 'em_kline', min_len=60)
        except Exception as ce:
            print("cache_err", code, repr(ce)[:40])
        if len(bars) < 60:
            print("skip(短)", code, name, len(bars))
            continue
        close = bars[-1]['c']
        ma5, ma10, ma20, ma50, ma200 = (ma(bars, k) for k in (5, 10, 20, 50, 200))
        bull = bool(ma5 and ma10 and ma20 and ma50 and ma5 > ma10 > ma20 > ma50 and close > ma5)
        s5, s10, s20, s60 = (stage(bars, k) for k in (5, 10, 20, 60))
        zt = zt_count(bars, '2026-01-01')
        by = bigyang(bars, 250, 5.0)
        sp = spike_ratio(bars, 120)
        rv = rvol(bars)
        # 过热：近3日累计涨幅
        last3 = sum((bars[-1 - i]['c'] / bars[-2 - i]['c'] - 1) * 100 for i in range(3) if len(bars) > i + 1)
        ma20_up = bool(ma20 and len(bars) > 21 and ma20 >= ma(bars[:-1], 20))
        rows.append({
            "code": code, "name": name, "close": round(close, 2),
            "ma5": round(ma5, 2) if ma5 else None, "ma10": round(ma10, 2) if ma10 else None,
            "ma20": round(ma20, 2) if ma20 else None, "ma50": round(ma50, 2) if ma50 else None,
            "ma200": round(ma200, 2) if ma200 else None,
            "bull": bull, "ma20_up": ma20_up,
            "s5": round(s5, 2) if s5 is not None else None,
            "s10": round(s10, 2) if s10 is not None else None,
            "s20": round(s20, 2) if s20 is not None else None,
            "s60": round(s60, 2) if s60 is not None else None,
            "zt": zt, "bigyang": by, "spike": round(sp, 3),
            "rvol": round(rv, 2) if rv else None,
            "last3": round(last3, 2),
        })
    except Exception as e:
        print("ERR", code, name, repr(e))
    finally:
        time.sleep(1.2)

# 评分（老罗框架：股性优先，不含阶段涨幅加分）
for r in rows:
    score = (r['zt'] * 1.0
             + r['bigyang'] * 0.4
             + (1 - r['spike']) * 2.0
             + (6 if r['bull'] else 0)
             + (3 if r['ma20_up'] else 0))
    r['score'] = round(score, 2)
    r['overheat'] = r['last3'] >= 15

rows.sort(key=lambda r: r['score'], reverse=True)
json.dump(rows, open(PRESCAN, "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(f"初筛 {len(rows)} 只，Top20：")
hdr = f"{'代码':8}{'名称':10}{'收':>8}{'多头':>5}{'s20':>7}{'s60':>7}{'涨停':>5}{'大阳':>5}{'毛刺':>6}{'RVOL':>6}{'过热':>5}{'分':>6}"
print(hdr)
for r in rows[:20]:
    print(f"{r['code']:8}{r['name']:10}{r['close']:>8}{('Y' if r['bull'] else '-'):>5}"
          f"{('' if r['s20'] is None else r['s20']):>7}{('' if r['s60'] is None else r['s60']):>7}"
          f"{r['zt']:>5}{r['bigyang']:>5}{r['spike']:>6}{('' if r['rvol'] is None else r['rvol']):>6}"
          f"{('Y' if r['overheat'] else '-'):>5}{r['score']:>6}")
