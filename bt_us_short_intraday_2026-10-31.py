#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_us_short_intraday_2026-10-31.py —— 美股做空·**日内口径**回测

老罗 2026-10-31 定：
  ① 「做空是**以日内为主**，持仓不会过夜，**盈亏比到位就能做**」
  ② 「虽然晚期肯定没有早期进好」—— 晚期是**排序器**不是**闸门**
  ③ 「难道下跌就只能跌这么多天吗？有的**无底洞**呢」
     ⇒ 背景年龄 / 信号新鲜度 **不能当硬闸门**（我此前设的 MAX_BG_AGE=10 /
       MAX_SIG_AGE=3 是拍脑袋的错阈值，本次实证推翻）
  ④ 「2026-10-30 日夜盘在闪迪 -1% 的时候，立刻进场 SNDQ，可以吃到 20%+ 的涨幅
     超级大肉，你回测一下，**能吃到就是对，吃不到就是错**」
     ⇒ 必须用**日内持仓（当日了结）**口径回测，且用**反向ETF的实际涨幅**衡量。

★ 与旧回测（bt_us_short_calib_2026-10-31.py）的根本差异：
  旧口径：入场=次日开盘，出场=先触目标/先触止损，持有窗口 40 根（**隔夜/波段**）
  新口径：**入场当日了结**（不过夜）；入场有三种；出场=当日收盘或当日触及目标

★ 三个待验问题：
  Q1 日内到底能吃到多少？（用反向ETF真实涨幅，不用 R 抽象）
  Q2 背景年龄是闸门还是排序器？（早/中/晚 三档各自的日内超额）
  Q3 「日内回落 -X% 就进」这个规则，行不行？（含你举的 -1%）
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

import reverse_kline_short as RK          # noqa: E402
from levels import levels_from_bars      # noqa: E402
from top_patterns import detect_head_shoulders_top   # noqa: E402
from top_shapes import bearish_context   # noqa: E402

CACHE = os.path.join(HERE, "data", "us_bars_cache_20261031.json")
CORE = ["SNDK", "MU", "SKHY", "SOXX", "LITE"]
CHAIN = ["WDC", "STX", "TER", "MRVL", "AMD", "INTC", "ARM",
         "SMCI", "NVDA", "ON", "AVGO", "PHLX", "COHR"]
TICKERS = CORE + CHAIN
# 反向ETF映射（美股只能走反向ETF入场）
PAIRS = {"SNDK": ("SNDQ", 2.0), "MU": ("MUZ", 2.0), "SKHY": ("SKDD", 2.0),
         "SOXX": ("SOXS", 3.0), "LITE": ("LITZ", 2.0)}


# ───────────────────────── 取数 ─────────────────────────
def fetch_etf(sym, timeout=40):
    """取反向ETF日线（带超时）。"""
    import threading
    box = {}

    def _w():
        try:
            import rule123 as R
            bars, _s, _m = R.bars_from_us(sym)
            box["bars"] = [{"d": b["d"], "o": b["o"], "h": b["h"], "l": b["l"],
                            "c": b["c"], "v": b.get("v", 0)} for b in bars]
        except Exception as e:
            box["err"] = str(e)[:60]

    t = threading.Thread(target=_w, daemon=True)
    t.start()
    t.join(timeout)
    return box.get("bars"), box.get("err")


def load_all():
    """读缓存（正股）+ 按需补取反向ETF（单独缓存）。"""
    with open(CACHE, encoding="utf-8") as f:
        data = json.load(f)
    etf_cache_path = os.path.join(HERE, "data", "us_etf_cache_20261031.json")
    etfs = {}
    if os.path.exists(etf_cache_path):
        with open(etf_cache_path, encoding="utf-8") as f:
            etfs = json.load(f)
    for sym, (code, _lev) in PAIRS.items():
        if code not in etfs or len(etfs[code]) < 30:
            bars, err = fetch_etf(code)
            if bars:
                etfs[code] = bars
                with open(etf_cache_path, "w", encoding="utf-8") as f:
                    json.dump(etfs, f)
                print("  fetched ETF %-6s %d 根" % (code, len(bars)), flush=True)
            else:
                print("  ! ETF %s %s" % (code, err), flush=True)
    return data, etfs


