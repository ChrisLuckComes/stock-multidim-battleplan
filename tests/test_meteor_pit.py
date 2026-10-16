# -*- coding: utf-8 -*-
"""流星砸坑（meteor_pit）模式：单元 + 接管 + 注册表回归测试。

用法（自带 __main__ 跑步器）：
    python tests/test_meteor_pit.py
"""
import math
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rule123
from gates import market_regime as MR


def _bar(d, o, h, l, c, v):
    return {"d": d, "o": o, "h": h, "l": l, "c": c, "v": v}


def _build_baseline(n, start=10.0, step=0.05, vol=2.0, date0="2026-"):
    """构造 n 根缓慢上行基线（保证 MA5/10/20 与 ATR 有效）。"""
    bars = []
    for i in range(n):
        c = start + i * step
        bars.append(_bar("%s%02d" % (date0, i), c - 0.03, c + 0.1, c - 0.12, c, vol))
    return bars


def _with_meteor_pattern(n=60, spike_i=52, spike_vol=60.0, pit_low_close=12.70,
                         today_close=13.70, pullback_lows=None, pullback_closes=None):
    """基线 + 天量高点(拉升) + 圆弧浅坑(5日下拐、10日线守住) + 今日站上5日线。

    返回 (bars, 天量日索引, 预期坑底)。
    """
    bars = _build_baseline(n)
    # 天量高点：放量大阳见高点
    sb = bars[spike_i]
    bars[spike_i] = _bar(sb["d"], sb["c"], 14.2, 13.8, 14.0, spike_vol)
    # 回调坑（spike_i+1 .. n-2），10日线守住（收盘不连续破）
    defaults = {spike_i + 1: 13.50, spike_i + 2: 13.10, spike_i + 3: 12.80,
                spike_i + 4: pit_low_close, spike_i + 5: 12.90, spike_i + 6: 13.20}
    closes = dict(defaults)
    if pullback_closes:
        closes.update(pullback_closes)
    lows = dict(defaults)
    if pullback_lows:
        lows.update(pullback_lows)
    for k in range(spike_i + 1, n - 1):
        c = closes.get(k, 13.0)
        l = lows.get(k, round(c - 0.15, 2))
        bars[k] = _bar(bars[k]["d"], c - 0.05, c + 0.1, l, c, 2.0)
    # 今日：站上5日线 + 收阳 + 在10日线上方
    tb = bars[n - 1]
    bars[n - 1] = _bar(tb["d"], today_close - 0.1, today_close + 0.15,
                       round(today_close - 0.15, 2), today_close, 3.0)
    # 坑底 = 回调段最低 low
    pit_low = min(bars[k]["l"] for k in range(spike_i + 1, n))
    return bars, spike_i, round(pit_low, 2)


# ───────────────────────────── 单元测试 ─────────────────────────────

