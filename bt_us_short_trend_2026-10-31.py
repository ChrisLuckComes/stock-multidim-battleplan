#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_us_short_trend_2026-10-31.py —— 美股做空·**趋势延续档**回测（主决策口径）

★ 为什么单独做这一档（老罗 2026-10-31 晚）：
  老罗举的 2026-10-30 案例（SNDK 跳空 −8.65% → SNDQ +27%）**不是反转K线触发的**
  ——实测 SNDK 在 10 月**根本没有反转K线信号**。那一笔吃的是
  **「顶部形态已破位 ⇒ 下跌波段成立 ⇒ 趋势延续」**。
  ⇒ 这一档 = `plan_levels`（**口径唯一真源**）的两个主决策候选本身：
     ① **破位延续空**（已跌破结构支撑 ⇒ 顺着跌）
     ② **反抽不过空**（反抽到破位区/阻力站不上 ⇒ 再跌）

★ 与「反转K线档」的关系（两者互补，不是替代）：
     反转K线档 = **提前预警**（在阻力区出现反转K线，量小、赔率低、但位置好）
     趋势延续档 = **顺势兑现**（结构已破位，跟着跌；量大、赔率高、但要防超跌反弹）
  ⇒ 模拟实盘实证（去未来函数后）：
     有反转K线信号才做 → 13:00 平仓 **+1.98%**（124 笔，胜率 56.5%）
     无信号纯靠 RR 达标 → 13:00 平仓 **−0.26%**（404 笔）⇒ **信号/结构是 alpha 来源，RR 只是安全垫**
  ⇒ 这一档正是补上「无信号时」的那块：**结构破位本身就是信号**。

★ 口径（严格照抄主决策 + 老罗执行约束）：
     · 结构位：plan_levels(anchor=**前一日收盘**，决策当时已知的价格)
     · 入场：① 破位延续 = 今日**开盘**；② 反抽不过 = 今日**触及**该价位（ETF 侧换算）
     · 过滤：RR ≥ 1.5（主决策门槛）｜止损距离 ≥ 0.25×ATR（噪声带）
     · 出场：**13:00 ET 强制平仓**（北京 01:00，老罗不能看盘过夜）
     · 收益：用**反向ETF真实涨幅**衡量，不用 R 抽象
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
GATES = os.path.join(HERE, "gates")
if GATES not in sys.path:
    sys.path.insert(0, GATES)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from levels import levels_from_bars          # noqa: E402
from us_short import (NOISE_ATR, STRUCT_STOP_ATR, plan_levels,  # noqa: E402
                       rr_ratio)
from top_shapes import bearish_context        # noqa: E402

CACHE = os.path.join(HERE, "data", "us_bars_cache_20261031.json")
ETF_CACHE = os.path.join(HERE, "data", "us_etf_cache_20261031.json")
CORE = ["SNDK", "MU", "SKHY", "SOXX", "LITE"]
CHAIN = ["WDC", "STX", "TER", "MRVL", "AMD", "INTC", "ARM",
         "SMCI", "NVDA", "ON", "AVGO", "PHLX", "COHR"]
PAIRS = {"SNDK": ("SNDQ", 2.0), "MU": ("MUZ", 2.0), "SKHY": ("SKDD", 2.0),
         "SOXX": ("SOXS", 3.0), "LITE": ("LITZ", 2.0)}

EXIT_ET = 13.0 / 16.0      # 13:00 ET 平仓（老罗 01:00 睡 ⇒ 必须）
MIN_RR = 1.5               # 主决策门槛（默认；下面还会扫变体）
NOISE_MULT = NOISE_ATR     # 止损最小距离（噪声带）


def price_at(b, frac):
    return b["o"] + (b["c"] - b["o"]) * frac


def load():
    with open(CACHE, encoding="utf-8") as f:
        data = json.load(f)
    etfs = {}
    if os.path.exists(ETF_CACHE):
        with open(ETF_CACHE, encoding="utf-8") as f:
            etfs = json.load(f)
    return data, etfs


