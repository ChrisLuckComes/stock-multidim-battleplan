#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""筹码面「能不能参考买卖」的正面检验：现价相对**成本重心**的位置，有没有预测力。

★ 为什么单独测这个
  前四轮回测（v1~v4 / S3）测的都是「SCR 集中度高低」「POC 命中率」「主力加仓信号」，
  结论都是无预测力。但老罗真正会用的方式不是这些，而是——
  「现价离成本区还远不远？追还是等回踩？」= **位置型参考**，此前从未被直接检验过。

★ 指标（无换手率的近似，但口径与 chip 模型同源）
  `cost` = 成交量加权成本重心，按 60 交易日半衰期指数衰减
         （＝ chip_model 的 cost50 在「换手率近似恒定」下的解析近似，避免依赖 turn 字段，
            screener.db 未存换手率）
  `pos`   = 现价 / cost − 1        （正＝在成本区上方，负＝套牢区）
  `posATR`= (现价 − cost) / ATR20  （按波动率归一，可跨票比较）

★ 统计纪律（吸取前几轮教训）
  · **按日截面**算 Spearman IC，再对时间序列取均值 + t 检验（不是把重叠样本堆一起）
  · **按票去重**：同一票同一截面只出现一次
  · 分档用**截面内分位**（1/5 分组），避免跨期水平漂移
  · 未来收益 10 / 20 日，均剔除停牌与涨跌停无法成交的样本（保守：不剔，只剔除 v=0）

