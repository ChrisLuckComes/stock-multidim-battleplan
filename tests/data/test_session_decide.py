#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""session_decide 纯函数回归。不联网。"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from gates.session_decide import (  # noqa: E402
    count_dip_minutes, decide, resolve_cap, tape_so_far, zone_vs_pre,
)


def _day(minutes, flows, pre=5.5, code="601218", name="吉鑫科技", done=False):
    return {
        "code": code, "name": name, "pre": pre, "date": "2026-09-28",
        "minutes": minutes, "flows": flows, "ticks": [],
        "bench_minutes": [], "bench_name": "上证指数",
        "quote": {"f170": (minutes[-1]["c"] / pre - 1) * 100},
        "session_done": done, "frame": "minute", "indexes": {},
    }


def _summary(day, main=1e7, small=-1e7):
    last = day["minutes"][-1]
    hi = max(day["minutes"], key=lambda x: x["h"])
    lo = min(day["minutes"], key=lambda x: x["l"])
    vol = sum(x["v"] for x in day["minutes"]) or 1.0
    after = sum(x["v"] for x in day["minutes"] if x["t"] > hi["t"])
    flow = {"main": main, "super": main, "large": 0, "mid": 0, "small": small}
    return {
        "hi": hi, "lo": lo, "last": last, "vol": vol,
        "up": 1, "dn": 1, "above": 1, "below": 1,
        "after": after, "after_share": after / vol,
        "pos": (last["c"] - lo["l"]) / (hi["h"] - lo["l"]) if hi["h"] != lo["l"] else 0.5,
        "bench_at_close": -1.0, "flow": flow,
        "session_done": day["session_done"],
        "buy": 0, "sell": 0, "big": [], "big_buy": 0, "big_sell": 0,
        "outer": None, "inner": None,
        "flow_low": flow, "flow_high": flow, "outflow": None,
        "reading_key": "mixed", "reading": "—",
        "conclusion": "", "conclusion_label": "", "conclusion_buy": "",
    }


def test_dip_zone_and_cap():
    pct, ok = zone_vs_pre(5.55, 5.5)
    assert ok and abs(pct - 0.00909) < 1e-4
    assert zone_vs_pre(5.70, 5.5)[1] is False
    assert count_dip_minutes([
        {"c": 5.50}, {"c": 5.55}, {"c": 5.70}, {"c": 5.52},
    ], 5.5) == 3
    cap = resolve_cap(5.30, 5.21, 5.99, 5.52, 5.5)
    assert abs(cap - 5.52) < 1e-9


def test_buy_in_0_2_window():
    # 13:30 现价 5.50，均价下，主力净买 —— 吉鑫那种窗口
    minutes = [
        {"t": "09:31", "o": 5.45, "h": 5.50, "l": 5.40, "c": 5.45, "v": 100, "avg": 5.45},
        {"t": "13:30", "o": 5.50, "h": 5.52, "l": 5.48, "c": 5.50, "v": 100, "avg": 5.60},
    ]
    day = _day(minutes, [{"t": "13:30", "main": 8e6, "super": 8e6, "large": 0, "mid": 0, "small": -8e6}])
    s = _summary(day, main=8e6)
    d = decide(day, s, entry=5.30, stop=5.21, target=5.99, cap=5.52)
    assert d["action"] == "能买", d
    assert d["in_dip_zone"] is True
    assert d["limit_buy"] == 5.50
    assert d["size"]["qty"] >= 100
    assert d["rr"] >= 1.5


def test_reject_near_limit_and_supply():
    minutes = [
        {"t": "09:31", "o": 5.5, "h": 5.6, "l": 5.4, "c": 5.5, "v": 100, "avg": 5.5},
        {"t": "14:20", "o": 6.0, "h": 6.05, "l": 5.9, "c": 6.0, "v": 100, "avg": 5.7},
    ]
    day = _day(minutes, [{"t": "14:20", "main": 1e7, "super": 1e7, "large": 0, "mid": 0, "small": -1e7}])
    s = _summary(day, main=1e7)
    d = decide(day, s, entry=5.30, stop=5.21, target=5.99, cap=5.52)
    assert d["action"] == "不买"
    assert "涨停" in d["reason"] or "上限" in d["reason"]

    minutes2 = [
        {"t": "09:31", "o": 5.6, "h": 5.8, "l": 5.5, "c": 5.7, "v": 50, "avg": 5.6},
        {"t": "11:00", "o": 5.5, "h": 5.55, "l": 5.4, "c": 5.45, "v": 100, "avg": 5.65},
    ]
    day2 = _day(minutes2, [{"t": "11:00", "main": -5e6, "super": -5e6, "large": 0, "mid": 0, "small": 5e6}])
    s2 = _summary(day2, main=-5e6, small=5e6)
    # 高点在第一根，高点后量 100/150≈0.67
    assert tape_so_far(day2, s2)["ok"] is False
    d2 = decide(day2, s2, entry=5.30, stop=5.21, target=5.99, cap=5.52)
    assert d2["action"] == "不买"


