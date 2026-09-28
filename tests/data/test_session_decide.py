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


if __name__ == "__main__":
    test_dip_zone_and_cap()
    test_buy_in_0_2_window()
    test_reject_near_limit_and_supply()
    print("ok 3")
