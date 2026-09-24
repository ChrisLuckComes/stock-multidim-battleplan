#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""股性体检（stock_character）

买票之前，先摸这只票的「脾气」—— 它是**突破之后能连续上涨**（加速型），
还是**拉一根大阳就要调几天**（消化型 / 震荡上涨）？

为什么要做（来源：睿创微纳 688002，trade-lessons L002，2026-09-24）：
    同一个「突破」信号，在不同标的上的期望值可以相反。
    - **加速型** ⇒ 突破类买法（平台突破 / 过前高）有效；
    - **消化型** ⇒ 突破当日追入是负期望，买点必须放回踩（MA10 / MA20）。
    老罗原话：「买票之前需要观察历史 K 线摸到股性 —— 有没有突破后连续上涨的，
    还是拉一根要调几天、震荡上涨。」本脚本把这句话变成可执行的一步。

判据：取历史**单日涨幅 ≥ BIG%** 的大阳，**以大阳当日收盘价作为买点**（= 突破追入），
     统计其后 HOLD 个交易日：
         赔率 odds = 后 HOLD 日最高价均值 ÷ |后 HOLD 日最低价均值|
         ≥ 1.5（且延续率 ≥ 50%）  ⇒ **突破加速型**，可做突破买
         < 1.0（或延续率 < 35%）   ⇒ **拉高消化型**，只做回踩买
         其余                       ⇒ **混合型**，优先回踩

用法：
    python stock_character.py 688002
    python stock_character.py 688002 --n 250 --big 5 --hold 5
    python stock_character.py 688002 --json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bars_source  # noqa: E402

BIG_DEFAULT = 5.0        # 大阳阈值（单日涨幅 %）
HOLD_DEFAULT = 5         # 后续观察交易日数
ODDS_BREAKOUT = 1.5      # 赔率 ≥ 此值 → 加速型
ODDS_GRIND = 1.0         # 赔率 < 此值 → 消化型
CONT_BREAKOUT = 0.50     # 延续率 ≥ 此值 → 加速型
CONT_GRIND = 0.35        # 延续率 < 此值 → 消化型

_BAR_KEYS = (("d", "date"), ("o", "open"), ("h", "high"), ("l", "low"),
             ("c", "close"), ("v", "volume"))


def prefix_of(code):
    code = str(code)
    if code.startswith(("6", "9")):
        return "sh"
    return "sz"


def load_bars(code, n=300):
    bars, _src, _notes = bars_source.ash_bars(prefix_of(code), str(code), n=n)
    return normalize(bars)


def normalize(bars):
    """统一成 [{d,o,h,l,c,v}]，兼容 dict 与 list/tuple 两种来源格式。"""
    out = []
    for b in bars or []:
        if isinstance(b, dict):
            out.append({k: b.get(k, b.get(alt)) for k, alt in _BAR_KEYS})
        elif isinstance(b, (list, tuple)):
            out.append(dict(zip([k for k, _ in _BAR_KEYS], b)))
        else:
            raise TypeError("不认识的 bar 类型：%s" % type(b))
    return out


def ma(bars, i, n):
    if i + 1 < n:
        return None
    return sum(x["c"] for x in bars[i + 1 - n:i + 1]) / n


def atr(bars, i, n=14):
    if i < n:
        return None
    trs = []
    for j in range(i + 1 - n, i + 1):
        pc = bars[j - 1]["c"]
        trs.append(max(bars[j]["h"] - bars[j]["l"],
                       abs(bars[j]["h"] - pc),
                       abs(bars[j]["l"] - pc)))
    return sum(trs) / len(trs)


def max_up_streak(bars):
    best = cur = 0
    for i in range(1, len(bars)):
        if bars[i]["c"] > bars[i - 1]["c"]:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def analyze(bars, big=BIG_DEFAULT, hold=HOLD_DEFAULT):
    if len(bars) < 60:
        return None
    last = len(bars) - 1
    close = bars[last]["c"]

    # —— 大阳后行为（以「大阳日收盘」为买点 = 突破追入） ——
    big_idx = [i for i in range(1, len(bars))
               if (bars[i]["c"] / bars[i - 1]["c"] - 1) * 100 >= big]
    h5, l5, c5, c10 = [], [], [], []
    cont = shock = 0
    for i in big_idx:
        if i + hold >= len(bars):
            continue
        c0 = bars[i]["c"]
        fwd = bars[i + 1:i + 1 + hold]
        hh = max(x["h"] for x in fwd)
        ll = min(x["l"] for x in fwd)
        h5.append((hh / c0 - 1) * 100)
        l5.append((ll / c0 - 1) * 100)
        c5.append((fwd[-1]["c"] / c0 - 1) * 100)
        if hh >= c0 * 1.03:
            cont += 1
        if ll <= c0 * 0.97:
            shock += 1
        if i + 10 < len(bars):
            c10.append((bars[i + 10]["c"] / c0 - 1) * 100)

    n_s = len(h5)
    a = {
        "code": None, "n_bars": len(bars),
        "span": "%s ~ %s" % (bars[0]["d"], bars[last]["d"]),
        "close": close, "ma10": ma(bars, last, 10), "ma20": ma(bars, last, 20),
        "big_thresh": big, "hold": hold, "big_count": n_s,
        "atr_pct": (atr(bars, last) / close * 100) if atr(bars, last) else None,
        "max_up_streak": max_up_streak(bars),
    }
    if n_s:
        a.update({
            "h_mean": mean(h5), "l_mean": mean(l5),
            "odds": (mean(h5) / abs(mean(l5))) if mean(l5) else None,
            "c_mean": mean(c5),
            "cont_rate": cont / n_s, "shock_rate": shock / n_s,
            "win5": sum(1 for x in c5 if x > 0) / n_s,
            "win10": (sum(1 for x in c10 if x > 0) / len(c10)) if c10 else None,
        })
    else:
        a.update({"h_mean": None, "l_mean": None, "odds": None, "c_mean": None,
                  "cont_rate": None, "shock_rate": None, "win5": None, "win10": None})

    klass, label, advice = classify(a)
    a.update({"class": klass, "class_label": label, "advice": advice})
    return a


