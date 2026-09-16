# -*- coding: utf-8 -*-
"""活平台沿 / 买区闸门回归（合成 K 线，不依赖 data/）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rule123 import atr14, living_platform, pivots, plan_entry, zone_at_level


def _bar(d, o, h, l, c, v=1e6):
    return {"d": d, "o": o, "h": h, "l": l, "c": c, "v": v}


def test_zone_at_level_not_hardcoded_in_zone():
    bars = [_bar("2026-01-01", 100, 101, 99, 100)]
    z = zone_at_level(100, 2.0, 112.0, "平台突破(优先T1)", {}, bars)
    assert z["in_zone"] is False
    assert z["dist_atr"] is not None and z["dist_atr"] > 2
    assert z["extended"] is True
    assert z["anchor"] == "platform_lip"


def test_platform_break_too_far_becomes_wait():
    bars = []
    for i in range(40):
        px = 100.0
        bars.append(_bar(f"2026-01-{i + 1:02d}", px, px + 1, px - 1, px, 8e5))
    bars.append(_bar("2026-03-11", 101, 105, 100.8, 104, 4e6))
    bars.append(_bar("2026-03-12", 104, 110, 103, 109, 4e6))
    bars.append(_bar("2026-03-13", 109, 113, 108, 112, 4e6))
    ev = {
        "rvol20": 3.0,
        "platform": {"price": 101.0, "i": 35, "kind": "fresh_break", "days_above": 3},
        "P0": {"i": 10, "price": 95},
        "P1": {"i": 20, "price": 96},
        "R1": {"i": 15, "price": 100},
        "c2": True,
        "w_bottom": None,
        "bull_flag": None,
        "down_tl": None,
    }
    plan = plan_entry(bars, ev)
    assert plan["recommend"] is False
    assert "延伸" in (plan["verdict"] or "") or "不追" in (plan["note"] or "")


def test_living_platform_fresh_break_ignores_spike_ath():
    """突破窗口内冲高不改写被破的活沿。"""
    bars = []
    # 缓慢抬高再在 50 留下确认高点 120
    for i in range(40):
        px = 100 + i * 0.2
        bars.append(_bar(f"2026-01-{i + 1:02d}", px, px + 1.5, px - 1, px, 1e6))
    # 明确摆动高 120，前后留足 w=3
    bars.append(_bar("2026-02-01", 110, 112, 109, 111, 1e6))
    bars.append(_bar("2026-02-02", 111, 113, 110, 112, 1e6))
    bars.append(_bar("2026-02-03", 112, 120, 111, 118, 2e6))  # 沿
    bars.append(_bar("2026-02-04", 117, 119, 115, 116, 1e6))
    bars.append(_bar("2026-02-05", 116, 117, 114, 115, 1e6))
    bars.append(_bar("2026-02-06", 115, 116, 113, 114, 1e6))
    # 突破并冲高到 130，收在沿上
    bars.append(_bar("2026-02-07", 115, 130, 114, 122, 5e6))
    bars.append(_bar("2026-02-08", 121, 125, 120, 123, 3e6))
    Hs, _ = pivots(bars, w=3)
    plat = living_platform(bars, Hs, atr14(bars))
    assert plat is not None, Hs[-5:]
    assert plat["price"] < 128, plat
    assert plat["kind"] in ("fresh_break", "pressing")


if __name__ == "__main__":
    test_zone_at_level_not_hardcoded_in_zone()
    test_platform_break_too_far_becomes_wait()
    test_living_platform_fresh_break_ignores_spike_ath()
    print("ok")
