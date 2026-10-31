# -*- coding: utf-8 -*-
"""A/B 验证：现有主力筹码层 vs 修正逻辑（合成序列，纯计算，不依赖网络）。

背景（2026-10-31 老罗："筹码面一直是错误的，影响买卖决策"）：
  代码级根因定位到两处串联缺陷：
    ① `_parse_fflow` 用 max(0.0, p[6]) ⇒ 主力净流出被清零，负值信息在数据层就丢失；
    ② `chip_model` 用 `if rho > 0` 才更新 m_dist ⇒ 流出日主力分布**既不减仓也不衰减**。
  合起来：模型在数学上只能看到"主力买入"，永远看不到"主力卖出" ⇒ 天生判"吸筹/锁仓"。

本脚本用一段合成的"建仓→拉升→派发"行情做 A/B：
  真值：主力 40 元建仓 ~187.5 万股，50 元每日净卖 15 万股 ⇒ 约第 12~13 个派发日清仓完毕。
  期望：现有模型应完全识别不到派发；修正逻辑应能在存量曲线上体现清仓。

修正逻辑（拟落地）：
  m_dist 每日先按 main_decay 基础老化（不论方向）；
  rho > 0：按当日三角分布加仓 tri*rho；
  rho < 0：按「净卖出量 / 当前存量」比例整体削减（主力卖的是既有持仓，不是当日新筹码）。

运行：python bt_main_layer_2026-10-31.py
"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scr_chip as S


def make_case():
    """40元建仓(15d, rho=+0.10) → 拉到50(5d, rho=+0.05) → 50元派发(20d, rho=-0.15)"""
    bars, rho = [], {}
    def add(d, px, shares, turn):
        bars.append({"d": d, "o": px, "h": px * 1.005, "l": px * 0.995, "c": px,
                     "v": shares / 100.0, "amt": px * shares, "turnover": turn})
    for i in range(15):
        add("D1%02d" % i, 40.0, 1_000_000, 2.0); rho["D1%02d" % i] = 0.10
    for i in range(5):
        add("D2%02d" % i, 40.0 + 2.5 * (i + 1), 1_500_000, 3.0); rho["D2%02d" % i] = 0.05
    for i in range(20):
        add("D3%02d" % i, 50.0, 1_000_000, 2.0); rho["D3%02d" % i] = -0.15
    return bars, rho


def chip_model_fixed(bars, rho_map, main_decay=0.999):
    """复刻修正逻辑（只算主力层）。"""
    lo = min(b["l"] for b in bars); hi = max(b["h"] for b in bars)
    span = max(hi - lo, hi * 0.001, 1e-6)
    n_bins = min(1500, max(200, int(span / 0.01) + 1))
    w = span / n_bins
    prices = [lo + (i + 0.5) * w for i in range(n_bins)]
    m = [0.0] * n_bins
    trace = []
    for b in bars:
        vol = b["v"] * 100.0
        l, h = b["l"], b["h"]
        tri = [0.0] * n_bins
        vwap = b["amt"] / vol if vol > 0 else (l + h) / 2
        peak = min(max(vwap, l + 1e-9), h - 1e-9)
        i0 = max(int((l - lo) / w), 0); i1 = min(int((h - lo) / w), n_bins - 1)
        tot_w = 0.0
        for i in range(i0, i1 + 1):
            x = prices[i]
            wt = (x - l) / (peak - l) if x <= peak else (h - x) / (h - peak)
            if wt > 0:
                tri[i] = wt; tot_w += wt
        if tot_w > 0:
            for i in range(i0, i1 + 1):
                tri[i] = tri[i] * vol / tot_w
        rho_v = float(rho_map.get(b["d"], 0.0) or 0.0)
        for i in range(n_bins):                       # 每日基础老化
            m[i] *= main_decay
        if rho_v > 0:
            for i in range(n_bins):
                m[i] += tri[i] * rho_v
        elif rho_v < 0:
            cur = sum(m)
            if cur > 0:
                cut = min(abs(rho_v) * vol / cur, 0.95)
                for i in range(n_bins):
                    m[i] *= (1 - cut)
        trace.append(sum(m))
    return prices, m, trace


def main():
    bars, rho = make_case()

    # ── 现有模型 ──
    prices, dist, snaps, w, miss, m_dist = S.chip_model(bars, main_rho=rho, decay_k=1.0)
    tot = sum(m_dist)
    mc = sum(p * mm for p, mm in zip(prices, m_dist)) / tot if tot > 0 else None
    print("=" * 66)
    print("现有模型（rho<=0 时 m_dist 冻结：不减仓、不衰减）")
    print("=" * 66)
    print("  主力成本 main_cost = %s" % ("%.2f" % mc if mc else "None"))
    print("  期末主力存量 mtot   = %.6g 股" % tot)
    print("  真值：40 元建仓 ≈187.5 万股，50 元每日净卖 15 万股 ⇒ 约第 12~13 派发日清仓")
    print("  ⇒ 存量应大幅下降；若 mtot 基本未变，说明模型完全看不到派发")

    # ── 修正逻辑 ──
    p2, m2, tr = chip_model_fixed(bars, rho)
    t2 = sum(m2)
    mc2 = sum(p * mm for p, mm in zip(p2, m2)) / t2 if t2 > 0 else None
    print()
    print("=" * 66)
    print("修正逻辑（每日老化 + 流入加仓 + 流出按存量比例减仓）")
    print("=" * 66)
    print("  主力成本 main_cost = %s" % ("%.2f" % mc2 if mc2 else "已清仓"))
    print("  期末主力存量 mtot   = %.6g 股" % t2)
    base = tr[19]                       # 派发开始前的存量
    print()
    print("  派发阶段主力存量轨迹（基准 100%% = %.4g 股）：" % base)
    cleared = None
    for k in range(20):
        v = tr[20 + k]
        pct = v / base * 100 if base > 0 else 0
        if cleared is None and pct < 2.0:
            cleared = k + 1
        if k % 3 == 0 or (cleared is not None and k + 1 in (cleared,)):
            mark = "  ← 视为清仓" if (cleared is not None and k + 1 == cleared) else ""
            print("    派发第 %2d 日  mtot = %.4g  (剩 %5.1f%%)%s" % (k + 1, v, pct, mark))
    print()
    print("  判定：修正逻辑%s识别出清仓（第 %s 派发日）"
          % ("能" if cleared else "未能", cleared if cleared else "—"))


if __name__ == "__main__":
    main()
