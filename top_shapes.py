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
                lo_i, lo_p = i, l
        else:
            if l <= lo_p:
                lo_i, lo_p = i, l
            if h - lo_p >= thr:
                pivots.append((lo_i, lo_p, "L"))
                state = "up"
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


def _det_v_top(bars, pivots, steep=0.08, max_bars=25):
    """V顶（尖顶）：急速拉升后急速下杀，形成 ∧ 形。

    「底」直接取自 bars（尖顶前的一段最低价），不要求它同时是摆动低点。
    """
    for hi in [p for p in pivots if p[2] == "H"][-3:]:
        hi_i, hi_p, _ = hi
        win0 = max(0, hi_i - max_bars)
        seg = bars[win0:hi_i + 1]
        if not seg:
            continue
        base_i = min(range(len(seg)), key=lambda k: seg[k]["l"])
        base_px = seg[base_i]["l"]
        rise = (hi_p / base_px - 1.0) if base_px > 0 else 0.0
        after = bars[hi_i + 1:]
        if not after:
            continue
        low_after = min(b["l"] for b in after)
        fall = (hi_p / low_after - 1.0) if low_after > 0 else 0.0
        n_up = hi_i - (win0 + base_i)
        if rise >= steep and fall >= steep and n_up <= max_bars:
            return _shape("V顶", "bearish", peak=hi_p, support=low_after,
                          break_level=hi_p, pivots=[hi], signal_i=hi_i, peak_i=hi_i,
                          note=f"尖顶：{n_up}根内涨{rise * 100:.0f}%，"
                               f"随后急速回落 {fall * 100:.0f}%")
    return None


def _det_dome_top(bars, pivots, min_rise=0.10, fade=0.75):
    """圆顶：连续三个更高的峰，但后段涨幅明显衰减（动能衰竭的圆弧顶）。"""
    hs = [p for p in pivots if p[2] == "H"]
    if len(hs) < 3:
        return None
    for k in range(len(hs) - 1, 1, -1):
        H1, H2, H3 = hs[k - 2], hs[k - 1], hs[k]
        if not (H1[1] < H2[1] < H3[1]):
            continue
        inc1, inc2 = H2[1] - H1[1], H3[1] - H2[1]
        if inc1 <= 0 or inc2 > fade * inc1:
            continue
        if (H3[1] / H1[1] - 1.0) < min_rise:
            continue
        if bars[-1]["c"] >= H3[1]:
            continue   # 还没回落，圆弧未完成
        lo_piv = [p for p in pivots if p[2] == "L" and H1[0] <= p[0] <= H3[0]]
        support = min([p[1] for p in lo_piv], default=None)
        return _shape("圆顶", "bearish", peak=H3[1], support=support,
                      break_level=H3[1], pivots=[H1, H2, H3], signal_i=H3[0], peak_i=H3[0],
                      note=f"三峰递增但动能衰竭（后段涨幅仅为前段 {inc2 / inc1:.0%}），已回落破顶")
    return None


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
                _det_diamond, _det_rectangle, _det_line_family):
        try:
            s = det(win, pivots)
        except Exception:
            s = None
        if s:
            out.append(s)
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


BIAS_CN = {"bearish": "偏空(顶部反转)", "bullish": "偏多", "neutral": "中性/需看突破方向"}


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
