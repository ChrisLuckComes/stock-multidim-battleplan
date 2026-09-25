#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「突破事件」与「沿均线背景」的优先级 —— 实证复现脚本（2026-09-24）。

回答老罗 2026-09-24 的第二轮质疑：

  ①「没有锚点就代表是新高，不需要找锚」
  ②「振华 603067 翻成 line_ride（锚 MA20 36.36）后，但平台突破是客观存在的，
     这个优先级，你要想想到底怎么判定。否则所有股大部分时间都是沿均线走的。」

两个问题各自量化：

  A. **「没有锚点」怎么判** —— `line_ride` 的候选线都在收盘下方，但「在下方」不等于
     「贴着」。按「距锚线几个 ATR」分档，看各档的真实表现：如果「远锚」档并不比
     「近锚」档好，那「远锚」就不该继续叫「沿这条线上行」。
  B. **突破 vs 沿线谁优先** —— 取「当日创 20 日新高」的突破日，同一天比较两种买法：
     突破买（当日收盘进）与回踩买（挂限价等回踩锚线，5 日内不成交即作废）。
     再按当日 ride 状态分组，看突破买在 line_ride 态下是否真的更差。

跑法：`python research_ride_priority.py`（首次联网拉数，之后走缓存）。
"""
from __future__ import annotations

import os
import re
import statistics as st
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import bars_source as bs
import rule123 as R

PFX = lambda c: "sh" if c[0] == "6" else "sz"      # noqa: E731


def sma_series(closes, n):
    out, s = [None] * len(closes), 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= n:
            s -= closes[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def atr_series(bars, n=14):
    out, trs = [None] * len(bars), []
    for i, b in enumerate(bars):
        tr = (b["h"] - b["l"]) if i == 0 else max(
            b["h"] - b["l"], abs(b["h"] - bars[i - 1]["c"]), abs(b["l"] - bars[i - 1]["c"]))
        trs.append(tr)
        if i >= n - 1:
            out[i] = sum(trs[i - n + 1:i + 1]) / n
    return out


def pool_codes():
    return sorted(f[:6] for f in os.listdir("data") if re.fullmatch(r"\d{6}\.json", f))


def ride_at(bars, closes, levels, i, atr_i):
    """`rule123.ma_ride_state` 的向量化等价实现（同一判据，返回骑乘线信息）。"""
    stats = {}
    for key, lv in levels.items():
        cur, prev = lv[i], (lv[i - R.MA_RIDE_SLOPE_WIN] if i >= R.MA_RIDE_SLOPE_WIN else None)
        if not cur or not prev:
            continue
        slope20 = (cur / prev - 1) * 100
        win = [k for k in range(max(0, i - 19), i + 1) if lv[k]]
        above = sum(1 for k in win if closes[k] > lv[k])
        bounce = sum(1 for k in range(max(1, i - 19), i + 1)
                     if lv[k] and bars[k]["l"] <= lv[k] and closes[k] > lv[k])
        stats[key] = {"slope20": slope20, "above": above, "bounce": bounce, "level": cur}
    riding = [k for k, s in stats.items()
              if s["slope20"] > 0 and s["above"] >= R.MA_RIDE_ABOVE_MIN
              and s["level"] <= closes[i]]
    if not riding:
        return None
    key = min(riding, key=lambda k: closes[i] - stats[k]["level"])
    s = stats[key]
    dist_atr = (closes[i] - s["level"]) / atr_i if atr_i else None
    return {"anchor": key, "line": s["level"], "slope20": s["slope20"],
            "above": s["above"], "dist_atr": dist_atr}


def collect(code, hold=5, n=500):
    bars, _src, _ = bs.ash_bars(PFX(code), code, n=n)
    if len(bars) < 120:
        return []
    closes = [b["c"] for b in bars]
    levels = {"ma5": sma_series(closes, 5),
              "ema10": R.ema_series(closes, 10),
              "sma20": sma_series(closes, 20)}
    atr = atr_series(bars, 14)
    out = []
    for i in range(40, len(bars) - hold - 1):
        if not atr[i] or atr[i] <= 0:
            continue
        ride = ride_at(bars, closes, levels, i, atr[i])
        # 突破日 = 收盘创 20 日新高（不含当日）且不弱于前收
        prior_hi = max(b["h"] for b in bars[i - 20:i])
        if not (closes[i] > prior_hi and closes[i] >= closes[i - 1]):
            continue
        # ── 买法 A：突破买（突破日收盘进，止损 1.5×ATR）──
        entry_a = closes[i]
        stop_a = entry_a - 1.5 * atr[i]
        exit_a = None
        for k in range(1, hold + 1):
            if bars[i + k]["l"] <= stop_a:
                exit_a = stop_a
                break
        if exit_a is None:
            exit_a = bars[i + hold]["c"]
        r_a = (exit_a - entry_a) / (entry_a - stop_a)
        pnl_a = (exit_a / entry_a - 1) * 100
        # ── 买法 B：回踩买（挂限价等回踩锚线，5 日内不成交即作废；结构止损 = 收盘破锚线）──
        r_b = pnl_b = None
        b_filled = False
        if ride:
            line = ride["line"]
            for k in range(1, hold + 1):
                fb = bars[i + k]
                if fb["l"] <= line:
                    entry_b = min(line, fb["o"])
                    exit_b = None
                    for m in range(i + k + 1, min(i + k + hold + 1, len(bars))):
                        if bars[m]["c"] < line:            # 结构止损：收盘破锚线
                            exit_b = bars[m]["c"]
                            break
                    if exit_b is None:
                        exit_b = bars[min(i + k + hold, len(bars) - 1)]["c"]
                    r_b = None                              # 结构止损无固定风险单位，只报收益率
                    pnl_b = (exit_b / entry_b - 1) * 100
                    b_filled = True
                    break
        # ── 买法 C：突破买 · 窄止损（0.5×ATR）—— 检验「锚太紧」这一说 ──
        stop_c = entry_a - 0.5 * atr[i]
        exit_c = None
        for k in range(1, hold + 1):
            if bars[i + k]["l"] <= stop_c:
                exit_c = stop_c
                break
        if exit_c is None:
            exit_c = bars[i + hold]["c"]
        out.append({"code": code, "d": bars[i]["d"], "state": "line_ride" if ride else None,
                    "anchor": ride["anchor"] if ride else None,
                    "dist_atr": ride["dist_atr"] if ride else None,
                    "r_a": r_a, "pnl_a": pnl_a, "r_b": r_b, "pnl_b": pnl_b,
                    "b_filled": b_filled,
                    "r_c": (exit_c - entry_a) / (entry_a - stop_c),
                    "pnl_c": (exit_c / entry_a - 1) * 100})
    return out


def fmt(rows, label):
    if not rows:
        return "  %-30s n=0" % label
    n = len(rows)
    ra = [x["r_a"] for x in rows]
    pa = [x["pnl_a"] for x in rows]
    rc = [x["r_c"] for x in rows]
    pc = [x["pnl_c"] for x in rows]
    s = ("  %-30s n=%-5d 突破买 均R %+.3f / 收益 %+5.2f%% / 胜 %3.0f%%"
         "  ｜窄止损 均R %+.3f / 收益 %+5.2f%%"
         % (label, n, st.mean(ra), st.mean(pa),
            100.0 * sum(1 for v in pa if v > 0) / n,
            st.mean(rc), st.mean(pc)))
    b = [x for x in rows if x["b_filled"]]
    if b:
        pb = [x["pnl_b"] for x in b]
        s += ("  ｜回踩买(成交 %d, %.0f%%) 期望 %+5.2f%% / 胜 %3.0f%%"
              % (len(b), 100.0 * len(b) / n, st.mean(pb),
                 100.0 * sum(1 for v in pb if v > 0) / len(b)))
    return s


def main():
    codes = pool_codes()
    print("股池 %d 只 A 股 × 500 根日线 ｜ 样本 = 「收盘创 20 日新高」的突破日\n" % len(codes))
    rows = []
    for c in codes:
        rows += collect(c)
    ride = [x for x in rows if x["state"] == "line_ride"]
    print("========== B · 突破日两种买法对照（持有 5 日）==========")
    print(fmt(rows, "全部突破日"))
    print(fmt([x for x in rows if not x["state"]], "无骑乘线（= 新高·无锚）"))
    print(fmt(ride, "有骑乘线 line_ride"))
    filled = [x for x in rows if x["b_filled"]]
    print("\n  -- 同日同子集对照（只取回踩真的成交的日子，两条腿可比）--")
    print(fmt(filled, "回踩成交日"))
    print("\n========== A · 「距锚线几个 ATR」分档（只取 line_ride）==========")
    for lo, hi, lab in ((None, 0.5, "≤0.5×ATR（贴线）"), (0.5, 1.0, "0.5~1.0×ATR"),
                        (1.0, 1.5, "1.0~1.5×ATR"), (1.5, None, ">1.5×ATR（远锚）")):
        sel = [x for x in ride if x["dist_atr"] is not None
               and (lo is None or x["dist_atr"] > lo) and (hi is None or x["dist_atr"] <= hi)]
        print(fmt(sel, "line_ride " + lab))
    print("\n  说明：「回踩买」列只统计 5 日内真的回踩到锚线的样本；成交率低 = 该锚太远、")
    print("        挂单大概率作废（振华 603067 锚 MA20 距 2.2×ATR 即此类）。")


if __name__ == "__main__":
    main()