def etf_map(etfs):
    """ticker → {date: bar} 便于按日期对齐。"""
    out = {}
    for code, bars in etfs.items():
        out[code] = {b["d"]: b for b in bars}
    return out


# ───────────────────────── 准备 ─────────────────────────
def prep(sym, bars):
    lv = levels_from_bars(bars)
    hs = detect_head_shoulders_top(bars, ma50=lv.get("ma50"))
    sigs, _h, _supp = RK.scan_reversal_kline(bars, from_date=None, lv=lv, hs=hs,
                                              atr=lv["atr14"], ctx_only=False)
    for s in sigs:
        s["h"] = bars[s["i"]]["h"]
        s["atr"] = (s["stop"] - s["h"]) / 0.30 if s.get("h") else None
    return sigs, lv


def build_bench(bars_by_sym):
    """等权市场基准（个股日收益中位数累乘）。"""
    daily = {}
    for _sym, bars in bars_by_sym.items():
        prev = None
        for b in bars:
            if prev is not None and prev > 0:
                daily.setdefault(b["d"], []).append(b["c"] / prev - 1.0)
            prev = b["c"]
    med = {}
    for dt, rs in daily.items():
        rs.sort()
        med[dt] = rs[len(rs) // 2]
    idx = {}
    v = 1.0
    for dt in sorted(med):
        v *= (1.0 + med[dt])
        idx[dt] = v
    return idx


# ───────────────────────── 日内入场规则 ─────────────────────────
def intraday_entries(bars, i):
    """在信号棒 bars[i] 之后、**当日内**的三个入场候选（价格口径都用正股）。

    返回 list of (tag, entry_px)：
      A 开盘：bars[i+1] 开盘价（信号次日开盘）
      B 回踩X%：bars[i+1] 盘中自最高回落 X%（X=1%/1.5%/2%），若当日触达
      C 收盘前：bars[i+1] 收盘价（最保守）
    """
    out = []
    if i + 1 >= len(bars):
        return out
    nxt = bars[i + 1]
    out.append(("A次日开盘", nxt["o"]))
    hi = nxt["h"]
    for pct in (1.0, 1.5, 2.0):
        lvl = hi * (1 - pct / 100.0)
        if nxt["l"] <= lvl <= hi:
            out.append(("B回踩%.1f%%" % pct, lvl))
    out.append(("C当日收盘", nxt["c"]))
    return out


def day_exit(bar, entry, target_px=None):
    """日内出场：当日触及目标（止盈）或当日收盘（兜底）。返回 (exit_px, kind, ret%)。"""
    if target_px is not None and bar["l"] <= target_px:
        return target_px, "触目标", (entry - target_px) / entry * 100.0
    return bar["c"], "收盘了结", (entry - bar["c"]) / entry * 100.0


def stats(rows, key="ret"):
    v = [r[key] for r in rows if r.get(key) is not None]
    if not v:
        return None
    s = sorted(v)
    n = len(v)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    return {"n": n, "mean": sum(v) / n, "med": med,
            "p25": s[n // 4], "p75": s[min(n - 1, 3 * n // 4)],
            "win": 100.0 * sum(1 for x in v if x > 0) / n,
            "hit20": 100.0 * sum(1 for x in v if x >= 20) / n,
            "max": s[-1], "min": s[0]}


def show(tag, st, extra=""):
    if not st:
        print("  %-34s （无样本）" % tag)
        return
    print("  %-34s n=%3d 均值%+6.2f%% 中位%+6.2f%% 胜率%5.1f%% ≥20%%占%5.1f%% 最好%+6.1f%% %s"
          % (tag, st["n"], st["mean"], st["med"], st["win"], st["hit20"],
             st["max"], extra))


def main():
    print("取数（缓存优先 + 补取反向ETF）…", flush=True)
    data, etfs = load_all()
    emap = etf_map(etfs)
    print("正股 %d 只 ｜ 反向ETF %s" % (len(data), ",".join(sorted(etfs))))

    prepped = {}
    for sym, bars in data.items():
        try:
            sigs, lv = prep(sym, bars)
            for sg in sigs:
                sg["_sym"] = sym
            prepped[sym] = (bars, sigs, lv)
        except Exception as e:
            print("  ! %s 预处理失败 %s" % (sym, e))
    idx = build_bench({k: v[0] for k, v in prepped.items()})
    dates = sorted(idx)

    # ── 收集所有日内交易（三种入场 × 全部信号 × 有/无背景）──
    trades = []
    for sym, (bars, sigs, lv) in prepped.items():
        code, lev = PAIRS.get(sym, (None, None))
        if not code:
            continue
        etf_by_d = emap.get(code, {})
        # 背景（时点化）
        ctx_cache = {}
        for sg in sigs:
            i = sg["i"]
            if i + 1 >= len(bars):
                continue
            if i not in ctx_cache:
                ctx_cache[i] = bearish_context(bars[:i + 1])
            ctx = ctx_cache[i]
            bg_age = (i - ctx["start_i"]) if ctx else None
            nxt = bars[i + 1]
            ed = etf_by_d.get(nxt["d"])
            if not ed:
                continue
            # 目标（正股口径）：结构支撑 T1，截 2R
            tgt = sg.get("target")
            atr = sg.get("atr")
            if tgt is None or not atr:
                continue
            risk = (sg["h"] + 0.30 * atr - nxt["o"]) / nxt["o"]
            if risk <= 0:
                continue
            plan_r = (nxt["o"] - tgt) / (sg["h"] + 0.30 * atr - nxt["o"])
            if plan_r > 2.0:
                tgt = nxt["o"] - 2.0 * (sg["h"] + 0.30 * atr - nxt["o"])
            for tag, entry_px in intraday_entries(bars, i):
                if entry_px <= 0:
                    continue
                # 反向ETF 的实际买点：正股 entry 相对当日开盘的跌幅 → ETF 涨幅
                # 用线性换算（杠杆 × 正股跌幅），并夹在当日 ETF 的实际 [低,高] 内
                drop = (nxt["o"] - entry_px) / nxt["o"]      # 正股从开盘跌了多少
                etf_entry = ed["o"] * (1 + lev * drop / 100.0)
                etf_entry = min(max(etf_entry, ed["l"]), ed["h"])
                # 当日了结：ETF 当日最高（吃到的肉）或当日收盘
                etf_high = ed["h"]
                ret_hi = (etf_high - etf_entry) / etf_entry * 100.0
                ret_close = (ed["c"] - etf_entry) / etf_entry * 100.0
                # 目标达成：正股当日最低 <= tgt ⇒ ETF 大约涨到 lev*(entry-tgt)/entry
                tgt_etf = etf_entry * (1 + lev * (entry_px - tgt) / entry_px / 100.0)
                hit_tgt = ed["l"] <= tgt_etf
                # ⚑ ret_hi = 当日最高，**不受 2R 约束** ⇒ 真正「能吃到多少的肉」
                #   ret_tgt = 若按 2R 止盈纪律了结的收益（更低，但更现实可执行）
                ret_tgt = min(ret_hi, (tgt_etf - etf_entry) / etf_entry * 100.0)
                # 超额：ETF 收益 − 基准当日收益（做空天然吃 beta ⇒ 必须看超额）
                mret = (idx.get(nxt["d"], 1.0) and
                        ((idx.get(ed["d"], 1.0) / idx.get(nxt["d"], 1.0) - 1) * 100.0))
                trades.append({
                    "sym": sym, "d": nxt["d"], "tag": tag,
                    "bg_age": bg_age, "ctx": (ctx or {}).get("name"),
                    "etf": code,
                    "ret": ret_close,             # 当日收盘了结收益(%)
                    "ret_hi": ret_hi,             # 当日最高（能吃到的肉，不受2R约束）
                    "ret_tgt": ret_tgt,           # 按2R止盈纪律了结的收益
                    "xret": (ret_close - mret) if mret is not None else None,
                    "hit_tgt": hit_tgt,
                    "stock_chg": (nxt["c"] / nxt["o"] - 1) * 100.0,
                })

    print()
    print("=" * 108)
    print("★ Q1：日内到底能吃到多少？（反向ETF真实涨幅；%d 笔日内交易）" % len(trades))
    print("=" * 108)
    show("全部（日收盘了结）", stats(trades, "ret"))
    show("全部（当日最高=能吃到的肉·不受2R约束）", stats(trades, "ret_hi"))
    show("全部（按2R止盈纪律了结）", stats(trades, "ret_tgt"))
    print()
    print("  按入场方式拆（日收盘了结 / 当日最高价）：")
    for tag in ("A次日开盘", "B回踩1.0%", "B回踩1.5%", "B回踩2.0%", "C当日收盘"):
        sub = [t for t in trades if t["tag"] == tag]
        show("    " + tag, stats(sub, "ret"))
        show("      └ 当日最高（能吃到的肉）", stats(sub, "ret_hi"))

    print()
    print("=" * 108)
    print("★ Q2：背景年龄是**闸门**还是**排序器**？（老罗：晚期不是不能做，只是没早期好）")
    print("=" * 108)
    AGE = [("★无背景/抢跑(bg_age=None)", lambda a: a is None),
           ("抢跑(背景未成立)", lambda a: a is not None and a < 0),
           ("刚成立0~3", lambda a: a is not None and 0 <= a <= 3),
           ("早期4~10", lambda a: a is not None and 4 <= a <= 10),
           ("中段11~25", lambda a: a is not None and 11 <= a <= 25),
           ("晚期26~60", lambda a: a is not None and 26 <= a <= 60),
           ("超晚期>60", lambda a: a is not None and a > 60),
           ("全部", lambda a: True)]
    for lbl, fn in AGE:
        sub = [t for t in trades if fn(t.get("bg_age"))]
        show(lbl + "（日收盘）", stats(sub, "ret"))
        show("  └ 同组·当日最高（能吃到的肉）", stats(sub, "ret_hi"))

    print()
    print("=" * 108)
    print("★ Q3：「日内回落 -X% 就进」（你举的 -1%）：单独看回踩型入场")
    print("=" * 108)
    for tag in ("B回踩1.0%", "B回踩1.5%", "B回踩2.0%"):
        sub = [t for t in trades if t["tag"] == tag]
        show(tag + "（日收盘）", stats(sub, "ret"))
        show("  └ 当日最高（能吃到的肉）", stats(sub, "ret_hi"))
        sub2 = [t for t in sub if t.get("bg_age") is not None and t["bg_age"] >= 0]
        show("  └ 仅波段内", stats(sub2, "ret_hi"))

    print()
    print("=" * 108)
    print("★ Q2b：**同票内配对**（分离「背景年龄」与「个股本身」——晚期组全负可能是票的问题）")
    print("=" * 108)
    pairs = []
    for sym in sorted(set(t["sym"] for t in trades)):
        ts = [t for t in trades if t["sym"] == sym]
        early = [t for t in ts if t.get("bg_age") is not None and 0 <= t["bg_age"] <= 10]
        late = [t for t in ts if t.get("bg_age") is not None and t["bg_age"] > 25]
        if early and late:
            e = stats(early, "ret")
            l = stats(late, "ret")
            pairs.append((sym, e["mean"], l["mean"], e["n"], l["n"]))
            print("  %-6s 早期(0~10) n=%2d 均值%+6.2f%%  ｜ 晚期(>25) n=%2d 均值%+6.2f%%  差 %+6.2fpp"
                  % (sym, e["n"], e["mean"], l["n"], l["mean"], l["mean"] - e["mean"]))
    if pairs:
        diffs = [p[2] - p[1] for p in pairs]
        neg = 100.0 * sum(1 for d in diffs if d < 0) / len(diffs)
        print("  ⇒ %d 只票可配对；晚期−早期 差中位 %+.2fpp；%.0f%% 的票晚期更差"
              % (len(diffs), sorted(diffs)[len(diffs) // 2], neg))
    else:
        print("  （无可配对样本：晚期信号集中在个别票上 ⇒ 晚期组结论是**个股效应**不是年龄效应）")

    print()
    print("=" * 108)
    print("★ Q4：按票拆（日收盘了结）——看是否「晚期组全负」其实是某只票的锅")
    print("=" * 108)
    for sym in sorted(set(t["sym"] for t in trades)):
        ts = [t for t in trades if t["sym"] == sym]
        ages = [t.get("bg_age") for t in ts if t.get("bg_age") is not None]
        nb = len([t for t in ts if t.get("bg_age") is None])
        st = stats(ts, "ret")
        if st:
            print("  %-6s n=%2d 均值%+6.2f%% 中位%+6.2f%% 胜率%5.1f%% 背景年龄 %s（无背景 %d 笔）"
                  % (sym, st["n"], st["mean"], st["med"], st["win"],
                     ("%d~%d" % (min(ages), max(ages))) if ages else "全无背景", nb))

    # ══ ★ Q5：**模拟实盘**——每天重新评估（老罗的真实流程）══
    #   每个交易日、每只票：用**当天现价 + 当天结构位**算盈亏比 → 达标就做空，
    #   **当日收盘了结**（日内不过夜）。这才是「每天跑作战方案照着做」的检验。
    print()
    print("=" * 108)
    print("★ Q5：模拟实盘（每日重评估 · 当日现价算盈亏比 · 达标就空 · 当日收盘了结）")
    print("=" * 108)
    MIN_RR = 1.5
    daily_rows = []
    for sym, (bars, sigs, lv) in prepped.items():
        code, lev = PAIRS.get(sym, (None, None))
        if not code:
            continue
        etf_by_d = emap.get(code, {})
        atr_now = lv.get("atr14") or 0.0
        try:
            from levels import structural_levels
            _i, s_highs = structural_levels(bars)
            _i2, s_lows = structural_levels(bars)
        except Exception:
            s_highs, s_lows = [], []
        # 背景（当日口径）：当日之前是否有形态已破位
        for k in range(30, len(bars)):
            b = bars[k]
            ed = etf_by_d.get(b["d"])
            if not ed:
                continue
            ctx = bearish_context(bars[:k + 1])
            if not ctx or not ctx.get("alive"):
                continue
            bg_age = k - ctx["start_i"]
            entry = b["c"]
            up = sorted([px for (_i, px) in s_highs if px > entry * 1.001])
            down = sorted([px for (_i, px) in s_lows if px < entry * 0.999], reverse=True)
            if up:
                cand = up[0]
                stop = (cand + 0.15 * atr_now if (cand - entry) <= 1.5 * atr_now
                        else entry + 0.30 * atr_now)
            else:
                stop = entry + 0.30 * atr_now
            risk = stop - entry
            if risk <= 0:
                continue
            target = entry - 2.0 * risk
            rr = (entry - target) / risk
            if rr < MIN_RR:
                continue
            # 日内可达性：2R 需要的跌幅 ≤ 1×ATR
            day_ok = (atr_now > 0 and 2.0 * risk <= atr_now)
            # 进场：ETF 当日开盘（实盘只能开盘买）
            etf_entry = ed["o"]
            if etf_entry <= 0:
                continue
            ret_hi = (ed["h"] - etf_entry) / etf_entry * 100.0
            ret_close = (ed["c"] - etf_entry) / etf_entry * 100.0
            mret = ((idx.get(b["d"], 1.0) and
                     (idx.get(ed["d"], 1.0) / idx.get(b["d"], 1.0) - 1) * 100.0))
            daily_rows.append({
                "sym": sym, "d": b["d"], "bg_age": bg_age,
                "rr": rr, "day_ok": day_ok, "risk_pct": risk / entry * 100.0,
                "ret": ret_close, "ret_hi": ret_hi,
                "xret": (ret_close - mret) if mret is not None else None,
                "ctx": ctx.get("name"),
            })
    show("全部（当日收盘了结）", stats(daily_rows, "ret"))
    show("全部（当日最高=能吃到的肉）", stats(daily_rows, "ret_hi"))
    print()
    print("  按票拆：")
    for sym in sorted(set(t["sym"] for t in daily_rows)):
        ts = [t for t in daily_rows if t["sym"] == sym]
        st = stats(ts, "ret")
        sh = stats(ts, "ret_hi")
        if st:
            print("    %-6s n=%3d 收盘均值%+6.2f%% 中位%+6.2f%% 胜率%5.1f%% ｜ 当日最高均值%+6.2f%% 最好%+6.1f%%"
                  % (sym, st["n"], st["mean"], st["med"], st["win"],
                     sh["mean"], sh["max"]))
    print()
    print("  按背景年龄（是否单调？）：")
    for lbl, fn in (("刚成立0~3", lambda a: a is not None and 0 <= a <= 3),
                    ("早期4~10", lambda a: a is not None and 4 <= a <= 10),
                    ("中段11~25", lambda a: a is not None and 11 <= a <= 25),
                    ("晚期26~60", lambda a: a is not None and 26 <= a <= 60),
                    ("超晚期>60", lambda a: a is not None and a > 60)):
        sub = [t for t in daily_rows if fn(t.get("bg_age"))]
        show("    " + lbl + "（收盘）", stats(sub, "ret"))
        show("      └ 当日最高", stats(sub, "ret_hi"))
    # 同票内配对（分离年龄 vs 个股）
    pairs = []
    for sym in sorted(set(t["sym"] for t in daily_rows)):
        ts = [t for t in daily_rows if t["sym"] == sym]
        e = [t for t in ts if t.get("bg_age") is not None and 0 <= t["bg_age"] <= 10]
        l = [t for t in ts if t.get("bg_age") is not None and t["bg_age"] > 25]
        if e and l:
            pairs.append((sym, stats(e, "ret")["mean"], stats(l, "ret")["mean"]))
    if pairs:
        diffs = [p[2] - p[1] for p in pairs]
        print("  ⇒ 同票内配对 %d 只：晚期−早期 差中位 %+.2fpp（%.0f%% 的票晚期更差）"
              % (len(diffs), sorted(diffs)[len(diffs) // 2],
                 100.0 * sum(1 for d in diffs if d < 0) / len(diffs)))
    else:
        print("  ⇒ 无同票配对（晚期信号集中在个别票）")

    print()
    print("=" * 108)
    print("★ 你举的那个案例复盘：SNDK / SNDQ 2026-10-30")
    print("=" * 108)
    sndk = prepped.get("SNDK")
    if sndk:
        bars = sndk[0]
        for b in bars[-3:]:
            print("  SNDK %s O%.2f H%.2f L%.2f C%.2f" % (b["d"], b["o"], b["h"], b["l"], b["c"]))
        ed = emap.get("SNDQ", {})
        for d in ("2026-10-29", "2026-10-30"):
            if d in ed:
                b = ed[d]
                print("  SNDQ %s O%.2f H%.2f L%.2f C%.2f" % (d, b["o"], b["h"], b["l"], b["c"]))
        b29 = ed.get("2026-10-29")
        b30 = ed.get("2026-10-30")
        if b29 and b30:
            print("  ⇒ 10-30 开盘进 SNDQ(%.2f) → 当日最高 %.2f（%+.1f%%）／收盘 %.2f（%+.1f%%）"
                  % (b30["o"], b30["h"], (b30["h"] / b30["o"] - 1) * 100,
                     b30["c"], (b30["c"] / b30["o"] - 1) * 100))
            print("  ⇒ 若 10-29 收盘进(%.2f) → 10-30 收盘 %.2f（%+.1f%%）"
                  % (b29["c"], b30["c"], (b30["c"] / b29["c"] - 1) * 100))


if __name__ == "__main__":
    main()
