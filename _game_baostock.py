# -*- coding: utf-8 -*-
"""游戏板块初筛：用 Baostock 后备数据源拉日线 → 写 bars_source 缓存 → 初筛。

取数核心见 src_baostock.py（通用模块，任意票可用：python src_baostock.py 603258）。
本脚本只做游戏板块专属的事：读成员清单、批量 fill_cache、再跑股性/多头初筛指标。
"""
import sys
import json
import os

sys.path.insert(0, r"D:\code\stock-multidim-battleplan")
import baostock as bs
from src_baostock import fetch_daily, fill_cache

members = json.load(open("game_board_members.json", encoding="utf-8"))


def ma(bars, n):
    if len(bars) < n:
        return None
    return sum(b["c"] for b in bars[-n:]) / n


def stage(bars, n):
    if len(bars) < n + 1:
        return None
    return (bars[-1]["c"] / bars[-1 - n]["c"] - 1) * 100


def zt_count(bars, since):
    c = 0
    for i, b in enumerate(bars):
        if b["d"] < since or i == 0:
            continue
        prev = bars[i - 1]["c"]
        if prev > 0 and (b["c"] / prev - 1) * 100 >= 9.5:
            c += 1
    return c


def bigyang(bars, n, th=5.0):
    c = 0
    lo = max(0, len(bars) - n)
    for i in range(lo, len(bars)):
        if i == 0:
            continue
        prev = bars[i - 1]["c"]
        if prev > 0 and (bars[i]["c"] / prev - 1) * 100 >= th:
            c += 1
    return c


def spike_ratio(bars, n):
    cnt = tot = 0
    lo = max(1, len(bars) - n)
    for i in range(lo, len(bars)):
        b = bars[i]
        hl = b["h"] - b["l"]
        if hl <= 0:
            continue
        up = (b["h"] - max(b["o"], b["c"])) / hl
        dn = (min(b["o"], b["c"]) - b["l"]) / hl
        body = abs(b["c"] - b["o"]) / hl
        tot += 1
        if max(up, dn) > 0.55 and body < 0.45:
            cnt += 1
    return cnt / tot if tot else 0.0


def rvol(bars):
    if len(bars) < 25:
        return None
    v5 = sum(b["v"] for b in bars[-5:]) / 5
    v20 = sum(b["v"] for b in bars[-25:-5]) / 20
    return v5 / v20 if v20 else None


rows = []
lg = bs.login()
print("baostock login", lg.error_code, lg.error_msg)
for code, name in members.items():
    try:
        bars = fetch_daily(code)
        if len(bars) < 60:
            print("skip(短)", code, name, len(bars))
            continue
        close = bars[-1]["c"]
        ma5, ma10, ma20, ma50, ma200 = (ma(bars, k) for k in (5, 10, 20, 50, 200))
        bull = bool(ma5 and ma10 and ma20 and ma50 and ma5 > ma10 > ma20 > ma50 and close > ma5)
        s5, s10, s20, s60 = (stage(bars, k) for k in (5, 10, 20, 60))
        zt = zt_count(bars, "2026-01-01")
        by = bigyang(bars, 250, 5.0)
        sp = spike_ratio(bars, 120)
        rv = rvol(bars)
        last3 = sum((bars[-1 - i]["c"] / bars[-2 - i]["c"] - 1) * 100 for i in range(3) if len(bars) > i + 1)
        ma20_up = bool(ma20 and len(bars) > 21 and ma20 >= ma(bars[:-1], 20))
        rows.append(
            {
                "code": code,
                "name": name,
                "close": round(close, 2),
                "ma5": round(ma5, 2) if ma5 else None,
                "ma10": round(ma10, 2) if ma10 else None,
                "ma20": round(ma20, 2) if ma20 else None,
                "ma50": round(ma50, 2) if ma50 else None,
                "ma200": round(ma200, 2) if ma200 else None,
                "bull": bull,
                "ma20_up": ma20_up,
                "s5": round(s5, 2) if s5 is not None else None,
                "s10": round(s10, 2) if s10 is not None else None,
                "s20": round(s20, 2) if s20 is not None else None,
                "s60": round(s60, 2) if s60 is not None else None,
                "zt": zt,
                "bigyang": by,
                "spike": round(sp, 3),
                "rvol": round(rv, 2) if rv else None,
                "last3": round(last3, 2),
                "overheat": last3 >= 15,
            }
        )
        # 写入 bars_source 磁盘缓存：stock_character/minesweep/battle_analyze 后续走缓存
        fill_cache(code)
        print("OK", code, name, "bars", len(bars))
    except Exception as e:
        print("ERR", code, name, repr(e)[:60])
bs.logout()
for r in rows:
    score = (
        r["zt"] * 1.0
        + r["bigyang"] * 0.4
        + (1 - r["spike"]) * 2.0
        + (6 if r["bull"] else 0)
        + (3 if r["ma20_up"] else 0)
    )
    r["score"] = round(score, 2)
rows.sort(key=lambda r: r["score"], reverse=True)
json.dump(rows, open("game_screen_prescan.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("Baostock 初筛完成", len(rows), "只 -> game_screen_prescan.json")
for r in rows[:22]:
    print(
        f"{r['code']:8}{r['name']:10}{r['close']:>8}{('Y' if r['bull'] else '-'):>5}"
        f"{r['s20']:>7}{r['s60']:>7}{r['zt']:>5}{r['bigyang']:>5}{r['spike']:>6}{r['rvol']:>6}{r['score']:>6}"
    )