def stats(rows, key="ret"):
    v = [r[key] for r in rows if r.get(key) is not None]
    if not v:
        return None
    s = sorted(v)
    n = len(v)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    return {"n": n, "mean": sum(v) / n, "med": med,
            "win": 100.0 * sum(1 for x in v if x > 0) / n,
            "hit20": 100.0 * sum(1 for x in v if x >= 20) / n,
            "max": s[-1], "min": s[0]}


def show(tag, st, extra=""):
    if not st:
        print("  %-36s （无样本）" % tag)
        return
    print("  %-36s n=%4d 均值%+6.2f%% 中位%+6.2f%% 胜率%5.1f%% ≥20%%占%5.2f%% 最好%+6.1f%% %s"
          % (tag, st["n"], st["mean"], st["med"], st["win"], st["hit20"],
             st["max"], extra))


def run_all(data, emap, min_rr, require_ctx=False):
    """按 RR 门槛跑一遍（require_ctx=True ⇒ 只在顶部形态破位的波段内做）。"""
    rows = []
    for sym in sorted(data):
        if sym not in PAIRS:
            continue
        code, lev = PAIRS[sym]
        bars = data[sym]
        etf_by_d = emap.get(code, {})
        for k in range(30, len(bars) - 1):
            prev = bars[:k]
            today = bars[k]
            ed = etf_by_d.get(today["d"])
            if not ed:
                continue
            anchor = prev[-1]["c"]
            pl = plan_levels(prev, anchor, STRUCT_STOP_ATR)
            atr = pl["atr"]
            if not atr or atr <= 0:
                continue
            ctx = bearish_context(prev)
            bg_age = (k - 1 - ctx["start_i"]) if ctx else None
            if require_ctx and (ctx is None or bg_age is None or bg_age < 0):
                continue
            cands = []
            if pl["broken"]:
                cands.append(("破位延续空", anchor, pl["stop"], pl["t2"]))
                cands.append(("反抽S_broken不过空", pl["s_broken"], pl["stop"], pl["t2"]))
            else:
                cands.append(("反抽R1不过空", pl["r1"], pl["stop"], pl["t1"]))
            for cname, entry, stop, target in cands:
                if entry <= 0 or stop <= entry or target >= entry:
                    continue
                r = rr_ratio(entry, target, stop)
                if r is None or r < min_rr:
                    continue
                if (stop - entry) < NOISE_MULT * atr:
                    continue
                if cname == "破位延续空":
                    fill_px = today["o"]
                    if today["l"] > fill_px:
                        continue
                    etf_entry = ed["o"]
                else:
                    if not (today["l"] <= entry <= today["h"]):
                        continue
                    fill_px = entry
                    drop = (anchor - fill_px) / anchor
                    etf_entry = min(max(ed["o"] * (1 + lev * drop), ed["l"]), ed["h"])
                if etf_entry <= 0:
                    continue
                rows.append({
                    "sym": sym, "d": today["d"], "cand": cname, "rr": r,
                    "bg_age": bg_age, "ctx": (ctx or {}).get("name"),
                    "ret_1300": (price_at(ed, EXIT_ET) - etf_entry) / etf_entry * 100.0,
                    "ret_hi": (ed["h"] - etf_entry) / etf_entry * 100.0,
                    "entry": fill_px, "stop": stop, "target": target,
                    "etf": code, "etf_entry": etf_entry,
                })
    return rows


