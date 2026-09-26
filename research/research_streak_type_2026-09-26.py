# -*- coding: utf-8 -*-
"""「连续拉升型」股性 —— 有没有惯性？以及它对买法的意义（2026-09-26）

老罗的追问：「还有那种连续拉升型，历史股性确实要参考，因为两只现在都不是主升浪」

⚠ 与上一份 `research_v_vs_n` 的分工：
  上一份测的是 **路径形状（V 型 / N 型）** 的个股惯性 ⇒ 结论 corr=-0.019、样本外 t=-1.24（无惯性）。
  本份测的是另一个属性 **「一波流连拉 vs 拉一根歇三天」** 的个股惯性。
  两者可以独立：一只票可以每次都先挖坑（N 型），但挖完坑是一波流涨上去。

方法（严格无未来函数）：
  信号日 T（T 收盘可见）：涨幅 ≥+5% 且 量比 ≥1.5× 且 收盘 > MA20。
  前瞻窗口 T+1..T+10，只用收盘价序列：
    up_days  = 收阳天数（0..10）
    streak   = 最长连续收阳天数
    run10    = T+10 收盘 / C0 - 1（%）
    max_dd   = 期间最大回撤（%）
    RUNNER   = streak >= 3（连续拉升型事件）；另做 streak>=4 敏感性
  惯性检验（三路互证）：
    ① ICC(1) 组内相关 —— 直接度量「个股间是否存在稳定差异」，比 corr 更有功效
       （corr 会被单股样本少带来的 habit 噪声衰减低估）
    ② split-half：前半期 habit → 后半期实测（corr + 三分位分组 + 样本外 t）
    ③ 随机置换基准：把信号打乱重排后重算 ICC，看真实 ICC 是否显著高于随机
  买法含义（回答「连拉型该怎么拿」）：
    A 固定目标：T+1 开盘买，触及 +8% 止盈走人，否则持有到 T+10 收盘
    B 不设目标：T+1 开盘买，移动止损（收盘破 T 日收盘 -5% 出局），否则到 T+10 收盘
    ⇒ 按「历史 habit 分组」（无未来函数）对比 A/B 谁更好
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
WIN = 10             # 路径/持有窗口
RUNNER_MIN = 3       # 「连续拉升」主定义：最长连阳 ≥ 3
TARGET_PCT = 8.0     # A 法固定止盈（%）
TRAIL_PCT = -5.0     # B 法收盘移动止损（%）


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


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0, 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return 0.0, 0.0
    r = sxy / math.sqrt(sxx * syy)
    # t 值
    if abs(r) >= 1:
        return r, 0.0
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return r, t


def icc1(groups):
    """单向随机效应 ICC(1)：组间方差占比。groups = [[观测...], ...]"""
    groups = [g for g in groups if len(g) >= 1]
    G = len(groups)
    N = sum(len(g) for g in groups)
    if G < 2 or N <= G:
        return None
    grand = sum(sum(g) for g in groups) / N
    # MSB / MSW
    ssb = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups)
    ssw = sum(sum((x - sum(g) / len(g)) ** 2 for x in g) for g in groups)
    dfb, dfw = G - 1, N - G
    if dfw <= 0:
        return None
    msb, msw = ssb / dfb, ssw / dfw
    k0 = (N - sum(len(g) ** 2 for g in groups) / N) / dfb
    denom = msb + (k0 - 1) * msw
    if denom <= 0:
        return None
    return (msb - msw) / denom


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
    """返回 [(idx, up_days, streak, run10, max_dd, feats...)]，按时间序"""
    bars = fetch_kline(code, n=BARS)
    if not bars or len(bars) < 130:
        return []
    bars.sort(key=lambda b: b['d'])
    c = [b['c'] for b in bars]
    h = [b['h'] for b in bars]
    lo = [b['l'] for b in bars]
    v = [b['v'] for b in bars]
    n = len(c)
    out = []
    for t in range(60, n - WIN - 1):
        if c[t - 1] <= 0:
            continue
        chg = (c[t] / c[t - 1] - 1) * 100
        if chg < 5:
            continue
        if h[t] == lo[t]:        # 一字板，排除
            continue
        v5 = sum(v[t - 5:t]) / 5
        if v5 <= 0 or v[t] / v5 < 1.5:
            continue
        ma20 = sum(c[t - 19:t + 1]) / 20
        if c[t] <= ma20:
            continue
        C0 = c[t]
        # 前瞻
        seq = c[t:t + WIN + 1]           # c[t] 起共 WIN+1 个点
        up = sum(1 for k in range(1, WIN + 1) if seq[k] > seq[k - 1])
        st = cur = 0
        for k in range(1, WIN + 1):
            if seq[k] > seq[k - 1]:
                cur += 1
                st = max(st, cur)
            else:
                cur = 0
        run10 = (c[t + WIN] / C0 - 1) * 100
        dd = min((lo[t + k] - C0) / C0 * 100 for k in range(1, WIN + 1))
        # T 日可见特征
        r20 = (c[t] / c[t - 20] - 1) * 100
        r60 = (c[t] / c[t - 60] - 1) * 100
        h60 = max(h[t - 59:t + 1])
        from_h60 = (c[t] / h60 - 1) * 100
        bias = (c[t] / ma20 - 1) * 100
        trs = []
        for j in range(t - 13, t + 1):
            pc = c[j - 1]
            trs.append(max(h[j] - lo[j], abs(h[j] - pc), abs(lo[j] - pc)))
        atrp = sum(trs) / len(trs) / C0 * 100
        out.append({
            "t": t, "up": up, "streak": st, "run10": run10, "dd": dd,
            "r20": r20, "r60": r60, "from_h60": from_h60, "bias": bias,
            "atrp": atrp, "rvol": v[t] / v5,
            # 买法（T+1 开盘入场）
            "buy": None, "exitA": None, "exitB": None,
        })
        b0 = bars[t + 1]['o']
        if b0 > 0:
            # A 固定目标 +8%
            hit = None
            for k in range(t + 1, t + WIN + 1):
                if h[k] / b0 - 1 >= TARGET_PCT / 100:
                    hit = k
                    break
            ea = (b0 * (1 + TARGET_PCT / 100) / b0 - 1) * 100 if hit else (c[t + WIN] / b0 - 1) * 100
            # B 移动止损：收盘价跌破 C0*(1+TRAIL) 出局（次日开盘价近似出场）
            eb = None
            for k in range(t + 1, t + WIN + 1):
                if c[k] / C0 - 1 <= TRAIL_PCT / 100:
                    eb = (bars[k + 1]['o'] / b0 - 1) * 100 if k + 1 <= t + WIN and bars[k + 1]['o'] > 0 \
                        else (c[k] / b0 - 1) * 100
                    break
            if eb is None:
                eb = (c[t + WIN] / b0 - 1) * 100
            out[-1]["buy"] = b0
            out[-1]["exitA"] = ea
            out[-1]["exitB"] = eb
            # C 自适应（修正版）：**不空等** —— T+1 起就按固定目标监控（不损失止盈窗口）；
            #   一旦在 T+3 收盘确认「信号日起三连阳」且尚未止盈 ⇒ 立刻切换成移动止损。
            if t + 3 <= t + WIN:
                ec = None
                hit0 = None
                for k in range(t + 1, t + 4):        # 阶段1：前 3 日正常止盈
                    if h[k] / b0 - 1 >= TARGET_PCT / 100:
                        hit0 = k
                        break
                if hit0 is not None:
                    ec, mode = TARGET_PCT, "target(前3日已止盈)"
                else:
                    running = all(c[t + k] > c[t + k - 1] for k in (1, 2, 3))
                    if running:                      # 阶段2a：已走成连拉 ⇒ 改移动止损
                        mode = "trail"
                        for k in range(t + 4, t + WIN + 1):
                            if c[k] / C0 - 1 <= TRAIL_PCT / 100:
                                ec = ((bars[k + 1]['o'] / b0 - 1) * 100
                                      if k + 1 <= t + WIN and bars[k + 1]['o'] > 0
                                      else (c[k] / b0 - 1) * 100)
                                break
                        if ec is None:
                            ec = (c[t + WIN] / b0 - 1) * 100
                    else:                            # 阶段2b：没走成 ⇒ 继续固定目标
                        mode = "target"
                        for k in range(t + 4, t + WIN + 1):
                            if h[k] / b0 - 1 >= TARGET_PCT / 100:
                                ec = TARGET_PCT
                                break
                        if ec is None:
                            ec = (c[t + WIN] / b0 - 1) * 100
                out[-1]["exitC"] = ec
                out[-1]["modeC"] = "trail" if mode == "trail" else "target"
                out[-1]["modeC_detail"] = mode
            # D 敏感性：判定点提前到 T+2（两连阳即切换），其余同 C
            if t + 2 <= t + WIN:
                ed = None
                hit0 = None
                for k in range(t + 1, t + 3):
                    if h[k] / b0 - 1 >= TARGET_PCT / 100:
                        hit0 = k
                        break
                if hit0 is not None:
                    ed, mode_d = TARGET_PCT, "target"
                else:
                    running2 = c[t + 1] > c[t] and c[t + 2] > c[t + 1]
                    if running2:
                        mode_d = "trail"
                        for k in range(t + 3, t + WIN + 1):
                            if c[k] / C0 - 1 <= TRAIL_PCT / 100:
                                ed = ((bars[k + 1]['o'] / b0 - 1) * 100
                                      if k + 1 <= t + WIN and bars[k + 1]['o'] > 0
                                      else (c[k] / b0 - 1) * 100)
                                break
                        if ed is None:
                            ed = (c[t + WIN] / b0 - 1) * 100
                    else:
                        mode_d = "target"
                        for k in range(t + 3, t + WIN + 1):
                            if h[k] / b0 - 1 >= TARGET_PCT / 100:
                                ed = TARGET_PCT
                                break
                        if ed is None:
                            ed = (c[t + WIN] / b0 - 1) * 100
                out[-1]["exitD"] = ed
                out[-1]["modeD"] = mode_d
    return out


def main():
    uni = load_universe()
    print("=" * 78)
    print("「连续拉升型」股性惯性检验   样本 %d 只 / %d 根K线   seed=%d" % (len(uni), BARS, SEED))
    print("=" * 78)

    per_stock = {}
    done = 0
    for it in uni:
        code = it.get('c') or it.get('code') or it.get('symbol')
        try:
            sigs = analyze(code)
        except Exception as e:
            print("  [跳过] %s %s: %s" % (code, it.get('name'), e))
            continue
        if sigs:
            per_stock[code] = (it.get('name'), sigs)
        done += 1
        if done % 50 == 0:
            print("  ...已取 %d/%d，有效 %d" % (done, len(uni), len(per_stock)))

    flat = []
    for code, (nm, sigs) in per_stock.items():
        for s in sigs:
            s2 = dict(s)
            s2["code"] = code
            s2["name"] = nm
            flat.append(s2)
    nsig = len(flat)
    print("\n样本：%d 只票 / %d 个信号（每票均 %.1f 个）"
          % (len(per_stock), nsig, nsig / max(1, len(per_stock))))

    # ── ① 分布：连续拉升到底常不常见 ──
    print("\n" + "─" * 78)
    print("① 连续拉升事件的分布（信号后 10 日内最长连阳）")
    for th in (2, 3, 4, 5):
        k = sum(1 for s in flat if s["streak"] >= th)
        print("   最长连阳 ≥ %d 天：%d / %d = %.1f%%" % (th, k, nsig, k / nsig * 100))
    ups = [s["up"] for s in flat]
    n_, m_, t_ = stats(ups)
    print("   10 日内收阳天数：均值 %.2f 天" % m_)
    n_, m_, t_ = stats([s["run10"] for s in flat])
    print("   T+10 累计涨幅：均值 %.2f%%（t=%.1f）" % (m_, t_))

    # ── ② ICC(1)：个股间是否存在稳定差异 ──
    print("\n" + "─" * 78)
    print("② ICC(1) 组内相关 —— 股性到底存不存在（0 = 完全是个股内随机）")
    metrics = [("streak", "最长连阳"), ("up", "收阳天数"), ("run10", "T+10涨幅"), ("dd", "最大回撤")]
    for key, label in metrics:
        groups = [[s[key] for s in sigs] for _, sigs in per_stock.values()]
        ic = icc1(groups)
        # 随机置换基准：打乱股票标签
        vals = [s[key] for s in flat]
        sizes = [len(g) for g in groups]
        random.seed(SEED)
        base = []
        for _ in range(10):
            random.shuffle(vals)
            gg, i0 = [], 0
            for sz in sizes:
                gg.append(vals[i0:i0 + sz])
                i0 += sz
            r = icc1(gg)
            if r is not None:
                base.append(r)
        bm = sum(base) / len(base) if base else 0.0
        print("   %-8s ICC(1) = %+.4f   随机置换基准 = %+.4f   %s"
              % (label, ic if ic is not None else float('nan'), bm,
                 "✔ 高于随机" if (ic is not None and ic > bm + 0.005) else "✘ 与随机无异"))

    # ── ③ split-half 惯性 ──
    print("\n" + "─" * 78)
    print("③ Split-half 惯性：前半期 habit（连拉率）→ 后半期实测")
    rows = []
    for code, (nm, sigs) in per_stock.items():
        sigs = sorted(sigs, key=lambda s: s["t"])
        if len(sigs) < 8:          # 太少不足以估计 habit
            continue
        half = len(sigs) // 2
        a, b = sigs[:half], sigs[half:]
        ha = sum(1 for s in a if s["streak"] >= RUNNER_MIN) / len(a)
        hb = sum(1 for s in b if s["streak"] >= RUNNER_MIN) / len(b)
        ua = sum(s["up"] for s in a) / len(a)
        ub = sum(s["up"] for s in b) / len(b)
        rows.append((ha, hb, ua, ub, len(a), len(b)))
    print("   可用股票数：%d（要求单票 ≥8 个信号）" % len(rows))
    for i, lab in ((0, "连拉率(streak≥%d)" % RUNNER_MIN), (2, "平均收阳天数")):
        r, t = pearson([x[i] for x in rows], [x[i + 1] for x in rows])
        print("   %-18s corr = %+.4f  t = %+.2f  %s"
              % (lab, r, t, "✔ 显著" if abs(t) >= 1.96 else "✘ 不显著"))
    # 三分位分组
    rows_sorted = sorted(rows, key=lambda x: x[0])
    k = len(rows_sorted) // 3
    for gi, grp in enumerate((rows_sorted[:k], rows_sorted[k:2 * k], rows_sorted[2 * k:])):
        if not grp:
            continue
        print("   habit 三分位 %s：前半连拉率 %.0f%% → 后半实测 %.0f%%；"
              "前半收阳 %.2f 天 → 后半 %.2f 天"
              % (["低", "中", "高"][gi],
                 sum(x[0] for x in grp) / len(grp) * 100,
                 sum(x[1] for x in grp) / len(grp) * 100,
                 sum(x[2] for x in grp) / len(grp),
                 sum(x[3] for x in grp) / len(grp)))
    hi = [x[1] for x in rows_sorted[2 * k:]]
    lo = [x[1] for x in rows_sorted[:k]]
    if hi and lo:
        n1, m1, _ = stats(hi)
        n0, m0, _ = stats(lo)
        sd = math.sqrt(((n1 - 1) * (sum((a - m1) ** 2 for a in hi) / (n1 - 1)) +
                        (n0 - 1) * (sum((a - m0) ** 2 for a in lo) / (n0 - 1))) / (n1 + n0 - 2))
        se = sd * math.sqrt(1 / n1 + 1 / n0) if sd > 0 else 0
        print("   高 habit 组 − 低 habit 组 = %+.1f pct（t=%+.2f）"
              % ((m1 - m0) * 100, (m1 - m0) / se if se > 0 else 0))
    # ★ ICC 与 corr 打架时的正确读法：股性「存在」但「估不准」 ⇒ 用 shrinkage 定量
    print("\n   【关键】ICC > 0（股性存在）但 corr ≈ 0（单票历史估不准）⇒ 该给 habit 多少权重？")
    print("   经验贝叶斯最优权重  w = n·ICC / (n·ICC + 1 − ICC)  （n = 该票历史信号数）")
    icc_u = 0.066     # 取「收阳天数」的 ICC
    for nn in (3, 6, 8, 12, 20):
        w = nn * icc_u / (nn * icc_u + 1 - icc_u)
        print("     n=%2d ⇒ habit 只值 %.0f%% 权重，其余 %.0f%% 用全市场基准"
              % (nn, w * 100, (1 - w) * 100))

    # ── ④ 可否用「T 日可见特征」预判连拉 ──
    print("\n" + "─" * 78)
    print("④ T 日可见特征：连拉组 vs 非连拉组（streak ≥ %d）" % RUNNER_MIN)
    R = [s for s in flat if s["streak"] >= RUNNER_MIN]
    N = [s for s in flat if s["streak"] < RUNNER_MIN]
    print("   连拉组 %d（%.0f%%）／ 非连拉组 %d" % (len(R), len(R) / nsig * 100, len(N)))
    for key, lab in (("r60", "60日涨幅"), ("r20", "20日涨幅"), ("from_h60", "距60日高"),
                     ("bias", "乖离MA20"), ("atrp", "ATR%"), ("rvol", "量比")):
        n1, m1, _ = stats([s[key] for s in R])
        n0, m0, _ = stats([s[key] for s in N])
        sd = math.sqrt(((n1 - 1) * (sum((a - m1) ** 2 for a in [s[key] for s in R]) / (n1 - 1)) +
                        (n0 - 1) * (sum((a - m0) ** 2 for a in [s[key] for s in N]) / (n0 - 1)))
                       / (n1 + n0 - 2))
        se = sd * math.sqrt(1 / n1 + 1 / n0) if sd > 0 else 0
        tt = (m1 - m0) / se if se > 0 else 0
        print("   %-8s 连拉 %+7.2f  vs 非连拉 %+7.2f   差 %+6.2f  t=%+5.2f %s"
              % (lab, m1, m0, m1 - m0, tt, "✔" if abs(tt) >= 1.96 else ""))

    # ── ⑤ 买法含义：固定目标 vs 移动止损 ──
    print("\n" + "─" * 78)
    print("⑤ 买法：A 固定 +%d%% 止盈  vs  B 不设目标（收盘破 %d%% 出局）" % (TARGET_PCT, TRAIL_PCT))
    A = [s["exitA"] for s in flat if s["exitA"] is not None]
    B = [s["exitB"] for s in flat if s["exitB"] is not None]
    n1, m1, t1 = stats(A)
    n0, m0, t0 = stats(B)
    print("   全样本：A %+.2f%%（t=%+.1f）  B %+.2f%%（t=%+.1f）  差 %+.2f pct"
          % (m1, t1, m0, t0, m1 - m0))
    print("\n   —— 按「实际是否连拉」分组（前瞻信息，只作经济意义解释，不可用于交易）——")
    for lab, grp in (("连拉组", [s for s in flat if s["streak"] >= RUNNER_MIN]),
                     ("非连拉组", [s for s in flat if s["streak"] < RUNNER_MIN])):
        aa = stats([s["exitA"] for s in grp if s["exitA"] is not None])
        bb = stats([s["exitB"] for s in grp if s["exitB"] is not None])
        print("   %-8s A %+.2f%%  B %+.2f%%  ⇒ 更好的：%s（差 %+.2f pct）"
              % (lab, aa[1], bb[1], "A 固定目标" if aa[1] > bb[1] else "B 移动止损", aa[1] - bb[1]))
    print("\n   —— 按「历史 habit」分组（无未来函数 ⇒ 这才是可执行版本）——")
    hab = {}
    for code, (nm, sigs) in per_stock.items():
        ss = sorted(sigs, key=lambda s: s["t"])
        for i, s in enumerate(ss):
            prev = ss[:i]
            if len(prev) < 3:
                continue
            hb = sum(1 for x in prev if x["streak"] >= RUNNER_MIN) / len(prev)
            hab[(code, s["t"])] = hb
    hv = sorted(set(hab.values()))
    if hv:
        q1, q2 = hv[len(hv) // 3], hv[2 * len(hv) // 3]
        for lab, flt in (("低 habit", lambda x: x <= q1),
                         ("中 habit", lambda x: q1 < x <= q2),
                         ("高 habit", lambda x: x > q2)):
            grp = [s for s in flat if (s["code"], s["t"]) in hab and flt(hab[(s["code"], s["t"])])]
            if len(grp) < 30:
                continue
            aa = stats([s["exitA"] for s in grp if s["exitA"] is not None])
            bb = stats([s["exitB"] for s in grp if s["exitB"] is not None])
            rr = sum(1 for s in grp if s["streak"] >= RUNNER_MIN) / len(grp) * 100
            print("   %-8s n=%4d 实际连拉率 %5.1f%%  A %+.2f%%  B %+.2f%%"
                  % (lab, len(grp), rr, aa[1], bb[1]))

    # ── ⑥ C 法：不用历史 habit，改用「T+3 时点已走成什么样」切换出场规则 ──
    print("\n" + "─" * 78)
    print("⑥ C 法（自适应）：T+1..T+3 只看不动，T+3 收盘按已走成的形态切换出场 —— 无未来函数")
    C = [s["exitC"] for s in flat if s.get("exitC") is not None]
    n2, m2, t2 = stats(C)
    print("   全样本：A %+.2f%%  B %+.2f%%  C %+.2f%%（t=%+.1f）  C−A = %+.2f pct  C−B = %+.2f pct"
          % (m1, m0, m2, t2, m2 - m1, m2 - m0))
    tr = [s for s in flat if s.get("modeC") == "trail"]
    tg = [s for s in flat if s.get("modeC") == "target"]
    print("   切换比例：走成连拉 %d（%.0f%%）走移动止损；其余 %d 走固定目标"
          % (len(tr), len(tr) / max(1, len(C)) * 100, len(tg)))
    for lab, grp in (("→ 连拉分支", tr), ("→ 非连拉分支", tg)):
        aa = stats([s["exitA"] for s in grp if s["exitA"] is not None])
        bb = stats([s["exitB"] for s in grp if s["exitB"] is not None])
        cc = stats([s["exitC"] for s in grp if s["exitC"] is not None])
        print("   %-12s n=%4d  A %+.2f%%  B %+.2f%%  C %+.2f%%"
              % (lab, len(grp), aa[1], bb[1], cc[1]))
    print("\n   D 敏感性：判定点提前到 T+2（两连阳即切换）")
    D = [s["exitD"] for s in flat if s.get("exitD") is not None]
    n3, m3, t3 = stats(D)
    trd = [s for s in flat if s.get("modeD") == "trail"]
    print("   全样本：D %+.2f%%（t=%+.1f）  D−A = %+.2f pct   切换比例 %.0f%%（两连阳）"
          % (m3, t3, m3 - m1, len(trd) / max(1, len(D)) * 100))
    for lab, grp in (("→ 连拉分支", trd),
                     ("→ 非连拉分支", [s for s in flat if s.get("modeD") == "target"])):
        aa = stats([s["exitA"] for s in grp if s["exitA"] is not None])
        bb = stats([s["exitB"] for s in grp if s["exitB"] is not None])
        dd = stats([s["exitD"] for s in grp if s["exitD"] is not None])
        print("   %-12s n=%4d  A %+.2f%%  B %+.2f%%  D %+.2f%%"
              % (lab, len(grp), aa[1], bb[1], dd[1]))
    print("   ⚠ 口径：C/D 的「连拉」= 信号日起连续 N 天收阳（T+2/T+3 时点可判定），"
          "与①的「10 日内最长连阳≥3」（40.4%）不是同一口径 —— 前者是新发状态、后者是事后统计。")

    print("\n" + "=" * 78)
    print("口径：信号=涨幅≥5% + 量比≥1.5× + 站上MA20；前瞻窗口 T+1..T+10；"
          "出场用次日开盘近似，未计手续费/滑点。")
    print("=" * 78)


if __name__ == "__main__":
    main()
