#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S1+S2 落地后的验证：用合成「建仓 → 拉升 → 派发」序列，检查新主力层能否识别清仓。

对照组 = 旧逻辑（rho>0 才更新，流出日冻结），实现见本文件 chip_model_old()。
真值：40 元建仓约 187.5 万股 → 50 元每日净卖 15 万股 ⇒ 第 12~13 个派发日清仓完毕。

用法：python bt_main_layer_v2_2026-10-31.py
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")
import scr_chip as S


def make_case():
    """40 元建仓(15d, rho=+0.10) → 拉到 50(5d, rho=+0.05) → 50 元派发(20d, rho=-0.15)。"""
    bars, rho = [], {}
    def add(d, px, shares, turn):
        bars.append({"d": d, "o": px, "h": px * 1.005, "l": px * 0.995, "c": px,
                     "v": shares / 100.0, "amt": px * shares, "turnover": turn})
    for i in range(15):
        k = "D1%02d" % i; add(k, 40.0, 1_000_000, 2.0); rho[k] = 0.10
    for i in range(5):
        k = "D2%02d" % i; add(k, 40.0 + 2.5 * (i + 1), 1_500_000, 3.0); rho[k] = 0.05
    for i in range(20):
        k = "D3%02d" % i; add(k, 50.0, 1_000_000, 2.0); rho[k] = -0.15
    return bars, rho


def chip_model_old(bars, main_rho=None, decay_k=1.0, main_decay=0.999):
    """旧逻辑复刻：rho>0 才更新 m_dist（流出日既不减仓也不老化）。"""
    lo = min(b["l"] for b in bars); hi = max(b["h"] for b in bars)
    span = max(hi - lo, hi * 0.001, 1e-6)
    n_bins = min(1500, max(200, int(span / 0.01) + 1)); w = span / n_bins
    prices = [lo + (i + 0.5) * w for i in range(n_bins)]
    m = [0.0] * n_bins
    trace = []
    for b in bars:
        r = min(max(b["turnover"] / 100.0, 0.0), 1.0)
        vol = b["v"] * 100.0
        tri = [0.0] * n_bins
        vwap = b["amt"] / vol if vol > 0 else (b["l"] + b["h"]) / 2
        peak = min(max(vwap, b["l"] + 1e-9), b["h"] - 1e-9)
        i0 = max(int((b["l"] - lo) / w), 0); i1 = min(int((b["h"] - lo) / w), n_bins - 1)
        tot = 0.0
        for i in range(i0, i1 + 1):
            x = prices[i]
            wt = (x - b["l"]) / (peak - b["l"]) if x <= peak else (b["h"] - x) / (b["h"] - peak)
            if wt > 0:
                tri[i] = wt; tot += wt
        if tot > 0:
            for i in range(i0, i1 + 1):
                tri[i] = tri[i] * vol / tot
        decay = max(0.0, 1.0 - r * decay_k)
        rho = float((main_rho or {}).get(b["d"], 0.0) or 0.0)
        if rho > 0:
            for i in range(n_bins):
                m[i] = m[i] * decay + tri[i] * rho
        trace.append(sum(m))
    return prices, m, trace


def main():
    bars, rho = make_case()
    prices, dist, snaps, w, miss, m_dist, meta = S.chip_model(
        bars, main_rho=rho, decay_k=1.0, return_meta=True)
    old_prices, old_m, old_tr = chip_model_old(bars, main_rho=rho, decay_k=1.0)

    def cost_of(p, m):
        t = sum(m)
        return (sum(a * b for a, b in zip(p, m)) / t) if t > 0 else None

    print("=" * 66)
    print("合成序列：40 元建仓 187.5 万股 → 50 元每日净卖 15 万股 → 真值 12~13 日清仓")
    print("=" * 66)
    mc_new = cost_of(prices, m_dist)
    mc_old = cost_of(old_prices, old_m)
    print("\n【期末对比】")
    print("  新模型(S1+S2) 主力存量=%.4g 股  主力成本=%s"
          % (meta["hold"], ("%.2f" % mc_new) if mc_new else "无(已清仓)"))
    print("  旧模型(冻结)  主力存量=%.4g 股  主力成本=%s"
          % (sum(old_m), ("%.2f" % mc_old) if mc_old else "无"))

    base_new = meta["trace"][19]           # 派发开始前一日的存量
    base_old = old_tr[19]
    print("\n【派发期存量轨迹】(基准 = 派发前一日)")
    print("  日  | 新模型剩余 | 旧模型剩余")
    cleared = None
    for k in range(20):
        vn = meta["trace"][20 + k]
        vo = old_tr[20 + k]
        pn = vn / base_new * 100 if base_new > 0 else 0
        po = vo / base_old * 100 if base_old > 0 else 0
        if cleared is None and pn < 5:
            cleared = k + 1
        if k % 2 == 0 or (cleared and k == cleared - 1):
            print("  %2d  |  %8.1f%%  |  %8.1f%%%s"
                  % (k + 1, pn, po, "   ← 新模型识别清仓" if cleared == k + 1 else ""))

    print("\n【判定】")
    ok = cleared is not None and 11 <= cleared <= 14
    print("  新模型清仓识别日: %s（真值 12~13）⇒ %s"
          % (cleared, "✅ 通过" if ok else "❌ 未命中"))
    print("  旧模型期末剩余: %.1f%%（完全不识别派发）⇒ %s"
          % (old_tr[-1] / base_old * 100,
             "✅ 已修复对派发的盲区" if (old_tr[-1] / base_old * 100) > 90 else "—"))

    # 主力阶段判定（analyze 层逻辑的最小复刻）
    keep = meta["hold"] / meta["peak"] if meta["peak"] > 0 else None
    chg5 = ((meta["trace"][-1] - meta["trace"][-6]) / meta["trace"][-6]
            if meta["trace"][-6] > 0 else None)
    print("\n【阶段判定字段】(analyze 用同口径)")
    print("  main_keep_ratio=%.2f%%  main_chg5=%s"
          % ((keep or 0) * 100, ("%.1f%%" % (chg5 * 100)) if chg5 is not None else "—"))
    phase = ("已清仓" if keep is not None and keep < 0.05 else
             ("减仓中" if chg5 is not None and chg5 <= -0.15 else
              ("加仓中" if chg5 is not None and chg5 >= 0.15 else "锁仓/持平")))
    print("  ⇒ main_phase=%s" % phase)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
