#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「均线刚收复」vs「已沿五日线上升」—— 模式判别器的实证复现脚本（2026-09-24）。

回答老罗 2026-09-24 的质疑：
  「T0 触发日看距 MA5 的判断，这个也不对吧……你这样判断岂不是和写死没区别，
   我是想提高判断 T0 和沿趋势上升的模式准确度」

## 做了什么

样本 = 本地 `data/` 下全部 A 股 6 位代码，各拉 500 根日线（约 2 年）。
三组对照：

  A. **前置样本**：收盘同时站上 MA5/MA10/MA20 的条件日。
  B. **引擎 T0 全判据**：再叠加 `rule123.ma_reclaim_break`（左侧平台度 / 双锚阻力位 / 板块涨幅上限）。
  C. **真实成交回放**：对 B 的每个条件日，按引擎给的**触发价**（= D0 最高价）与
     **硬止损锚**模拟 D1 入场、持有 N 日或止损出局，算 R。

分层变量：
  · 旧口径 `距 MA5`（4% 闸门）
  · 新判据 `ma_ride_state`：line_ride / mixed / fresh_reclaim

## 结论（脚本可复跑）

1. **旧 4% 闸门不成立**：扩到 53 只后，B 组「买入持有 5 日」口径 >4% +1.50% vs ≤4%
   +0.95%（原 4 票表是 +1.75% / +3.35%，腰斩）；C 组「真实触发价 + MA5 硬止损」
   口径 **>4% 均R −0.032 vs ≤4% −0.196，方向反转** —— 机制是离 MA5 近时硬止损
   只有 ~2.3%，正常波动就扫掉（≤4% 档 77% 止损出局，>4% 档 61%）。
2. **状态判别有效且稳健**：C 组 5 日 `line_ride` −0.29 / 胜率 19% / 扫损 77%，
   `fresh_reclaim` +0.16 / 26% / 68%；10 日 −0.20 vs +0.48；20 日 +0.09 vs +0.73。
   `slope20` 阈值 0/+2/+5 单调，2025-10 前后各半样本排序一致。

