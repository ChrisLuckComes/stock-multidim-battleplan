# -*- coding: utf-8 -*-
"""「V型（短调续攻）vs N型（画N字·深调再上）」实证（2026-09-26）

老罗的问题：有的股短调一下就上，有的喜欢画 N 字。① 怎么提前判断一只票属于哪种？
② 「次日买入」是不是适合它？

方法（严格无未来函数）：
  信号日 T（T 收盘可见）：涨幅 ≥+5% 且 量比 ≥1.5× 且 收盘 > MA20。
  路径定义（前瞻 T+1..T+10，只用最低价/最高价）：
    H0 = T 日最高价；C0 = T 日收盘价
    max_dd = min_k (low_k - C0)/C0      ← 从 T 收盘起算的最大回撤
    break  = 是否存在 k 使 high_k > H0   ← 是否突破信号日高点
    FAIL：10 日内未突破 H0
    V 型：突破 且 回撤 ≤ 3%（浅调就走）
    N 型：突破 且 回撤 > 3%（先挖坑再上）
  预测变量（全部 T 日可见，habit 只用「已结算」的历史信号 ⇒ signal_idx+10 < T）：
    乖离MA20 / 20日涨幅 / 60日涨幅 / RVOL / ATR% / 距60日高 / 60日分位 / habit(历史V型占比)
  买法对比（回答「次日买入合不合适」）：
    A 次日开盘买：T+1 开盘价成交，持有到 T+5 收盘
    B 挂 −2%：T+1..T+5 最低 ≤ 0.98*C0 成交，成交价 0.98*C0
    C 挂 −5%：成交价 0.95*C0
    指标：成交率 / 成交后到 T+5 收益 / 信号级期望 = 成交率 × 成交后期望
"""
import os
import sys
import json
import math
import random

_HERE = os.path.dirname(os.path.abspath(__file__))
_UP = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_HERE, 'fetch'), _UP, os.path.join(_UP, 'fetch')):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
from fetch_ashare import fetch_kline

SEED = 20260926
N_STOCK = int(os.environ.get("N_STOCK", "400"))
BARS = 300
WIN = 10          # 路径观察窗口
HOLD = 5          # 买法持有到 T+5 收盘
DD_SPLIT = -3.0   # 回撤深度分界（%）：≤-3% 记 N 型


def ma(v, n):
    return sum(v[-n:]) / n if len(v) >= n else None


def stats(x):
    n = len(x)
    if n == 0:
        return 0, 0.0, 0.0
    m = sum(x) / n
    if n < 2:
        return n, m, 0.0
    var = sum((a - m) ** 2 for a in x) / (n - 1)
    sd = math.sqrt(var)
    return n, m, (m / (sd / math.sqrt(n)) if sd > 0 else 0.0)


def load_universe():
    p = os.path.join(_HERE, 'scan_all', 'codes.json')
    if not os.path.isfile(p):
        p = os.path.join(_UP, 'scan_all', 'codes.json')
    d = json.load(open(p, encoding='utf-8'))
    d = [x for x in d if x.get('name') and 'ST' not in x['name'] and '退' not in x['name']]
    random.seed(SEED)
    random.shuffle(d)
    return d[:N_STOCK]


def analyze(code):
    """返回该票的信号列表（按时间序），每个信号含类型与 T 日可见特征"""
    bars = fetch_kline(code, n=BARS)
    if not bars or len(bars) < 130:
        return []
    bars.sort(key=lambda b: b['d'])
    c = [b['c'] for b in bars]
    h = [b['h'] for b in bars]
    lo = [b['l'] for b in bars]
    o = [b['o'] for b in bars]
    v = [b['v'] for b in bars]
    n = len(c)
    sigs = []
    for t in range(60, n - WIN - 1):
        if c[t - 1] <= 0:
            continue
        chg = (c[t] - c[t - 1]) / c[t - 1] * 100
        if chg < 5.0:
            continue
        if lo[t] == h[t]:            # 一字板，排除
            continue
        v5 = sum(v[t - 5:t]) / 5.0
        if v5 <= 0:
            continue
        rvol = v[t] / v5
        if rvol < 1.5:
            continue
        m20 = ma(c[:t + 1], 20)
        if m20 is None or c[t] <= m20:
            continue
        # ---- 前瞻路径（未来信息，仅用于打标签，不进特征）----
        C0, H0 = c[t], h[t]
        max_dd = 0.0
        t_break = None
        for k in range(1, WIN + 1):
            dd = (lo[t + k] - C0) / C0 * 100
            if dd < max_dd:
                max_dd = dd
            if t_break is None and h[t + k] > H0:
                t_break = k
        if t_break is None:
            typ = 'FAIL'
        elif max_dd <= DD_SPLIT:
            typ = 'N'
        else:
            typ = 'V'
        # ---- 买法收益 ----
        buy = {}
        # A: T+1 开盘买
        if t + HOLD < n:
            buy['A_open'] = (1.0, (c[t + HOLD] - o[t + 1]) / o[t + 1] * 100)
        # B/C: 回踩挂单
        for tag, px in (('B_m2', 0.98), ('C_m5', 0.95)):
            filled = False
            for k in range(1, HOLD + 1):
                if lo[t + k] <= C0 * px:
                    filled = True
                    break
            if filled and t + HOLD < n:
                buy[tag] = (1.0, (c[t + HOLD] - C0 * px) / (C0 * px) * 100)
            else:
                buy[tag] = (0.0, 0.0)
        # ---- T 日可见特征 ----
        atrs = []
        for i in range(t - 13, t + 1):
            if i <= 0:
                continue
            atrs.append(max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])))
        atrpct = (sum(atrs) / len(atrs) / C0 * 100) if atrs else 0.0
        m5 = ma(c[:t + 1], 5)
        h60 = max(h[t - 59:t + 1])
        pct60 = (C0 - min(c[t - 59:t + 1])) / max(1e-9, (max(c[t - 59:t + 1]) - min(c[t - 59:t + 1]))) * 100
        sigs.append({
            'i': t, 'date': bars[t]['d'], 'typ': typ, 'dd': max_dd, 'tbreak': t_break,
            'bias20': (C0 - m20) / m20 * 100,
            'r20': (C0 / c[t - 20] - 1) * 100 if t >= 20 else 0.0,
            'r60': (C0 / c[t - 60] - 1) * 100 if t >= 60 else 0.0,
            'rvol': rvol, 'atrpct': atrpct,
            'from_h60': (C0 / h60 - 1) * 100,
            'pct60': pct60,
            'buy': buy,
        })
    return sigs


