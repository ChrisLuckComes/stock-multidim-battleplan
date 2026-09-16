# -*- coding: utf-8 -*-
"""W底颈线突破 / 旗形下降趋势线突破"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rule123 import atr14, detect_bull_flag, detect_w_bottom, plan_entry, pivots


def _bar(d, o, h, l, c, v=1e6):
    return {"d": d, "o": o, "h": h, "l": l, "c": c, "v": v}


def _flat_then(seq):
    """seq: list of (h, l, c) after a flat base; dates auto."""
    bars = []
    px = 100.0
    for i in range(20):
        bars.append(_bar(f"2026-01-{i + 1:02d}", px, px + 1, px - 1, px, 8e5))
    base = len(bars)
    for j, (h, l, c) in enumerate(seq):
        o = (h + l) / 2
        bars.append(_bar(f"2026-02-{j + 1:02d}", o, h, l, c, 2.5e6))
    return bars, base


def test_detect_w_bottom_shape():
    # 左底 ~90，颈线 ~105，右底 ~91，随后仍在颈下
    seq = []
    # 下行到左底
    for x in [98, 95, 92, 90, 91, 93]:
        seq.append((x + 2, x - 1, x))
    # 反弹到颈
    for x in [96, 100, 104, 105, 103]:
        seq.append((x + 1, x - 2, x))
    # 右底
    for x in [100, 96, 92, 91, 93]:
        seq.append((x + 2, x - 1, x))
    # 颈下盘整
    for x in [96, 98, 100, 102]:
        seq.append((x + 1, x - 1, x))
    bars, _ = _flat_then(seq)
    Hs, Ls = pivots(bars, w=3)
    atr_v = atr14(bars)
    w = detect_w_bottom(bars, Hs, Ls, atr_v)
    assert w is not None, "should find W"
    assert w["neckline"] >= 103
    assert abs(w["l1"]["price"] - w["l2"]["price"]) <= max(0.5 * atr_v, 3)


def test_plan_w_bottom_break():
    bars = []
    for i in range(30):
        px = 100 + (i % 3) * 0.2
        bars.append(_bar(f"2026-03-{i + 1:02d}", px, px + 1.5, px - 1.5, px, 1e6))
    # 末 4 根：3 根在颈下，最后 1 根放量站上
    neck = 110.0
    bars[-4] = _bar("2026-03-27", 108, 109.5, 107, 108.5, 1e6)
    bars[-3] = _bar("2026-03-28", 108, 109, 107, 108, 1e6)
    bars[-2] = _bar("2026-03-29", 108, 109.2, 107.5, 108.8, 1e6)
    bars[-1] = _bar("2026-03-30", 109, 112, 108.5, 111.5, 3e6)
    ev = {
        "rvol20": 2.2,
        "platform": {"price": 999, "i": 0, "kind": "test"},
        "P0": {"i": 5, "price": 95},
        "P1": {"i": 15, "price": 96},
        "R1": {"i": 10, "price": 108},
        "c2": True,
        "w_bottom": {
            "l1": {"i": 8, "price": 100, "d": "2026-03-09"},
            "l2": {"i": 20, "price": 100.5, "d": "2026-03-21"},
            "neckline": neck,
            "neck_i": 12,
            "neck_d": "2026-03-13",
            "span": 12,
            "score": 2,
        },
        "bull_flag": None,
        "down_tl": None,
    }
    plan = plan_entry(bars, ev)
    assert plan["mode"] == "w_bottom_break", plan
    assert plan["priority"] == 1
    assert plan["recommend"] is True
    assert plan["buy_zone"]["anchor"] == "w_neckline"
    assert abs(plan["buy_zone"]["level"] - neck) < 0.01


def test_plan_flag_tl_break():
    bars = []
    for i in range(30):
        px = 100 + (i % 3) * 0.2
        bars.append(_bar(f"2026-04-{i + 1:02d}", px, px + 1.5, px - 1.5, px, 1e6))
    # 旗面趋势线今日约 108；近端在线下，末日站上
    bars[-4] = _bar("2026-04-27", 106, 107.5, 105, 106.5, 1e6)
    bars[-3] = _bar("2026-04-28", 106, 107, 105, 106, 1e6)
    bars[-2] = _bar("2026-04-29", 106, 107.2, 105.5, 106.8, 1e6)
    bars[-1] = _bar("2026-04-30", 107, 110, 106.5, 109.5, 3e6)
    tl_now = 108.0
    ev = {
        "rvol20": 2.0,
        "platform": {"price": 999, "i": 0, "kind": "test"},
        "P0": {"i": 5, "price": 90},
        "P1": {"i": 12, "price": 95},
        "R1": {"i": 8, "price": 105},
        "c2": True,
        "w_bottom": None,
        "bull_flag": {
            "pole_start": 5,
            "pole_end": 12,
            "pole_lo": 90,
            "pole_hi": 115,
            "pole_d0": "2026-04-06",
            "pole_d1": "2026-04-13",
            "pb": {"i": 15, "price": 112, "d": "2026-04-16"},
            "pa": {"i": 22, "price": 109, "d": "2026-04-23"},
            "tl_now": tl_now,
            "days_above_tl": 1,
            "mid_pole": 102.5,
        },
        # 同时给一条泛化下降趋势线，确认不会走 T2
        "down_tl": {
            "b": {"i": 10, "price": 120},
            "a": {"i": 20, "price": 110},
        },
    }
    plan = plan_entry(bars, ev)
    assert plan["mode"] == "flag_tl_break", plan
    assert plan["priority"] == 1
    assert plan["buy_zone"]["anchor"] == "flag_tl"


def test_flag_blocks_generic_dtl():
    """已识别旗形但尚未突破时，也不要用泛化 downtrend_tl_break 抢标。"""
    bars = []
    for i in range(30):
        px = 100 - i * 0.3
        bars.append(_bar(f"2026-05-{i + 1:02d}", px, px + 1, px - 1, px, 1e6))
    # 泛化下降趋势线末根约 97.3；末日站上它，但仍在旗面线下
    bars[-3] = _bar("2026-05-28", 94, 96, 93, 95, 1e6)
    bars[-2] = _bar("2026-05-29", 95, 97, 94, 96, 1e6)
    bars[-1] = _bar("2026-05-30", 96, 100, 95, 99, 3e6)
    ev = {
        "rvol20": 2.5,
        "platform": {"price": 999, "i": 0, "kind": "test"},
        "P0": {"i": 5, "price": 110},
        "P1": {"i": 20, "price": 100},
        "R1": {"i": 10, "price": 115},
        "c2": False,
        "w_bottom": None,
        "bull_flag": {
            "pole_start": 2,
            "pole_end": 8,
            "pole_lo": 80,
            "pole_hi": 120,
            "pole_d0": "a",
            "pole_d1": "b",
            "pb": {"i": 12, "price": 118, "d": "c"},
            "pa": {"i": 20, "price": 112, "d": "d"},
            "tl_now": 130.0,  # 仍在旗面线下，不触发 flag_tl_break
            "days_above_tl": 0,
            "mid_pole": 100,
        },
        "down_tl": {
            "b": {"i": 10, "price": 110},
            "a": {"i": 25, "price": 100},
        },
    }
    plan = plan_entry(bars, ev)
    assert plan["mode"] != "downtrend_tl_break", plan
    assert plan["mode"] != "flag_tl_break", plan


def test_detect_bull_flag_shape():
    bars = []
    px = 50.0
    for i in range(15):
        bars.append(_bar(f"2026-06-{i + 1:02d}", px, px + 0.8, px - 0.8, px, 5e5))
        px += 0.1
    # 旗杆：急涨
    pole = [52, 55, 58, 62, 66, 70]
    for j, c in enumerate(pole):
        bars.append(_bar(f"2026-06-{16 + j:02d}", c - 2, c + 1, c - 3, c, 3e6))
    # 旗面：下降高点
    flag_cs = [68, 67, 66, 65, 64, 63.5, 63]
    for j, c in enumerate(flag_cs):
        h = c + 1.5 - j * 0.15
        bars.append(_bar(f"2026-06-{22 + j:02d}", c, h, c - 1.2, c, 8e5))
    Hs, _ = pivots(bars, w=3)
    atr_v = atr14(bars)
    flag = detect_bull_flag(bars, Hs, atr_v)
    assert flag is not None, "should find bull flag"
    assert flag["pole_hi"] >= 68
    assert flag["tl_now"] is not None


if __name__ == "__main__":
    test_detect_w_bottom_shape()
    test_plan_w_bottom_break()
    test_plan_flag_tl_break()
    test_flag_blocks_generic_dtl()
    test_detect_bull_flag_shape()
    print("ok")
