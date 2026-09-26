# -*- coding: utf-8 -*-
"""W底颈线突破 / 旗形下降趋势线突破"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
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


def _pole_then(base=15):
    """15 根平台 + 6 根旗杆（52→70）：旗杆末根大阳 o=68/h=71/l=**67**/c=70。

    ⇒ 牛旗的失效线（旗杆最后一根大阳的最低点）= **67**。
    """
    bars = []
    px = 50.0
    for i in range(base):
        bars.append(_bar(f"2026-06-{i + 1:02d}", px, px + 0.8, px - 0.8, px, 5e5))
        px += 0.1
    for j, c in enumerate([52, 55, 58, 62, 66, 70]):
        bars.append(_bar(f"2026-06-{16 + j:02d}", c - 2, c + 1, c - 3, c, 3e6))
    return bars


def _flags_of(bars, flag_rows):
    for j, (o, h, l, c) in enumerate(flag_rows):
        bars.append(_bar(f"2026-07-{j + 1:02d}", o, h, l, c, 8e5))
    return bars


def test_detect_bull_flag_shape():
    """旧合成数据（旗面低 61.8、旗面线收 63）—— 2026-09-26 老罗加条件后**应该判无效**。

    它的旗面从 68 一路阴跌到 63，最低 61.8 < 旗杆末根大阳低点 67 ⇒
    「旗形整理跌破了大阳线的最低点」⇒ 形态无效（诊断分支要说得出是这个原因）。
    """
    bars = _flags_of(_pole_then(), [(68, 69.5, 66.8, 68), (67, 68.5, 65.8, 67),
                                    (66, 67.5, 64.8, 66), (65, 66.5, 63.8, 65),
                                    (64, 65.5, 62.8, 64), (63.5, 64.9, 61.8, 63),
                                    (63, 64.3, 61.9, 63)])
    Hs, _ = pivots(bars, w=3)
    atr_v = atr14(bars)
    assert detect_bull_flag(bars, Hs, atr_v) is None
    d = detect_bull_flag(bars, Hs, atr_v, diagnostic=True)
    assert d["valid"] is False
    assert d["invalid_reason"] == "flag_break_pole_low", d
    assert d["anchor"]["price"] == pytest.approx(67.0, abs=0.01)
    assert d["flag_min_close"] < d["anchor"]["price"]
    # 反向对照：不传 diagnostic 时必须是 None（老调用方一个都不能收到「无效件」）
    assert detect_bull_flag(bars, Hs, atr_v, diagnostic=False) is None


def test_detect_bull_flag_holds_pole_low():
    """守住旗杆大阳最低点（旗面低 67.25 > 67）⇒ 形态有效，并带上失效线字段。"""
    bars = _flags_of(_pole_then(), [(68.5, 69.5, 68.1, 68.6), (68.4, 69.0, 67.9, 68.2),
                                    (68.1, 68.9, 67.7, 68.0), (68.0, 68.8, 67.5, 67.9),
                                    (67.9, 68.7, 67.4, 67.8), (67.8, 68.6, 67.25, 67.75)])
    Hs, _ = pivots(bars, w=3)
    atr_v = atr14(bars)
    flag = detect_bull_flag(bars, Hs, atr_v)
    assert flag is not None, "should find bull flag"
    assert flag["valid"] is True
    assert flag["pole_hi"] >= 68
    assert flag["tl_now"] is not None
    assert flag["flag_len"] == 6
    assert flag["anchor"]["price"] == pytest.approx(67.0, abs=0.01)
    assert flag["anchor"]["d"] == "2026-06-21"
    assert flag["anchor"]["kind"] == "low"
    assert flag["flag_low"] == pytest.approx(67.25, abs=0.01)
    assert flag["undercut"] is False


def test_detect_bull_flag_undercut_intraday_is_still_valid():
    """盘中插破失效线、**收盘收回** ⇒ 仍有效（老罗口径：收盘破才无效），只标 undercut。"""
    bars = _flags_of(_pole_then(), [(68.5, 69.5, 68.1, 68.6), (68.4, 69.0, 67.9, 68.2),
                                    (68.1, 68.9, 67.7, 68.0), (68.0, 68.8, 66.4, 67.9),
                                    (67.9, 68.7, 67.4, 67.8), (67.8, 68.6, 67.25, 67.5)])
    Hs, _ = pivots(bars, w=3)
    flag = detect_bull_flag(bars, Hs, atr14(bars))
    assert flag is not None
    assert flag["flag_low"] < flag["anchor"]["price"] <= flag["flag_min_close"]
    assert flag["undercut"] is True and flag["valid"] is True


def test_detect_bull_flag_too_long_is_invalid():
    """② 整理天数不超过 20 日（老罗 2026-09-26）：22 根旗面 ⇒ 无效。"""
    rows = []
    for j in range(22):
        c = 68.6 - j * 0.13
        rows.append((c, c + 0.9, c - 0.5, c))
    bars = _flags_of(_pole_then(), rows)
    Hs, _ = pivots(bars, w=3)
    atr_v = atr14(bars)
    assert detect_bull_flag(bars, Hs, atr_v) is None
    d = detect_bull_flag(bars, Hs, atr_v, diagnostic=True)
    assert d["invalid_reason"] == "flag_too_long", d
    assert d["flag_len"] == 22
    assert d["limit"] == 20


if __name__ == "__main__":
    test_detect_w_bottom_shape()
    test_plan_w_bottom_break()
    test_plan_flag_tl_break()
    test_flag_blocks_generic_dtl()
    test_detect_bull_flag_shape()
    test_detect_bull_flag_holds_pole_low()
    test_detect_bull_flag_undercut_intraday_is_still_valid()
    test_detect_bull_flag_too_long_is_invalid()
    print("ok")
