#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_us_short_calib_2026-10-31.py —— 美股做空阈值**校准**（30 只样本，含稳健性检验）

老罗 2026-10-31：「先做3（阈值校准）再做2（写进默认）……以后我会这样问 或者定时跑
然后直接出作战方案 点位 盈亏比 推荐/不推荐」。

要校准的四个维度（网格）：
  ① 背景过滤：全部信号 / 仅「背景下」信号（bg_age≥0，即形态破位之后的波段内）
  ② 止损宽度：信号棒高 + mult×ATR（mult ∈ 0.2/0.3/0.45/0.6）
  ③ 目标 R 上限：结构支撑 T1 原样 / 截断到 1.5R / 2R / 3R
  ④ 同票冷却：同一票 N 根内不再开新仓（0/5/10/15）

防过拟合设计：
  · 样本分**前后半**（按 ticker 排序切分），看两半的最优参数是否一致；
  · 每只票单独统计（看结论是否靠个别票撑着）；
  · 输出样本数，低样本组合一律标注「样本不足」。

口径：入场=信号棒**次日开盘**（真实成交）；判定窗口 40 根；先触目标=止盈 / 先触止损=止损。
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

# ⚑ 样本池（老罗 2026-10-31：「用我的标的测试」）：
#   **核心池 = 他的固定做空标的**（gates/us_short_watchlist.json，五只）
#   **同链扩展池 = 存储/半导体链同股性票**（波动率、节奏同源，用来把样本量补到可校准）
#   ⚠ **不再放大盘科技**（MSFT/NFLX/NXPI/AMZN…）——股性不同（低波动、慢节奏），
#     在它们上面校出的阈值套到存储链上是错的。
CORE = ["SNDK", "MU", "SKHY", "SOXX", "LITE"]          # ★ 他的固定标的（核心）
CHAIN = ["WDC", "STX", "TER", "MRVL", "AMD", "INTC", "ARM",   # 同链扩展（补样本量）
         "SMCI", "NVDA", "ON", "AVGO", "PHLX", "COHR"]
TICKERS = CORE + CHAIN
CACHE = os.path.join(HERE, "data", "us_bars_cache_20261031.json")
FWD = 40
MIN_SAMPLE = 12          # 组合样本数下限（低于此标注「样本不足」）


def _fetch_one(sym, timeout=40):
    """单票取数（带超时；卡住就放弃该票，不拖死整轮）。"""
    import threading
    box = {}

    def _work():
        try:
            import rule123 as R
            bars, _s, _m = R.bars_from_us(sym)
            box["bars"] = [{"d": b["d"], "o": b["o"], "h": b["h"], "l": b["l"],
                            "c": b["c"], "v": b.get("v", 0)} for b in bars]
        except Exception as e:
            box["err"] = str(e)[:60]

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, "取数超时(%ds)" % timeout
    if "bars" in box:
        return box["bars"], None
    return None, box.get("err", "未知错误")


def fetch_all(skip_fetch=False):
    """取数并**增量**落盘（取到一只存一只；卡住的票下次复跑自动补上）。

    skip_fetch=True ⇒ 只读缓存、不联网（缓存缺失的票直接跳过）。
    """
    data = {}
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            data = json.load(f)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    for sym in TICKERS:
        if sym in data and len(data[sym]) >= 60:
            continue
        if skip_fetch:
            continue
        bars, err = _fetch_one(sym)
        if bars:
            data[sym] = bars
            with open(CACHE, "w", encoding="utf-8") as f:
                json.dump(data, f)
            print("  fetched %-6s %d 根" % (sym, len(bars)), flush=True)
        else:
            print("  ! %-6s %s（跳过）" % (sym, err), flush=True)
    return data


