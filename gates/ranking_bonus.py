#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
形态质量排序器（ranking_bonus）· 2026-11-18 老罗拍板吸收 1/2/3
================================================================
来源：GetMktStrength「4 most profitable swing setups」图，老罗 2026-11-18
      指认 ① 回踩企稳确认 ② 回撤幅度递减 ③ 平台紧凑度 三项可吸收。

★★★ 性质（不得越界）：
  - **全部是排序器/加分项，hard_gate 恒为 False**。
  - 只写 analysis["ranking_bonus"] 一个软键；**绝不**写 plan["recommend"]、
    odds_recommend / rec_up / 仓位 / 止损 / buy_zone，也**绝不**改任何既有阈值
    （RR_GATE、plateau_degree<50、FLAG_LEN_MAX 等一律不动）。
  - 失败/缺数据一律 computable=False 降级，不得抛异常打断主流程。

三项判据：
  1. stabilize_turn_up  回踩企稳"转头向上"（图④ holds EMA and turns back up）：
     先触及锚，后出现收盘站回锚 + 收盘高于前收 + 收盘在当日振幅上半区的确认K。
     复用 rule123.price_conf 的形态（不判实体阳线，判收涨方向，老罗 10-19 口径）。
  2. drawdown_series    回撤幅度递减序列（图① VCP 收缩）：
     局部高点→后续低点的回撤幅度逐段收窄。
     ⚠ 与老罗 2026-10-08 实证有张力：上升段回撤幅度反而更深，正确表述是
       "别等深均线"而非"回撤幅度更小"。故本项**仅为形态描述性加分**，
       输出 advisory_only=True，不得据此否决或要求更小回撤。
  3. platform_tightness 平台紧凑度（图② flat tight base）：
     窗口内收盘落在中位价 ±5% 的根数占比。与 T0 硬闸门 plateau_degree
     （rule123.py:4317，±10%/<50 否决）**另起函数、另起键、互不影响**。

