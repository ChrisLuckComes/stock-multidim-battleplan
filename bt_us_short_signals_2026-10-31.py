#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_us_short_signals_2026-10-31.py —— 美股做空信号的真实回测（回答「有没有提升」）

老罗 2026-10-31：顶部形态识别 + 「破MA20 才做空」这套，对我每天跑做空有没有提升？

口径（真实成交假设，不用理想价）：
  · 信号：gates/reverse_kline_short.scan_reversal_kline（trend_gate=True = 收盘破 MA20）
    —— 反弹K线（4模板×3过滤）在阻力区触发。
  · 入场：**信号棒次日开盘**（信号在收盘后才成形 ⇒ 次日开盘才能成交）。
  · 止损：信号棒高 + 0.30×ATR；目标：当时结构支撑 T1。
  · 判定：往后 40 根里，**先触及目标**=止盈 / **先触及止损**=止损 / 都没触及=未了结。
  · 另跑一版「旧口径」（收盘破 MA50 或破颈线）做对照 ⇒ 放宽到 MA20 是提升还是稀释。
输出：笔数、止盈/止损/未了结率、计划平均R、实际平均R、MFE/MAE 分布，
      以及「计划止损是否被扫」「R 能否达成」的直接检验。
"""
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

import top_shapes as TS            # noqa: E402
from top_patterns import detect_head_shoulders_top  # noqa: E402
import reverse_kline_short as RK   # noqa: E402
from levels import levels_from_bars  # noqa: E402

TICKERS = [("SNDK", "闪迪"), ("MU", "美光"), ("SOXX", "费半ETF"), ("SKHY", "SK海力士")]
FROM = "2026-06-01"
FWD = 40


def old_gate_signals(bars, from_date, lv, hs, atr):
    """旧口径：收盘破 MA50 **或** 破颈线 才放行（对照用）。"""
    from reverse_kline_short import (_metrics, _roll_ma, build_resists,
                                     detect_reversal_kline, structural_levels)
    closes = [b["c"] for b in bars]
    ma20 = _roll_ma(closes, 20)
    ma50 = _roll_ma(closes, 50)
    neck = hs.get("neckline") if hs.get("detected") else None
    out = []
    for i in range(max(21, 0), len(bars)):
        if bars[i]["d"] < from_date:
            continue
        resists, _ = build_resists(bars, i, ma20, lv, hs)
        r = detect_reversal_kline(bars, i, _metrics(bars), ma20, resists, atr)
        if r is None or not r["passed"]:
            continue
        below50 = ma50[i] is not None and bars[i]["c"] < ma50[i]
        below_neck = neck is not None and bars[i]["c"] < neck
        if not (below50 or below_neck):
            continue
        _, s_lows = structural_levels(bars[:i + 1])
        sup = sorted([px for (_, px) in s_lows if px < r["entry"]], reverse=True)
        t1 = sup[0] if sup else r["entry"] * 0.95
        r["target"] = t1
        r["rr"] = (r["entry"] - t1) / (r["stop"] - r["entry"]) if r["stop"] > r["entry"] else None
        out.append(r)
    return out


def bearish_context(bars):
    """**大背景层**：顶部形态已识别且**已破位** ⇒ 开启一段下跌波段（可持续做空）。

    返回 dict(bg_name, bg_break, bg_start_i, bg_start_d, bg_alive)。
    背景成立日 = 首次「收盘跌破形态触发位（颈线/下轨）」那根。
    """
    shapes = TS.detect_top_shapes(bars)
    bear = [x for x in shapes if x.get("bias") == "bearish" or x.get("pattern") == "菱形"]
    if not bear:
        return None
    # 取「最近一个已破位」的形态作背景（破位 = 收盘跌破其触发位）
    best = None
    for x in bear:
        bl = x.get("break_level")
        if bl is None:
            continue
        # ⚑ 破位日必须是**形态完成之后**的首次跌破（否则会找到很久以前的旧跌破，
        #   把背景成立日推到远古 ⇒ 所有信号都变成"背景晚期"）
        st = None
        frm = (x.get("signal_i") or 0)
        for i in range(frm, len(bars)):
            if bars[i]["c"] < bl:
                st = i
                break
        if st is None:
            continue
        if best is None or st > best["bg_start_i"]:
            best = {"bg_name": x["pattern"], "bg_break": bl, "bg_start_i": st,
                    "bg_start_d": bars[st]["d"], "shape": x}
    if best is None:
        return None
    last = bars[-1]["c"]
    best["bg_alive"] = last < best["bg_break"]
    return best


def evaluate(bars, sigs, sym, name):
    """逐笔回测（bars 为该票的日线）。"""
    res = []
    n = len(bars)
    for s in sigs:
        i = s["i"]
        if i + 1 >= n:
            continue
        entry = bars[i + 1]["o"]                      # 次日开盘成交
        stop, tgt = s["stop"], s["target"]
        seg = bars[i + 1:i + 1 + FWD]
        if len(seg) < 5:
            continue
        hit_t = hit_s = None
        mfe = mae = None
        for k, b in enumerate(seg):
            # ⚑ 空头口径：MFE = 期间**最大浮盈**（entry-low），MAE = 期间**最大浮亏**（负值）
            p_ = (entry - b["l"]) / entry * 100
            mfe = p_ if mfe is None else max(mfe, p_)
            p2 = (entry - b["h"]) / entry * 100
            mae = p2 if mae is None else min(mae, p2)
            if hit_t is None and lo_ok(b["l"], tgt):
                hit_t = k
            if hit_s is None and b["h"] >= stop:
                hit_s = k
        risk = (stop - entry) / entry * 100 if stop > entry else None
        rew = (entry - tgt) / entry * 100 if tgt < entry else None
        plan_r = (rew / risk) if (risk and rew) else None
        if hit_t is not None and (hit_s is None or hit_t <= hit_s):
            outcome, real_r = "止盈", (plan_r or 0)
        elif hit_s is not None:
            outcome, real_r = "止损", -1.0
        else:
            outcome = "未了结"
            real_r = (r_act(seg[-1]["c"], entry, risk) if risk else 0)
        res.append({
            "sym": sym, "name": name, "d": bars[i]["d"], "entry": entry,
            "stop": stop, "target": tgt, "plan_r": plan_r, "real_r": real_r,
            "risk": risk, "outcome": outcome, "mfe": mfe, "mae": mae,
            "act40": (seg[-1]["c"] - entry) / entry * 100,
            "pat": "/".join(s.get("templates") or []),
            "resist": (s.get("resist") or ("-", 0))[0],
        })
    return res


def lo_ok(price, target):
    return price <= target


def r_act(cend, entry, risk):
    if not risk:
        return 0.0
    return max(-1.0, ((cend - entry) / entry * 100) / risk)


def agg(rows, label):
    if not rows:
        return None
    n = len(rows)
    nw = sum(1 for r in rows if r["outcome"] == "止盈")
    ns = sum(1 for r in rows if r["outcome"] == "止损")
    no = sum(1 for r in rows if r["outcome"] == "未了结")
    pr = [r["plan_r"] for r in rows if r["plan_r"]]
    beyond = sum(1 for r in rows if r["risk"] and r["mae"] is not None
                 and abs(r["mae"]) > r["risk"] * 1.05)   # MAE 为负值 ⇒ 取绝对值比
    return {
        "label": label, "n": n, "win": nw, "stop": ns, "open": no,
        "win_rate": 100.0 * nw / n, "stop_rate": 100.0 * ns / n,
        "plan_r": sum(pr) / max(1, len(pr)),
        "real_r": sum(r["real_r"] for r in rows) / n,
        "mfe": sum(r["mfe"] for r in rows) / n,
        "mae": sum(r["mae"] for r in rows) / n,
        "mae_beyond": 100.0 * beyond / n,
        "rows": rows,
    }


def variants(bars, sigs, sym, name, stop_mult=None, cooldown=0, cap_r=None, ctx=None):
    """变体回测：改止损宽度 / 同票冷却 / 目标 R 上限。"""
    res = []
    n = len(bars)
    last_entry_i = -99
    for s in sigs:
        i = s["i"]
        if i + 1 >= n:
            continue
        if cooldown and (i - last_entry_i) < cooldown:
            continue
        entry = bars[i + 1]["o"]
        stop = s["stop"] if stop_mult is None else (s["h"] if "h" in s else None)
        # 重新按 stop_mult 算止损：信号棒高 + mult*ATR（ATR 取 bars 的近似：s 里没存则用原 stop 反推）
        if stop_mult is not None:
            atr = (s["stop"] - s["h"]) / 0.30 if "h" in s and s.get("h") else None
            if atr is None:
                continue
            stop = s["h"] + stop_mult * atr
        tgt = s["target"]
        risk = (stop - entry) / entry * 100 if stop > entry else None
        rew = (entry - tgt) / entry * 100 if tgt < entry else None
        if not risk or not rew:
            continue
        plan_r = rew / risk
        if cap_r and plan_r > cap_r:
            # 目标太远 ⇒ 按 R 上限截断目标（更现实的可达目标）
            tgt = entry * (1 - cap_r * risk / 100.0)
            rew = (entry - tgt) / entry * 100
            plan_r = cap_r
        seg = bars[i + 1:i + 1 + FWD]
        if len(seg) < 5:
            continue
        hit_t = hit_s = None
        mfe = mae = None
        for k, b in enumerate(seg):
            p_ = (entry - b["l"]) / entry * 100
            mfe = p_ if mfe is None else max(mfe, p_)
            p2 = (entry - b["h"]) / entry * 100
            mae = p2 if mae is None else min(mae, p2)
            if hit_t is None and b["l"] <= tgt:
                hit_t = k
            if hit_s is None and b["h"] >= stop:
                hit_s = k
        if hit_t is not None and (hit_s is None or hit_t <= hit_s):
            outcome, real_r = "止盈", plan_r
        elif hit_s is not None:
            outcome, real_r = "止损", -1.0
        else:
            outcome = "未了结"
            real_r = max(-1.0, (((seg[-1]["c"] - entry) / entry * 100) / risk))
        bg_age = (i - ctx["bg_start_i"]) if ctx else None
        res.append({"sym": sym, "d": bars[i]["d"], "outcome": outcome,
                    "plan_r": plan_r, "real_r": real_r, "mfe": mfe, "mae": mae,
                    "risk": risk, "pat": "/".join(s.get("templates") or []),
                    "bg_age": bg_age, "bg": (ctx or {}).get("bg_name")})
        if outcome != "未了结":
            last_entry_i = i
    return res


def main():
    import rule123 as R
    cache = {}
    for sym, name in TICKERS:
        bars, _spot, _meta = R.bars_from_us(sym)
        lv = levels_from_bars(bars)
        hs = detect_head_shoulders_top(bars, ma50=lv.get("ma50"))
        sigs, _h, _sp = RK.scan_reversal_kline(bars, from_date=FROM, lv=lv, hs=hs, atr=lv["atr14"])
        for sg in sigs:
            sg["h"] = bars[sg["i"]]["h"]
        old = old_gate_signals(bars, FROM, lv, hs, lv["atr14"])
        for sg in old:
            sg["h"] = bars[sg["i"]]["h"]
        ctx = bearish_context(bars)
        cache[sym] = (bars, sigs, old, ctx, name)
        print("%-6s %-8s 背景:%-10s 成立 %s (破位 %.2f) ｜新口径 %2d 笔（旧 %2d）" % (
            sym, name, (ctx or {}).get("bg_name", "无"), (ctx or {}).get("bg_start_d", "-"),
            (ctx or {}).get("bg_break", 0), len(sigs), len(old)), flush=True)

    def build(rowsets, **kw):
        out = []
        for sym, (bars, sigs, old, ctx, name) in cache.items():
            ss = rowsets.get(sym)
            out += variants(bars, ss, sym, name, ctx=ctx, **kw)
        return out

    print("\n" + "=" * 108)
    print("美股做空信号真实回测（%s 起 · 入场=次日开盘 · 止损=信号棒高+0.3ATR · 目标=结构支撑T1 · 判定 %d 根）"
          % (FROM, FWD))
    print("=" * 108)
    print("%-26s %5s %7s %7s %7s %10s %10s %9s %9s %10s" % (
        "口径", "笔数", "止盈率", "止损率", "未了结", "计划平均R", "实际平均R", "平均MFE", "平均MAE", "MAE超止损"))
    newsigs = {sym: cache[sym][1] for sym in cache}
    oldsigs = {sym: cache[sym][2] for sym in cache}
    for tag, sigs in (("新口径：破MA20（现行）", newsigs), ("旧口径：破MA50/破颈线", oldsigs)):
        rows = []
        for sym, (bars, _s, _o, ctx, name) in cache.items():
            rows += evaluate(bars, sigs[sym], sym, name)
        st = agg(rows, tag)
        if not st:
            continue
        print("%-26s %5d %6.1f%% %6.1f%% %6.1f%% %10.2f %10.2f %8.1f%% %8.1f%% %9.0f%%" % (
            st["label"], st["n"], st["win_rate"], st["stop_rate"],
            100.0 * st["open"] / st["n"], st["plan_r"], st["real_r"],
            st["mfe"], st["mae"], st["mae_beyond"]))

    # ── ★ 背景年龄分层（老罗范式：背景下持续做空，刚成立时进场最好）──
    rows = build(newsigs)
    print("\n" + "=" * 108)
    print("★ 背景年龄分层（大背景=顶部形态破位成立；bg_age = 信号日 - 背景成立日）")
    print("=" * 108)
    print("%-30s %5s %8s %8s %10s %10s %9s" % (
        "分层", "笔数", "止盈率", "止损率", "计划平均R", "实际平均R", "平均MAE"))
    buckets = [("★抢跑(背景成立前)", lambda a: a is not None and a < 0),
               ("背景刚成立(0~3根)", lambda a: a is not None and 0 <= a <= 3),
               ("背景早期(4~10根)", lambda a: a is not None and 4 <= a <= 10),
               ("背景中段(11~25根)", lambda a: a is not None and 11 <= a <= 25),
               ("背景晚期(>25根)", lambda a: a is not None and a > 25),
               ("背景成立前(无背景)", lambda a: a is None)]
    for lbl, fn in buckets:
        sub = [r for r in rows if fn(r.get("bg_age"))]
        if not sub:
            continue
        st = agg(sub, lbl)
        print("%-30s %5d %7.1f%% %7.1f%% %10.2f %10.2f %8.1f%%" % (
            lbl, st["n"], st["win_rate"], st["stop_rate"], st["plan_r"], st["real_r"], st["mae"]))

    # ── 变体：止损宽度 / 同票冷却 / 目标R上限 ──
    print("\n" + "=" * 108)
    print("变体对比（止损 = 信号棒高 + mult×ATR；冷却 = 同票 N 根内不再开新仓）")
    print("=" * 108)
    print("%-40s %5s %8s %8s %10s %10s %9s" % (
        "变体", "笔数", "止盈率", "止损率", "计划平均R", "实际平均R", "平均MAE"))
    var_rows = {}
    for sm in (None, 0.6, 1.0):
        for cd in (0, 10):
            for cap in (None, 2.0):
                rr = build(newsigs, stop_mult=sm, cooldown=cd, cap_r=cap)
                lbl = "止损%.1fATR" % (sm or 0.3) + (" +冷却10根" if cd else "") + (" +目标截2R" if cap else "")
                stv = agg(rr, lbl)
                if not stv:
                    continue
                var_rows[lbl] = stv
                print("%-40s %5d %7.1f%% %7.1f%% %10.2f %10.2f %8.1f%%" % (
                    lbl, stv["n"], stv["win_rate"], stv["stop_rate"],
                    stv["plan_r"], stv["real_r"], stv["mae"]))
    if var_rows:
        best = max(var_rows.items(), key=lambda kv: kv[1]["real_r"])
        print("  ⇒ 实际平均R最优：%s（%.2f）" % (best[0], best[1]["real_r"]))


if __name__ == "__main__":
    main()
