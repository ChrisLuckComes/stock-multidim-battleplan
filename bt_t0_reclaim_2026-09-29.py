# -*- coding: utf-8 -*-
"""回测 T0（ma_reclaim_break 出水芙蓉）向上档买点：引擎能否给出正确的买入计划。

老罗 2026-09-29 追问：「回测一下T0级别的数据」。

背景：2026-09-29 修了 `rule123.targets()` 的突破目标口径（近端墙 → 能给 R>=1.5
的连续判据），飙股回测合格率 0% → 100%。但 **`ma_reclaim_break`（T0）不在
`BREAKOUT_MODES` 里**：

    BREAKOUT_MODES = ("platform_break", "w_bottom_break",
                      "flag_tl_break", "downtrend_tl_break")

⇒ T0 走 targets() 的 **else 分支 `t1 = min(cands)`** = 仍是近端墙旧口径，
  且 T0 自己另有一套 `rr_near_wall`（近端墙赔率）。**本回测就是要量出：
  T0 是不是也被同一堵近端墙压死了。**

无未来函数（实证方法论第 1 条）：
  - trigger / 止损 / 目标 / R 一律只用【D0 信号日及之前】的数据（base = bars[:i+1]）；
  - 成交与结果才用 D1 及之后的 K 线验证。

对照两组目标口径：
  旧口径 = 引擎 `near_wall`（trigger 上方最近的一道墙）
  新口径 = 能给到 R>=1.5 的【最近真阻力】（与突破类同一条连续判据）

用法：python bt_t0_reclaim_2026-09-29.py [--limit 30]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rule123 as R  # noqa: E402

CACHE = "data/cache"
HOLD_DAYS = 10
RR_GATE = getattr(R, "RR_GATE", 1.5)


def pivots_up(bars, w=3):
    """简单摆动高点（左右各 w 根内最高）。"""
    out = []
    for i in range(w, len(bars) - w):
        h = bars[i]["h"]
        if all(bars[j]["h"] <= h for j in range(i - w, i + w + 1)):
            out.append((i, h))
    return out


def atr14(bars):
    return R.atr14(bars, 14) if hasattr(R, "atr14") else None


def new_target(base, trigger, hard, atr_v):
    """新口径：能给 R>=RR_GATE 的最近真阻力（与突破类同一连续判据）。

    取不到才回落到 space_open（远景），并如实标记。
    """
    risk = trigger - hard
    if risk <= 0:
        return None, False, None
    door = trigger + 0.5 * atr_v
    pv = pivots_up(base)
    resist = sorted({h for _i, h in pv if h > door})
    if not resist:
        return round(door + 6.0 * atr_v, 2), True, None
    need = trigger + RR_GATE * risk
    wall = next((h for h in resist if h >= need), None)
    if wall is not None:
        return round(wall, 2), False, round(wall, 2)
    return round(door + 6.0 * atr_v, 2), True, None


def simulate(fut, entry, stop, target):
    """成交后逐根判定：先到目标 / 先到止损 / 持有到期。返回 (结果, 天数)。"""
    for k, x in enumerate(fut, start=1):
        if x["l"] <= stop:
            return "被止损", k
        if target and x["h"] >= target:
            return "到目标", k
    return "持有到期", len(fut)


def run(limit=30):
    files = sorted(f for f in os.listdir(CACHE) if f.startswith("cn_") and f.endswith(".json"))
    rows = []
    n_sig = 0
    for fn in files:
        code = fn[3:-5]
        try:
            obj = json.load(open(os.path.join(CACHE, fn), encoding="utf-8"))
            bars = obj.get("bars") if isinstance(obj, dict) else obj
        except Exception:
            continue
        if not bars or len(bars) < 60:
            continue
        ds = [x["d"] for x in bars]
        # 逐日滚动：D0 = i，验证窗口 = i+1 .. i+HOLD_DAYS
        for i in range(40, len(bars) - HOLD_DAYS - 1):
            base = bars[: i + 1]
            last_c = base[-1]["c"]
            a = atr14(base)
            if not a or a <= 0:
                continue
            try:
                sig = R.ma_reclaim_break(base, {}, a, last_c)
            except Exception:
                sig = None
            if not sig:
                continue
            n_sig += 1
            trigger = sig.get("trigger")
            hard = sig.get("hard_stop")
            if not trigger or not hard or trigger <= hard:
                continue
            risk = trigger - hard
            near_wall = sig.get("near_wall")
            rr_old = sig.get("rr_near_wall")
            if rr_old is None and near_wall:
                rr_old = round((near_wall - trigger) / risk, 2)
            t_new, sp, _ = new_target(base, trigger, hard, a)
            rr_new = round((t_new - trigger) / risk, 2) if t_new else None

            fut = bars[i + 1: i + 1 + HOLD_DAYS]
            filled = any(x["h"] >= trigger for x in fut)
            # 成交后：入场价 = 触发价（挂单等触发），止损/目标不变
            res, day, pnl, mae, hit_wall = "未触发", None, None, None, None
            if filled:
                post = []
                seen = False
                for x in fut:
                    if not seen and x["h"] >= trigger:
                        seen = True
                    if seen:
                        post.append(x)
                res, day = simulate(post, trigger, hard, t_new if not sp else None)
                pnl = round((post[-1]["c"] - trigger) / trigger * 100, 1)
                mae = round((min(x["l"] for x in post) - trigger) / trigger * 100, 1)
                # ★ 事后有没有真的越过「第一道墙」near_wall。
                #   老罗定的「越贴墙越好」是胜率口径，R>=1.5 是赔率口径，
                #   两者打架 —— 用「墙到底有没有被吃掉」来裁决。
                if near_wall:
                    hit_wall = any(x["h"] >= near_wall for x in post)

            rows.append(dict(code=code, d0=ds[i], trigger=round(trigger, 2),
                             hard=round(hard, 2), risk=round(risk, 2),
                             risk_pct=round(risk / trigger * 100, 2),
                             wall=round(near_wall, 2) if near_wall else None,
                             t_new=t_new, rr_old=rr_old, rr_new=rr_new, sp=sp,
                             filled=filled, res=res, day=day, pnl=pnl, mae=mae,
                             hit_wall=hit_wall,
                             anchor=sig.get("stop_anchor"),
                             grade=sig.get("grade"),
                             ride=sig.get("ride_state")))

    n = len(rows)
    print("=" * 118)
    print("T0（ma_reclaim_break 出水芙蓉）向上档买点回测 · 基准=%s" % (ds[-1] if ds else ""))
    print("扫描 %d 只本地缓存 → T0 信号 %d 个，可算档位 %d 个" % (len(files), n_sig, n))
    print("=" * 118)

    if not n:
        print("无样本")
        return

    # ---- 核心：两口径合格率 ----
    ok_old = sum(1 for r in rows if r["rr_old"] is not None and r["rr_old"] >= RR_GATE)
    ok_new = sum(1 for r in rows if r["rr_new"] is not None and r["rr_new"] >= RR_GATE)
    filled_n = sum(1 for r in rows if r["filled"])
    print()
    print("【核心】向上档 R>=%.1f 合格率" % RR_GATE)
    print("  旧口径（引擎 near_wall 近端墙）: %5.1f%%  (%d/%d)" % (100 * ok_old / n, ok_old, n))
    print("  新口径（能给 R>=%.1f 的最近真阻力）: %5.1f%%  (%d/%d)" % (
        RR_GATE, 100 * ok_new / n, ok_new, n))
    print("  触发价事后可成交率: %5.1f%%  (%d/%d)" % (100 * filled_n / n, filled_n, n))
    print("  止损宽度中位: %.2f%%   风险/ATR 中位: %.2f" % (
        sorted(r["risk_pct"] for r in rows)[n // 2],
        sorted(r["risk"] / (r["risk_pct"] / 100 * r["trigger"]) for r in rows)[n // 2]
        if n else 0))

    # ---- 目标构成 ----
    sp_n = sum(1 for r in rows if r["sp"])
    print()
    print("  目标口径构成：真阻力 %d 个 / 远景(space_open) %d 个" % (n - sp_n, sp_n))

    # ---- 事后表现（新口径合格的那批）----
    good = [r for r in rows if r["rr_new"] is not None and r["rr_new"] >= RR_GATE]
    if good:
        import collections
        ct = collections.Counter(r["res"] for r in good)
        gf = [r for r in good if r["filled"]]
        pn = [r["pnl"] for r in gf if r["pnl"] is not None]
        win = sum(1 for p in pn if p > 0)
        print()
        print("【新口径合格组】n=%d → %s" % (len(good), dict(ct)))
        if pn:
            print("  10 日收益：正 %d / 负 %d，中位 %+.1f%%，均值 %+.1f%%" % (
                win, len(pn) - win, sorted(pn)[len(pn) // 2], sum(pn) / len(pn)))

    # ---- 贴墙 vs 半路 ----
    print()
    print("【分组】grade / ride_state")
    for key in ("grade", "ride"):
        import collections
        g = collections.defaultdict(lambda: [0, 0, 0])
        for r in rows:
            k = r.get(key)
            g[k][0] += 1
            if r["rr_old"] is not None and r["rr_old"] >= RR_GATE:
                g[k][1] += 1
            if r["rr_new"] is not None and r["rr_new"] >= RR_GATE:
                g[k][2] += 1
        for k, (tot, o, nw) in sorted(g.items(), key=lambda x: -x[1][0]):
            print("  %-12s n=%-5d 旧合格 %5.1f%%  新合格 %5.1f%%" % (
                str(k), tot, 100 * o / tot, 100 * nw / tot))

    # ---- ★ 深度分层：贴墙组是不是被旧口径系统性误杀 ----
    print()
    print("=" * 118)
    print("【深度分层 A】★ 老罗定的「越贴墙越好」（0~2% 胜率 50%）vs 旧口径合格率")
    print("-" * 118)
    import collections
    for g in ("贴墙", "半路"):
        sub = [r for r in rows if r.get("grade") == g]
        if not sub:
            continue
        o = sum(1 for r in sub if r["rr_old"] is not None and r["rr_old"] >= RR_GATE)
        nw = sum(1 for r in sub if r["rr_new"] is not None and r["rr_new"] >= RR_GATE)
        fz = [r for r in sub if r["filled"]]
        ct = collections.Counter(r["res"] for r in sub)
        pn = [r["pnl"] for r in fz if r["pnl"] is not None]
        w = sum(1 for p in pn if p > 0)
        print("  %-4s n=%-6d 旧合格 %5.1f%%  新合格 %5.1f%%  可成交 %5.1f%%" % (
            g, len(sub), 100 * o / len(sub), 100 * nw / len(sub), 100 * len(fz) / len(sub)))
        print("       事后: %s" % dict(ct))
        if pn:
            print("       10日收益 正%d/负%d 中位%+.1f%% 均值%+.1f%%" % (
                w, len(pn) - w, sorted(pn)[len(pn) // 2], sum(pn) / len(pn)))

    # ---- ★ 深度分层 B：止损宽度 vs 被止损率（验证「止损太窄」是不是主死因）----
    print()
    print("【深度分层 B】止损宽度 vs 被止损率（新口径全合格，看事后谁活下来）")
    print("-" * 118)
    buckets = [(0, 2), (2, 3), (3, 5), (5, 8), (8, 100)]
    print("  %-12s %6s %8s %8s %10s %10s" % ("止损宽度", "n", "被止损率", "到目标率", "收益中位", "收益均值"))
    for lo, hi in buckets:
        sub = [r for r in rows if lo <= r["risk_pct"] < hi]
        if len(sub) < 20:
            continue
        st = sum(1 for r in sub if r["res"] == "被止损")
        tg = sum(1 for r in sub if r["res"] == "到目标")
        pn = [r["pnl"] for r in sub if r["pnl"] is not None]
        if not pn:
            continue
        print("  %-12s %6d %7.1f%% %7.1f%% %9.1f%% %9.1f%%" % (
            "%g~%g%%" % (lo, hi), len(sub), 100 * st / len(sub), 100 * tg / len(sub),
            sorted(pn)[len(pn) // 2], sum(pn) / len(pn)))

    # ---- ★ 深度分层 C：真阻力 vs 远景（验证「远景不作买点依据」）----
    print()
    print("【深度分层 C】真阻力目标 vs 远景(space_open) 目标 —— 事后谁靠谱")
    print("-" * 118)
    for lbl, cond in (("真阻力", False), ("远景", True)):
        sub = [r for r in rows if r["sp"] is cond]
        if not sub:
            continue
        fz = [r for r in sub if r["filled"]]
        ct = collections.Counter(r["res"] for r in sub)
        pn = [r["pnl"] for r in fz if r["pnl"] is not None]
        if not pn:
            continue
        w = sum(1 for p in pn if p > 0)
        print("  %-6s n=%-6d 事后: %s" % (lbl, len(sub), dict(ct)))
        print("         10日收益 正%d/负%d 中位%+.1f%% 均值%+.1f%%" % (
            w, len(pn) - w, sorted(pn)[len(pn) // 2], sum(pn) / len(pn)))

    # ---- ★ 深度分层 D：第一道墙到底有没有被吃掉（裁决「贴墙该不该做」）----
    print()
    print("【深度分层 D】★ 事后 10 日内，触发价上方的「第一道墙」有没有被吃掉")
    print("   （裁决：老罗「越贴墙越好」= 胜率口径 vs R>=1.5 = 赔率口径）")
    print("-" * 118)
    for g in ("贴墙", "半路"):
        sub = [r for r in rows if r.get("grade") == g and r["filled"]]
        hw = [r for r in sub if r["hit_wall"] is not None]
        if not hw:
            continue
        hit = sum(1 for r in hw if r["hit_wall"])
        print("  %-4s n=%-6d 墙被吃掉 %5.1f%% (%d)  未吃掉 %5.1f%%" % (
            g, len(hw), 100 * hit / len(hw), hit, 100 * (len(hw) - hit) / len(hw)))
        for lbl, cond in (("吃掉墙的", True), ("没吃掉的", False)):
            s2 = [r for r in hw if r["hit_wall"] is cond]
            pn = [r["pnl"] for r in s2 if r["pnl"] is not None]
            if not pn:
                continue
            w = sum(1 for p in pn if p > 0)
            print("        %s n=%-6d 10日收益 正%d/负%d 中位%+.1f%% 均值%+.1f%%" % (
                lbl, len(s2), w, len(pn) - w,
                sorted(pn)[len(pn) // 2], sum(pn) / len(pn)))

    # ---- 明细 ----
    print()
    print("-" * 118)
    print("%-8s %-11s %8s %8s %8s %8s %8s %8s %7s %6s  %s" % (
        "代码", "D0", "触发价", "止损", "旧目标", "新目标", "R旧", "R新", "10日收益", "远景", "结果"))
    show = sorted(rows, key=lambda r: r["d0"], reverse=True)[:limit]
    for r in show:
        print("%-8s %-11s %8.2f %8.2f %8s %8s %8s %8s %7s %6s  %s" % (
            r["code"], r["d0"], r["trigger"], r["hard"],
            ("%.2f" % r["wall"]) if r["wall"] else "-",
            ("%.2f" % r["t_new"]) if r["t_new"] else "-",
            ("%.2f" % r["rr_old"]) if r["rr_old"] is not None else "-",
            ("%.2f" % r["rr_new"]) if r["rr_new"] is not None else "-",
            ("%+.1f%%" % r["pnl"]) if r["pnl"] is not None else "-",
            "远景" if r["sp"] else "真阻力",
            ("%s(T+%s)" % (r["res"], r["day"])) if r["day"] is not None else r["res"]))
    print("-" * 118)


if __name__ == "__main__":
    lim = 30
    if "--limit" in sys.argv:
        try:
            lim = int(sys.argv[sys.argv.index("--limit") + 1])
        except Exception:
            pass
    run(lim)