def main():
    uni = load_universe()
    print(f"样本：{len(uni)} 只（seed {SEED}，剔除 ST/退市）  参数：涨≥5% 量比≥1.5 站MA20  窗口 T+1..T+{WIN}")
    all_sigs = []          # (code, sig)
    for j, it in enumerate(uni, 1):
        try:
            s = analyze(it['c'])
        except Exception:
            s = []
        all_sigs.append((it['c'], s))
        if j % 50 == 0:
            print(f"  ...{j}/{len(uni)}", flush=True)

    # ---- habit：只用「已结算」历史信号（idx+WIN < 当前 idx）----
    for code, sigs in all_sigs:
        done = []                       # 已结算的类型序列
        for s in sigs:
            prev = [d for (idx, d) in done if idx + WIN < s['i']]
            vs = [d for d in prev if d != 'FAIL']
            s['n_hist'] = len(vs)
            s['habit'] = (sum(1 for d in vs if d == 'V') / len(vs)) if len(vs) >= 3 else None
            done.append((s['i'], s['typ']))
    flat = [s for _, ss in all_sigs for s in ss]
    print(f"\n信号总数 {len(flat)}")

    # ============ ① 类型分布与基本结果 ============
    print("\n" + "=" * 78)
    print("① 类型分布（从 T 收盘起算，前瞻 10 日）")
    for t in ('V', 'N', 'FAIL'):
        g = [s for s in flat if s['typ'] == t]
        dds = [s['dd'] for s in g]
        tbs = [s['tbreak'] for s in g if s['tbreak']]
        print(f"  {t:>4}: {len(g):>5} ({len(g)/len(flat)*100:5.1f}%)  "
              f"平均最大回撤 {sum(dds)/len(dds):>6.2f}%  "
              f"平均突破日 T+{sum(tbs)/len(tbs):.1f}" if tbs else f"  {t:>4}: {len(g):>5}")

    # 回撤分位数（止损宽度设计的直接依据）
    def q(xs, p):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(len(xs) * p))]
    ddall = [s['dd'] for s in flat]
    print("\n  最大回撤分位数（全部信号，从 T 收盘起算）："
          f"P50 {q(ddall,.5):.2f}%  P75 {q(ddall,.75):.2f}%  P90 {q(ddall,.90):.2f}%  P95 {q(ddall,.95):.2f}%")
    dda = [s['dd'] for s in flat if s['typ'] != 'FAIL']
    print(f"  （仅最终突破的票）P50 {q(dda,.5):.2f}%  P75 {q(dda,.75):.2f}%  P90 {q(dda,.90):.2f}%")

    # ============ ② 买法 × 类型 ============
    print("\n" + "=" * 78)
    print("② 买法 × 类型（收益 = 到 T+5 收盘，%；信号级期望 = 成交率 × 成交后期望）")
    print(f"  {'买法':<8}{'类型':<6}{'n':>6}{'成交率':>9}{'成交后均值':>12}{'t':>7}{'信号级期望':>12}")
    res = {}
    for tag in ('A_open', 'B_m2', 'C_m5'):
        for typ in ('ALL', 'V', 'N', 'FAIL'):
            g = [s['buy'][tag] for s in flat if (typ == 'ALL' or s['typ'] == typ)]
            if not g:
                continue
            fill = sum(1 for f, _ in g if f > 0) / len(g)
            rets = [r for f, r in g if f > 0]
            n, m, t = stats(rets)
            exp = fill * m
            res[(tag, typ)] = (len(g), fill, m, t, exp)
            print(f"  {tag:<8}{typ:<6}{len(g):>6}{fill*100:>8.1f}%{m:>11.2f}%{t:>7.2f}{exp:>11.2f}%")

    # ============ ③ 预测变量：能否提前分辨 V / N ============
    print("\n" + "=" * 78)
    print("③ 预测力检验（只看 V vs N 两组，FAIL 剔除；Δ = V组均值 − N组均值）")
    feats = ['bias20', 'r20', 'r60', 'rvol', 'atrpct', 'from_h60', 'pct60']
    vn = [s for s in flat if s['typ'] in ('V', 'N')]
    print(f"  可用样本 {len(vn)}（V {sum(1 for s in vn if s['typ']=='V')} / "
          f"N {sum(1 for s in vn if s['typ']=='N')}）")
    print(f"  {'特征':<10}{'V组':>9}{'N组':>9}{'Δ':>9}{'t':>8}{'判定':>10}")
    for f in feats:
        a = [s[f] for s in vn if s['typ'] == 'V']
        b = [s[f] for s in vn if s['typ'] == 'N']
        na, ma_, sa = stats(a)
        nb, mb, sb = stats(b)
        # 两样本 t
        if na > 1 and nb > 1:
            sd = math.sqrt(((na - 1) * (sa * math.sqrt(na)) ** 2 / (na - 1) + (nb - 1) * (sb * math.sqrt(nb)) ** 2 / (nb - 1)) / (na + nb - 2)) if False else None
        pool = math.sqrt(((na - 1) * sa ** 2 + (nb - 1) * sb ** 2) / (na + nb - 2)) if (na > 1 and nb > 1) else 0
        tt = (ma_ - mb) / (pool * math.sqrt(1 / na + 1 / nb)) if pool > 0 else 0
        verdict = '显著' if abs(tt) >= 2 else '不显著'
        print(f"  {f:<10}{ma_:>9.2f}{mb:>9.2f}{ma_-mb:>9.2f}{tt:>8.2f}{verdict:>10}")

    # ============ ④ habit：个股自身的路径惯性 ============
    print("\n" + "=" * 78)
    print("④ ★ 股性惯性（habit = 该股此前「已结算」信号中 V 型占比，≥3 次才计）")
    hs = [s for s in vn if s['habit'] is not None]
    print(f"  有 habit 的样本 {len(hs)}")
    if len(hs) > 30:
        hs_sorted = sorted(hs, key=lambda s: s['habit'])
        q = len(hs_sorted) // 3
        for lbl, grp in (('低 habit（偏N字股）', hs_sorted[:q]),
                         ('中 habit', hs_sorted[q:2 * q]),
                         ('高 habit（偏V型股）', hs_sorted[2 * q:])):
            pv = sum(1 for s in grp if s['typ'] == 'V') / len(grp)
            print(f"  {lbl:<20} n={len(grp):>4}  habit均值 {sum(s['habit'] for s in grp)/len(grp):.2f}"
                  f"  ⇒ 本次实际 V 型占比 {pv*100:.1f}%")
        # 相关性
        xs = [s['habit'] for s in hs]
        ys = [1.0 if s['typ'] == 'V' else 0.0 for s in hs]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        vx = math.sqrt(sum((a - mx) ** 2 for a in xs))
        vy = math.sqrt(sum((b - my) ** 2 for b in ys))
        r = cov / (vx * vy) if vx > 0 and vy > 0 else 0
        t_r = r * math.sqrt((len(xs) - 2) / max(1e-9, 1 - r * r))
        print(f"  corr(habit, 本次为V) = {r:+.4f}   t = {t_r:+.2f}"
              f"   ⇒ {'有惯性' if abs(t_r) >= 2 else '★无惯性（不可预测）'}")
        # 样本外：按时间后半段
        hs2 = sorted(hs, key=lambda s: s['date'])
        half = hs2[len(hs2) // 2:]
        xs = [s['habit'] for s in half]
        ys = [1.0 if s['typ'] == 'V' else 0.0 for s in half]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        vx = math.sqrt(sum((a - mx) ** 2 for a in xs))
        vy = math.sqrt(sum((b - my) ** 2 for b in ys))
        r2 = cov / (vx * vy) if vx > 0 and vy > 0 else 0
        t2 = r2 * math.sqrt((len(xs) - 2) / max(1e-9, 1 - r2 * r2))
        print(f"  样本外（时间后半段 n={len(half)}）: corr={r2:+.4f}  t={t2:+.2f}"
              f"   ⇒ {'成立' if abs(t2) >= 2 else '★不成立'}")

    # ============ ⑤ 结论所需的混合期望 ============
    print("\n" + "=" * 78)
    print("⑤ 不预判类型时，按 base rate 加权的信号级期望（= 现实可得的期望）")
    tot = len(flat)
    for tag in ('A_open', 'B_m2', 'C_m5'):
        exp = sum(res[(tag, t)][0] * res[(tag, t)][4] for t in ('V', 'N', 'FAIL') if (tag, t) in res) / tot
        print(f"  {tag:<8} 混合信号级期望 = {exp:+.2f}%")


if __name__ == '__main__':
    main()