def test_soft_tape_does_not_veto_pullback_entry():
    """回归：主力净流出是软约束，只否决「追高」，不否决「回踩到价」。

    2026-10-09 斯菱智驱 301550 实证：10:38 主力 −2308 万被判「至今偏派发」，
    旧实现直接 _reject → 误杀；当日实际 92.33 → 103.78（+12.4%），
    且主力于 13:37 转正至 +4435 万。
    ⇒ 价格在挂单价及以下时，软读数不得阻断「能买」，只给 caution 提示减半仓。
    """
    minutes = [
        {"t": "09:30", "o": 5.50, "h": 5.55, "l": 5.20, "c": 5.25, "v": 400, "avg": 5.40},
        {"t": "10:00", "o": 5.26, "h": 5.30, "l": 5.18, "c": 5.20, "v": 300, "avg": 5.33},
        {"t": "10:30", "o": 5.22, "h": 5.26, "l": 5.19, "c": 5.21, "v": 200, "avg": 5.30},
    ]
    day = _day(minutes, [{"t": "10:30", "main": -2e7, "super": -2e7,
                          "large": 0, "mid": 0, "small": 2e7}],
               pre=5.50, code="301550", name="斯菱智驱")
    s = _summary(day, main=-2e7, small=2e7)
    # 分时读数本身是「偏弱」类
    assert tape_so_far(day, s)["ok"] is False
    # 关键：现价 5.21 在挂单价 5.30 及以下（回踩档），软读数不得否决
    d = decide(day, s, entry=5.30, stop=5.00, target=6.50, cap=5.40)
    assert d["action"] == "能买", d["reason"]
    assert d["caution"], "软约束必须留下 caution 供模型提示减半仓"
    assert "偏弱" in d["caution"] or "派发" in d["caution"] or "净卖" in d["caution"]


def test_soft_tape_still_blocks_chasing():
    """同一软读数下，现价高于挂单价 = 追高，仍应被拦（口径不得放松到无脑买）。"""
    minutes = [
        {"t": "09:30", "o": 5.50, "h": 5.60, "l": 5.45, "c": 5.55, "v": 300, "avg": 5.50},
        {"t": "10:00", "o": 5.56, "h": 5.62, "l": 5.50, "c": 5.58, "v": 200, "avg": 5.53},
        {"t": "10:30", "o": 5.57, "h": 5.59, "l": 5.52, "c": 5.54, "v": 150, "avg": 5.55},
    ]
    day = _day(minutes, [{"t": "10:30", "main": -2e7, "super": -2e7,
                          "large": 0, "mid": 0, "small": 2e7}],
               pre=5.50, code="301550", name="斯菱智驱")
    s = _summary(day, main=-2e7, small=2e7)
    d = decide(day, s, entry=5.30, stop=5.00, target=6.50, cap=5.60)
    assert d["action"] == "不买", d["reason"]
    assert "不追高" in d["reason"] or "回踩" in d["reason"]


def test_limit_down_board_is_rejected():
    """回归：跌停板必须硬拦，不得因 R 算得出来就判「能买」。

    2026-10-09 大金重工 002487 实证：昨收 48.61，当日 43.75 = 跌停板（−10.00%）。
    旧实现只检涨停价、不检跌停 ⇒ 在跌停板上判「能买 · R 8.96」，属接飞刀。
    跌停板可成交所以 R 算得出来，但卖盘排队、次日大概率继续低开，实际卖不出。
    """
    pre = 48.61
    dn = round(pre * 0.9, 2)          # 主板 −10% ⇒ 43.75
    assert abs(dn - 43.75) < 1e-9
    minutes = [
        {"t": "09:31", "o": 47.23, "h": 48.00, "l": 47.00, "c": 47.50, "v": 900, "avg": 47.50},
        {"t": "10:30", "o": 46.00, "h": 46.20, "l": 44.50, "c": 45.00, "v": 800, "avg": 46.40},
        {"t": "14:00", "o": 44.00, "h": 44.00, "l": 43.75, "c": 43.75, "v": 700, "avg": 45.10},
    ]
    day = _day(minutes, [{"t": "14:00", "main": -3e8, "super": -3e8,
                          "large": 0, "mid": 0, "small": 3e8}],
               pre=pre, code="002487", name="大金重工")
    s = _summary(day, main=-3e8, small=3e8)
    r = decide(day, s, entry=44.14, stop=41.97, target=59.69, cap=None)
    assert r["action"] == "不买", r["reason"]
    assert "跌停" in r["reason"], r["reason"]
    # 盯价提示必须抬到跌停价之上，不能落在跌停板内
    assert r["arm_price"] is not None and r["arm_price"] > dn, (
        "盯价 %s 必须高于跌停价 %s" % (r["arm_price"], dn))


if __name__ == "__main__":
    test_dip_zone_and_cap()
    test_buy_in_0_2_window()
    test_reject_near_limit_and_supply()
    test_soft_tape_does_not_veto_pullback_entry()
    test_soft_tape_still_blocks_chasing()
    test_limit_down_board_is_rejected()
    print("ok 6")
