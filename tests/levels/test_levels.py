#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""levels.py 回归 —— 纯离线，不触网。

覆盖：rsi14（Wilder 手算锚）、levels_from_bars（引擎口径接线）、
压力/支撑分区排序去重、flex_map 输出互锁、市场无关性（同一套判据）。
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import levels as L
import rule123 as R

FAILS = []


def _raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


def eq(name, got, want, tol=1e-6):
    ok = (got is None and want is None) or (
        got is not None and want is not None and abs(got - want) <= tol)
    if not ok:
        FAILS.append(f"{name}: got {got} want {want}")
    return ok


def chk(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name}: {detail}")


# ── 1. rsi14（Wilder 手算锚）──
eq("rsi 手算锚 n=5", L.rsi14([2, 2.5, 2.2, 2.8, 2.4, 3.0], n=5), 100 * 0.34 / 0.48, tol=1e-9)
eq("rsi 全平 → 50", L.rsi14([5.0] * 20), 50.0)
eq("rsi 单边涨 → 100", L.rsi14([100.0 + i for i in range(30)]), 100.0)
eq("rsi 单边跌 → 0", L.rsi14([100.0 - i for i in range(30)]), 0.0)
chk("rsi 序列不足 → None", L.rsi14([1.0] * 10) is None)
midx = [10, 10.6, 10.2, 10.9, 10.4, 11.2, 10.7, 11.5, 10.9, 11.8,
        11.1, 12.0, 11.4, 12.3, 11.8, 12.6, 12.0, 12.9, 12.3, 13.2]
r_mid = L.rsi14(midx)
chk("rsi 震荡上行在 50~100", 50 < r_mid <= 100, f"got {r_mid}")

# ── 2. levels_from_bars（引擎口径接线）──
def mk_bars(n=60, base=100.0):
    bars = []
    px = base
    for i in range(n):
        o = px
        c = px * (1.01 if i % 3 else 0.995)
        h = max(o, c) * 1.004
        l = min(o, c) * 0.996
        bars.append({"d": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                     "o": o, "h": h, "l": l, "c": c})
        px = c
    return bars

bars = mk_bars()
lv = L.levels_from_bars(bars)
closes = [b["c"] for b in bars]
eq("levels last_close", lv["last_close"], closes[-1])
eq("levels ma5 = 引擎", lv["ma5"], R.sma(closes, 5))
eq("levels ma10", lv["ma10"], sum(closes[-10:]) / 10)
eq("levels ma20", lv["ma20"], sum(closes[-20:]) / 20)
eq("levels atr14 = 引擎 Wilder", lv["atr14"], R.atr14(bars))
eq("levels prev_low", lv["prev_low"], bars[-1]["l"])
eq("hi20 接线", lv["hi20"], max(b["h"] for b in bars[-20:]))
eq("lo20 接线", lv["lo20"], min(b["l"] for b in bars[-20:]))
eq("hi60 接线", lv["hi60"], max(b["h"] for b in bars[-60:]))
eq("levels rsi = rsi14()", lv["rsi14"], L.rsi14(closes))
chk("levels 短序列报错", _raises(lambda: L.levels_from_bars(mk_bars(15))))

# ── 3. 压力/支撑分区与排序（合成单边上行）──
bars_up = []
px = 100.0
for i in range(70):
    o = px
    c = px * 1.01
    bars_up.append({"d": f"x{i}", "o": o, "h": c * 1.002, "l": o * 0.998, "c": c})
    px = c
lv_up = L.levels_from_bars(bars_up)
spot_up = lv_up["last_close"]
res = L.resistance_levels(lv_up, spot_up)
sup = L.support_levels(lv_up, spot_up)
chk("上行趋势：现价上方只剩结构高/昨高（或空）",
    all(p > spot_up for _, p in res) and all(lbl in ("20日高", "60日高", "昨高") for lbl, _ in res),
    str(res))
chk("上行趋势：支撑含 MA5/10/20", any(lbl == "MA5" for lbl, _ in sup), str(sup))
chk("支撑全部在现价下方", all(p < spot_up for _, p in sup))
chk("压力近→远升序", [p for _, p in res] == sorted(p for _, p in res))
chk("支撑近→远降序", [p for _, p in sup] == sorted((p for _, p in sup), reverse=True))

# 去重：两档价位相同只留一个
lv_d = dict(lv_up)
lv_d["ma5"] = lv_d["ma10"] = 105.0
res_d = L.resistance_levels(lv_d, 100.0)
eq("同价位去重", len([1 for lbl, _ in res_d if lbl in ("MA5", "MA10")]), 1)

# ── 4. rsi_stance ──
chk("超买", L.rsi_stance(71.0)[0] == "超买")
chk("超卖", L.rsi_stance(29.0)[0] == "超卖")
chk("中性", L.rsi_stance(50.0)[0] == "中性")
chk("None → n/a", L.rsi_stance(None)[0] == "n/a")

# ── 5. flex_map 输出互锁 ──
lv_fx = dict(lv_up)
lv_fx["rsi14"] = 74.0
lines = L.flex_map(spot_up, lv_fx)
kinds = [k for k, _ in lines]
chk("地图含 头/压力标题/支撑标题/规则", {"head", "res_title", "sup_title", "rule"} <= set(kinds))
chk("超买时标题带加分提示", any("加分" in t for k, t in lines if k == "res_title"))
chk("标题不带「N档」计数（老罗 2026-09-24）",
    all("档" not in t for k, t in lines if k in ("res_title", "sup_title")))
lv_fx["rsi14"] = 22.0
lines2 = L.flex_map(spot_up, lv_fx)
chk("超卖时头部含禁追空", any("禁追空" in t for k, t in lines2 if k == "head"))
# 旧签名兼容：三参调用不炸
L.flex_map(spot_up, lv_fx, spot_up)
chk("flex_map 三参签名兼容", True)

# ── 6. 市场无关性：A股 bars 与美股 bars 同构（同一 dict 键）──
# 美股格式取 rule123.bars_from_us 返回的 dict 键集；这里用离线断言锁键
us_keys = {"o", "h", "l", "c", "d"}
chk("合成 bars 含美股同构键", us_keys <= set(bars[0].keys()))
# levels_from_bars 不看 v 键 —— 无量的 bars 也能算
bars_novol = [{k: b[k] for k in ("d", "o", "h", "l", "c")} for b in bars[:30]]
chk("无量 bars 不炸", _raises(lambda: L.levels_from_bars(bars_novol)) is False)


print(f"test_levels: {len(FAILS)} failed" if FAILS else "test_levels: ALL PASS")
for f in FAILS:
    print("  FAIL", f)
sys.exit(1 if FAILS else 0)