def run_chase(data, emap, require_ctx=True, stop_mode="prev_low"):
    """★ **日内追跌档**（老罗手法）：跌势成立就顺势做，**止损用前一日低点**（日内止损）。

    ⚠ 为什么必须另立一档：主决策（裸K结构位）**抓不到**老罗 2026-10-30 那一笔——
      正股跳空 −8.65% 后，下方结构支撑(t2=1277)已在入场价(1215)**上方** ⇒ 无效档；
      且结构止损(1444)在入场价上方 18.8% ⇒ 结构位口径**正确地拒绝**。
      而他实际做的是「跌势已成立 ⇒ 顺势追跌」，止损用**前一日低点**（日内风险），
      目标=当日能走的空间（不要求下方有结构支撑）。
      ⇒ 止损口径不同 ⇒ 必须分开回测，不能混进主决策。

    口径：
      · 前置：背景已破位且有效（顶部形态破位 ⇒ 下跌波段成立）
      · 触发：破位延续（今日开盘低于昨收）｜反抽不过（今日触及 s_broken/r1 后被打回）
      · 入场：破位延续=今日开盘；反抽=该价位（ETF 按比例折算）
      · 止损：**前一日最低**（决策时已知，无未来函数）；反抽档= max(前低, 反抽位+0.3ATR)
      · 出场：13:00 ET 平仓
    """
    rows = []
    for sym in sorted(data):
        if sym not in PAIRS:
            continue
        code, lev = PAIRS[sym]
        bars = data[sym]
        etf_by_d = emap.get(code, {})
        for k in range(30, len(bars) - 1):
            prev = bars[:k]
            today = bars[k]
            ed = etf_by_d.get(today["d"])
            if not ed:
                continue
            anchor = prev[-1]["c"]
            pl = plan_levels(prev, anchor, STRUCT_STOP_ATR)
            atr = pl["atr"]
            if not atr or atr <= 0:
                continue
            ctx = bearish_context(prev)
            bg_age = (k - 1 - ctx["start_i"]) if ctx else None
            if require_ctx and (ctx is None or bg_age is None or bg_age < 0):
                continue
            prev_low = min(b["l"] for b in prev[-5:])
            cands = []
            if pl["broken"] and today["o"] < anchor:
                # 破位延续：今日开盘低于昨收 ⇒ 顺势追
                cands.append(("破位延续追跌", today["o"], prev_low, today["o"]))
            # 反抽不过：**触达即进**（⚠ 早期版本用 `today["c"] < lvl`（收盘被打回）判定
            #   ⇒ **未来函数**：实盘挂单到阻力位时并不知道今天会不会被打回，
            #   而那个条件在下跌趋势里近乎等价于「今天是下跌日」⇒ 胜率虚高到 92.8%。
            #   正确口径 = **盘中触达该价位就进**（不看收盘），次日 13:00 平。
            for lvl, nm in ((pl["s_broken"], "反抽S_broken不过"),
                            (pl["r1"], "反抽R1不过")):
                if today["h"] >= lvl:
                    stop = max(prev_low, lvl - 1.0 * atr) if stop_mode == "prev_low" else lvl
                    cands.append((nm, lvl, stop, today["c"]))
            for cname, fill_px, stop, _ref in cands:
                if fill_px <= 0 or stop <= 0 or stop >= fill_px:
                    continue
                if cname == "破位延续追跌":
                    if today["l"] > fill_px:
                        continue
                    etf_entry = ed["o"]
                else:
                    if not (today["l"] <= fill_px <= today["h"]):
                        continue
                    drop = (anchor - fill_px) / anchor
                    etf_entry = min(max(ed["o"] * (1 + lev * drop), ed["l"]), ed["h"])
                if etf_entry <= 0:
                    continue
                # 目标：当日能走到的空间（用 ETF 当日最低倒推「正股最多能跌多少」）——不含未来
                rows.append({
                    "sym": sym, "d": today["d"], "cand": cname,
                    "bg_age": bg_age, "ctx": (ctx or {}).get("name"),
                    "etf": code, "etf_entry": etf_entry,
                    "entry": fill_px, "stop": stop,
                    "risk_pct": (fill_px - stop) / fill_px * 100.0,
                    "ret_1300": (price_at(ed, EXIT_ET) - etf_entry) / etf_entry * 100.0,
                    "ret_hi": (ed["h"] - etf_entry) / etf_entry * 100.0,
                    "ret_close": (ed["c"] - etf_entry) / etf_entry * 100.0,
                })
    return rows