def character(bars):
    """★ **股性指标**（老罗 2026-10-31：股性很重要 ⇒ 阈值必须分层校准，不能一刀切）。

    四项：
      atr_pct  = ATR14 / 收盘            —— 波动幅度（决定止损宽度与目标距离）
      vol20    = 近20日收益率标准差×100  —— 波动率
      amt      = 近20日日均成交额(美元)   —— 流动性（决定滑点/跳空风险）
      trend20  = 近20日累计收益(%)       —— 背景方向（下跌趋势中做空才顺）
    返回 dict；bars 太短返回 None。
    """
    n = len(bars)
    if n < 25:
        return None
    closes = [b["c"] for b in bars]
    trs = []
    for i in range(1, n):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr14 = sum(trs[-14:]) / min(14, len(trs))
    rets = [(closes[i] / closes[i - 1] - 1.0) for i in range(1, n)]
    r20 = rets[-20:]
    m = sum(r20) / len(r20)
    sd = (sum((x - m) ** 2 for x in r20) / max(1, len(r20) - 1)) ** 0.5
    amt = sum(b["c"] * (b.get("v") or 0) for b in bars[-20:]) / len(bars[-20:])
    return {"atr_pct": atr14 / closes[-1] * 100.0 if closes[-1] else 0.0,
            "vol20": sd * 100.0,
            "amt": amt,
            "trend20": (closes[-1] / closes[-20] - 1.0) * 100.0}


def prep(sym, bars):
    """算信号 + 背景（每只票一次）。"""
    lv = levels_from_bars(bars)
    hs = detect_head_shoulders_top(bars, ma50=lv.get("ma50"))
    # ⚑ 扫描区间：**用可得的最早数据**（不看固定起点）——
    #   校准需要覆盖完整的"见顶→波段下跌"过程，起点太晚会漏掉最早的信号。
    sigs, _h, _supp = RK.scan_reversal_kline(bars, from_date=None,
                                              lv=lv, hs=hs, atr=lv["atr14"])
    ctx = bearish_context(bars)
    for s in sigs:
        s["h"] = bars[s["i"]]["h"]
        s["atr"] = (s["stop"] - s["h"]) / 0.30 if "h" in s else None
    return sigs, ctx, lv


def run(bars, sigs, stop_mult, cap_r, cooldown, ctx_only, bars_all, ctx_cache=None):
    """按参数组合回测一只票，返回逐笔结果列表。

    ⚑ **无未来函数**：`ctx` 必须用**信号时点**的 bars[:i+1] 计算背景，
      否则背景成立日可能晚于信号日 ⇒ 「背景下/抢跑」分组会偷看未来
      （老罗 2026-10-31 校准前发现的口径漏洞）。
      ctx_cache 缓存每个 i 的背景结果，避免重复计算。
    """
    res = []
    n = len(bars_all)
    last_i = -99
    for s in sigs:
        i = s["i"]
        if i + 1 >= n:
            continue
        ctx = None
        if ctx_cache is not None:
            if i not in ctx_cache:
                ctx_cache[i] = bearish_context(bars_all[:i + 1])
            ctx = ctx_cache[i]
        else:
            ctx = bearish_context(bars_all[:i + 1])
        bg_age = (i - ctx["start_i"]) if ctx else None
        if ctx_only and (bg_age is None or bg_age < 0):
            continue
        if cooldown and (i - last_i) < cooldown:
            continue
        entry = bars_all[i + 1]["o"]
        atr = s.get("atr")
        if atr is None or atr <= 0:
            continue
        stop = s["h"] + stop_mult * atr
        tgt = s["target"]
        if stop <= entry or tgt >= entry:
            continue
        risk = (stop - entry) / entry * 100
        rew = (entry - tgt) / entry * 100
        plan_r = rew / risk
        if cap_r and plan_r > cap_r:
            tgt = entry * (1 - cap_r * risk / 100.0)
            rew = (entry - tgt) / entry * 100
            plan_r = cap_r
        seg = bars_all[i + 1:i + 1 + FWD]
        if len(seg) < 10:
            continue
        hit_t = hit_s = None
        for k, b in enumerate(seg):
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
            real_r = max(-1.0, ((seg[-1]["c"] - entry) / entry * 100) / risk)
        res.append({"sym": s.get("_sym"), "d": s["date"], "outcome": outcome,
                    "plan_r": plan_r, "real_r": real_r, "bg_age": bg_age,
                    "ctx_name": (ctx or {}).get("name"),
                    "ctx_fallback": bool((ctx or {}).get("fallback")),
                    "mfe": max((entry - b["l"]) / entry * 100 for b in seg),
                    "mae": min((entry - b["h"]) / entry * 100 for b in seg)})
        if outcome != "未了结":
            last_i = i
    return res


