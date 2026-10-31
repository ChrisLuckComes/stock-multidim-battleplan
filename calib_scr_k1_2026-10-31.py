# -*- coding: utf-8 -*-
"""SCR90 衰减口径切换标定：k=auto(半衰期60日) → k=1.0（官方换手衰减）。

目的：
  1. 量化两套口径的 SCR90 位移（全样本 + 按换手分层）—— 回答「降多少」
  2. 用**分位对齐**给出新阈值候选 —— 让新口径下的「高度集中/压缩/发散」覆盖面不变
  3. 用**个股级混淆矩阵**检验新阈值是否保住了原判断（阈值重标不能改结论）

用法：python calib_scr_k1_2026-10-31.py [--limit N]
输出：终端表格，无副作用（只读 data/cache/scr_*.json）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")

import scr_chip as sc

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "data", "cache")

# 现行阈值（k=auto 口径下标的的）
OLD = {"TIGHT": sc.SCR_TIGHT, "BUY": sc.SCR_BUY, "LOOSE": sc.SCR_LOOSE,
       "SHIP": sc.SCR_SHIP}


def scr_of_bars(bars, k):
    prices, dist = sc.chip_model(bars, keep_last=1, decay_k=k)[:2]
    p95, p5 = sc.cost_at(prices, dist, 95), sc.cost_at(prices, dist, 5)
    return sc.scr_of(p95, p5)


def pct(vals, q):
    """线性插值分位（q ∈ [0,100]）。"""
    s = sorted(vals)
    if not s:
        return None
    pos = (len(s) - 1) * q / 100.0
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（调试用）")
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(CACHE, "scr_*.json")))
    if a.limit:
        files = files[:a.limit]

    rows = []
    for fp in files:
        code = os.path.basename(fp)[4:-5]
        try:
            d = json.load(open(fp, encoding="utf-8"))
            bars = d.get("bars") or []
            if len(bars) < sc.MIN_BARS:
                continue
            if not any(b.get("turnover") for b in bars):
                continue
            k = sc.solve_decay_k(bars, half_life=sc.CHIP_HALF_LIFE)
            old = scr_of_bars(bars, k)
            new = scr_of_bars(bars, 1.0)
            turn = statistics.mean(b["turnover"] for b in bars)
            rows.append({"code": code, "name": (d.get("name") or "")[:8],
                         "k": k, "turn": turn, "old": old, "new": new})
        except Exception as e:                                    # noqa: BLE001
            print(f"  跳过 {code}: {type(e).__name__} {e}")

    if not rows:
        print("无可用样本")
        return
    print(f"样本 {len(rows)} 只（均含换手率）\n")

    o = [r["old"] for r in rows]
    n = [r["new"] for r in rows]
    rat = [r["new"] / r["old"] for r in rows if r["old"] > 0]

    print("## 1. 整体分布对照")
    print(f"{'':6} {'均值':>8} {'中位':>8} {'P10':>8} {'P25':>8} {'P75':>8} {'P90':>8}")
    for tag, v in (("旧 k=auto", o), ("新 k=1.0 ", n)):
        print(f"{tag:6} {statistics.mean(v):8.2f} {statistics.median(v):8.2f} "
              f"{pct(v,10):8.2f} {pct(v,25):8.2f} {pct(v,75):8.2f} {pct(v,90):8.2f}")
    print(f"\n新/旧 比值：中位 {statistics.median(rat):.3f}  "
          f"均值 {statistics.mean(rat):.3f}  最小 {min(rat):.3f}  最大 {max(rat):.3f}")
    print(f"k=1.0 的票（本就不受口径影响）：{sum(1 for r in rows if r['k'] >= 0.999)} / {len(rows)}")

    print("\n## 2. 按日均换手分层（换手越高，口径差越大）")
    bands = [(0, 2), (2, 4), (4, 6), (6, 8), (8, 100)]
    print(f"{'换手区间':>12} {'票数':>5} {'旧中位':>8} {'新中位':>8} {'比值':>7}")
    for lo, hi in bands:
        sub = [r for r in rows if lo <= r["turn"] < hi]
        if not sub:
            continue
        print(f"{f'{lo}~{hi}%':>12} {len(sub):5d} {statistics.median([r['old'] for r in sub]):8.2f} "
              f"{statistics.median([r['new'] for r in sub]):8.2f} "
              f"{statistics.median([r['new']/r['old'] for r in sub if r['old']>0]):7.3f}")

    print("\n## 3. 分位对齐 ⇒ 新阈值候选")
    print("   （旧阈值落在旧分布的哪个分位，新口径取同分位值）")
    for key, v in (("TIGHT", OLD["TIGHT"]), ("BUY", OLD["BUY"]),
                   ("LOOSE", OLD["LOOSE"]), ("SHIP", OLD["SHIP"])):
        share = sum(1 for x in o if x < v) / len(o) * 100
        newv = pct(n, share)
        print(f"   {key:5} 旧 {v:5.1f} → 旧分布第 {share:5.1f}% 分位 → 新口径 {newv:5.2f}"
              f"（缩放 {newv/v:.2f}x，取整建议 {round(newv*2)/2:g}）")

    print("\n## 4. 个股级混淆矩阵（用上一节的取整建议阈值）")
    sug = {k: round(pct(n, sum(1 for x in o if x < OLD[k]) / len(o) * 100) * 2) / 2
           for k in OLD}
    print("   建议阈值：", {k: sug[k] for k in ("TIGHT", "BUY", "LOOSE", "SHIP")})

    def cls(v, th):
        if v < th["TIGHT"]:
            return "高度集中"
        if v < th["BUY"]:
            return "偏集中"
        if v < th["LOOSE"]:
            return "中性"
        if v < th["SHIP"]:
            return "发散"
        return "极发散"

    agree = 0
    detail = []
    for r in rows:
        a_ = cls(r["old"], OLD)
        b_ = cls(r["new"], sug)
        if a_ == b_:
            agree += 1
        else:
            detail.append((r, a_, b_))
    print(f"   档位一致的票：{agree}/{len(rows)} = {agree/len(rows)*100:.1f}%")
    if detail:
        print(f"   不一致 {len(detail)} 只，前 12 只：")
        print(f"   {'code':8} {'换手':>6} {'k':>6} {'旧SCR':>7} {'新SCR':>7} {'旧判':>10} {'新判':>10}")
        for r, a_, b_ in detail[:12]:
            print(f"   {r['code']:8} {r['turn']:6.2f} {r['k']:6.3f} {r['old']:7.2f} "
                  f"{r['new']:7.2f} {a_:>10} {b_:>10}")

    print("\n## 5. 若沿用旧阈值（不改）会怎样")
    wrong = sum(1 for r in rows if cls(r["old"], OLD) != cls(r["new"], OLD))
    print(f"   档位不一致：{wrong}/{len(rows)} = {wrong/len(rows)*100:.1f}%"
          f"  ⇒ {'必须重标' if wrong/len(rows) > 0.15 else '影响有限，但建议同步'}")


if __name__ == "__main__":
    main()
