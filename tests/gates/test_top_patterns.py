#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""top_patterns.py 回归 —— 纯离线，不触网。

覆盖：
  · 头肩顶（含放量头部下跌）→ detected/confirmed/volume_confirmed 全 True
  · 上涨趋势 / 双顶 / 下跌趋势 → detected 必须 False（反例，防误触发）
每条判据带反例，符合项目「铁律都要有反例」约定。
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import top_patterns as T

FAILS = []


def eq(name, got, want, tol=1e-6):
    ok = (got is None and want is None) or (
        got is not None and want is not None and abs(got - want) <= tol)
    if not ok:
        FAILS.append(f"{name}: got {got} want {want}")


def chk(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name}: {detail}")


def _ramp(seq, target, vol):
    """把 seq 末端平滑拉到 target（每步 1/8），返回追加后的 seq；vol 用于本段成交量。"""
    cur = seq[-1]
    step = (target - cur) / 8.0
    for _ in range(8):
        cur += step
        seq.append(cur)
    return seq


def mk_hs(vol_drop_high=True):
    """头肩顶 + 破位 + 2 波反弹至 MA50。leg 顺序：基线 → LS → T1 → H → T2 → RS → 破位 → 反弹×2。

    头→T2（下跌段）给高量，左肩→头（上涨段）给低量 ⇒ 头部下跌放量确认。
    """
    seq, vmap = [], {}
    px = 80.0
    for i in range(60):
        px *= 1.0033
        seq.append(px)
    legs = [(110.0, 1.0), (104.0, 1.0), (124.0, 1.0), (105.0, 3.0),
            (112.0, 1.0), (96.0, 1.0), (118.0, 1.0), (94.0, 1.0),
            (114.0, 1.0), (90.0, 1.0)]
    for target, vol in legs:
        before = len(seq)
        _ramp(seq, target, vol)
        for i in range(before, len(seq)):
            vmap[i] = vol
    bars = []
    for i, c in enumerate(seq):
        bars.append({"d": f"d{i}", "o": c, "h": c * 1.005, "l": c * 0.995,
                     "c": c, "v": vmap.get(i, 1.0)})
    return bars


def mk_uptrend():
    """纯上涨趋势：无摆动高点 ⇒ 必不识别。"""
    seq, px = [], 80.0
    for _ in range(120):
        px *= 1.004
        seq.append(px)
    return [{"d": f"u{i}", "o": c, "h": c * 1.002, "l": c * 0.998, "c": c, "v": 1.0}
            for i, c in enumerate(seq)]


def mk_double_top():
    """双顶（两峰等高，无更高头）⇒ 必不识别为头肩顶。"""
    seq = []
    px = 80.0
    for _ in range(40):
        px *= 1.005
        seq.append(px)
    legs = [(120.0, 1.0), (105.0, 1.0), (120.0, 1.0), (95.0, 1.0), (80.0, 1.0)]
    for target, vol in legs:
        _ramp(seq, target, vol)
    return [{"d": f"x{i}", "o": c, "h": c * 1.005, "l": c * 0.995, "c": c, "v": 1.0}
            for i, c in enumerate(seq)]


def mk_downtrend():
    """纯下跌趋势（无头肩）⇒ 必不识别。"""
    seq, px = [], 130.0
    for _ in range(120):
        px *= 0.996
        seq.append(px)
    return [{"d": f"y{i}", "o": c, "h": c * 1.002, "l": c * 0.998, "c": c, "v": 1.0}
            for i, c in enumerate(seq)]


# ── 1. 头肩顶正例 ──
hs = mk_hs()
r = T.detect_head_shoulders_top(hs)
chk("头肩顶 detected=True", r["detected"] is True, str(r))
chk("头肩顶 confirmed（颈线破位）=True", r["neckline_broken"] is True, str(r))
chk("头部下跌放量确认=True", r["volume_confirmed"] is True, str(r))
eq("头肩顶量比≈3.0", r["head_drop_vol_ratio"], 3.0, tol=0.5)
chk("头（H）高于两肩", r["head"] and r["head"][1] > r["left_shoulder"][1]
    and r["head"][1] > r["right_shoulder"][1], str(r))
chk("左肩≈右肩（≤10%）", r["left_shoulder"] and r["right_shoulder"]
    and abs(r["left_shoulder"][1] - r["right_shoulder"][1]) / r["head"][1] <= 0.10, str(r))
chk("破位后反弹至 MA50 ≥1 波", r["rebound_to_ma50"] >= 1, str(r))
chk("颈线 < 头", r["neckline"] is not None and r["neckline"] < r["head"][1], str(r))

# ── 2. 反例：上涨趋势 ──
chk("上涨趋势 detected=False", T.detect_head_shoulders_top(mk_uptrend())["detected"] is False)
# ── 3. 反例：双顶 ──
chk("双顶 detected=False", T.detect_head_shoulders_top(mk_double_top())["detected"] is False)
# ── 4. 反例：下跌趋势 ──
chk("下跌趋势 detected=False", T.detect_head_shoulders_top(mk_downtrend())["detected"] is False)

# ── 5. 短序列兜底 ──
chk("短序列 detected=False", T.detect_head_shoulders_top(mk_hs()[:20])["detected"] is False)

# ── 6. _empty 结构完整 ──
e = T._empty()
chk("_empty detected=False", e["detected"] is False)
chk("_empty 字段齐全", all(k in e for k in
    ("confirmed", "neckline", "head_drop_vol_ratio", "rebound_to_ma50", "notes")))

# ── 7. fmt_hs 渲染 ──
lines = T.fmt_hs(r, hs)
chk("fmt_hs 多行", len(lines) >= 4, str(lines))
chk("fmt_hs 标注参考非闸门", any("非闸门" in x or "参考" in x for x in lines), str(lines))

print(f"test_top_patterns: {len(FAILS)} failed" if FAILS else "test_top_patterns: ALL PASS")
for f in FAILS:
    print("  FAIL", f)
sys.exit(1 if FAILS else 0)