def _test(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    return cond


def suite():
    ok = True

    # 1) 正向：标准流星砸坑 → 命中、recommend、止损锚=坑底
    bars, si, pit = _with_meteor_pattern()
    ev = {"rvol20": None}
    r = rule123.meteor_pit(bars, ev, rule123.atr14(bars), bars[-1]["c"])
    ok &= _test("正向-返回非空", r is not None)
    ok &= _test("正向-recommend True", bool(r and r.get("recommend")))
    ok &= _test("正向-mode=meteor_pit", r and r.get("mode") == "meteor_pit")
    ok &= _test("正向-硬止损=坑底", r and r.get("hard_stop") == pit)
    ok &= _test("正向-天量日命中", r and r.get("vol_bar", {}).get("i") == si)
    ok &= _test("正向-强确认", r and r.get("strong_confirm"))

    # 2) 负向：无天量（量能恒定）→ None
    bars2 = _build_baseline(60, vol=2.0)
    r2 = rule123.meteor_pit(bars2, ev, rule123.atr14(bars2), bars2[-1]["c"])
    ok &= _test("负向-无量→None", r2 is None)

    # 3) 负向：今日未站上5日线（收在5日线下方）→ 不 recommend
    bars3, _, _ = _with_meteor_pattern(today_close=12.55)  # 低于坑区，未站上MA5
    r3 = rule123.meteor_pit(bars3, ev, rule123.atr14(bars3), bars3[-1]["c"])
    ok &= _test("负向-未站上MA5→不recommend",
                r3 is not None and not r3.get("recommend"))

    # 4) 负向：坑太深（>15%）→ 不 recommend
    bars4, _, _ = _with_meteor_pattern(pit_low_close=11.80)  # 坑底 11.65，回撤>15%
    r4 = rule123.meteor_pit(bars4, ev, rule123.atr14(bars4), bars4[-1]["c"])
    ok &= _test("负向-坑太深>15%→不recommend",
                r4 is not None and not r4.get("recommend"))

    # 5) 负向：10日线有效跌破（连破2根）→ 不 recommend
    bars5, _, _ = _with_meteor_pattern(
        pullback_closes={57: 12.50, 58: 12.45},  # 连续破 MA10
        today_close=13.70)
    r5 = rule123.meteor_pit(bars5, ev, rule123.atr14(bars5), bars5[-1]["c"])
    ok &= _test("负向-连破MA10→不recommend",
                r5 is not None and not r5.get("recommend"))

    # 6) 负向：追高（仍在坑区内、但收盘远离5日线>5%）→ 不 recommend
    bars6, _, _ = _with_meteor_pattern(today_close=14.0)  # 仍在天量高点14.2下方、但远离5日线
    r6 = rule123.meteor_pit(bars6, ev, rule123.atr14(bars6), bars6[-1]["c"])
    ok &= _test("负向-追高(远离5日线>5%)→不recommend",
                r6 is not None and not r6.get("recommend"))

    # 7) 接管 helper：mode=wait → meteor_pit 接管
    fake_result = {
        "mode": "wait", "priority": None, "setup": "wait", "path": None,
        "verdict": "等待", "recommend": False, "note": "无买点",
        "buy_zone": {"level": 13.70},
    }
    r7 = rule123.meteor_pit(bars, ev, rule123.atr14(bars), bars[-1]["c"])
    out = rule123._meteor_pit_takeover(dict(fake_result), r7)
    ok &= _test("接管-mode=meteor_pit", out["mode"] == "meteor_pit")
    ok &= _test("接管-recommend True", out["recommend"] is True)
    ok &= _test("接管-tier=T0/priority=1", out.get("tier") == "T0" and out.get("priority") == 1)
    ok &= _test("接管-保留被接管mode", out.get("meteor_pit_superseded_mode") == "wait")
    ok &= _test("接管-stop_plan锚=坑底", out["stop_plan"]["struct"] == pit)

    # 8) 注册表：meteor_pit 进 ALL_MODES（down 状态才可全禁），不在 REGIME_SENSITIVE
    ok &= _test("注册-ALL_MODES含meteor_pit", "meteor_pit" in MR.ALL_MODES)
    ok &= _test("注册-不在REGIME_SENSITIVE", "meteor_pit" not in MR.REGIME_SENSITIVE_MODES)

    # 9) 复合闸门：宽基 down + 个股 RS 弱 → meteor_pit 被拦（与 ALL_MODES 一致）
    fake_state = {
        "enabled": True, "broad_state": "down", "leader": False,
        "rs_slope": -5.0, "rs_strong": False,
        "metrics": {"ma10": 13.0, "ma20": 12.5, "slope_ma20": -0.3, "close": 13.7,
                    "above_ma20": True, "ma10_over_ma20": True, "rs_slope": -5.0},
    }
    blks = MR._block_set_for("down")
    ok &= _test("闸门-down弱禁meteor_pit", "meteor_pit" in blks)

    print(("\nALL PASS" if ok else "\nHAS FAIL"))
    return ok


if __name__ == "__main__":
    sys.exit(0 if suite() else 1)
