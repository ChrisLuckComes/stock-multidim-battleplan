# -*- coding: utf-8 -*-
"""股东户数暴增段的成交密集区（= 日后解套抛压带）定位
用法: python research_hh_trap.py <code> [code2 ...]
口径：新浪无成交额字段 ⇒ 用成交量加权的典型价 (h+l+c)/3 作 VWAP 代理（不是收盘价均值）。
MEMORY 纪律⑥：户数暴增那一段的成交密集区 = 日后解套抛压带。
"""
import sys, os
_HERE = os.path.dirname(os.path.abspath(__file__))
_UP = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_HERE, "fetch"), _UP, os.path.join(_UP, "fetch")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
from fetch_ashare import fetch_kline

HH = {  # 股东户数时序（TDX F9 f9_ashare_sl_gdhs）: 日期 -> (人数, 环比%)
    "300850": [("2025-06-30", 30898, 20.33), ("2025-09-30", 43355, 40.32),
               ("2025-12-31", 45035, 3.88), ("2026-02-28", 53814, 19.49),
               ("2026-03-31", 58441, 8.60), ("2026-06-30", 49563, -15.19)],
    "300904": [("2025-06-30", 8066, -4.67), ("2025-09-30", 10446, 29.51),
               ("2025-12-31", 8714, -16.58), ("2026-03-31", 8961, 2.83),
               ("2026-06-30", 8444, -5.77)],
}
SEGS = [
    ("2025-07-01", "2025-09-30", "2025Q3"),
    ("2025-10-01", "2025-12-31", "2025Q4"),
    ("2026-01-01", "2026-03-31", "2026Q1"),
    ("2026-04-01", "2026-06-30", "2026Q2"),
]
BOOM = {  # 暴增段（户数累计增幅最大的连续区间）
    "300850": ("2025-07-01", "2026-03-31", "2025Q3~2026Q1 +89.1%"),
    "300904": ("2025-07-01", "2025-09-30", "2025Q3 +29.5%"),
}


def load(code):
    bars = fetch_kline(code, n=400)
    rows = []
    for b in bars:
        rows.append({"date": str(b.get("d"))[:10], "close": float(b["c"]),
                     "high": float(b["h"]), "low": float(b["l"]), "vol": float(b["v"])})
    rows.sort(key=lambda r: r["date"])
    return rows


def vwap(rows, lo, hi):
    seg = [r for r in rows if lo <= r["date"] <= hi]
    if not seg:
        return None
    v = sum(r["vol"] for r in seg)
    if v <= 0:
        return None
    vw = sum(r["vol"] * (r["high"] + r["low"] + r["close"]) / 3.0 for r in seg) / v
    return vw, max(r["high"] for r in seg), min(r["low"] for r in seg), len(seg)


def main(code):
    rows = load(code)
    last = rows[-1]
    print("=" * 78)
    print(f"{code}  K线 {rows[0]['date']} ~ {last['date']}  最新收盘 {last['close']:.2f}")
    print("-- 股东户数时序 --")
    base = None
    for dt, n, ch in HH.get(code, []):
        if base is None:
            base = n
        print(f"   {dt}  {n:>7,d} 户  环比 {ch:+.2f}%   相对首期 {(n/base-1)*100:+.1f}%")
    print("-- 各季度 VWAP（量加权典型价）--")
    for lo, hi, lbl in SEGS:
        r = vwap(rows, lo, hi)
        if r:
            print(f"   {lbl}  VWAP={r[0]:.2f}  区间 {r[2]:.2f}~{r[1]:.2f}  ({r[3]}根)")
    lo, hi, lbl = BOOM[code]
    r = vwap(rows, lo, hi)
    if r:
        print(f"   ★暴增段 {lbl}: VWAP={r[0]:.2f}  区间 {r[2]:.2f}~{r[1]:.2f}")
        allv = sum(x["vol"] for x in rows if x["date"] >= "2025-07-01")
        segv = sum(x["vol"] for x in rows if lo <= x["date"] <= hi)
        print(f"      成交量占 2025-07 以来 {segv/allv*100:.1f}%")
        print(f"      现价 {last['close']:.2f} vs 暴增段VWAP {r[0]:.2f} => {(last['close']/r[0]-1)*100:+.1f}%")
        print(f"      ⇒ 上方套牢带（解套抛压）= {r[2]:.2f} 以上")
    r3 = vwap(rows, "2026-07-01", last["date"])
    if r3:
        print(f"   2026Q3至今 VWAP={r3[0]:.2f}  区间 {r3[2]:.2f}~{r3[1]:.2f}  ← 本季新进筹码成本线")


if __name__ == "__main__":
    for c in sys.argv[1:] or ["300850", "300904"]:
        main(c)
