# -*- coding: utf-8 -*-
"""重试拉初筛 Top12 的日线（push2his，降频+重试），写入 bars_source 缓存，供深度分析。"""
import os, sys, json, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# 产物落 data/cache/（AGENTS.md 2b：运行产物不得落根目录）
CACHE = os.path.join(ROOT, "data", "cache")
PRESCAN = os.path.join(CACHE, "game_screen_prescan.json")
DEEP = os.path.join(CACHE, "game_deep.json")
MEMBERS = os.path.join(ROOT, "game_board_members.json")
os.makedirs(CACHE, exist_ok=True)
import bars_source as BS
import fetch_market as FM

prescan = json.load(open(PRESCAN, encoding="utf-8"))
top = sorted(prescan, key=lambda r: r['score'], reverse=True)[:12]


def secid(code):
    return ('1.' if code[0] == '6' else '0.') + code


def em_kline(code, lmt=320):
    sid = secid(code)
    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={sid}&klt=101&fqt=1&end=20500101&lmt={lmt}"
           f"&fields1=f1,f2,f3,f4,f5,f6"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
    last = None
    for attempt in range(4):
        try:
            d = FM.fetch_json_fallback(url, timeout=20, retries=2)
            kd = (d.get("data") or {}).get("klines") or []
            bars = []
            for line in kd:
                p = line.split(",")
                if len(p) < 7:
                    continue
                bars.append({"d": p[0], "o": float(p[1]), "c": float(p[2]),
                             "h": float(p[3]), "l": float(p[4]), "v": float(p[5]),
                             "amt": float(p[6])})
            return bars
        except Exception as e:
            last = e
            if attempt < 3:
                time.sleep(6)
    raise last


ok = 0
for r in top:
    code, name = r['code'], r['name']
    try:
        bars = em_kline(code, 320)
        market = 'sh' if code[0] == '6' else 'sz'
        BS.cache_save(market, code, bars, 'em_kline', min_len=60)
        print("OK", code, name, "bars", len(bars))
        ok += 1
    except Exception as e:
        print("FAIL", code, name, repr(e)[:50])
    time.sleep(8)
print("refill ok=%d / %d" % (ok, len(top)))
