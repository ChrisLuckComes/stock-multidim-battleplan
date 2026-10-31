#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""top_shapes.py —— 顶部反转形态识别（12种几何形态，A 股 / 美股 通用）

来历（2026-10-31，老罗给的「顶部反转形态」图）：
  头肩顶 / 双重顶 / 三重顶 / V顶 / 圆顶 / 对称三角形 / 上升三角形 / 下降三角形 /
  扩散三角形 / 菱形 / 楔形(上升/下降) / 矩形

⚑ 本模块性质：**识别 + 定位触发位**，输出「形态 + 顶部价 + 支撑/颈线 + 破位触发位
  + 是否已破位」，供做空/减仓决策参考。**本身不放行任何交易**——能否空仍由
  us_short.py 原闸（结构破位 + RR + 止损距离）或 reverse_kline_short.py 的
  「破MA20 + 形态符合」决定。

⚑ 市场无关：只用日线 OHLC（bars dict: d/o/h/l/c/v），A股来自 data/screener.db
  （前复权），美股来自 Yahoo；**同一套算法两个市场共用**，无任何 A 股/美股特判。

⚑ 图注两条（已落进输出）：
  ① 「形态走完前可能只是整理形态」→ 未破位的形态一律标 `未完成(可能为整理)`。
  ② 「菱形反转一般只在头部；矩形反转出现在底部比头部多」→ bias 字段区分偏空/中性。

形态识别口径（全部基于「摆动点 zigzag」，不依赖任何指标）：
  摆动点 = 以 ATR×thr 为反转阈值提取的 H/L 交替序列（ATR 缺失用 0.5% 兜底）。
  三角形/楔形/矩形 = 对最近若干摆动点做最小二乘趋势线，按斜率组合分类（经典教科书口径）：
      上轨降 + 下轨升        → 对称三角形
      上轨平 + 下轨升        → 上升三角形
      上轨降 + 下轨平        → 下降三角形
      上轨升 + 下轨降        → 扩散三角形
      上轨升 + 下轨升(收敛)  → 上升楔形（偏空）
      上轨降 + 下轨降(收敛)  → 下降楔形（偏多）
      上轨平 + 下轨平        → 矩形
  其余形态按摆动点几何判定（双顶/三顶/V顶/圆顶/菱形/头肩顶）。

输出 dict：
  pattern 形态名 / bias 偏向(bearish|bullish|neutral) / peak 顶部价 /
  support 支撑·颈线 / resistance 阻力 / break_level 做空触发位(收盘跌破=反转确认) /
  confirmed 已破位 / state 完成状态 / pivots 参与摆动点 / note 说明
