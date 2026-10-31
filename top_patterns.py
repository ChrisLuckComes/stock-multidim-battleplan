#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""top_patterns.py —— 顶部形态识别（市场无关，做空参考/排序器，非闸门）

来历（2026-10-31，老罗给的经典做空模型图「圖 2.1 空頭交易剖析」）：
  头肩顶 A–B–C + 破颈线 + 头部下跌放巨量 + 随后 3~4 波反弹回抽 50 日线
  + 在反弹末端（③）放空。老罗定：**本模块只作「形态上下文 / 排序器」**，
  不得替代 us_short.py 现有的「纯裸K结构位 + RR≥1.5 + 止损距离≥0.25×ATR」决策闸，
  也**不得用 50 日线作否决/触发依据**（均线只展示，2026-10-10 铁律）。
  ⇒ 命中头肩顶只「提升反抽空优先级 / 提供颈线·MA50 参考锚」，最终能否空仍走原闸。

判据（纯价格+成交量，日线 bars dict 同构 A 股/美股）：
  ① 三段摆动高点 LS(左肩) < H(头) > RS(右肩)，且 H 高出于两肩 ≥ head_premium，
     两肩高度差 ≤ shoulder_tol（头肩顶几何）。
  ② 颈线 = 左肩—头之间谷 T1 与 头—右肩之间谷 T2 的均值；破位 = 右肩之后出现收盘 < 颈线。
  ③ 头部下跌放量确认 = 头→T2 下跌段「下跌日均值量」/ 左肩→头 上涨段「上涨日均值量」
     ≥ 1.0 ⇒ 派发式下跌（经典模型「頭部下跌 伴隨大成交量」）。
  ④ 反弹计数 = 破位后摆动高点中 ≥ MA50 的根数（经典「三~四波反弹到 50 天均线之上」）。

返回 dict（detected=False 时其余字段填空），便于 us_short.py 直接渲染 + 回归覆盖。
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

from levels import structural_levels          # noqa: E402  裸K摆动高低点（与 us_short 同口径）


# ────────────────────────────── 纯函数 ──────────────────────────────
def _sma(closes, n):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def _empty(detected=False):
    return {
        "detected": detected, "confirmed": False,
        "left_shoulder": None, "head": None, "right_shoulder": None,
        "neckline": None, "neckline_slope": None, "neckline_broken": False,
        "head_drop_vol_ratio": None, "volume_confirmed": False,
        "rebound_to_ma50": 0, "ma50": None, "pattern_age_bars": None,
        "near_ma50_rebound": False, "notes": [],
    }


def _avg_vol_direction(bars, i0, i1, direction):
    """[i0, i1] 区间内，按日涨跌方向取成交量均值（direction='down'/'up'）。"""
    vols = []
    for i in range(max(i0 + 1, 1), i1 + 1):
        ch = bars[i]["c"] - bars[i - 1]["c"]
        if direction == "down" and ch < 0:
            vols.append(bars[i].get("v") or 0.0)
        elif direction == "up" and ch > 0:
            vols.append(bars[i].get("v") or 0.0)
    return (sum(vols) / len(vols)) if vols else 0.0


def _min_low(bars, i0, i1):
    lo, idx = None, i0
    for i in range(i0, i1 + 1):
        v = bars[i]["l"]
        if lo is None or v < lo:
            lo, idx = v, i
    return (idx, lo)


