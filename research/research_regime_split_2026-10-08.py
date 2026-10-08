#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
实证：股性统计是否应该按「regime（是否处于启动/上升段）」分层？

动机（老罗 2026-10-08）：
  「三夫户外我看之前的走势，启动之后连续性还挺好的，所以不能指望回调太深」
  现 stock_character.py 的坑深/路径统计是**全样本**口径 —— 把震荡期、下跌期的大回撤
  混进「启动后」的样本，导致坑深 P75 被高估（三夫 14.47%），进而误判
  「必须等深回踩 / 禁止突破追」。

本脚本验证：把大阳信号按「信号日是否处于上升段」分层后，
回撤深度 / 延续率 / 赔率 是否系统性不同。

口径（防未来函数）：
  · 上升段判定只用 t 及之前的数据：收盘 > MA20 且 MA5 > MA10 > MA20（多头排列）
  · 前瞻观察窗口 = t+1 ~ t+HOLD（回测观察，不参与标签判定）
  · 回撤深度同时给「绝对 %」与「ATR 归一化（坑深/ATR14%）」两种口径

用法：
  python research_regime_split_2026-10-08.py [--n 500] [--seed 7] [--bars 300]
"""
import argparse
import json
import math
import os
import random
import statistics as st
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from fetch_ashare import fetch_kline  # noqa: E402

BIG = 0.05      # 大阳阈值：单日涨幅 >= 5%
HOLD = 5        # 前瞻观察窗口
CONT = 0.03     # 延续判定：后 HOLD 日最高摸到 +3%


def ma(v, i, n):
    if i + 1 < n:
        return None
    return sum(v[i + 1 - n:i + 1]) / n


def atr(bars, i, n=14):
    """bars: [{'d','o','c','h','l','v'}]，返回 ATR14（简单均值口径）"""
    if i < n:
        return None
    trs = []
    for k in range(i - n + 1, i + 1):
        h, l, pc = bars[k]["h"], bars[k]["l"], bars[k - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def load_universe():
    for p in (os.path.join(_HERE, 'scan_all', 'codes.json'),
              os.path.join(os.path.dirname(_HERE), 'scan_all', 'codes.json')):
        if os.path.exists(p):
            return json.load(open(p, encoding='utf-8'))
    raise SystemExit('找不到 scan_all/codes.json')


def is_uptrend(bars, i):
    """上升段：收盘 > MA20 且 MA5 > MA10 > MA20（多头排列）。只用 t 及之前数据。"""
    c = [b["c"] for b in bars]
    m5, m10, m20 = ma(c, i, 5), ma(c, i, 10), ma(c, i, 20)
    if m5 is None or m10 is None or m20 is None:
        return False
    return c[i] > m20 and m5 > m10 > m20


def analyze(code, bars):
    """返回该票的大阳信号列表：dict(regime, depth, depth_atr, up, cont, pos5, broke_ma10)

    信号定义（宽口径，便于分层对比）：单日涨幅 >= 5%，且非一字板。
    """
    bars = sorted(bars, key=lambda b: b["d"])
    c = [b["c"] for b in bars]
    out = []
    for i in range(20, len(bars) - HOLD):
        prev = c[i - 1]
        if prev <= 0:
            continue
        if (c[i] - prev) / prev < BIG:
            continue
        if bars[i]["l"] == bars[i]["h"]:        # 一字板，排除
            continue
        fwd = bars[i + 1:i + 1 + HOLD]
        if len(fwd) < HOLD:
            continue
        base = c[i]
        hi = max(r["h"] for r in fwd)
        lo = min(r["l"] for r in fwd)
        a = atr(bars, i)
        depth = (base - lo) / base                               # 绝对回撤
        depth_atr = (depth * base) / a if a and a > 0 else None  # ATR 归一化
        # 回踩落点：后 HOLD 日内「最低价触及 / 收盘跌破」各条均线
        broke_ma10 = 0
        touch = {5: 0, 10: 0, 20: 0}      # 盘中最低价触及该均线
        cbreak = {5: 0, 10: 0, 20: 0}     # 收盘跌破该均线
        for k in range(HOLD):
            j = i + 1 + k
            for nn in (5, 10, 20):
                m = ma(c[:j + 1], j, nn)
                if m is None:
                    continue
                if bars[j]["l"] <= m:
                    touch[nn] = 1
                if bars[j]["c"] < m:
                    cbreak[nn] = 1
            m10 = ma(c[:j + 1], j, 10)
            if m10 is not None and bars[j]["c"] < m10:
                broke_ma10 = 1
        out.append({
            "code": code,
            "date": bars[i]["d"],
            "up": (hi - base) / base,
            "depth": depth,
            "depth_atr": depth_atr,
            "cont": 1 if (hi - base) / base >= CONT else 0,
            "pos5": 1 if fwd[-1]["c"] > base else 0,
            "broke_ma10": broke_ma10,
            "touch_ma5": touch[5], "touch_ma10": touch[10], "touch_ma20": touch[20],
            "cbreak_ma5": cbreak[5], "cbreak_ma10": cbreak[10], "cbreak_ma20": cbreak[20],
            "regime": "up" if is_uptrend(bars, i) else "other",
        })
    return out


def pct(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    k = min(int(len(s) * p), len(s) - 1)
    return s[k]


def summarize(sigs, label):
    if not sigs:
        print(f"\n[{label}] 无样本")
        return None
    ups = [s["up"] for s in sigs]
    dep = [s["depth"] for s in sigs]
    dat = [s["depth_atr"] for s in sigs if s["depth_atr"] is not None]
    cont = [s["cont"] for s in sigs]
    pos5 = [s["pos5"] for s in sigs]
    brk = [s["broke_ma10"] for s in sigs]
    t5 = [s["touch_ma5"] for s in sigs]
    t10 = [s["touch_ma10"] for s in sigs]
    t20 = [s["touch_ma20"] for s in sigs]
    cb10 = [s["cbreak_ma10"] for s in sigs]
    cb20 = [s["cbreak_ma20"] for s in sigs]
    r = {
        "label": label, "n": len(sigs),
        "touch_ma5": st.mean(t5), "touch_ma10": st.mean(t10), "touch_ma20": st.mean(t20),
        "cbreak_ma10": st.mean(cb10), "cbreak_ma20": st.mean(cb20),
        "up_mean": st.mean(ups),
        "depth_mean": st.mean(dep),
        "depth_med": st.median(dep),
        "depth_p75": pct(dep, 0.75),
        "depth_atr_med": st.median(dat) if dat else None,
        "depth_atr_p75": pct(dat, 0.75) if dat else None,
        "payoff": st.mean(ups) / max(st.mean(dep), 1e-9),
        "cont": st.mean(cont),
        "pos5": st.mean(pos5),
        "broke_ma10": st.mean(brk),
    }
    print(f"\n=== [{label}] n={r['n']} ===")
    print(f"  后{HOLD}日上行均值     +{r['up_mean']*100:.2f}%")
    print(f"  回撤深度 均值 {r['depth_mean']*100:.2f}%  中位 {r['depth_med']*100:.2f}%  P75 {r['depth_p75']*100:.2f}%")
    if r["depth_atr_med"] is not None:
        print(f"  回撤(ATR归一化) 中位 {r['depth_atr_med']:.2f}×ATR  P75 {r['depth_atr_p75']:.2f}×ATR")
    print(f"  赔率 上/下      {r['payoff']:.2f}")
    print(f"  延续率(摸到+3%) {r['cont']*100:.0f}%    后{HOLD}日收盘为正 {r['pos5']*100:.0f}%")
    print(f"  ★回踩落点：盘中触及MA5 {r['touch_ma5']*100:.0f}% / MA10 {r['touch_ma10']*100:.0f}% / MA20 {r['touch_ma20']*100:.0f}%")
    print(f"           收盘跌破MA10 {r['cbreak_ma10']*100:.0f}% / MA20 {r['cbreak_ma20']*100:.0f}%")
    return r


def welch_t(a, b):
    """Welch t 检验（不依赖 scipy）"""
    if len(a) < 2 or len(b) < 2:
        return None, None
    ma_, mb = st.mean(a), st.mean(b)
    va, vb = st.variance(a), st.variance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    if se == 0:
        return None, None
    t = (ma_ - mb) / se
    return t, t  # 自由度近似，p 值用 |t|>1.96 粗判（大样本）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500, help="抽样股票数")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--bars", type=int, default=300)
    ap.add_argument("--out", default="data/cache/research_regime_split.json")
    a = ap.parse_args()

    uni = load_universe()
    uni = [u for u in uni if u.get("c") and not (u.get("name") or "").startswith(("*", "S"))]
    uni = [u for u in uni if "退" not in (u.get("name") or "") and "ST" not in (u.get("name") or "")]
    random.seed(a.seed)
    sample = random.sample(uni, min(a.n, len(uni)))
    print(f"全市场 {len(uni)} 只 → 抽样 {len(sample)} 只，每只 {a.bars} 根日线")

    all_sigs = []
    ok = 0
    for idx, u in enumerate(sample, 1):
        code = u["c"]
        try:
            bars = fetch_kline(code, n=a.bars)
        except Exception:
            continue
        if not bars or len(bars) < 60:
            continue
        ok += 1
        all_sigs.extend(analyze(code, bars))
        if idx % 50 == 0:
            print(f"  ...{idx}/{len(sample)} 已取 {ok} 只，信号 {len(all_sigs)} 个", flush=True)

    print(f"\n有效股票 {ok} 只，大阳信号 {len(all_sigs)} 个")
    up = [s for s in all_sigs if s["regime"] == "up"]
    other = [s for s in all_sigs if s["regime"] == "other"]

    r_all = summarize(all_sigs, "全样本（现状口径）")
    r_up = summarize(up, "上升段子样本（多头排列+收盘>MA20）")
    r_ot = summarize(other, "其余（震荡/下跌段）")

    # 差异检验
    print("\n" + "=" * 66)
    print("【差异检验】上升段 vs 其余")
    if r_up and r_ot and r_all:
        for key, name, unit in (("depth_med", "回撤中位", "%"),
                                ("depth_p75", "回撤P75", "%"),
                                ("depth_atr_med", "回撤ATR中位", "×ATR"),
                                ("cont", "延续率", "%"),
                                ("payoff", "赔率", ""),
                                ("touch_ma5", "触及MA5", "%"),
                                ("touch_ma10", "触及MA10", "%"),
                                ("touch_ma20", "触及MA20", "%"),
                                ("cbreak_ma10", "收盘破MA10", "%"),
                                ("cbreak_ma20", "收盘破MA20", "%"),
                                ("broke_ma10", "跌破MA10率", "%")):
            vu, vo = r_up[key], r_ot[key]
            if vu is None or vo is None:
                continue
            sc = 100 if unit == "%" else 1
            print(f"  {name:<12} 上升段 {vu*sc:7.2f}{unit}  vs  其余 {vo*sc:7.2f}{unit}   差 {(vu-vo)*sc:+.2f}{unit}")

        du = [s["depth"] for s in up]
        do = [s["depth"] for s in other]
        t, _ = welch_t(du, do)
        if t is not None:
            print(f"\n  回撤深度 Welch t = {t:+.2f}  （|t|>1.96 ≈ p<0.05）")
        cu = [s["cont"] for s in up]
        co = [s["cont"] for s in other]
        t2, _ = welch_t(cu, co)
        if t2 is not None:
            print(f"  延续率   Welch t = {t2:+.2f}")

    # 时间分段（样本外粗验）：按信号日期前后半分
    dated = sorted(all_sigs, key=lambda s: s["date"])
    half = len(dated) // 2
    print("\n" + "=" * 66)
    print("【时间分段稳健性】前半 / 后半")
    summarize(dated[:half], f"前半 {dated[0]['date']} ~ {dated[max(half-1,0)]['date']}")
    summarize(dated[half:], f"后半 {dated[half]['date']} ~ {dated[-1]['date']}")

    out = {
        "meta": {"n_stocks": ok, "n_sigs": len(all_sigs), "big": BIG, "hold": HOLD,
                 "universe": len(uni), "seed": a.seed},
        "all": r_all, "up": r_up, "other": r_ot,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n已写 {a.out}")


if __name__ == "__main__":
    main()
