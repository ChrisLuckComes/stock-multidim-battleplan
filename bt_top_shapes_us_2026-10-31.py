#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_top_shapes_us_2026-10-31.py —— 顶部形态识别在**美股知名股**上的检验

老罗 2026-10-31：「怀疑的话你可以去美股找几个知名股票，NVDA等」「有的见顶了，有的没见顶」。

为什么用美股验：A 股今年是普跌熊市，任何择时信号的**绝对**差都被 beta 淹没
（对照组的绝对跌幅一样大）⇒ 美股（分化大、有的见顶有的没见顶）才能测出
形态判断**自身**的判别力。

检验口径（每只票逐周取样，全部用 bars[:i+1]，**无未来函数**）：
  每个采样时点 t：
    · 该时点报出偏空顶部形态吗？（形态名 / 硬指标 block 还是 alert）
    · 之后 60 个交易日：是否**创出新高**（= 没见顶）？最大回撤多少？
  分类：
    真顶 = 之后 60 日**未创新高** 且 最大回撤 ≤ -10%
    误报 = 之后 60 日**创出新高**（那不是顶）
  对照 = 同一只票**未报形态**的时点，同口径统计（= 基础发生率）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import top_shapes as TS        # noqa: E402
import top_judgment as TJ      # noqa: E402

TICKERS = [
    ("NVDA", "英伟达"), ("AAPL", "苹果"), ("MSFT", "微软"), ("AMZN", "亚马逊"),
    ("META", "Meta"), ("GOOGL", "谷歌"), ("TSLA", "特斯拉"), ("AVGO", "博通"),
    ("PLTR", "Palantir"), ("AMD", "AMD"), ("MU", "美光"), ("NFLX", "奈飞"),
    ("COIN", "Coinbase"), ("MSTR", "Strategy"), ("SMCI", "超微"), ("ARM", "ARM"),
    ("QCOM", "高通"), ("INTC", "英特尔"), ("ORCL", "甲骨文"), ("CRM", "赛富时"),
]
FWD = 60           # 前瞻窗口（交易日）
TOP_DD = -10.0     # 真顶判据：之后 60 日最大回撤 ≤ -10%
STEP = 5           # 每 5 根取一个采样时点（逐周）


def analyze(sym, name):
    """**事件级**检验：找出每个「局部顶」，看形态判断有没有抓到它。

    局部顶 i = bars[i].h 是 [i-10, i+10] 内的最高点（摆动高点）。
    真顶   = 之后 60 日**未创新高** 且 最大回撤 ≤ -10%
    非真顶 = 之后 60 日**创出新高**（那不是顶，形态报出即误报）
    形态窗口 = [i-10, i+3]（顶部形成前后）内是否报出偏空形态（逐根用 bars[:k+1] 判断）。
    """
    import rule123 as R
    bars, _spot, _meta = R.bars_from_us(sym)
    n = len(bars)
    events = []
    for i in range(70, n - 20):
        h = bars[i]["h"]
        if h < max(b["h"] for b in bars[max(0, i - 10):i]) - 1e-9:
            continue
        if h < max(b["h"] for b in bars[i + 1:i + 11]) - 1e-9:
            continue
        after = bars[i + 1:i + 1 + FWD]
        if len(after) < 20:
            continue
        hi_after = max(b["h"] for b in after)
        dd = (min(b["l"] for b in after) / bars[i]["c"] - 1) * 100
        new_high = hi_after > h
        is_top = (not new_high) and dd <= TOP_DD
        # 形态窗口：顶部前后逐根看。
        # ⚑ 命中判定 = 形态**新形成**（signal_i 恰好落在 k）——不是「窗口内存在该形态」：
        #   形态一旦形成会一直挂在图上（摆动点未被取代前一直存在），
        #   用「存在」会把「这票历史上出现过形态」当成命中 ⇒ 灵敏度/误报率都 100%（假象）。
        hit_i, hit_pat = None, []
        for k in range(max(0, i - 10), min(n - 1, i + 3) + 1):
            try:
                shp = TS.detect_top_shapes(bars[:k + 1])
            except Exception:
                continue
            fresh = [x for x in shp
                     if x.get("signal_i") == k
                     and (x.get("bias") == "bearish" or x.get("pattern") == "菱形")]
            if fresh:
                hit_i = k
                hit_pat = sorted({x["pattern"] for x in fresh})
                break
        # 叠加闸门：窗口内是否出现「顶部硬指标已确认(block)」= 位置闸门 + 次日走弱
        blk_i = None
        for k in range(max(0, i - 10), min(n - 1, i + 3) + 1):
            try:
                vv = TJ.top_verdict(bars[:k + 1])
            except Exception:
                continue
            if vv["level"] == "block":
                blk_i = k
                break
        events.append({
            "sym": sym, "name": name, "i": i, "d": bars[i]["d"], "h": h,
            "c": bars[i]["c"], "is_top": is_top, "new_high": new_high,
            "dd": dd, "hit_i": hit_i, "hit_pat": hit_pat,
            "lead": (i - hit_i) if hit_i is not None else None,
            "blk_i": blk_i,
            "blk_lead": (i - blk_i) if blk_i is not None else None,
        })
    return events