def detect_head_shoulders_top(bars, window=200, shoulder_tol=0.10,
                              head_premium=0.04, min_gap=8, ma50=None):
    """头肩顶识别（参考/排序器）。返回 _empty() 派生的 dict。

    ma50：外部已算（levels_from_bars['ma50']）可传入；否则按简单 SMA50 估。
    仅用「给定的 bars 的最后一根 = 当下」做破位/反弹判断，不含未来函数。
    """
    n = len(bars)
    if n < 30:
        return _empty()
    closes = [b["c"] for b in bars]
    lows, highs = structural_levels(bars)
    if ma50 is None:
        ma50 = _sma(closes, 50)
    win0 = max(0, n - window)
    Hs = [(i, px) for (i, px) in highs if i >= win0]
    if len(Hs) < 3:
        return _empty()

    best = None  # (score, rs_idx, LS, H, RS)
    for ai in range(len(Hs)):
        for ci in range(ai + 1, len(Hs)):
            LS, RS = Hs[ai], Hs[ci]
            if RS[0] - LS[0] < 2 * min_gap:
                continue
            # 头：LS 与 RS 之间、且严格高于两肩的最高摆动高点
            H = None
            for h in Hs[ai + 1:ci]:
                if h[1] > LS[1] and h[1] > RS[1] and (H is None or h[1] > H[1]):
                    H = h
            if H is None:
                continue
            sh_max = max(LS[1], RS[1])
            sh_min = min(LS[1], RS[1])
            if (H[1] - sh_max) / H[1] < head_premium:
                continue
            if (sh_max - sh_min) / H[1] > shoulder_tol:
                continue
            score = (H[1] - sh_max) / H[1] - (sh_max - sh_min) / H[1]
            if best is None or score > best[0]:
                best = (score, RS[0], LS, H, RS)
    if best is None:
        return _empty()

    _, _, LS, H, RS = best
    T1 = _min_low(bars, LS[0], H[0])
    T2 = _min_low(bars, H[0], RS[0])
    if T1[0] >= H[0] or T2[0] <= H[0]:
        return _empty()
    neckline = (T1[1] + T2[1]) / 2.0
    neckline_slope = ((T2[1] - T1[1]) / (T2[0] - T1[0])) if T2[0] > T1[0] else 0.0

    broken = any(b["c"] < neckline for b in bars[RS[0]:])

    down_v = _avg_vol_direction(bars, H[0], T2[0], "down")
    up_v = _avg_vol_direction(bars, LS[0], H[0], "up")
    vol_ratio = (down_v / up_v) if up_v > 0 else None
    vol_confirmed = vol_ratio is not None and vol_ratio >= 1.0

    reb = [h for h in highs if h[0] > RS[0] and ma50 is not None and h[1] >= ma50]
    rebound_count = len(reb)

    last = bars[-1]["c"]
    near_ma50 = bool(ma50) and (ma50 * 0.97 <= last <= ma50 * 1.03) and last < ma50

    notes = []
    if not broken:
        notes.append("颈线未破（右肩后无收盘破位）⇒ 形态未确认，暂非做空上下文")
    else:
        notes.append("颈线已破 ⇒ 经典「破位后反弹放空」区间成立")
    if vol_confirmed:
        notes.append(f"头部下跌放量确认（量比 {vol_ratio:.2f}≥1，派发式下跌）")
    else:
        notes.append("头部下跌未现放量（量比不足）⇒ 派发确认弱")
    notes.append(f"破位后已现 {rebound_count} 波反弹触及 MA50，越往后越偏「下跌中继」"
                 if rebound_count else "破位后尚未出现反弹至 MA50（首跌段）")
    if near_ma50:
        notes.append("当前价贴近 MA50 下方 ⇒ 正处于「反弹末端」窗口，与反抽结构位空共振")

    return {
        "detected": True, "confirmed": broken,
        "left_shoulder": LS, "head": H, "right_shoulder": RS,
        "neckline": neckline, "neckline_slope": neckline_slope,
        "neckline_broken": broken,
        "head_drop_vol_ratio": vol_ratio, "volume_confirmed": vol_confirmed,
        "rebound_to_ma50": rebound_count, "ma50": ma50,
        "pattern_age_bars": n - RS[0],
        "near_ma50_rebound": near_ma50, "notes": notes,
    }


def fmt_hs(res, bars):
    """把 detect_head_shoulders_top 的结果渲染成多行文本（us_short.py 调用）。

    仅展示 + 参考，明确标注「非闸门」。
    """
    if not res.get("detected"):
        return ["（顶部形态：未识别到头肩顶几何，按原裸K闸执行）"]
    out = []
    d = lambda p: bars[p[0]]["d"] if p else "?"
    LS, H, RS = res["left_shoulder"], res["head"], res["right_shoulder"]
    out.append("⚑ 头肩顶几何：左肩 %.2f(%s) · 头 %.2f(%s) · 右肩 %.2f(%s)"
               % (LS[1], d(LS), H[1], d(H), RS[1], d(RS)))
    out.append("  颈线 ≈ %.2f（斜率 %+.4f/根）%s"
               % (res["neckline"], res["neckline_slope"] or 0,
                  "· 已破位" if res["neckline_broken"] else "· 未破"))
    vr = res["head_drop_vol_ratio"]
    out.append("  头部下跌量比 %.2f %s" % (vr if vr is not None else 0.0,
               "（放量确认）" if res["volume_confirmed"] else "（未放量）"))
    out.append("  破位后反弹至 MA50 次数 %d（MA50≈%.2f）；当前%s"
               % (res["rebound_to_ma50"], res["ma50"] or 0.0,
                  "贴近 MA50 下方（反弹末端窗口）" if res["near_ma50_rebound"] else "不在 MA50 反弹末端"))
    out.append("  ★ 参考性质：本形态只提升「反抽结构位空」优先级，并提供颈线/MA50 参考锚；"
               "能否空仍由 结构破位 + RR≥1.5 + 止损距离≥0.25×ATR 决定，50日线不作否决依据")
    return out


if __name__ == "__main__":
    import json
    # 简易自检：构造一条头肩顶 + 破位 + 反弹至 MA50 的日线，验证 detected/confirmed/volume
    def mk():
        s, vbase = [], 1.0
        # 60 根基线（80→100），保证 MA50 有定义且破位后反弹能触及
        px = 80.0
        for i in range(60):
            px *= 1.0033
            s.append(px)
        seq = list(s)
        legs = [(110, 1.0), (104, 1.0), (124, 1.0), (105, 1.0), (112, 1.0),
                (96, 2.5), (118, 1.0), (94, 2.5), (114, 1.0), (90, 2.5)]
        cur = seq[-1]
        for target, vmult in legs:
            step = (target - cur) / 8.0
            for k in range(8):
                cur += step
                seq.append(cur)
        bars = []
        for i, c in enumerate(seq):
            bars.append({"d": "d%d" % i, "o": c, "h": c * 1.005, "l": c * 0.995,
                         "c": c, "v": vbase})
        # 头部下跌段（96 附近）给高量：把 index 对应段 v 抬上去
        return bars
    bb = mk()
    r = detect_head_shoulders_top(bb)
    print(json.dumps({k: (list(v) if isinstance(v, tuple) else v)
                      for k, v in r.items()}, ensure_ascii=False, default=str))