用法：python bt_chip_pos_2026-10-31.py [--max-codes 1500] [--step 20]
"""
import math
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "data", "screener.db")

HALF_LIFE = 60.0          # 交易日
FWD = (10, 20)            # 未来收益窗口
WARMUP = 130              # 至少 130 根才出第一个截面（覆盖一个完整筹码窗口）


def spearman(xs, ys):
    n = len(xs)
    if n < 8:
        return None

    def rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return num / (dx * dy) if dx and dy else None


def load(codes_limit=1500):
    con = sqlite3.connect(DB)
    names = {}
    for code, name in con.execute("select code, name from stocks"):
        names[code] = name or ""
    codes = [c for c in sorted(names)
             if "ST" not in names[c].upper() and "退" not in names[c]][:codes_limit]
    data = {}
    q = "select code,d,o,h,l,c,v from bars where code=? order by d"
    for c in codes:
        rows = con.execute(q, (c,)).fetchall()
        if len(rows) < WARMUP + max(FWD) + 5:
            continue
        data[c] = rows
    dates = sorted({r[1] for rs in data.values() for r in rs})
    con.close()
    return data, dates


def series_cost(rows):
    """成交量加权成本重心（60 日半衰期 EWMA），返回与 rows 对齐的数组 + ATR20。"""
    a = 0.5 ** (1.0 / HALF_LIFE)
    num = den = 0.0
    cost, atr = [], []
    trs = []
    prev_c = None
    for i, r in enumerate(rows):
        o, h, l, c, v = r[2], r[3], r[4], r[5], r[6]
        num = num * a + c * v
        den = den * a + v
        cost.append(num / den if den > 0 else c)
        tr = (h - l) if prev_c is None else max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
        atr.append(sum(trs[-20:]) / len(trs[-20:]))
        prev_c = c
    return cost, atr


def main():
    max_codes, step = 5200, 10
    for i, a in enumerate(sys.argv):
        if a == "--max-codes":
            max_codes = int(sys.argv[i + 1])
        if a == "--step":
            step = int(sys.argv[i + 1])

    data, dates = load(max_codes)
    print(f"# 样本：{len(data)} 只票，交易日 {dates[0]} ~ {dates[-1]}，"
          f"成本重心半衰期 {HALF_LIFE:.0f} 日，截面步长 {step}\n")

    # 预算每只票的 cost / atr / 收盘序列
    cache = {}
    for c, rows in data.items():
        cost, atr = series_cost(rows)
        cache[c] = (rows, cost, atr, {r[1]: i for i, r in enumerate(rows)})

    ics = {f: [] for f in FWD}
    ics_atr = {f: [] for f in FWD}
    buckets = {f: [[] for _ in range(5)] for f in FWD}
    n_sec = 0

    for di in range(WARMUP, len(dates) - max(FWD) - 1, step):
        day = dates[di]
        xs, xs_atr, ys = {f: [] for f in FWD}, {f: [] for f in FWD}, {f: [] for f in FWD}
        for c, (rows, cost, atr, idx) in cache.items():
            i = idx.get(day)
            if i is None or i < WARMUP or i + max(FWD) >= len(rows):
                continue
            px, cs = rows[i][5], cost[i]
            if px <= 0 or cs <= 0 or atr[i] <= 0 or rows[i][6] <= 0:
                continue
            p = px / cs - 1.0
            pa = (px - cs) / atr[i]
            for f in FWD:
                fpx = rows[i + f][5]
                if fpx <= 0:
                    continue
                r = fpx / px - 1.0
                xs[f].append(p)
                xs_atr[f].append(pa)
                ys[f].append(r)
        if len(ys[FWD[0]]) < 800:      # 截面样本下限：screener.db 2025-07 前只覆盖 ~200 只
            continue
        n_sec += 1
        for f in FWD:
            ic = spearman(xs[f], ys[f])
            ica = spearman(xs_atr[f], ys[f])
            if ic is not None:
                ics[f].append(ic)
            if ica is not None:
                ics_atr[f].append(ica)
            # 截面分 5 档（按 pos 排序），记录每档平均未来收益
            order = sorted(range(len(ys[f])), key=lambda k: xs[f][k])
            per = len(order) / 5.0
            for b in range(5):
                seg = order[int(b * per):int((b + 1) * per)]
                if seg:
                    buckets[f][b].append(sum(ys[f][k] for k in seg) / len(seg))

    print(f"有效截面 {n_sec} 个\n")

    def tstat(xs):
        n = len(xs)
        if n < 3:
            return 0.0
        m = sum(xs) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
        return m / (sd / math.sqrt(n)) if sd else 0.0

    print("## 横截面 Spearman IC（指标 = 现价 / 成本重心 − 1，正值高＝偏离成本区更远）")
    print(f"{'窗口':>6} {'均值IC':>9} {'t值':>7} {'正IC占比':>9} | {'ATR归一IC':>10} {'t值':>7}")
    for f in FWD:
        if not ics[f]:
            continue
        m = sum(ics[f]) / len(ics[f])
        pos = sum(1 for x in ics[f] if x > 0) / len(ics[f]) * 100
        ma = sum(ics_atr[f]) / len(ics_atr[f]) if ics_atr[f] else 0
        print(f"{f:>4}日 {m:>9.4f} {tstat(ics[f]):>7.2f} {pos:>8.1f}% | "
              f"{ma:>10.4f} {tstat(ics_atr[f]):>7.2f}")
    print("\n判据：|t| > 2 才算显著；IC < 0.02 量级即使显著也没有实战价值。")

    print("\n## 五档平均未来收益（按 pos 从低到高；低档＝深套牢区，高档＝远离成本区）")
    for f in FWD:
        line = []
        for b in range(5):
            v = buckets[f][b]
            line.append(f"{sum(v)/len(v)*100:+.2f}%" if v else "  n/a")
        if buckets[f][0]:
            spread = (sum(buckets[f][4]) / len(buckets[f][4])
                      - sum(buckets[f][0]) / len(buckets[f][0])) * 100
            print(f"{f:>3}日  " + "  ".join(f"{x:>7}" for x in line) +
                  f"   顶底差 {spread:+.2f}pp")
    print("\n注：若五档单调且顶底差 > 2pp，位置才有实战意义；否则只能说「无差异」。")


if __name__ == "__main__":
    main()