纯函数：无 I/O、无网络、无全局状态。bars 为 list[dict]，键 d/o/h/l/c/v。
"""
from __future__ import annotations

DATE = "2026-11-18"

# 企稳确认
STAB_LOOKBACK = 8          # 回看窗口（交易日）
STAB_TOUCH_TOL = 1.01      # low ≤ 锚×1.01 视为触及
STAB_CLOSE_POS_MIN = 0.5   # 收盘须在当日振幅上半区

# VCP 回撤序列
VCP_WINDOW = 60            # 观察窗口
VCP_PIVOT_W = 2            # 局部高点判定半径
VCP_MIN_DRAWDOWNS = 2      # 至少两段回撤才判收缩
VCP_SHRINK_EPS = 0.05      # 允许 0.05 个百分点的噪声
VCP_MAX_SEG = 3            # 只用最近 3 段

# 平台紧凑度
TIGHT_LOOKBACK = 20
TIGHT_BAND_PCT = 5.0       # 中位价 ±5%（比 T0 的 ±10% 更严，但仅加分）
TIGHT_HIT_PCT = 60.0       # 命中根数占比 ≥60% 判紧凑


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sma(vals, n):
    vals = [x for x in vals if x is not None]
    if len(vals) < n or n <= 0:
        return None
    return sum(vals[-n:]) / n


def _pivot_highs(bars, w):
    """局部高点下标（严格极大 + 最小间距）。

    ⚑ 不能用 `h >= all(...)`：全平 K 线（h 全相等）时每根都成立 ⇒ 每根都当
      高点 ⇒ 凭空造出多段假回撤，VCP 会把横盘误判成"收缩"。故要求**至少一侧严格大于**。
    """
    out = []
    for i in range(w, len(bars) - w):
        h = _f(bars[i].get("h"))
        if h is None:
            continue
        left = [_f(bars[j].get("h")) for j in range(i - w, i)]
        right = [_f(bars[j].get("h")) for j in range(i + 1, i + w + 1)]
        left = [x for x in left if x is not None]
        right = [x for x in right if x is not None]
        if not left or not right:
            continue
        strict_max = ((h > max(left) and h >= max(right)) or
                      (h >= max(left) and h > max(right)))
        if not strict_max:
            continue
        if out and i - out[-1] <= w:      # 相邻高点至少隔 w 根，防密集伪峰
            continue
        out.append(i)
    return out


# ── ① 回踩企稳"转头向上" ─────────────────────────────────────────
def stabilize_turn_up(bars, anchor, lookback=STAB_LOOKBACK,
                      touch_tol=STAB_TOUCH_TOL,
                      close_pos_min=STAB_CLOSE_POS_MIN):
    """先触及锚 → 后出现"转头向上"确认K。纯加分，不否决。"""
    base = {"rule": "turn_up", "hit": False, "computable": False}
    anchor = _f(anchor)
    if anchor is None or anchor <= 0 or not bars:
        base["note"] = "缺锚或空 bars"
        return base
    seg = bars[-max(2, int(lookback)):]
    base["computable"] = True
    base["anchor"] = round(anchor, 3)
    touch_i = None
    for k, b in enumerate(seg):
        low = _f(b.get("l"))
        if low is not None and low <= anchor * float(touch_tol):
            touch_i = k
    if touch_i is None:
        base["note"] = "窗口内未触及锚（不构成回踩）"
        return base
    for k in range(touch_i, len(seg)):
        b, prev = seg[k], seg[k - 1] if k > 0 else None
        c = _f(b.get("c"))
        h, l = _f(b.get("h")), _f(b.get("l"))
        pc = _f(prev.get("c")) if prev else None
        if c is None or pc is None or h is None or l is None:
            continue
        rng = h - l
        close_pos = (c - l) / rng if rng > 0 else 1.0
        if c > pc and c >= anchor and close_pos >= float(close_pos_min):
            base.update({
                "hit": True,
                "confirm_i": (len(bars) - len(seg)) + k,
                "confirm_date": b.get("d"),
                "close": round(c, 3),
                "prev_close": round(pc, 3),
                "gap_bars": len(seg) - 1 - touch_i,
                "close_pos": round(close_pos, 3),
            })
            base["note"] = "回踩触及锚后转头向上（收盘站回锚+高于前收+上半区）"
            return base
    base["note"] = "已触及锚但未见转头向上确认K"
    return base


# ── ② 回撤幅度递减序列（VCP 收缩） ────────────────────────────────
def drawdown_series(bars, window=VCP_WINDOW, pivot_w=VCP_PIVOT_W,
                    min_dd=VCP_MIN_DRAWDOWNS, eps=VCP_SHRINK_EPS,
                    max_seg=VCP_MAX_SEG):
    """局部高点→后续低点的回撤幅度逐段收窄。仅形态描述性加分。"""
    base = {"rule": "drawdown_shrink", "hit": False, "computable": False,
            "advisory_only": True,
            "note_caveat": "与 2026-10-08 实证有张力：上升段回撤幅度反而更深，"
                           "正确表述是'别等深均线'。本项仅作形态描述，不得据此否决或要求更小回撤。"}
    if not bars:
        base["note"] = "空 bars"
        return base
    seg = bars[-int(window):] if len(bars) > int(window) else list(bars)
    if len(seg) < 6:
        base["note"] = "样本不足（<6 根）"
        return base
    base["computable"] = True
    hs = _pivot_highs(seg, int(pivot_w))
    depths = []
    for a, b in zip(hs, hs[1:]):
        H = _f(seg[a].get("h"))
        lows = [_f(seg[t].get("l")) for t in range(a, b + 1)]
        lows = [x for x in lows if x is not None]
        if H and lows:
            L = min(lows)
            depths.append({"date": seg[a].get("d"),
                           "high": round(H, 3), "low": round(L, 3),
                           "depth_pct": round((H - L) / H * 100, 2)})
    use = depths[-int(max_seg):]
    base["depths"] = use
    base["n_drawdowns"] = len(use)
    if len(use) < int(min_dd):
        base["note"] = "回撤段不足 %d 段（仅 %d）" % (int(min_dd), len(use))
        return base
    contracting = all(
        use[t]["depth_pct"] <= use[t - 1]["depth_pct"] + float(eps)
        for t in range(1, len(use))
    )
    base["hit"] = bool(contracting)
    base["contracting"] = bool(contracting)
    base["note"] = ("回撤幅度逐段收窄（VCP 收缩）" if contracting
                    else "回撤幅度未递减（非收缩形态）")
    return base


# ── ③ 平台紧凑度 ──────────────────────────────────────────────────
def platform_tightness(bars, lookback=TIGHT_LOOKBACK,
                       band_pct=TIGHT_BAND_PCT, hit_pct=TIGHT_HIT_PCT):
    """窗口内收盘落在中位价 ±band% 的占比。仅加分，不动 T0 硬闸门。"""
    base = {"rule": "platform_tightness", "hit": False, "computable": False}
    if not bars:
        base["note"] = "空 bars"
        return base
    seg = bars[-int(lookback):] if len(bars) > int(lookback) else list(bars)
    closes = [_f(b.get("c")) for b in seg]
    closes = [c for c in closes if c is not None]
    if len(closes) < 5:
        base["note"] = "样本不足（<5 根）"
        return base
    base["computable"] = True
    med = sorted(closes)[len(closes) // 2]
    within = sum(1 for c in closes if abs(c - med) / med * 100 <= float(band_pct))
    tightness = within / len(closes) * 100
    base.update({
        "median": round(med, 3),
        "tightness_pct": round(tightness, 2),
        "band_pct": float(band_pct),
        "n_bars": len(closes),
    })
    base["hit"] = bool(tightness >= float(hit_pct))
    base["tight"] = base["hit"]
    base["note"] = ("平台紧凑（±%.1f%% 内占比 %.1f%%）" % (float(band_pct), tightness)
                    if base["hit"] else
                    "平台不紧凑（±%.1f%% 内占比仅 %.1f%%）" % (float(band_pct), tightness))
    return base


# ── 聚合入口 ──────────────────────────────────────────────────────
def from_analysis(analysis, bars=None, **overrides):
    """把三项聚合成一个软键。bars 由 battle_analyze 作用域传入。

    ⚠ 不读 analysis["plan"]["recommend"] 做任何反向修改；本函数无副作用。
    """
    out = {
        "rule": "ranking_bonus", "date": DATE, "hard_gate": False,
        "advisory_only": True, "computable": False,
        "items": {}, "bonus_hits": 0, "bonus_max": 3,
        "line": "形态质量排序器（只加分不否决）：无数据",
    }
    if not bars or len(bars) < 5:
        out["line"] = "形态质量排序器（只加分不否决）：日线不足，未评估"
        return out

    plan = analysis.get("plan") or {} if isinstance(analysis, dict) else {}
    bz = plan.get("buy_zone") or {}
    anchor = bz.get("primary_lo") if isinstance(bz, dict) else None
    if anchor is None:
        anchor = bz.get("level") if isinstance(bz, dict) else None
    if anchor is None:
        anchor = _sma([_f(b.get("c")) for b in bars], 20)
    for k, v in overrides.items():
        if k == "anchor" and v is not None:
            anchor = v

    items = {
        "turn_up": stabilize_turn_up(bars, anchor),
        "drawdown_shrink": drawdown_series(bars),
        "platform_tightness": platform_tightness(bars),
    }
    out["items"] = items
    out["computable"] = any(x.get("computable") for x in items.values())
    hits = sum(1 for x in items.values() if x.get("hit"))
    out["bonus_hits"] = hits
    marks = []
    marks.append("企稳%s" % ("✓" if items["turn_up"].get("hit") else "—"))
    marks.append("收缩%s" % ("✓" if items["drawdown_shrink"].get("hit") else "—"))
    marks.append("紧凑%s" % ("✓" if items["platform_tightness"].get("hit") else "—"))
    out["line"] = ("形态质量排序器（只加分不否决）：%s，合计 %d/3。"
                   " 仅用于同档候选排序，不改 recommend / 仓位 / 止损 / 既有阈值。"
                   % (" / ".join(marks), hits))
    return out


def _demo():
    print(from_analysis({}, []).get("line"))


if __name__ == "__main__":
    _demo()