跑法：`python research_ma_ride.py`（首次联网拉数，之后走缓存；全量约 1~2 分钟）。
"""
from __future__ import annotations

import os
import re
import statistics as st
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import bars_source as bs
import rule123 as R

NAMES = {"601208": "东材科技", "688432": "有研硅", "603067": "振华股份",
         "688002": "睿创微纳", "300404": "博济医药"}


# ── 工具 ──────────────────────────────────────────────────────────────
def pfx(code):
    return "sh" if code[0] in "6" else "sz"


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


def pctile(seq, v):
    return 100.0 * sum(1 for x in seq if x < v) / len(seq) if seq else None


def pool_codes():
    return sorted(f[:6] for f in os.listdir("data") if re.fullmatch(r"\d{6}\.json", f))


# ── A/B：条件日 + 前 5 日口径 ─────────────────────────────────────────
def condition_days(code, t0_only=False, n=500):
    bars, _src, _ = bs.ash_bars(pfx(code), code, n=n)
    if len(bars) < 120:
        return []
    closes = [b["c"] for b in bars]
    ma5 = sma_series(closes, 5)
    ma10, ma20 = sma_series(closes, 10), sma_series(closes, 20)
    dist = [None if ma5[i] is None else (closes[i] - ma5[i]) / ma5[i] * 100
            for i in range(len(closes))]
    out = []
    for i in range(40, len(bars) - 6):
        # 条件日 = 收盘同时站上 MA5/MA10/MA20（T0 的前置，与文档同口径）
        if not (ma5[i] and ma10[i] and ma20[i]):
            continue
        if not (closes[i] > ma5[i] and closes[i] > ma10[i] and closes[i] > ma20[i]):
            continue
        if t0_only and R.ma_reclaim_break(bars[:i + 1], {"ticker": code},
                                          atr_series(bars, 14)[i], closes[i]) is None:
            continue
        base = [x for x in dist[max(0, i - 120):i] if x is not None]
        ride = R.ma_ride_state(bars[:i + 1], atr_series(bars, 14)[i]) or {}
        fwd = [bars[i + k] for k in range(1, 6)]
        up = st.mean(b["h"] for b in fwd) - closes[i]
        dn = closes[i] - st.mean(b["l"] for b in fwd)
        out.append({
            "code": code, "i": i, "dist": dist[i],
            "dist_pctile": pctile(base, dist[i]),
            "state": ride.get("state"),
            "fwd_ret": (closes[i + 5] / closes[i] - 1) * 100,
            "odd": (up / dn) if dn > 0 else None,
        })
    return out


# ── C：真实成交回放 ───────────────────────────────────────────────────
def t0_trades(code, hold=5, n=500):
    bars, _src, _ = bs.ash_bars(pfx(code), code, n=n)
    if len(bars) < 120:
        return []
    closes = [b["c"] for b in bars]
    ma5 = sma_series(closes, 5)
    atr = atr_series(bars, 14)
    dist = [None if ma5[i] is None else (closes[i] - ma5[i]) / ma5[i] * 100
            for i in range(len(closes))]
    out = []
    for i in range(40, len(bars) - hold - 2):
        if not ma5[i] or not atr[i] or atr[i] <= 0:
            continue
        t0 = R.ma_reclaim_break(bars[:i + 1], {"ticker": code}, atr[i], closes[i])
        if t0 is None:
            continue
        trig, hard = t0["trigger"], t0["hard_stop"]
        if trig <= hard:
            continue
        d1 = bars[i + 1]
        # 引擎口径：D1 过昨高 → max(开, 触发价)；未过 → D1 收盘（P3 补救价）
        entry = max(d1["o"], trig) if d1["h"] > trig else d1["c"]
        if entry <= hard:
            continue
        exit_px = None
        for k in range(1, hold + 1):
            if bars[i + k]["l"] <= hard:
                exit_px = hard
                break
        if exit_px is None:
            exit_px = bars[i + hold]["c"]
        base = [x for x in dist[max(0, i - 120):i] if x is not None]
        ride = R.ma_ride_state(bars[:i + 1], atr[i]) or {}
        out.append({
            "code": code, "i": i, "d": bars[i]["d"],
            "r": (exit_px - entry) / (entry - hard),
            "risk_pct": (entry - hard) / entry * 100,
            "dist": dist[i], "dist_pctile": pctile(base, dist[i]),
            "state": ride.get("state"),
            "slope20": ride.get("ma5_slope20_pct"),
        })
    return out


def fmt(rows, label, hold_note=""):
    if not rows:
        return f"  {label:<22} n=0"
    n = len(rows)
    r = [x["r"] for x in rows]
    return (f"  {label:<22} n={n:<5} 均R {st.mean(r):+.3f}  胜率 "
            f"{100.0*sum(1 for v in r if v > 0)/n:3.0f}%  扫损 "
            f"{100.0*sum(1 for v in r if v <= -0.999)/n:3.0f}%  ≥2R "
            f"{100.0*sum(1 for v in r if v >= 2.0)/n:3.0f}%  均风险 "
            f"{st.mean(x['risk_pct'] for x in rows):.2f}%")


def fmt_fwd(rows, label):
    if not rows:
        return f"  {label:<22} n=0"
    n = len(rows)
    odd = [x["odd"] for x in rows if x["odd"] is not None]
    return (f"  {label:<22} n={n:<5} 期望 {st.mean(x['fwd_ret'] for x in rows):+5.2f}%  "
            f"赔率 {st.mean(odd) if odd else float('nan'):5.2f}  为正 "
            f"{100.0*sum(1 for x in rows if x['fwd_ret'] > 0)/n:3.0f}%")


def main():
    codes = pool_codes()
    print(f"股池 {len(codes)} 只 A 股 × 500 根日线\n")

    # ── C 组：真实成交回放（核心证据）──
    trades = []
    for c in codes:
        trades += t0_trades(c, hold=5)
    print(f"========== C · 真实成交回放（触发价买入 / MA5 硬止损 / 持有 5 日）"
          f"  可交易样本 n={len(trades)} ==========")
    print(fmt([x for x in trades if x["dist"] > 4], "旧口径 距MA5 > 4%"))
    print(fmt([x for x in trades if x["dist"] <= 4], "旧口径 距MA5 ≤ 4%"))
    for lab in ("line_ride", "mixed", "fresh_reclaim"):
        print(fmt([x for x in trades if x["state"] == lab], f"新判据 · {lab}"))
    print("  -- 稳健性：MA5 20 根斜率单变量（0 / +2 / +5 与反向 -2）--")
    for t in (0.0, 2.0, 5.0):
        print(fmt([x for x in trades if (x["slope20"] or -99) > t], f"slope20 > {t:g}"))
    for t in (0.0, -2.0):
        print(fmt([x for x in trades if x["slope20"] is not None and x["slope20"] <= t],
                  f"slope20 ≤ {t:g}"))

    # ── 持有期对照 ──
    print("\n========== C · 持有期对照 ==========")
    for hold in (10, 20):
        tr = []
        for c in codes:
            tr += t0_trades(c, hold=hold)
        print(f"  [持有 {hold} 日]")
        for lab in ("line_ride", "mixed", "fresh_reclaim"):
            print(fmt([x for x in tr if x["state"] == lab], f"  {lab}"))

    # ── A/B 组：前 5 日收盘期望 ──
    print("\n========== A/B · 条件日「买入持有 5 日」口径 ==========")
    for tag, t0only in (("前置：站上三条均线", False), ("引擎 T0 全判据", True)):
        pool = []
        for c in codes:
            pool += condition_days(c, t0_only=t0only)
        print(f"  [{tag}] n={len(pool)}")
        print(fmt_fwd([x for x in pool if x["dist"] > 4], "  旧口径 距MA5 > 4%"))
        print(fmt_fwd([x for x in pool if x["dist"] <= 4], "  旧口径 距MA5 ≤ 4%"))
        for lab in ("line_ride", "mixed", "fresh_reclaim"):
            print(fmt_fwd([x for x in pool if x["state"] == lab], f"  新判据 · {lab}"))

    # ── 用户点名个股的最新状态 ──
    print("\n========== 点名个股最新状态（2026-09-24）==========")
    for code, name in NAMES.items():
        bars, _src, _ = bs.ash_bars(pfx(code), code, n=500)
        stt = R.ma_ride_state(bars, atr_series(bars, 14)[-1]) or {}
        print(f"  {name} {code} 收{bars[-1]['c']:.2f} 距MA5 {stt.get('dist_ma5_pct'):+.2f}% "
              f"slope20 {stt.get('ma5_slope20_pct'):+.2f}% above20 {stt.get('above20')} "
              f"→ {stt.get('state')}")




if __name__ == "__main__":
    main()
