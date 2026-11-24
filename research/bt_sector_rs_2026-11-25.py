# -*- coding: utf-8 -*-
"""板块内相对强度（RS 排位）有效性回测 —— 2026-11-25。

## 要回答的问题

老罗 2026-11-25：「RS 需要在同板块/概念排前列，是选股必要条件，后排杂毛不做」。
在把 RS 做成**必要条件闸门**之前必须用自己取的数据证明：
  Q1 同板块内 RS 前排 vs 后排，未来 5/10 日有没有可读的差距？
  Q2 「前排」的阈值划在哪里（前 20%/30%/40%），过了哪条线开始没有 alpha？
  Q3 RS 用 20 日还是 60 日窗口？

## 口径（自洽原则）

- 数据：本地 `data/screener.db`（前复权日线，2024-01 起）+ `references/sector_map.json`
  （自维护板块→成分股，57 板块 807 只，零缺失）。
- RS 分位：**同板块内**按过去 W 日涨幅排名，`pct = 1 - (rank-1)/(n-1)`，
  1.0 = 板块内最强，0.0 = 最弱。只收板块有效样本 n ≥ MIN_N 的格子。
- 超额：`fwd 收益 − 同日同板块截面均值`。**这是关键**——减掉板块 beta，
  剩下的才是「选谁」的差异；若减全市场均值，前排组会因所属板块本身强而虚高。
- 时点：2025-09 起每 5 个交易日一个截面（前向 10 日不越界）。
- 排除：fwd 窗口内任一价格缺失的样本直接丢弃（不做插值）。

跑法：`python research/bt_sector_rs_2026-11-25.py [--json out.json]`
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sqlite3
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.path.join(ROOT, "data", "screener.db")
MAP = os.path.join(ROOT, "references", "sector_map.json")

MIN_N = 8          # 板块内有效样本 < 8 不算分位（分位不稳）
STEP = 5           # 每 5 个交易日取一个截面
START = "2025-09-01"
FWD = (5, 10)


def load():
    with open(MAP, encoding="utf-8") as f:
        raw = json.load(f)
    smap = {k: v for k, v in raw.items() if not k.startswith("_")}
    con = sqlite3.connect(DB)
    bars = collections.defaultdict(dict)
    for code, d, cl in con.execute(
            "select code,d,c from bars where d>=? order by d", (START,)):
        if cl is not None and cl > 0:
            bars[code][d] = cl
    dates = sorted({d for m in bars.values() for d in m})
    return smap, bars, dates


def pct_rank(vals):
    """→ {code: pct}，1.0=最强。并列取平均名次。"""
    s = sorted(vals.items(), key=lambda kv: -kv[1])
    n = len(s)
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and s[j + 1][1] == s[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0
        p = 1.0 - avg_rank / (n - 1) if n > 1 else 1.0
        for k in range(i, j + 1):
            out[s[k][0]] = p
        i = j + 1
    return out


def _ma(m, dates, di, n):
    """截至 di（含）的 n 日均线；缺任一天返回 None。"""
    if di - n + 1 < 0:
        return None
    s = 0.0
    for k in range(di - n + 1, di + 1):
        c = m.get(dates[k])
        if c is None:
            return None
        s += c
    return s / n


def build_rows(smap, bars, dates, win):
    """每个截面一行：含 RS 分位、板块方向、个股趋势状态、前向收益。"""
    rows = []
    for di in range(0, len(dates), STEP):
        if di < max(win, 60):
            continue
        i5, i10 = di + FWD[0], di + FWD[1]
        if i10 >= len(dates):
            continue
        d, d0 = dates[di], dates[di - win]
        d5, d10 = dates[i5], dates[i10]

        # —— 先算全市场等权 20 日中位，作为「板块超额」的基准 ——
        allc = {}
        for c, m in bars.items():
            c0, c1 = m.get(d0), m.get(d)
            if c0 and c1:
                allc[c] = c1 / c0 - 1.0
        mkt_med = statistics.median(allc.values()) if len(allc) >= 100 else None
        pcts_mkt = pct_rank(allc) if mkt_med is not None else {}

        for sec, codes in smap.items():
            vals, fwd, trend = {}, {}, {}
            for c in codes:
                m = bars.get(c)
                if not m:
                    continue
                c0, c1, c5, c10 = m.get(d0), m.get(d), m.get(d5), m.get(d10)
                if None in (c0, c1, c5, c10):
                    continue
                vals[c] = c1 / c0 - 1.0
                fwd[c] = (c5 / c1 - 1.0, c10 / c1 - 1.0)
                ma20, ma60 = _ma(m, dates, di, 20), _ma(m, dates, di, 60)
                # 趋势成立：收盘 > MA20 > MA60（老罗「站上均线才做」的最简代理）
                trend[c] = bool(ma20 and ma60 and c1 > ma20 > ma60)
            if len(vals) < MIN_N:
                continue
            pcts = pct_rank(vals)
            r_med = statistics.median(vals.values())
            sec_exc = (r_med - mkt_med) if mkt_med is not None else None
            for c, p in pcts.items():
                f5, f10 = fwd[c]
                rows.append({
                    "d": d, "sec": sec, "code": c, "pct": p,
                    "f5": f5, "f10": f10, "r_win": vals[c], "r_med": r_med,
                    "sec_exc": sec_exc, "trend": trend.get(c, False),
                    "n_sec": len(vals), "pct_mkt": pcts_mkt.get(c),
                })
    return rows


def agg(rows, key):
    """超额 = fwd − 同日同板块截面均值。"""
    base = {}
    for r in rows:
        base.setdefault((r["d"], r["sec"]), []).append(r)
    for k, g in base.items():
        for h in FWD:
            m = statistics.mean(x["f%d" % h] for x in g)
            for x in g:
                x["e%d" % h] = x["f%d" % h] - m
    out = collections.OrderedDict()
    for r in rows:
        b = key(r)
        if b is None:
            continue
        o = out.setdefault(b, {"n": 0, "e5": [], "e10": [], "f5": [], "f10": [], "w5": 0, "w10": 0})
        o["n"] += 1
        o["e5"].append(r["e5"])
        o["e10"].append(r["e10"])
        o["f5"].append(r["f5"])
        o["f10"].append(r["f10"])
        o["w5"] += 1 if r["f5"] > 0 else 0
        o["w10"] += 1 if r["f10"] > 0 else 0
    for b, o in out.items():
        o["exc5"] = statistics.mean(o["e5"]) * 100
        o["exc10"] = statistics.mean(o["e10"]) * 100
        o["fwd5"] = statistics.mean(o["f5"]) * 100
        o["fwd10"] = statistics.mean(o["f10"]) * 100
        o["win5"] = o["w5"] / o["n"] * 100
        o["win10"] = o["w10"] / o["n"] * 100
        o["med5"] = statistics.median(o["e5"]) * 100
        del o["e5"], o["e10"], o["f5"], o["f10"], o["w5"], o["w10"]
    return out


BUCKETS = [(0.80, 1.01, "前20%（最强）"), (0.60, 0.80, "20~40%"),
           (0.40, 0.60, "40~60%（中位）"), (0.20, 0.40, "60~80%"),
           (-0.01, 0.20, "后20%（最弱）")]


def bucket_of(p):
    for lo, hi, name in BUCKETS:
        if lo <= p < hi:
            return name
    return None


def show(title, table):
    print("\n" + "=" * 84)
    print(title)
    print("-" * 84)
    print("%-18s %6s %9s %9s %8s %8s %9s" %
          ("分组", "n", "超额5日", "超额10日", "中位5日", "胜率5", "胜率10"))
    for b, o in table.items():
        print("%-18s %6d %+8.2f%% %+8.2f%% %+7.2f%% %7.0f%% %7.0f%%" %
              (b, o["n"], o["exc5"], o["exc10"], o["med5"], o["win5"], o["win10"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()

    smap, bars, dates = load()
    print("板块 %d 个 / 成员 %d 只 / 交易日 %d（%s ~ %s）" %
          (len(smap), sum(len(v) for v in smap.values()), len(dates), dates[0], dates[-1]))

    report = {}
    for win in (5, 10, 20, 60):
        rows = build_rows(smap, bars, dates, win)
        t = agg(rows, lambda r: bucket_of(r["pct"]))
        show("【RS 窗口 %d 日】全样本 · 同板块内 RS 分位 → 相对板块均值超额" % win, t)
        report["w%d" % win] = {"buckets": t, "n_rows": len(rows)}

        # ★ 关键分层：只在「趋势成立」的票里比排名（老罗的真实用法）
        tr = [r for r in rows if r["trend"]]
        print("\n[趋势子集] 收盘>MA20>MA60：%d 条（占 %d%%）"
              % (len(tr), round(len(tr) / max(len(rows), 1) * 100)))
        show("【RS %d 日】趋势票池内 · RS 分位分桶" % win, agg(tr, lambda r: bucket_of(r["pct"])))

        # ★★ 双重条件：板块跑赢全市场 + 个股趋势成立
        both = [r for r in tr if r["sec_exc"] is not None and r["sec_exc"] > 0]
        print("\n[趋势 + 板块超额>0]：%d 条" % len(both))
        show("【RS %d 日】趋势票池 + 板块跑赢全市场 · RS 分位分桶" % win,
             agg(both, lambda r: bucket_of(r["pct"])))
        print("  阈值扫描（趋势 + 板块超额>0，pct ≥ 阈值）：")
        for th in (0.5, 0.6, 0.7, 0.8):
            tt = agg(both, lambda r, th=th: ("前%d%%" % round((1 - th) * 100))
                     if r["pct"] >= th else None)
            for b, o in tt.items():
                print("  %-8s n=%5d 超额5日 %+6.2f%% / 10日 %+6.2f%% / 胜率5 %3.0f%%"
                      % (b, o["n"], o["exc5"], o["exc10"], o["win5"]))
                report["w%d_trend_scan_%s" % (win, b)] = o

        # 反向对照：板块跑输全市场时，前排还能不能做
        weak = [r for r in tr if r["sec_exc"] is not None and r["sec_exc"] <= 0]
        show("【RS %d 日】趋势票池 + 板块跑输全市场（反向对照）" % win,
             agg(weak, lambda r: bucket_of(r["pct"])))
        report["w%d_trend_weak" % win] = agg(weak, lambda r: bucket_of(r["pct"]))

        if win == 20:
            # —— 诊断 A：板块内「绝对名次」（龙头 vs 跟风） ——
            def by_rank(r):
                n = r["n_sec"]
                if n < 10:
                    return None
                rk = round((1 - r["pct"]) * (n - 1)) + 1
                if rk == 1:
                    return "第1名（龙头）"
                if rk <= 3:
                    return "第2~3名"
                if rk <= 5:
                    return "第4~5名"
                if rk <= 10:
                    return "第6~10名"
                return "10名以后"
            show("【诊断A】趋势票池 · 板块内绝对名次（仅 n≥10 的板块）",
                 agg(tr, by_rank))

            # —— 诊断 B：全市场 20 日涨幅分位（对照：板块内 vs 全市场） ——
            show("【诊断B】趋势票池 · 个股在全市场 20 日涨幅分位",
                 agg(tr, lambda r: bucket_of(r["pct_mkt"]) if r["pct_mkt"] is not None else None))

            # —— 诊断 C：板块规模是否影响 RS 判据 ——
            show("【诊断C】趋势票池 · 按板块成分数分组（全RS桶合计）",
                 agg(tr, lambda r: "板块<15只" if r["n_sec"] < 15
                     else ("板块15~30只" if r["n_sec"] <= 30 else "板块>30只")))

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        print("\n已写 %s" % a.json)


if __name__ == "__main__":
    main()