def main():
    allev = []
    for sym, name in TICKERS:
        try:
            ev = analyze(sym, name)
        except Exception as e:
            print("%-7s 取数失败：%s" % (sym, e))
            continue
        allev.extend(ev)
        tops = [e for e in ev if e["is_top"]]
        nts = [e for e in ev if not e["is_top"]]
        hit_t = sum(1 for e in tops if e["hit_i"] is not None)
        fp_n = sum(1 for e in nts if e["hit_i"] is not None)
        print("%-7s %-8s 局部顶%3d（真顶%3d/非顶%3d）｜形态抓到真顶 %3d（灵敏度 %5.1f%%）"
              "｜非顶误报 %3d/%3d（误报率 %5.1f%%）" % (
            sym, name, len(ev), len(tops), len(nts), hit_t,
            100.0 * hit_t / max(1, len(tops)), fp_n, len(nts),
            100.0 * fp_n / max(1, len(nts))), flush=True)

    tops = [e for e in allev if e["is_top"]]
    nts = [e for e in allev if not e["is_top"]]
    hit_t = [e for e in tops if e["hit_i"] is not None]
    fp_n = [e for e in nts if e["hit_i"] is not None]
    print("\n" + "=" * 100)
    print("事件级汇总（%d 只美股 / %d 个局部顶事件：真顶 %d、非真顶 %d）"
          % (len(TICKERS), len(allev), len(tops), len(nts)))
    print("=" * 100)
    print("  灵敏度（真顶被形态抓到）：%d/%d = %.1f%%" % (
        len(hit_t), len(tops), 100.0 * len(hit_t) / max(1, len(tops))))
    print("  误报率（非真顶也报形态）：%d/%d = %.1f%%" % (
        len(fp_n), len(nts), 100.0 * len(fp_n) / max(1, len(nts))))
    print("  精确率（报形态的里是真顶的比例）：%.1f%%" % (
        100.0 * len(hit_t) / max(1, len(hit_t) + len(fp_n))))
    leads = [e["lead"] for e in hit_t if e["lead"] is not None]
    if leads:
        print("  提前量（形态报出相对顶部，根数中位）：%.0f（负=报在顶之前）" % (
            sorted(leads)[len(leads) // 2]))
    # 误报时的后续涨幅 vs 真顶时的后续跌幅
    if fp_n:
        print("  误报事件：之后 60 日平均最大涨幅 %.1f%%（继续涨）" % (
            sum(max(0, -e["dd"]) for e in fp_n) / len(fp_n)))
    if tops:
        print("  真顶事件：之后 60 日平均最大回撤 %.1f%%" % (
            sum(e["dd"] for e in tops) / len(tops)))

    # 逐形态
    print("\n" + "-" * 100)
    print("逐形态（真顶事件里，形态在顶部窗口内报出的情况）")
    print("-" * 100)
    print("%-12s %7s %10s %14s %14s" % ("形态", "抓到真顶", "占真顶比例", "平均提前根数", "非顶误报次数"))
    pat_hit, pat_fp = {}, {}
    for e in hit_t:
        for p in (e["hit_pat"] or ["(未标)"]):
            pat_hit.setdefault(p, []).append(e)
    for e in fp_n:
        for p in (e["hit_pat"] or ["(未标)"]):
            pat_fp.setdefault(p, []).append(e)
    for p, es in sorted(pat_hit.items(), key=lambda kv: -len(kv[1])):
        lds = [e["lead"] for e in es if e["lead"] is not None]
        print("%-12s %7d %9.1f%% %13.0f %14d" % (
            p, len(es), 100.0 * len(es) / max(1, len(tops)),
            sorted(lds)[len(lds) // 2] if lds else 0, len(pat_fp.get(p, []))))

    # NVDA 明细（老罗点名）
    # ── 叠加闸门后的精确率（关键：哪一组条件能区分真顶/非顶）──
    print("\n" + "-" * 100)
    print("叠加闸门后的区分度（分母：真顶 %d / 非真顶 %d）" % (len(tops), len(nts)))
    print("-" * 100)
    def stat2(key):
        ht = sum(1 for e in tops if e.get(key) is not None)
        hn = sum(1 for e in nts if e.get(key) is not None)
        prec = 100.0 * ht / max(1, ht + hn)
        base = 100.0 * len(tops) / max(1, len(tops) + len(nts))
        return ht, hn, prec, base
    for key, label in (("hit_i", "① 形态新形成（任意偏空形态）"),
                       ("blk_i", "② 形态新形成 + 硬指标block(位置闸门+次日走弱)")):
        ht, hn, prec, base = stat2(key)
        print("  %-44s 灵敏度 %5.1f%%（%d/%d）｜误报 %5.1f%%（%d/%d）｜精确率 %5.1f%%（基准 %5.1f%%）"
              % (label, 100.0 * ht / max(1, len(tops)), ht, len(tops),
                 100.0 * hn / max(1, len(nts)), hn, len(nts), prec, base))
        bl = [e["blk_lead"] for e in tops if e.get("blk_lead") is not None]
        if bl:
            print("      ② 的提前量中位：%.0f 根（负=报在顶之前）" % sorted(bl)[len(bl) // 2])

    print("\n" + "-" * 100)
    print("NVDA 局部顶明细（形态是否抓到 + 之后 60 日结果）")
    print("-" * 100)
    for e in [x for x in allev if x["sym"] == "NVDA"]:
        print("  %-10s 高%9.2f  %-6s 形态:%-16s 提前%s根  后60日回撤%7.1f%% %s" % (
            e["d"], e["h"], "真顶" if e["is_top"] else "非顶",
            "/".join(e["hit_pat"]) or "未报", e["lead"], e["dd"],
            "★抓到" if e["hit_i"] is not None else "—漏"))


if __name__ == "__main__":
    main()