def classify(a):
    odds, cont, shock = a.get("odds"), a.get("cont_rate"), a.get("shock_rate")
    if odds is None:
        return "unknown", "样本不足", "大阳样本不足，改用更长历史或降低 --big 阈值后再判"
    if odds >= ODDS_BREAKOUT and (cont or 0) >= CONT_BREAKOUT:
        return ("breakout", "突破加速型",
                "突破后能延续 —— 可用突破类买法（平台突破 / 过前高）；回踩买同样有效")
    if odds < ODDS_GRIND or (cont or 0) < CONT_GRIND or (shock or 0) > 0.7:
        return ("grind", "拉高消化型（震荡上涨）",
                "大阳后横盘/回落概率高 —— 突破当日禁止追入，买点只放回踩（MA10 / MA20）")
    return ("mixed", "混合型",
            "延续性一般 —— 优先回踩买；若做突破买，止损必须收紧、仓位减半")


def render(a):
    def pf(x):
        return "   n/a" if x is None else "%+7.2f%%" % x

    def pn(x):
        return "  n/a" if x is None else "%.2f" % x

    L = []
    L.append("")
    L.append("股性体检 · %s" % a["code"])
    L.append("样本：%d 个交易日（%s）   现价 %.2f"
             % (a["n_bars"], a["span"], a["close"]))
    L.append("-" * 58)
    L.append("一、突破后行为（单日涨幅 >= %.0f%% 的大阳 %d 根，以大阳当日收盘为买点 = 突破追入）"
             % (a["big_thresh"], a["big_count"]))
    if a["big_count"]:
        odds_s = "n/a" if a["odds"] is None else "%.2f" % a["odds"]
        win5 = "n/a" if a["win5"] is None else "%.0f%%" % (a["win5"] * 100)
        win10 = "n/a" if a["win10"] is None else "%.0f%%" % (a["win10"] * 100)
        L.append("    后%d日最高价均值      %s" % (a["hold"], pf(a["h_mean"])))
        L.append("    后%d日最低价均值      %s" % (a["hold"], pf(a["l_mean"])))
        L.append("    赔率（上/下）         %-6s  <- >= %.1f 加速型 / < %.1f 消化型"
                 % (odds_s, ODDS_BREAKOUT, ODDS_GRIND))
        L.append("    延续率（后%d日摸到 +3%%）  %5.0f%%" % (a["hold"], a["cont_rate"] * 100))
        L.append("    冲击率（后%d日摸到 -3%%）  %5.0f%%" % (a["hold"], a["shock_rate"] * 100))
        L.append("    收盘为正  后%d日 %-5s   后10日 %s" % (a["hold"], win5, win10))
    else:
        L.append("    （无大阳样本）")
    L.append("-" * 58)
    L.append("二、波动")
    L.append("    ATR14 / 现价   %-8s      最长连涨 %d 个交易日"
             % (("n/a" if a["atr_pct"] is None else "%.2f%%" % a["atr_pct"]),
                a["max_up_streak"]))
    L.append("    MA10 %s     MA20 %s" % (pn(a["ma10"]), pn(a["ma20"])))
    L.append("-" * 58)
    L.append("三、股性判定：%s" % a["class_label"])
    L.append("    %s" % a["advice"])
    L.append("-" * 58)
    L.append("提示：股性判据决定「用哪种买法」，不决定「买不买」。以上不构成投资建议。")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="股性体检：突破加速型 vs 拉高消化型")
    ap.add_argument("code", help="6 位代码，如 688002")
    ap.add_argument("--n", type=int, default=300, help="取的日线根数（默认 300，约 14 个月）")
    ap.add_argument("--big", type=float, default=BIG_DEFAULT, help="大阳阈值 %%（默认 5）")
    ap.add_argument("--hold", type=int, default=HOLD_DEFAULT, help="后续观察交易日数（默认 5）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    bars = load_bars(args.code, n=args.n)
    if not bars:
        print("取不到 %s 的日线数据" % args.code)
        return 2
    a = analyze(bars, big=args.big, hold=args.hold)
    if not a:
        print("%s 日线样本不足（< 60 根）" % args.code)
        return 2
    a["code"] = str(args.code)

    if args.json:
        print(json.dumps(a, ensure_ascii=False, indent=2))
    else:
        print(render(a))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
