#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_breakout_edge.py —— breakout_edge 纯离线回归（无网络）。

锚定 2026-09-24 定稿口径：
  临近带 ≤0.5×ATR｜突破确认=3 日内收盘站上｜放量 ≥1.3×前5日均量
  突破买 stop=entry−0.30×ATR｜T1=上方下一压力位，无参照位按 +2×ATR 估
"""
import copy
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'gates')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
import breakout_edge as B          # noqa: E402
from levels import levels_from_bars, resistance_levels   # noqa: E402

FAILS = []


def chk(name, cond, detail=""):
    if cond:
        print(f"  ok  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name}  {detail}")


def eq(name, got, want, tol=1e-6):
    ok = (got is not None and want is not None
          and abs(got - want) <= tol) if isinstance(want, float) else got == want
    chk(name, ok, f"got={got} want={want}")


def mk(n=80, base=100.0, amp=1.0, steps=None, v=1e6):
    """合成日线：横盘 amp 振幅；steps = {index: (close_delta, v_mult)}。"""
    bars = []
    px = base
    for i in range(n):
        dc, vm = (steps or {}).get(i, (0.0, 1.0))
        o = px
        c = base + dc if dc else px
        h = max(o, c) + amp * 0.6
        lo = min(o, c) - amp * 0.6
        bars.append({"d": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                     "o": o, "h": h, "l": lo, "c": c, "v": v * vm})
        px = c
    return bars


# ── 1. _vol_ratio ──
bars = mk(10)
eq("vol_ratio i<5 → None", B._vol_ratio(bars, 3), None)
eq("vol_ratio 均量=1.0", B._vol_ratio(bars, 8), 1.0)
bars2 = mk(10, steps={8: (0.0, 2.0)})
eq("vol_ratio 放量 2.0x", B._vol_ratio(bars2, 8), 2.0)
bars3 = mk(10, steps={i: (0.0, 0.0) for i in range(3, 8)})
eq("vol_ratio 基期量 0 → None", B._vol_ratio(bars3, 8), None)

# ── 2. _first_break ──
b = mk(10)                                    # 全横盘 close=100，昨高 100.6
eq("未突破 → gap None/未触及", B._first_break(b, 5, 100.6, 3), (None, False, False))
b2 = mk(10, steps={7: (1.2, 1.0)})            # 第7根收盘 101.2 > 100.6
eq("3日内收盘站上", B._first_break(b2, 5, 100.6, 3)[0], 2)
eq("  closed=True", B._first_break(b2, 5, 100.6, 3)[2], True)
b3 = mk(10, steps={6: (0.9, 1.0)})            # h=101.5>100.6 触及，但收盘 100.9>100.6 也算突破
gap, touch, closed = B._first_break(b3, 5, 100.6, 3)
eq("触及即突破样本", (gap, touch, closed), (1, True, True))
b4 = mk(10, steps={6: (0.5, 1.0), 7: (0.0, 1.0)})   # h=101.1>100.6 触及，收盘 100.5/100 未站上
gap, touch, closed = B._first_break(b4, 5, 100.6, 3)
eq("盘中触及未收盘站上", (gap, touch, closed), (None, True, False))
b5 = mk(10, steps={9: (1.2, 1.0)})            # 突破发生在窗口外（horizon=3, i=5 → 看 6,7,8）
eq("窗口外突破不计", B._first_break(b5, 5, 100.6, 3), (None, False, False))

# ── 3. near_resistances / breakout_rr_table 手算锚 ──
b = mk(80)                                    # 横盘：spot=100, ATR=1.2, 昨高=20日高=100.6
lv = levels_from_bars(b)
rows = B.near_resistances(lv, 100.0)
chk("横盘序列压力位仅昨高/20日高去重一档", len(rows) == 1 and rows[0][0] in ("昨高", "20日高"),
    str(rows))
eq("  dist=0.5×ATR", rows[0][2], 0.5, tol=1e-9)
rt = B.breakout_rr_table(b, lv, 100.0)
lbl, px, d, stop, t1, t1_lbl, rr, t1_est = rt[0]
eq("  entry=px", px, 100.6, tol=1e-9)
eq("  stop=px−0.30×ATR", stop, 100.6 - 0.36, tol=1e-9)
eq("  T1 无参照位 +2×ATR", t1, 100.6 + 2.4, tol=1e-9)
chk("  t1_est=True", t1_est)
eq("  RR=(t1−px)/(px−stop)", rr, 2.4 / 0.36, tol=1e-9)

# 上方有参照位时 T1 取下一压力位
b2 = mk(80, steps={79: (3.0, 1.0)})           # 末根跳到 103：昨高 103.6，20日高 100.6
lv2 = levels_from_bars(b2)
rt2 = B.breakout_rr_table(b2, lv2, 103.0)
entry_row = [r for r in rt2 if r[1] > 103.0][0]      # 最近档=昨高 103.6
# 昨高之上无参照位（MA 类 ≈100 不在上方）→ t1_est
chk("跳空后最近压力位=昨高", entry_row[0] == "昨高" and abs(entry_row[1] - 103.6) < 1e-9,
    str(entry_row))
chk("  其 T1 无参照位 → est", entry_row[7])

# ── 4. backtest_breakout：横盘 0 突破 + 正例 ──
s0 = B.backtest_breakout(mk(90))
chk("横盘 90 根：临近样本 >0", s0["n"] > 0, str(s0))
eq("  但收盘突破 =0", s0["k"], 0)
b3 = mk(90, steps={80: (1.2, 3.0), 81: (1.2, 1.0), 82: (1.2, 1.0)})
# i=80 临近（spot=100, px=100.6, dist=0.5×ATR），81/82 收盘 101.2>100.6 → 突破样本
s1 = B.backtest_breakout(b3)
chk("构造正例 k≥1", s1["k"] >= 1, str(s1))
chk("  突破样本计入 RR（risk>0）", s1["n_rr"] >= 1, str(s1))

# ── 5. 无未来函数：篡改 i+horizon 之后的数据不影响既有样本 ──
full = mk(160, steps={100: (1.2, 3.0), 101: (1.2, 1.0)})
s_full = list(B._iter_samples(full))
mut = copy.deepcopy(full)
for j in range(120, len(mut)):                # 120 之后全部翻倍
    mut[j]["h"] *= 2.0
    mut[j]["l"] *= 2.0
    mut[j]["c"] *= 2.0
s_mut = list(B._iter_samples(mut[:120]))      # 截断序列：窗口 60..118
old_full = [s for s in s_full if s["i"] <= 116]   # i≤116 的突破判定只看 ≤119 根
old_mut = [s for s in s_mut if s["i"] <= 116]
chk("篡改未来不影响既有样本判定",
    [(s["i"], s["lbl"], round(s["px"], 4), s["closed"]) for s in old_full]
    == [(s["i"], s["lbl"], round(s["px"], 4), s["closed"]) for s in old_mut],
    f"n_full={len(old_full)} n_mut={len(old_mut)}")

# ── 6. edge_verdict 文案 ──
lv90 = levels_from_bars(mk(90))
stats_small = {"n": 3, "k": 1, "n_touch": 2, "n_vol": 2, "k_vol": 1,
               "n_dry": 1, "k_dry": 0, "r_multiple": None, "win": 0,
               "flat": 1, "n_rr": 1}
rows90 = B.breakout_rr_table(mk(90), lv90, 100.0)
v1 = B.edge_verdict(stats_small, rows90, 100.0, lv90)
chk("小样本 → 提示样本不足", any("样本不足" in t for _, t in v1), str(v1))
v2 = B.edge_verdict(stats_small, [], 100.0, lv90)
chk("无临近位 → 新高区文案", any("新高区" in t for _, t in v2), str(v2))
stats_big = dict(stats_small, n=25, k=12, n_vol=15, k_vol=9, n_dry=10, k_dry=3)
v3 = B.edge_verdict(stats_big, rows90, 100.0, lv90)
chk("大样本 → 给出突破率", any("25 次" in t and "48%" in t for _, t in v3), str(v3))
chk("  始终带维度声明", any("板块强度" in t for _, t in v3), str(v3))

# ── 7. 常数锚 ──
eq("PROX_ATR", B.PROX_ATR, 0.5)
eq("HORIZON", B.HORIZON, 3)
eq("VOL_MULT", B.VOL_MULT, 1.3)
eq("STOP_ATR", B.STOP_ATR, 0.30)
eq("MIN_SAMPLE", B.MIN_SAMPLE, 10)


print(f"test_breakout_edge: {len(FAILS)} failed" if FAILS
      else "test_breakout_edge: ALL PASS")
sys.exit(1 if FAILS else 0)