WINSOR = 3.0    # ★ 单笔实际R 截尾上限（±3R）——防单笔暴赚/巨亏主导均值


def agg(rows):
    """聚合统计。⚑ **必须看稳健口径**：

    老罗 2026-10-31 校准实测踩到：MRVL 单笔实际R = **+72.5**（目标位极远，
    恰逢暴跌）——单笔就把全样本均值抬爆，**直接把参数排序带偏**
    （"目标不截断"因此虚高，掩盖了"目标截 2R"才是真结论）。
    ⇒ 一律用**截尾均值**（±3R 截尾）作主指标，另给中位数/去极值均值。
    """
    if not rows:
        return {"n": 0, "wr": 0.0, "rr": 0.0, "sr": 0.0, "med": 0.0, "rr_raw": 0.0}
    n = len(rows)
    win = sum(1 for r in rows if r["outcome"] == "止盈")
    raws = [r["real_r"] for r in rows]
    clip = [max(-WINSOR, min(WINSOR, x)) for x in raws]
    srt = sorted(raws)
    med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2.0
    return {"n": n, "wr": 100.0 * win / n,
            "rr": sum(clip) / n,                      # ★ 截尾均值（主指标）
            "rr_raw": sum(raws) / n,                  # 原始均值（对照，会被极值带偏）
            "med": med,                               # 中位数
            "sr": 100.0 * sum(1 for r in rows if r["outcome"] == "止损") / n}