"""
import argparse
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ⚑ `top_patterns`（头肩顶复用）**惰性导入**：否则会形成循环
#   top_shapes → top_patterns → levels → rule123 → top_judgment → top_shapes。

# ── 可调阈值 ──
PIVOT_ATR_MULT = 2.0     # zigzag 反转阈值 = ATR14 × 此值
FLAT_RATIO = 0.18        # 「平」的判定：|线漂移| ≤ 形态高度的 18%
EQ_TOL = 0.035           # 双顶/三顶：两价差 ≤ 3.5% 视为等高
SHAPE_WINDOW = 140       # 形态识别回看窗口（根）
RECENT_BARS = 60         # 「仍在有效期内」判定


# ────────────────────────────── 基础工具 ──────────────────────────────
def _atr14(bars):
    if len(bars) < 15:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-14:]) / 14.0


def detect_pivots(bars, thr_mult=PIVOT_ATR_MULT, atr=None):
    """Zigzag 摆动点提取。返回 [(idx, price, 'H'/'L')]，时间升序。

    阈值 = ATR14 × thr_mult（ATR 不可用时用「末根收盘 × 0.5%」兜底）。
    纯价格，无未来函数：每根只用到当根为止的极值。
    """
    n = len(bars)
    if n < 5:
        return []
    if atr is None:
        atr = _atr14(bars)
    base = bars[-1]["c"] or 1.0
    thr = thr_mult * atr if atr else 0.005 * base
    thr = max(thr, 0.004 * base)
    pivots = []
    hi_i, hi_p = 0, bars[0]["h"]
    lo_i, lo_p = 0, bars[0]["l"]
    state = "up"
    for i in range(1, n):
        h, l = bars[i]["h"], bars[i]["l"]
        if state == "up":
            if h >= hi_p:
                hi_i, hi_p = i, h
            if hi_p - l >= thr:
                pivots.append((hi_i, hi_p, "H"))
                state = "down"
                # ⚑ 摆动点索引必须唯一：同一根 K 不能既是高又是低
                #   （涨停日「开=最低、收=最高」最常见）⇒ 低点候选从**下一根**起算
                if i + 1 < n:
                    lo_i, lo_p = i + 1, bars[i + 1]["l"]
                else:
                    lo_i, lo_p = i, l
        else:
            if l <= lo_p:
                lo_i, lo_p = i, l
            if h - lo_p >= thr:
                pivots.append((lo_i, lo_p, "L"))
                state = "up"
                if i + 1 < n:
                    hi_i, hi_p = i + 1, bars[i + 1]["h"]
                else:
                    hi_i, hi_p = i, h
    # 收尾：当前候选极值即为最后一个摆动点
    tail = (hi_i, hi_p, "H") if state == "up" else (lo_i, lo_p, "L")
    if not pivots or pivots[-1][0] != tail[0]:
        pivots.append(tail)
    return pivots


def _fit(pts):
    """最小二乘拟合 y = a·x + b（x=bar 索引）。返回 (a, b)。"""
    m = len(pts)
    if m < 2:
        return (0.0, pts[0][1] if pts else 0.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    mx = sum(xs) / m
    my = sum(ys) / m
    den = sum((x - mx) ** 2 for x in xs)
    a = sum((xs[k] - mx) * (ys[k] - my) for k in range(m)) / den if den else 0.0
    return (a, my - a * mx)


def _drift(a, pts):
    """趋势线在 pts 跨度内的价格漂移比例（用来判「平/斜」）。"""
    if len(pts) < 2:
        return 0.0
    span = pts[-1][0] - pts[0][0]
    ref = max(abs(pts[0][1]), 1e-9)
    return abs(a * span) / ref


def _shape(pattern, bias, peak=None, support=None, resistance=None,
           break_level=None, pivots=(), note="", signal_i=None, peak_i=None):
    return {
        "pattern": pattern, "bias": bias, "peak": peak, "support": support,
        "resistance": resistance, "break_level": break_level,
        "pivots": list(pivots), "note": note,
        # signal_i = 形态**完成**的那一根（次日确认制的锚）；
        # peak_i   = 形态**峰值**那一根（位置闸门「这一波最高点」的锚）。
        "signal_i": signal_i, "peak_i": peak_i,
    }


# ────────────────────────────── 各形态检测器 ──────────────────────────────
def _recent_windows(pivots, size, max_back=3):
    """产出最近若干个「长度=size 的摆动点窗口」（窗口可结束在任意摆动点上）。

    目的：形态识别不能要求「最后一个摆动点恰好是峰」——回踩一档后形态仍在，
    只应要求形态**最近**。
    """
    n = len(pivots)
    out = []
    for end in range(n - 1, max(-1, n - 1 - max_back) - 1, -1):
        if end - size + 1 >= 0:
            out.append(pivots[end - size + 1:end + 1])
    return out


def _det_double_top(bars, pivots, tol=EQ_TOL):
    """双重顶：H-L-H，两峰等高；颈线=中间谷。"""
    for p3 in _recent_windows(pivots, 3):
        if [p[2] for p in p3] != ["H", "L", "H"]:
            continue
        H1, L, H2 = p3
        if abs(H1[1] - H2[1]) / max(H1[1], H2[1]) > tol:
            continue
        prev_h = [p for p in pivots[:pivots.index(H1)] if p[2] == "H"]
        if prev_h and abs(prev_h[-1][1] - max(H1[1], H2[1])) / max(H1[1], H2[1]) <= tol:
            continue   # 三个峰几乎等高 ⇒ 那是三重顶，不是双顶
        # ⚠ 「左侧有更高峰 ⇒ 不是顶部」**不在这里判**：那是 top_judgment 的
        #   位置闸门（「这一波最高点」）的职责。检测器只管几何是否成立。
        pk = H1 if H1[1] >= H2[1] else H2
        return _shape("双重顶", "bearish", peak=max(H1[1], H2[1]), support=L[1],
                      break_level=L[1], pivots=p3, signal_i=H2[0], peak_i=pk[0],
                      note=f"两峰 {H1[1]:.2f}/{H2[1]:.2f} 基本等高，颈线(谷) {L[1]:.2f}")
    return None


def _det_triple_top(bars, pivots, tol=EQ_TOL):
    """三重顶：H-L-H-L-H，三峰等高；颈线=两谷均值。"""
    for p5 in _recent_windows(pivots, 5):
        if [p[2] for p in p5] != ["H", "L", "H", "L", "H"]:
            continue
        H1, L1, H2, L2, H3 = p5
        hs = [H1[1], H2[1], H3[1]]
        if (max(hs) - min(hs)) / max(hs) > tol:
            continue
        neck = (L1[1] + L2[1]) / 2.0
        pk = max([H1, H2, H3], key=lambda p: p[1])
        return _shape("三重顶", "bearish", peak=max(hs), support=neck,
                      break_level=neck, pivots=p5, signal_i=H3[0], peak_i=pk[0],
                      note=f"三峰 {hs[0]:.2f}/{hs[1]:.2f}/{hs[2]:.2f} 等高，颈线 {neck:.2f}")
    return None


def _det_v_top(bars, pivots, steep=0.12, fall_pct=0.10, max_bars=8):
    """V顶（尖顶）：**短促**的急速拉升 + 随后的急速下杀（∧ 形）。

    ⚑ 口径收紧（老罗 2026-10-31 回溯实测：旧判据「25根内涨8%」在 13 只
      今年见顶股上**全部**于 2026-04-01 误触发 ⇒ 涨 8% 又跌 8% 的普通回调
      根本不是尖顶）。现在要求：涨 ≥12% 且**用时 ≤8 根**，跌 ≥10% 且 ≤8 根。
    """
    # ⚑ 从**最新**的峰往回试：返回第一个成立的匹配（顶部判断要的是"最近那个顶"）
    for hi in reversed([p for p in pivots if p[2] == "H"][-3:]):
        hi_i, hi_p, _ = hi
        win0 = max(0, hi_i - max_bars)
        seg = bars[win0:hi_i + 1]
        if not seg:
            continue
        base_i = min(range(len(seg)), key=lambda k: seg[k]["l"])
        base_px = seg[base_i]["l"]
        rise = (hi_p / base_px - 1.0) if base_px > 0 else 0.0
        n_up = hi_i - (win0 + base_i)
        after = bars[hi_i + 1:hi_i + 1 + max_bars]
        if not after:
            continue
        low_after = min(b["l"] for b in after)
        fall = (hi_p / low_after - 1.0) if low_after > 0 else 0.0
        if rise >= steep and fall >= fall_pct and n_up <= max_bars:
            return _shape("V顶", "bearish", peak=hi_p, support=low_after,
                          break_level=hi_p, pivots=[hi], signal_i=hi_i, peak_i=hi_i,
                          note=f"尖顶：{n_up}根内涨{rise * 100:.0f}%，"
                               f"随后 ≤{max_bars}根回落 {fall * 100:.0f}%")
    return None


def _det_dome_top(bars, pivots, min_rise=0.10, fade=0.75, min_leg=4,
                  max_pulse=0.45, min_span=12):
    """圆顶：连续三个更高的峰，且后段涨幅明显衰减（动能衰竭的**圆弧**顶）。

    ⚑ 三条排除口径（老罗 2026-10-31 实测踩过：明月镜片「旗形」被误判成圆顶）：
      ① 每段间隔 ≥ min_leg 根 —— 圆弧是**渐进推进**，不是一两根拉完；
      ② 单根脉冲不得主导：区间内单根最大涨幅 ≤ max_pulse × 总涨幅
         （一根涨停占 94% 涨幅 = **旗杆**，不是圆弧）；
      ③ 上涨段总长 ≥ min_span 根。
    """
    hs = [p for p in pivots if p[2] == "H"]
    if len(hs) < 3:
        return None
    for k in range(len(hs) - 1, 1, -1):
        H1, H2, H3 = hs[k - 2], hs[k - 1], hs[k]
        if not (H1[1] < H2[1] < H3[1]):
            continue
        if (H2[0] - H1[0]) < min_leg or (H3[0] - H2[0]) < min_leg:
            continue
        inc1, inc2 = H2[1] - H1[1], H3[1] - H2[1]
        if inc1 <= 0 or inc2 > fade * inc1:
            continue
        if (H3[1] / H1[1] - 1.0) < min_rise:
            continue
        if bars[-1]["c"] >= H3[1]:
            continue   # 还没回落，圆弧未完成
        seg = bars[H1[0]:H3[0] + 1]
        if len(seg) < min_span:
            continue
        tot = H3[1] - H1[1]
        pulse = max((seg[i]["c"] - seg[i - 1]["c"]) for i in range(1, len(seg)))
        if pulse > max_pulse * tot:
            continue   # 单根脉冲主导 ⇒ 旗杆式急拉，不是圆弧
        lo_piv = [p for p in pivots if p[2] == "L" and H1[0] <= p[0] <= H3[0]]
        support = min([p[1] for p in lo_piv], default=None)
        return _shape("圆顶", "bearish", peak=H3[1], support=support,
                      break_level=H3[1], pivots=[H1, H2, H3], signal_i=H3[0],
                      peak_i=H3[0],
                      note=f"三峰递增但动能衰竭（后段涨幅仅为前段 {inc2 / inc1:.0%}）、"
                           f"总涨 {tot / H1[1] * 100:.0f}% 跨 {len(seg)} 根（单根最大仅占 "
                           f"{pulse / tot * 100:.0f}%），已回落破顶")
    return None


def _det_flag(bars, pivots, pole_gain=0.15, pole_bars=16, band_ratio=0.45,
              flag_min=3, flag_max=8):
    """旗形（上升中继）：陡峭**旗杆** + 窄幅下倾**旗面**。

    ⚑ 这是**中继形态**，不是顶部反转 ⇒ bias=continuation，**不进顶部候选池**
      （老罗 2026-10-31：明月镜头的"旗形"曾被误判成圆顶 → 顶部闸门误杀）。
    """
    n = len(bars)
    # ① 旗杆：最近 20 根内最强的一段上攻。
    #    ⚑ 口径：**先定旗杆低点 l，再取其后 pole_bars 根内的最高点作旗杆顶**
    #      （不能只按「涨幅最大」取区间终点——那样旗杆顶会落在半山腰，
    #        导致「旗面超过旗杆顶」误判，实测踩过）。
    best = None
    for l in range(max(0, n - 20), n - 1):
        lo_px = bars[l]["l"]
        if lo_px <= 0:
            continue
        seg = list(enumerate(bars[l + 1:min(n, l + 1 + pole_bars)], start=l + 1))
        if not seg:
            continue
        e, top = max(seg, key=lambda t: t[1]["h"])
        gain = (top["h"] - lo_px) / lo_px
        if best is None or gain > best[0]:
            best = (gain, l, e)
    if best is None or best[0] < pole_gain:
        return None
    gain, l, e = best
    pole_hi = bars[e]["h"]
    pole_lo = bars[l]["l"]
    pole_len = pole_hi - pole_lo
    if pole_len <= 0:
        return None
    # ② 旗面：旗杆顶之后的窄幅震荡（必须是**当前**结构）
    fl = bars[e + 1:e + 1 + flag_max]
    if len(fl) < flag_min or e + 1 + flag_max < n - 1:
        return None
    hi = max(b["h"] for b in fl)
    lo = min(b["l"] for b in fl)
    band = hi - lo
    if band > band_ratio * pole_len:
        return None       # 旗面太宽 ⇒ 不是旗形（是下跌或杂乱震荡）
    if lo <= pole_lo:
        return None       # 旗面跌破旗杆底 ⇒ 旗形已失败
    if hi > pole_hi:
        return None       # 旗面超过旗杆顶 ⇒ 那是加速不是旗形
    drift = (fl[-1]["c"] / fl[0]["h"] - 1.0) if fl[0]["h"] else 0.0
    if drift > 0.02:
        return None       # 旗面不是下倾/走平 ⇒ 不认（那是新一轮上攻）
    return _shape(
        "旗形", "continuation", peak=pole_hi, support=pole_lo,
        break_level=pole_lo, pivots=[(l, pole_lo, "L"), (e, pole_hi, "H")],
        signal_i=e, peak_i=e,
        note=f"旗杆 {bars[l]['d']}({pole_lo:.2f})→{bars[e]['d']}({pole_hi:.2f}) "
             f"涨{gain * 100:.0f}%/{e - l}根；旗面 {fl[0]['d']}~{fl[-1]['d']}（{len(fl)}根）"
             f"带宽{band:.2f}=旗杆{band / pole_len * 100:.0f}%、下倾{drift * 100:.0f}% "
             f"⇒ **上涨中继，不是顶部反转**")


def _det_diamond(bars, pivots):
    """菱形：先扩张（H升/L降）后收敛（顶降/底升）。"""
    for p5 in _recent_windows(pivots, 5):
        if [p[2] for p in p5] != ["H", "L", "H", "L", "H"]:
            continue
        H1, L1, H2, L2, H3 = p5
        if not (H2[1] > H1[1] and L2[1] > L1[1] and H3[1] < H2[1]):
            continue
        return _shape("菱形", "neutral", peak=H2[1], support=L2[1],
                      break_level=L2[1], pivots=p5, signal_i=H3[0], peak_i=H2[0],
                      note=f"先扩后收：扩张顶 {H2[1]:.2f}，收敛后顶 {H3[1]:.2f}、"
                           f"底 {L2[1]:.2f}（图注：菱形反转一般只在头部）")
    return None


def _det_rectangle(bars, pivots, flat_ratio=FLAT_RATIO, min_touch=2):
    """矩形：横盘震荡，上下沿近水平且各触 ≥2 次。

    「平」按形态自身高度归一（波动区间内上下沿的漂移 ≤ flat_ratio）。
    """
    ps = pivots[-7:]
    hs = [(p[0], p[1]) for p in ps if p[2] == "H"]
    ls = [(p[0], p[1]) for p in ps if p[2] == "L"]
    if len(hs) < min_touch or len(ls) < min_touch:
        return None
    hmax, hmin = max(p[1] for p in hs), min(p[1] for p in hs)
    lmax, lmin = max(p[1] for p in ls), min(p[1] for p in ls)
    rng = hmax - lmin
    if rng <= 0:
        return None
    if (hmax - hmin) / rng > flat_ratio or (lmax - lmin) / rng > flat_ratio:
        return None
    res = (hmax + hmin) / 2.0
    sup = (lmax + lmin) / 2.0
    pk = max(hs, key=lambda p: p[1])
    return _shape("矩形", "neutral", peak=hmax, support=sup, resistance=res,
                  break_level=sup, pivots=ps, signal_i=ps[-1][0], peak_i=pk[0],
                  note=f"横盘区间 {sup:.2f}~{res:.2f}（图注：矩形反转出现底部多于头部）")


def _gaps(hs, ls):
    """「峰 — 其后谷」的间距序列：衡量形态收敛/扩张。

    比最小二乘外推可靠——减速的楔形线拟合会失真，真实峰谷间距不会。
    """
    gaps = []
    for k in range(len(hs) - 1):
        nxt = [l for l in ls if l[0] > hs[k][0]]
        if nxt:
            gaps.append(hs[k][1] - nxt[0][1])
    if hs and ls:
        tail = [l for l in ls if l[0] > hs[-1][0]]
        gaps.append(hs[-1][1] - (tail[0][1] if tail else ls[-1][1]))
    return gaps


def _det_line_family(bars, pivots, flat_ratio=FLAT_RATIO):
    """三角形 / 楔形 / 扩散：用最近摆动点拟合上下轨，按斜率组合分类。

    ⚑ 三个口径要点：
      ① 「平」需同时满足：漂移占形态高度小 **且** 明显比另一侧平
         （否则对称三角形那种「两侧斜率相近」会被误吃成下降三角形）。
      ② 收敛/扩张用**真实峰谷间距**（_gaps），不用最小二乘外推。
      ③ 几何量（上下轨）按**最后一个摆动点**求值；只有「跌破触发位」
         才把下轨投影到当前 bar（标准趋势线外推）。
    """
    ps = pivots[-6:]
    hs = [(p[0], p[1]) for p in ps if p[2] == "H"]
    ls = [(p[0], p[1]) for p in ps if p[2] == "L"]
    if len(hs) < 2 or len(ls) < 2:
        return None
    a_h, b_h = _fit(hs)
    a_l, b_l = _fit(ls)
    last_piv_i = ps[-1][0]
    up_p = a_h * last_piv_i + b_h
    lo_p = a_l * last_piv_i + b_l
    if up_p <= lo_p:
        return None
    height = max(p[1] for p in ps) - min(p[1] for p in ps)
    drift_h = abs(a_h) * (hs[-1][0] - hs[0][0])
    drift_l = abs(a_l) * (ls[-1][0] - ls[0][0])
    up_flat = (height > 0 and drift_h <= flat_ratio * height
               and drift_h <= 0.5 * max(drift_l, 1e-9))
    lo_flat = (height > 0 and drift_l <= flat_ratio * height
               and drift_l <= 0.5 * max(drift_h, 1e-9))
    gaps = _gaps(hs, ls)
    converging = bool(gaps) and gaps[-1] < gaps[0] * 0.85
    diverging = bool(gaps) and gaps[-1] > gaps[0] * 1.15
    # 触发位：下轨投影到当前 bar（若已交叉则退回摆动点处的值）
    up_now = a_h * (len(bars) - 1) + b_h
    lo_now = a_l * (len(bars) - 1) + b_l
    if lo_now >= up_now:
        up_now, lo_now = up_p, lo_p
    piv = ps
    pk_i = max(hs, key=lambda p: p[1])[0]
    sig_i = ps[-1][0]
    # ⚑ 分类顺序：**先判「平」**（上/下轨水平）——水平线是更具体的特征，
    #   斜率微小的对称/下降三角形会先被平轨判据吃掉，避免误标。
    if up_flat and a_l > 0:
        return _shape("上升三角形", "neutral", signal_i=sig_i, peak_i=pk_i, peak=hs[0][1], support=lo_now,
                      resistance=up_now, break_level=lo_now, pivots=piv,
                      note=f"上轨平({up_now:.2f})下轨升；向上突破为多头，"
                           f"跌破下轨({lo_now:.2f})=冲高失败（顶部反转）")
    if lo_flat and a_h < 0:
        return _shape("下降三角形", "bearish", signal_i=sig_i, peak_i=pk_i, peak=hs[0][1], support=lo_now,
                      resistance=up_now, break_level=lo_now, pivots=piv,
                      note=f"下轨平({lo_now:.2f})上轨降；跌破下轨=空头突破确认")
    if a_h < 0 and a_l > 0 and converging:
        return _shape("对称三角形", "bearish", signal_i=sig_i, peak_i=pk_i, peak=hs[0][1], support=lo_now,
                      resistance=up_now, break_level=lo_now, pivots=piv,
                      note=f"上下轨收敛：上轨→{up_now:.2f} 下轨→{lo_now:.2f}，跌破下轨=空头突破")
    if a_h > 0 and a_l < 0 and diverging:
        return _shape("扩散三角形", "bearish", signal_i=sig_i, peak_i=pk_i, peak=hs[-1][1], support=lo_now,
                      resistance=up_now, break_level=lo_now, pivots=piv,
                      note=f"喇叭口扩张：上轨→{up_now:.2f} 下轨→{lo_now:.2f}，波动放大，"
                           f"向下解决概率大")
    if a_h > 0 and a_l > 0 and converging:
        return _shape("上升楔形", "bearish", signal_i=sig_i, peak_i=pk_i, peak=hs[-1][1], support=lo_now,
                      resistance=up_now, break_level=lo_now, pivots=piv,
                      note=f"两轨上行收敛，弱势上攻；跌破下轨({lo_now:.2f})=顶反转")
    if a_h < 0 and a_l < 0 and converging:
        return _shape("下降楔形", "bullish", signal_i=sig_i, peak_i=pk_i, peak=hs[0][1], support=lo_now,
                      resistance=up_now, break_level=up_now, pivots=piv,
                      note=f"两轨下行收敛（多头形态）：上破 {up_now:.2f} 才是向上解决")
    return None


# ────────────────────────────── 汇总入口 ──────────────────────────────
def detect_top_shapes(bars, window=SHAPE_WINDOW, atr=None):
    """识别全部顶部反转形态。返回按「距今最近」排序的 shape 列表。

    ⚑ 所有索引（pivots / signal_i / peak_i）都是**相对原始 bars 的绝对索引**——
      内部会把 bars 截到 window 根窗口，若返回窗口内相对索引，调用方按原始
      bars 取 K 线会取错位置（曾踩：top_judgment 拿相对索引去查原始 bars）。
    """
    if bars is None or len(bars) < 30:
        return []
    off = max(0, len(bars) - window)
    win = bars[off:] if off else bars
    if atr is None:
        atr = _atr14(win)
    pivots = detect_pivots(win, atr=atr)
    out = []
    # 头肩顶（复用 top_patterns 的既有实现，保证与作战卡同一口径）
    from top_patterns import detect_head_shoulders_top   # 惰性导入，避循环
    hs = detect_head_shoulders_top(win)
    if hs.get("detected"):
        _hst_piv = [(p[0], p[1], "H") for p in
                    (hs["left_shoulder"], hs["head"], hs["right_shoulder"])]
        out.append(_shape(
            "头肩顶", "bearish", peak=hs["head"][1], support=hs["neckline"],
            break_level=hs["neckline"],
            # ⚑ 头肩顶：signal_i = **右肩**（形态完成的那一根，次日确认制的锚），
            #   peak_i = **头部**（形态峰值，位置闸门「这一波最高点」的锚）。
            pivots=_hst_piv, signal_i=hs["right_shoulder"][0], peak_i=hs["head"][0],
            note=f"颈线 {hs['neckline']:.2f}"
                 + ("（头部下跌放量确认）" if hs.get("volume_confirmed") else "")
                 + ("· 已破位" if hs.get("neckline_broken") else "· 未破位")))
    for det in (_det_double_top, _det_triple_top, _det_v_top, _det_dome_top,
                _det_flag, _det_diamond, _det_rectangle, _det_line_family):
        try:
            s = det(win, pivots)
        except Exception:
            s = None
        if s:
            out.append(s)
    # ── 旗形覆盖：同一位置若同时存在「旗形(中继)」与偏空顶部形态，
    #    且旗面未破（旗形检测已保证）⇒ 该顶部形态改判为「旗面内，按中继处理」。
    #    依据：老罗 2026-10-31 —— 明月镜头的"旗形"曾被误判成圆顶/尖顶 → 顶部闸门误杀。
    flags = [x for x in out if x["pattern"] == "旗形"]
    if flags:
        for x in out:
            if x is flags[0] or x["bias"] != "bearish":
                continue
            for fg in flags:
                if abs((x.get("peak_i") or -99) - (fg.get("signal_i") or -99)) <= 2:
                    x["bias"] = "continuation"
                    x["covered_by_flag"] = True
                    x["note"] = (x.get("note") or "") + "｜⚑ 与旗形同位 ⇒ 旗面未破，按**中继**处理"
                    break
    # ── 索引还原成绝对索引 ──
    for s in out:
        s["pivots"] = [(i + off, px, t) for (i, px, t) in s.get("pivots", [])]
        if s.get("signal_i") is not None:
            s["signal_i"] += off
        if s.get("peak_i") is not None:
            s["peak_i"] += off
    last = bars[-1]["c"]
    for s in out:
        bl = s.get("break_level")
        s["confirmed"] = bool(bl is not None and last < bl)
        s["state"] = "已破位(反转确认)" if s["confirmed"] else "未完成(可能为整理形态)"
        idxs = [p[0] for p in s.get("pivots", []) if isinstance(p, (list, tuple)) and p]
        s["age_bars"] = (len(bars) - 1 - max(idxs)) if idxs else None
    out.sort(key=lambda s: (s.get("age_bars") if s.get("age_bars") is not None else 9999))
    return out


def bearish_context(bars, window=SHAPE_WINDOW):
    """★ **大背景层**（老罗 2026-10-31 定的做空范式）：顶部形态已识别且**已破位**
    ⇒ 这只票进入**下跌波段**，该波段内**持续可以做空**（不是只做一次）。

    背景成立日 = **形态完成之后**首次「收盘跌破形态触发位（颈线/下轨）」那根。
    ⚠ 必须限定在形态完成之后找破位日——否则会找到远古的旧跌破，
      把背景成立日推到几个月前，导致所有信号都算"背景晚期"。
    返回 dict(name, break_level, start_i, start_d, age_bars, alive, shape)；
    无可用背景时返回 None。
    """
    if not bars or len(bars) < 30:
        return None
    shapes = detect_top_shapes(bars)
    best = None
    for x in shapes:
        if not (x.get("bias") == "bearish" or x.get("pattern") == "菱形"):
            continue
        bl = x.get("break_level")
        if bl is None:
            continue
        frm = x.get("signal_i") or 0
        st = None
        for i in range(frm, len(bars)):
            if bars[i]["c"] < bl:
                st = i
                break
        if st is None:
            continue
        if best is None or st > best["start_i"]:
            best = {"name": x["pattern"], "break_level": bl, "start_i": st,
                    "start_d": bars[st]["d"], "shape": x}
    if best is None:
        return None
    last = bars[-1]["c"]
    best["alive"] = bool(last < best["break_level"])
    best["age_bars"] = len(bars) - 1 - best["start_i"]
    best["bars"] = bars
    return best


def ctx_grade(ctx, age=None):
    """背景成熟度分档（对应老罗「刚形成入场最好」）。

    `age` = 信号日相对背景成立日的位置（负=抢跑，背景尚未成立）；
    不传则用 ctx["age_bars"]（背景至今的年龄）。
    """
    if not ctx:
        return "无背景", 0
    a = age if age is not None else ctx.get("age_bars")
    if a is None:
        return "无背景", 0
    if a < 0:
        return "抢跑(背景未成立)", 0
    if a <= 3:
        return "背景刚成立(优先)", 3
    if a <= 10:
        return "背景早期", 2
    if a <= 25:
        return "背景中段", 1
    return "背景晚期(降档)", 0


BIAS_CN = {"bearish": "偏空(顶部反转)", "bullish": "偏多",
           "neutral": "中性/需看突破方向", "continuation": "中继形态(非顶部反转)"}


def fmt_shapes(shapes, bars=None):
    """渲染形态清单。"""
    if not shapes:
        return ["（顶部反转形态：未识别到图中的几何顶部形态）"]
    out = [f"共识别 {len(shapes)} 种顶部形态（按距今最近排序）："]
    for s in shapes:
        piv = " · ".join(
            f"{'高' if p[2] == 'H' else '低'}{p[1]:.2f}" for p in s.get("pivots", []) if p)
        parts = [f"  ▸ {s['pattern']}", BIAS_CN.get(s["bias"], s["bias"])]
        if s.get("peak") is not None:
            parts.append(f"顶 {s['peak']:.2f}")
        if s.get("support") is not None:
            parts.append(f"支撑/颈线 {s['support']:.2f}")
        if s.get("resistance") is not None:
            parts.append(f"阻力 {s['resistance']:.2f}")
        bl = s.get("break_level")
        if bl is not None:
            parts.append(f"触发位(收盘跌破) {bl:.2f}")
        parts.append(s.get("state", ""))
        if piv:
            parts.append(f"摆动点 {piv}")
        out.append("｜".join(parts))
        if s.get("note"):
            out.append(f"      · {s['note']}")
    out.append("  ⚑ 识别 ≠ 交易：本清单只给「形态 + 触发位」，能否空/减仓仍走既有闸门"
               "（做空：破MA20 + 反转K线识别 + 原 RR/止损闸）")
    return out


# ────────────────────────────── 数据加载 / CLI ──────────────────────────────
def load_bars(code, market="cn"):
    """A股：data/screener.db（前复权）；美股：Yahoo。返回 bars 列表。"""
    if market.lower() in ("us", "美股"):
        import rule123 as R
        bars, _spot, _meta = R.bars_from_us(code.upper())
        return bars
    db = os.path.join(HERE, "data", "screener.db")
    con = sqlite3.connect(db)
    rows = con.execute(
        "select d,o,h,l,c,v from bars where code=? order by d", (code,)).fetchall()
    con.close()
    return [{"d": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]} for r in rows]


def main():
    ap = argparse.ArgumentParser(description="顶部反转形态识别（12种，A 股 / 美股通用）")
    ap.add_argument("code", help="A股代码（301101）或美股 ticker（SNDK）")
    ap.add_argument("--market", default="cn", choices=["cn", "us"], help="市场（默认 cn）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()
    try:
        bars = load_bars(a.code, a.market)
    except Exception as e:
        print(f"[{a.code}] 取数失败：{type(e).__name__}: {e}")
        sys.exit(2)
    if not bars:
        print(f"[{a.code}] 无数据")
        sys.exit(2)
    shapes = detect_top_shapes(bars)
    if a.json:
        import json
        print(json.dumps(shapes, ensure_ascii=False, default=str, indent=2))
        return
    print("=" * 72)
    print(f"顶部反转形态识别 · {a.code.upper()} · {a.market.upper()} · "
          f"{bars[0]['d']} ~ {bars[-1]['d']} · {len(bars)} 根")
    print("=" * 72)
    print(f"末收 {bars[-1]['c']:.2f}")
    print()
    for line in fmt_shapes(shapes, bars):
        print(line)


if __name__ == "__main__":
    main()