def main():
    data, etfs = load()
    emap = {c: {b["d"]: b for b in bs} for c, bs in etfs.items()}
    print("正股 %d 只 ｜ 反向ETF %s" % (len(data), ",".join(sorted(etfs))))

    rows = []
    for sym in sorted(data):
        if sym not in PAIRS:
            continue
        code, lev = PAIRS[sym]
        bars = data[sym]
        etf_by_d = emap.get(code, {})
        n = len(bars)
        for k in range(30, n - 1):
            prev = bars[:k]                 # 截至前一日（决策时已知）
            today = bars[k]
            ed = etf_by_d.get(today["d"])
            if not ed:
                continue
            anchor = prev[-1]["c"]          # ★ anchor = 前一日收盘（无未来函数）
            pl = plan_levels(prev, anchor, STRUCT_STOP_ATR)
            atr = pl["atr"]
            if not atr or atr <= 0:
                continue
            ctx = bearish_context(prev)     # 背景（时点化）
            bg_age = (k - 1 - ctx["start_i"]) if ctx else None
            # ── 主决策候选（照抄 us_short.main 的 _cands 逻辑）──
            cands = []
            if pl["broken"]:
                cands.append(("破位延续空", anchor, pl["stop"], pl["t2"]))
                cands.append(("反抽S_broken不过空", pl["s_broken"], pl["stop"], pl["t2"]))
            else:
                cands.append(("反抽R1不过空", pl["r1"], pl["stop"], pl["t1"]))
            for cname, entry, stop, target in cands:
                if entry <= 0 or stop <= entry or target >= entry:
                    continue
                r = rr_ratio(entry, target, stop)
                if r is None or r < MIN_RR:
                    continue
                if (stop - entry) < NOISE_MULT * atr:
                    continue        # 噪声带（假赔率）
                # ── 入场可行性：①破位延续=今日开盘；②反抽=今日须触及该价 ──
                if cname == "破位延续空":
                    fill_px = today["o"]
                    if today["l"] > fill_px:
                        continue     # 今日未跌破开盘价（没成交）
                else:
                    if not (today["l"] <= entry <= today["h"]):
                        continue     # 今日未触及反抽价
                    fill_px = entry
                # ── ETF 侧入场价（⚠ 破位延续 vs 反抽必须分开算）──
                #   ① 破位延续空：fill = **今日开盘** ⇒ ETF 成交价就是 **ETF 开盘价**
                #      （⚠ 早期版本又乘了一次「正股相对 anchor 的跌幅」⇒ 重复计算，
                #        ETF 开盘价本身已包含跳空涨幅 ⇒ 老罗 10-30 那笔被算漏了）
                #   ② 反抽不过空：fill = 日内某价位 ⇒ 才需要从 ETF 开盘价按比例折算
                if cname == "破位延续空":
                    etf_entry = ed["o"]
                else:
                    drop = (anchor - fill_px) / anchor
                    etf_entry = ed["o"] * (1 + lev * drop)
                    etf_entry = min(max(etf_entry, ed["l"]), ed["h"])
                if etf_entry <= 0:
                    continue
                px_exit = price_at(ed, EXIT_ET)
                ret_1300 = (px_exit - etf_entry) / etf_entry * 100.0
                ret_hi = (ed["h"] - etf_entry) / etf_entry * 100.0
                ret_close = (ed["c"] - etf_entry) / etf_entry * 100.0
                rows.append({
                    "sym": sym, "d": today["d"], "cand": cname,
                    "rr": r, "bg_age": bg_age, "ctx": (ctx or {}).get("name"),
                    "ret_1300": ret_1300, "ret_hi": ret_hi, "ret_close": ret_close,
                    "entry": fill_px, "stop": stop, "target": target,
                    "etf": code, "etf_entry": etf_entry,
                })

    print()
    print("=" * 110)
    print("★ 趋势延续档（主决策 plan_levels 候选）· 13:00 ET 平仓 · 入场=前一日结构位判定")
    print("=" * 110)
    show("全部（13:00 平仓）", stats(rows, "ret_1300"))
    show("全部（当日最高=能吃到的肉）", stats(rows, "ret_hi"))
    show("全部（日收盘，偏乐观）", stats(rows, "ret_close"))
    print()
    print("=" * 110)
    print("★★ 日内追跌档（老罗手法：跌势成立就顺势做，止损=前一日低点，不要求下方有结构支撑）")
    print("=" * 110)
    ch = run_chase(data, emap, require_ctx=True)
    show("全部（13:00 平仓）", stats(ch, "ret_1300"))
    show("全部（当日最高=能吃到的肉）", stats(ch, "ret_hi"))
    show("全部（日收盘）", stats(ch, "ret_close"))
    print()
    print("  按候选类型：")
    for cname in ("破位延续追跌", "反抽S_broken不过", "反抽R1不过"):
        sub = [x for x in ch if x["cand"] == cname]
        if sub:
            show("    " + cname, stats(sub, "ret_1300"))
            show("      └ 当日最高", stats(sub, "ret_hi"))
    print()
    print("  按背景年龄（波段内）：")
    for lbl, fn in (("刚成立0~3", lambda a: a is not None and 0 <= a <= 3),
                    ("早期4~10", lambda a: a is not None and 4 <= a <= 10),
                    ("中段11~25", lambda a: a is not None and 11 <= a <= 25),
                    ("晚期26~60", lambda a: a is not None and 26 <= a <= 60),
                    ("超晚期>60", lambda a: a is not None and a > 60)):
        sub = [x for x in ch if fn(x.get("bg_age"))]
        show("    " + lbl, stats(sub, "ret_1300"))
        show("      └ 当日最高", stats(sub, "ret_hi"))
    print()
    print("  ★ 老罗案例复盘（SNDK 2026-10-30）：")
    for x in ch:
        if x["sym"] == "SNDK" and x["d"] >= "2026-10-28":
            print("    %s %-16s 正股入场%.2f 止损%.2f(风险%.1f%%)｜ETF %s 进%.4g"
                  " 13:00平%+.2f%% 当日最高%+.2f%%｜背景 %s 第%s根"
                  % (x["d"], x["cand"], x["entry"], x["stop"], x["risk_pct"],
                     x["etf"], x["etf_entry"], x["ret_1300"], x["ret_hi"],
                     x.get("ctx") or "无", x.get("bg_age")))

    print()
    print("=" * 110)
    print("★ RR 门槛变体（趋势延续档该用哪个口径？——老罗那笔 RR 0.54 被 1.5 门槛过滤掉）")
    print("=" * 110)
    print("%-10s %-8s %6s %9s %9s %9s %9s %s" % (
        "RR门槛", "范围", "样本", "13:00均值", "13:00中位", "胜率", "≥20%占", "能否吃到10-30那笔"))
    for mrr in (1.5, 1.0, 0.75, 0.5, 0.3, 0.0):
        rr_rows = run_all(data, emap, mrr)
        st = stats(rr_rows, "ret_1300")
        if not st:
            continue
        hit = any(x["sym"] == "SNDK" and x["d"] == "2026-10-30" for x in rr_rows)
        lbl = ("%.2f" % mrr) if mrr else "不筛"
        print("%-10s %-8s %6d %+9.2f%% %+9.2f%% %8.1f%% %8.2f%% %s"
              % (lbl, "全部", st["n"], st["mean"], st["med"], st["win"],
                 st["hit20"], "★能" if hit else "—"))
        # 波段内（顶部形态破位）
        rr2 = [x for x in run_all(data, emap, mrr, require_ctx=True)]
        st2 = stats(rr2, "ret_1300")
        if st2:
            print("%-10s %-8s %6d %+9.2f%% %+9.2f%% %8.1f%% %8.2f%%"
                  % ("", "波段内", st2["n"], st2["mean"], st2["med"], st2["win"], st2["hit20"]))
    print()
    print("  按候选类型拆：")
    for cname in ("破位延续空", "反抽S_broken不过空", "反抽R1不过空"):
        sub = [x for x in rows if x["cand"] == cname]
        if not sub:
            continue
        show("    " + cname, stats(sub, "ret_1300"))
        show("      └ 当日最高", stats(sub, "ret_hi"))
    print()
    print("  按票拆（13:00 平仓）：")
    for sym in sorted(set(x["sym"] for x in rows)):
        ts = [x for x in rows if x["sym"] == sym]
        st = stats(ts, "ret_1300")
        sh = stats(ts, "ret_hi")
        if st:
            print("    %-6s n=%3d 均值%+6.2f%% 中位%+6.2f%% 胜率%5.1f%% ≥20%%占%5.2f%% ｜ 当日最高均值%+6.2f%% 最好%+6.1f%%"
                  % (sym, st["n"], st["mean"], st["med"], st["win"], st["hit20"],
                     sh["mean"], sh["max"]))
    print()
    print("  按背景年龄（波段内 vs 无背景）：")
    for lbl, fn in (("波段内(bg_age≥0)", lambda a: a is not None and a >= 0),
                    ("无背景(未破位/未见顶)", lambda a: a is None)):
        sub = [x for x in rows if fn(x.get("bg_age"))]
        show("    " + lbl, stats(sub, "ret_1300"))
        show("      └ 当日最高", stats(sub, "ret_hi"))
    print()
    print("  按背景年龄细分（波段内）：")
    for lbl, fn in (("刚成立0~3", lambda a: a is not None and 0 <= a <= 3),
                    ("早期4~10", lambda a: a is not None and 4 <= a <= 10),
                    ("中段11~25", lambda a: a is not None and 11 <= a <= 25),
                    ("晚期26~60", lambda a: a is not None and 26 <= a <= 60),
                    ("超晚期>60", lambda a: a is not None and a > 60)):
        sub = [x for x in rows if fn(x.get("bg_age"))]
        show("    " + lbl, stats(sub, "ret_1300"))
        show("      └ 当日最高", stats(sub, "ret_hi"))
    # 同票内配对（分离背景年龄 vs 个股）
    pairs = []
    for sym in sorted(set(x["sym"] for x in rows)):
        ts = [x for x in rows if x["sym"] == sym]
        e = [x for x in ts if x.get("bg_age") is not None and 0 <= x["bg_age"] <= 10]
        l = [x for x in ts if x.get("bg_age") is not None and x["bg_age"] > 25]
        if e and l:
            pairs.append((sym, stats(e, "ret_1300")["mean"], stats(l, "ret_1300")["mean"]))
    if pairs:
        diffs = [p[2] - p[1] for p in pairs]
        print("  ⇒ 同票内配对 %d 只：晚期−早期 差中位 %+.2fpp（%.0f%% 的票晚期更差）"
              % (len(diffs), sorted(diffs)[len(diffs) // 2],
                 100.0 * sum(1 for d in diffs if d < 0) / len(diffs)))
    else:
        print("  ⇒ 无同票配对样本")

    # ★ 老罗的案例复盘：SNDK 2026-10-30
    print()
    print("=" * 110)
    print("★ 老罗案例复盘：SNDK 2026-10-30（主决策口径能不能吃到那一笔）")
    print("=" * 110)
    for x in rows:
        if x["sym"] == "SNDK" and x["d"] >= "2026-10-27":
            print("  %s %-18s 入场%9.2f 止损%9.2f 目标%9.2f RR%5.2f｜ETF %s 进%.4g"
                  " 13:00平%+.2f%% 当日最高%+.2f%%｜背景 %s 第%s根"
                  % (x["d"], x["cand"], x["entry"], x["stop"], x["target"], x["rr"],
                     x["etf"], x["etf_entry"], x["ret_1300"], x["ret_hi"],
                     x.get("ctx") or "无", x.get("bg_age")))


if __name__ == "__main__":
    main()