def main():
    use_cache = "--use-cache" in sys.argv
    if use_cache:
        print("用本地缓存（--use-cache，不联网）…", flush=True)
        data = fetch_all(skip_fetch=True)
    else:
        print("取数（本地缓存优先）…", flush=True)
        data = fetch_all()
    prepped = {}
    chars = {}
    for sym, bars in data.items():
        try:
            sigs, ctx, lv = prep(sym, bars)
            for sg in sigs:
                sg["_sym"] = sym
            prepped[sym] = (bars, sigs, ctx, {})
            chars[sym] = character(bars)
        except Exception as e:
            print("  ! %s 预处理失败 %s" % (sym, e))
    print("可算信号：%d 只（核心池 %d / 链池 %d）"
          % (len(prepped), len([x for x in prepped if x in CORE]),
             len([x for x in prepped if x in CHAIN])))

    # ── 股性表 ──
    print("\n" + "=" * 100)
    print("股性画像（核心池 = ★他的固定标的；链池 = 同存储/半导体链，股性相近补样本）")
    print("=" * 100)
    print("%-8s %-6s %8s %8s %14s %9s  %s" % (
        "标的", "池", "ATR%", "vol20", "日均成交额", "20日趋势", "备注"))
    for sym in sorted(chars, key=lambda x: (x not in CORE, x)):
        c = chars[sym]
        if not c:
            continue
        tag = "★核心" if sym in CORE else "链池"
        print("%-8s %-6s %7.2f%% %7.2f%% %13.0f %8.1f%%  %s" % (
            sym, tag, c["atr_pct"], c["vol20"], c["amt"], c["trend20"],
            "高波动" if c["atr_pct"] > 3.5 else ("低波动" if c["atr_pct"] < 2.0 else "中波动")))

    GRID = []
    for ctx_only in (False, True):
        for sm in (0.2, 0.3, 0.45, 0.6):
            for cap in (None, 1.5, 2.0, 3.0):
                for cd in (0, 5, 10, 15):
                    GRID.append((ctx_only, sm, cap, cd))

    def run_all(syms, **kw):
        rows = []
        for sym in syms:
            bars, sigs, _c, ccache = prepped[sym]
            rows += run(bars, sigs, bars_all=bars, ctx_cache=ccache, **kw)
        return rows

    syms = sorted(prepped)
    half = len(syms) // 2
    A, B = syms[:half], syms[half:]
    core_syms = [x for x in syms if x in CORE]

    results = []
    for (ctx_only, sm, cap, cd) in GRID:
        rows = run_all(syms, stop_mult=sm, cap_r=cap, cooldown=cd, ctx_only=ctx_only)
        st = agg(rows)
        ra = agg(run_all(A, stop_mult=sm, cap_r=cap, cooldown=cd, ctx_only=ctx_only))
        rb = agg(run_all(B, stop_mult=sm, cap_r=cap, cooldown=cd, ctx_only=ctx_only))
        rcore = agg(run_all(core_syms, stop_mult=sm, cap_r=cap, cooldown=cd,
                            ctx_only=ctx_only))
        results.append({"ctx_only": ctx_only, "sm": sm, "cap": cap, "cd": cd,
                        "full": st, "A": ra, "B": rb, "core": rcore})
    results.sort(key=lambda r: -r["full"]["rr"])

    def show(r):
        cons = ""
        if r["A"]["n"] >= 6 and r["B"]["n"] >= 6:
            cons = "一致(同向)" if (r["A"]["rr"] > 0) == (r["B"]["rr"] > 0) else "⚠不一致"
        flag = "" if r["full"]["n"] >= MIN_SAMPLE else " ⚠样本不足"
        return ("%-6s %-8s %-6s %-5d | %5d %7.1f%% %8.2f %8.2f | %8.2f %8.2f | %6.2f %s%s" % (
            "仅背景" if r["ctx_only"] else "全部", "%.2fATR" % r["sm"],
            ("%.1fR" % r["cap"]) if r["cap"] else "原样", r["cd"],
            r["full"]["n"], r["full"]["wr"], r["full"]["rr"], r["full"]["med"],
            r["A"]["rr"], r["B"]["rr"], r["core"]["rr"], cons, flag))

    print("\n" + "=" * 112)
    print("网格结果（全 %d 只 = 核心%d + 链池%d；A组=%d只/B组=%d只；样本<%d 标注不足）"
          % (len(syms), len(core_syms), len(syms) - len(core_syms), len(A), len(B), MIN_SAMPLE))
    print("=" * 112)
    print("%-6s %-8s %-6s %-5s | %5s %7s %8s %8s | %8s %8s | %6s %s" % (
        "背景", "止损", "目标R", "冷却", "样本", "止盈率", "截尾R", "中位R",
        "A组R", "B组R", "核心池R", "一致性"))
    for r in results[:20]:
        print(show(r))

    best = results[0]
    print("\n" + "-" * 112)
    print("★ 全样本最优：%s｜止损 %.2fATR｜目标 %s｜冷却 %d 根 → 实际R %.2f"
          "（止盈率 %.0f%%，样本 %d）"
          % ("仅背景" if best["ctx_only"] else "全部信号", best["sm"],
             ("%.1fR" % best["cap"]) if best["cap"] else "原样", best["cd"],
             best["full"]["rr"], best["full"]["wr"], best["full"]["n"]))
    print("  A组(前半) 截尾R %.2f（n=%d）｜B组(后半) 截尾R %.2f（n=%d）｜**核心池** 截尾R %.2f（n=%d）"
          % (best["A"]["rr"], best["A"]["n"], best["B"]["rr"], best["B"]["n"],
             best["core"]["rr"], best["core"]["n"]))
    print("  ⚑ 稳健性对照：原始均值 %.2f（被极值带偏）／中位数 %.2f（更保守）"
          % (best["full"]["rr_raw"], best["full"]["med"]))

    # ── ★ 股性分层：波动率高的票 vs 低的票，最优参数是否不同 ──
    print("\n" + "=" * 112)
    print("★ 股性分层（按 ATR% 分档；同一参数在高低波动票上的表现差异 ⇒ 阈值要不要分档）")
    print("=" * 112)
    atrs = sorted(chars[x]["atr_pct"] for x in syms if chars.get(x))
    med_atr = atrs[len(atrs) // 2] if atrs else 0.0
    hi = [x for x in syms if chars.get(x) and chars[x]["atr_pct"] >= med_atr]
    lo = [x for x in syms if chars.get(x) and chars[x]["atr_pct"] < med_atr]
    print("⚑ 按**中位 ATR%%=%.2f** 分档（原来用绝对阈值3.0 ⇒ 17 只全落高档，分层失效）" % med_atr)
    print("高波动档(ATR%%≥%.2f)：%s" % (med_atr, "、".join(hi)))
    print("低波动档(ATR%%<%.2f)：%s\n" % (med_atr, "、".join(lo)))
    print("%-6s %-8s %-6s %-5s | %-18s | %-18s" % (
        "背景", "止损", "目标R", "冷却", "高波动档(样本/实际R)", "中低波动档(样本/实际R)"))
    for (ctx_only, sm, cap, cd) in GRID:
        rh = agg(run_all(hi, stop_mult=sm, cap_r=cap, cooldown=cd, ctx_only=ctx_only))
        rl = agg(run_all(lo, stop_mult=sm, cap_r=cap, cooldown=cd, ctx_only=ctx_only))
        if rh["n"] < 8 or rl["n"] < 8:
            continue
        print("%-6s %-8s %-6s %-5d | %6d / %+8.2f    | %6d / %+8.2f" % (
            "仅背景" if ctx_only else "全部", "%.2fATR" % sm,
            ("%.1fR" % cap) if cap else "原样", cd, rh["n"], rh["rr"], rl["n"], rl["rr"]))

    # ── 逐票（最优参数下）──
    print("\n  逐票（最优参数下；★=核心池）：")
    rows = run_all(syms, stop_mult=best["sm"], cap_r=best["cap"],
                   cooldown=best["cd"], ctx_only=best["ctx_only"])
    per = {}
    for r in rows:
        per.setdefault(r["sym"], []).append(r)
    pos = 0
    for sym in sorted(per, key=lambda x: (x not in CORE, x)):
        st = agg(per[sym])
        if st["n"]:
            pos += 1 if st["rr"] > 0 else 0
        c = chars.get(sym) or {}
        print("    %-6s %s n=%2d 止盈率%5.1f%% 截尾R%+6.2f 中位R%+6.2f  ATR%%=%.2f"
              % (sym, "★" if sym in CORE else " ", st["n"], st["wr"], st["rr"],
                 st["med"], c.get("atr_pct", 0)))
    print("  ⇒ %d/%d 只在该参数下为正期望（样本小，仅看方向一致性）" % (pos, len(per)))

    # ── 背景年龄分层（时点化口径）──
    print("\n  背景年龄分层（时点口径，验证「越晚进场越差」）：")
    buckets = [("★抢跑(背景未成立)", lambda a: a is not None and a < 0),
               ("背景刚成立(0~3根)", lambda a: a is not None and 0 <= a <= 3),
               ("背景早期(4~10根)", lambda a: a is not None and 4 <= a <= 10),
               ("背景中段(11~25根)", lambda a: a is not None and 11 <= a <= 25),
               ("背景晚期(>25根)", lambda a: a is not None and a > 25)]
    for lbl, fn in buckets:
        sub = [r for r in rows if fn(r.get("bg_age"))]
        if not sub:
            continue
        st = agg(sub)
        print("    %-22s n=%3d 止盈率%5.1f%% 实际R%+6.2f" % (lbl, st["n"], st["wr"], st["rr"]))


if __name__ == "__main__":
    main()
