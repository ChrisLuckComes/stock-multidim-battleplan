# -*- coding: utf-8 -*-
"""结构止损不得被 5% 成本兜底冒充。

飞科 603868 / 富瀚微 300613：突破位或回踩线被 `_finish` 当成「锚不可用」，
改写成 level×(1−5%)。买区上沿到这条假止损的距离把 R 压到 0.33 / 0.27。
成本兜底只在没有技术锚、或技术锚宽于 5% 需要收紧时使用，并且不覆盖结构位去算作战 R。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from battle_analyze import _pick_recommend_cn
from rule123 import STOP_MAX_PCT, stop_plan


def _bar(d, o, h, l, c, v=1e6):
    return {"d": d, "o": o, "h": h, "l": l, "c": c, "v": v}


def _flat(n, px, start=1):
    return [_bar("2026-09-%02d" % (start + i), px, px + 0.2, px - 0.2, px)
            for i in range(n)]


def test_platform_line_is_not_replaced_by_cost_floor():
    """飞科几何：平台 32.99，涨停收 34.22。结构止损留在平台，不落到 31.34。"""
    bars = _flat(25, 32.0) + [_bar("2026-10-28", 33.0, 34.22, 31.5, 34.22, 5e6)]
    atr = 1.027
    z = {
        "level": 32.99,
        "primary_lo": 32.99,
        "primary_hi": 34.02,
        "anchor": "platform_lip",
        "ma5": 32.2,
    }
    sp = stop_plan(bars, "platform_break", z, atr)
    assert "成本兜底" not in (sp["struct_anchor"] or ""), sp
    assert abs(sp["struct"] - 32.99) < 0.011, sp
    # 大阳中点 (34.22+31.5)/2 = 32.86，再减 0.10×ATR，不是 5% 地板 31.34
    assert abs(sp["hard"] - (32.86 - 0.10 * atr)) < 0.02, sp
    assert sp["hard"] > 32.99 * (1 - STOP_MAX_PCT), sp
    assert z["primary_lo"] == 32.99, z


def test_gap_hard_stop_is_the_gap_not_five_percent_under_the_old_level():
    """富瀚微几何：突破位 60.99，跳空买区从 66 起。结构位留在趋势线，硬止损在缺口下。"""
    bars = _flat(25, 70.0) + [_bar("2026-10-28", 66.0, 72.48, 66.0, 72.48, 8e6)]
    atr = 5.573
    z = {
        "level": 60.99,
        "primary_lo": 66.0,
        "primary_hi": 71.57,
        "anchor": "down_tl",
        "ma5": 66.56,
    }
    sp = stop_plan(bars, "wait", z, atr)
    assert abs(sp["struct"] - 60.99) < 0.011, sp
    assert "成本兜底" not in (sp["struct_anchor"] or ""), sp
    assert abs(sp["hard"] - (66.0 - 0.10 * atr)) < 0.02, sp
    assert sp["hard"] > 60.99 * (1 - STOP_MAX_PCT) + 1, sp


def test_wide_technical_anchor_still_tightens_to_five_percent():
    """技术锚宽于 5% 仍然收紧。这条成本上限不废，只是不许拿它替换本就在线上的结构位。"""
    bars = _flat(20, 100.0) + [_bar("2026-10-28", 100.0, 101.0, 99.0, 100.5)]
    z = {
        "level": 100.0,
        "primary_lo": 99.5,
        "primary_hi": 102.0,
        "anchor": "hl_trendline",
        "ma5": 90.0,
    }
    sp = stop_plan(bars, "line_pullback", z, 2.0)
    assert abs(sp["struct"] - 100.0 * (1 - STOP_MAX_PCT)) < 0.011, sp
    assert "成本上限" in (sp["struct_anchor"] or ""), sp


def _row(entry, stop, r1, name="买区上沿", stop_name="引擎结构止损", need=-0.6, risk_atr=1.0):
    return {
        "entry": entry,
        "entry_name": name,
        "stop": stop,
        "stop_name": stop_name,
        "need_pct": need,
        "risk_atr": risk_atr,
        "r1": r1,
        "prehang": True,
    }


def test_recommend_uses_tighter_technical_stop_when_line_r_is_short():
    """结构位 R<1.5 时，用仍在结构位之上的更紧技术锚，不用结构位以下的成本兜底。"""
    z = {"struct_stop": 32.99, "primary_hi": 34.02, "hard_stop": 32.76}
    rows = [
        _row(34.02, 31.34, 0.33, stop_name="成本兜底(浮亏≤5%)", risk_atr=2.6),
        _row(34.02, 32.99, 0.85, risk_atr=1.0),
        _row(34.02, 33.61, 2.15, stop_name="基准日实体中点", risk_atr=0.40),
        _row(34.02, 33.90, 9.0, stop_name="贴太近", risk_atr=0.27),
        _row(31.99, 31.34, 3.43, name="MA20(滚动)", stop_name="引擎结构止损",
             need=-6.0, risk_atr=0.6),
    ]
    rec = _pick_recommend_cn(rows, z)
    assert rec["entry"] == 34.02
    assert rec["stop"] == 33.61
    assert rec["r1"] == 2.15


def test_recommend_keeps_line_stop_when_its_r_already_clears():
    """结构位自己 R≥1.5 时不改去追更紧的锚。"""
    z = {"struct_stop": 20.85, "primary_hi": 22.22}
    rows = [
        _row(21.22, 20.85, 1.89, name="MA5(滚动)", need=-1.0, risk_atr=0.4),
        _row(21.22, 21.05, 4.0, name="MA5(滚动)", stop_name="更紧",
             need=-1.0, risk_atr=0.3),
    ]
    rec = _pick_recommend_cn(rows, z)
    assert rec["stop"] == 20.85
    assert rec["r1"] == 1.89
