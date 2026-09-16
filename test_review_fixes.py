# -*- coding: utf-8 -*-
"""审查修复回归：P0/P1 买卖点专项。"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rule123 import (
    atr14,
    held_lows_3d,
    is_live_bar,
    is_yizi,
    pivots,
    plan_entry,
    stop_plan,
    too_far_from_zone,
    yang_floor,
    zone_at_level,
    zone_from_demand,
)


def _bar(d, o, h, l, c, v=1e6):
    return {"d": d, "o": o, "h": h, "l": l, "c": c, "v": v}


def test_yizi_not_gap_yang():
    """光脚跳空大阳不得判一字；防守=大阳低点。"""
    bars = [
        _bar("2026-01-01", 99, 100, 98, 100),
        _bar("2026-01-02", 106, 110, 106, 110),
    ]
    atr_v = 13.71
    assert is_yizi(bars[1], 100, atr_v) is False
    fl = yang_floor(bars, 1, atr_v)
    assert fl["yi_zi"] is False
    assert fl["floor"] == 106


def test_true_yizi_uses_prev_close():
    bars = [
        _bar("2026-01-01", 99, 100, 98, 100),
        _bar("2026-01-02", 110, 110.05, 109.98, 110, 0),
    ]
    atr_v = 5.0
    fl = yang_floor(bars, 1, atr_v)
    assert fl["yi_zi"] is True
    assert fl["floor"] == 100


def test_flat_open_yizi_is_yang_bar():
    """只认跳空一字为大阳（SKILL.md）；无缺口微涨一字/收平一字/十字星均否。"""
    from rule123 import is_yang_bar
    prev = _bar("2026-01-01", 100, 110, 100, 110)
    flat = _bar("2026-01-02", 110, 110.05, 110, 110, 1e5)
    up_tick = _bar("2026-01-03", 110.1, 110.15, 110.1, 110.1, 1e5)
    gap_yi = _bar("2026-01-04", 113.5, 113.55, 113.5, 113.5, 1e5)
    atr_v = 2.0
    assert is_yang_bar(flat, atr_v, prev) is False
    assert is_yang_bar(up_tick, atr_v, prev) is False
    assert is_yizi(gap_yi, 110, atr_v) is True
    assert is_yang_bar(gap_yi, atr_v, prev) is True
    doji = _bar("2026-01-05", 100, 100.06, 99.94, 100.0)
    assert is_yang_bar(doji, atr_v, _bar("2026-01-04", 99, 101, 99, 100)) is False


def test_held_lows_no_ratchet_on_lower_lows():
    """逐日阴跌不得判 held。"""
    bars = [_bar("2026-01-01", 100, 108, 99, 107, 3e6)]
    for i, c in enumerate([105, 103, 101, 99, 97], start=2):
        bars.append(_bar(f"2026-01-{i:02d}", c + 0.5, c + 1, c - 0.5, c, 5e5))
    atr_v = 2.0
    assert held_lows_3d(bars, 0, atr_v) is False


def test_pivots_dedup_limit_up_cluster():
    bars = []
    px = 100.0
    for i in range(15):
        bars.append(_bar(f"2026-01-{i + 1:02d}", px, px + 1, px - 1, px + 0.5))
        px += 0.8
    for j in range(6):
        bars.append(_bar(f"2026-02-{j + 1:02d}", 110, 110, 110, 110, 1e5))
    for k in range(5):
        bars.append(_bar(f"2026-02-{k + 10:02d}", 111, 112, 110.5, 111.5))
    Hs, Ls = pivots(bars, w=3)
    for seq in (Hs, Ls):
        for a, b in zip(seq, seq[1:]):
            assert b[0] - a[0] >= 3, (seq, a, b)


def test_stop_plan_two_layers_and_hard_below_buy():
    bars = [_bar("2026-01-01", 17.55, 20.88, 17.41, 19.81, 5e6)]
    z = {
        "level": 19.33,
        "primary_lo": 19.04,
        "primary_hi": 19.62,
        "anchor": "platform_lip",
        "ma5": 18.45,
    }
    atr_v = 0.84
    sp = stop_plan(bars, "platform_break", z, atr_v)
    assert sp["struct"] == 19.33
    assert sp["hard"] < z["primary_lo"]
    assert sp["hard_anchor"] in ("大阳中点", "阳线下沿", "MA5", "缺口下沿")
    assert sp["hard_anchor"] != "买区下沿"


def test_stop_plan_narrow_break_hard_below_buy_lo():
    """贴沿窄幅突破：阳线下沿 − gap 仍可能 ≥ 买区下沿，必须兜底，但锚名不改。"""
    bars = [_bar("2026-01-01", 101.4, 102.0, 101.2, 101.5, 2e6)]
    atr_v = 1.785
    z = zone_at_level(101.0, atr_v, 101.5, "平台突破(优先T1)", {}, bars)
    sp = stop_plan(bars, "platform_break", z, atr_v)
    assert sp["hard"] < z["primary_lo"], (sp, z)
    assert sp["hard_anchor"] in ("大阳中点", "阳线下沿")
    assert sp["hard_anchor"] != "买区下沿"
    assert sp.get("warning") or z.get("stop_warning")


def test_stop_plan_long_yang_keeps_mid_anchor_name():
    """长阳突破：即便数值被压到买区下，hard_anchor 仍为「大阳中点」并挂警告。"""
    bars = [_bar("2026-01-01", 100.5, 106.0, 100.4, 105.5, 3e6)]
    atr_v = 2.0
    z = zone_at_level(100.0, atr_v, 105.5, "平台突破(优先T1)", {}, bars)
    sp = stop_plan(bars, "platform_break", z, atr_v)
    assert sp["hard_anchor"] == "大阳中点"
    assert sp["hard"] < z["primary_lo"]
    assert sp.get("warning") or z.get("stop_warning")


def test_too_far_gate():
    """闸门按买位 level，不按买区上沿；距 level 2.1×ATR 应拦，1.9×ATR 不拦。"""
    atr_v = 2.75
    z = zone_at_level(100, atr_v, 112, "平台突破(优先T1)", {}, [_bar("2026-01-01", 100, 101, 99, 100)])
    assert z["level"] == 100
    assert too_far_from_zone(z, atr_v, 100 + 2.1 * atr_v, limit=2.0) is True
    assert too_far_from_zone(z, atr_v, 100 + 1.9 * atr_v, limit=2.0) is False
    # 距 level=2.2ATR 应拦；相对买区上沿仅 1.7ATR（旧闸门会漏）
    px = 100 + 2.2 * atr_v
    assert (px - z["primary_hi"]) / atr_v < 2.0
    assert too_far_from_zone(z, atr_v, px, limit=2.0) is True


def test_breakout_in_zone_matches_band():
    """突破买区半宽 0.5×ATR，in_zone 同步为 ±0.5。"""
    bars = [_bar("2026-01-01", 100, 101, 99, 100)]
    atr_v = 4.0
    z_in = zone_at_level(100, atr_v, 101.5, "平台突破(优先T1)", {}, bars)
    assert z_in["primary_lo"] == 98.0
    assert z_in["primary_hi"] == 102.0
    assert z_in["in_zone"] is True
    z_out = zone_at_level(100, atr_v, 102.1, "平台突破(优先T1)", {}, bars)
    assert z_out["in_zone"] is False


def test_line_zone_pad_matches_in_zone():
    demand = {
        "anchor": "hl_trendline",
        "level": 100.0,
        "dist_atr": 0.9,
        "hits": 4,
        "atr": 4.0,
        "cluster_lo": 100.0,
        "cluster_hi": 100.0,
    }
    bars = [_bar("2026-01-01", 100, 104, 99, 103.6)]
    z = zone_from_demand(demand, bars, {})
    assert z["primary_lo"] == 96.0
    assert z["primary_hi"] == 104.0
    assert z["in_zone"] is True
    demand2 = dict(demand, dist_atr=1.2)
    z2 = zone_from_demand(demand2, bars, {})
    assert z2["in_zone"] is False


def test_is_live_bar_session():
    bars = [_bar(datetime.date.today().isoformat(), 10, 11, 9, 10.5)]
    noon = datetime.datetime.now().replace(hour=10, minute=30, second=0, microsecond=0)
    assert is_live_bar(bars, market="ASH", now=noon) is True
    lunch = noon.replace(hour=12, minute=0)
    assert is_live_bar(bars, market="ASH", now=lunch) is True
    night = noon.replace(hour=16, minute=0)
    assert is_live_bar(bars, market="ASH", now=night) is False
    assert is_live_bar(bars, market="US", now=night) is True


def test_no_platform_does_not_fallback_to_r1():
    bars = [_bar(f"2026-01-{i + 1:02d}", 100, 101, 99, 100.5, 1e6) for i in range(28)]
    bars += [_bar(f"2026-02-{i + 1:02d}", 100, 101, 99, 100.5, 1e6) for i in range(12)]
    bars.append(_bar("2026-02-20", 100.5, 102, 100, 101.5, 2e6))
    ev = {
        "rvol20": 2.0,
        "platform": None,
        "P0": {"i": 10, "price": 95},
        "P1": {"i": 20, "price": 96},
        "R1": {"i": 30, "price": 101.0},
        "c2": True,
        "w_bottom": None,
        "bull_flag": None,
        "down_tl": None,
    }
    plan = plan_entry(bars, ev)
    assert plan["mode"] != "platform_break"


def test_plan_entry_no_yang_in_uptrend_does_not_crash():
    """升势 + 无大阳 + 离线稍远：不得 TypeError（第二轮 P0-①）。"""
    bars = []
    px = 100.0
    for i in range(78):
        if i % 6 < 3:
            px += 0.45
        else:
            px -= 0.30
        d = f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}"
        bars.append(_bar(d, px - 0.15, px + 0.18, px - 0.40, px, 1e6))
    from rule123 import build_ev
    ev, bars2, meta = build_ev(bars, drop_live=False)
    assert ev is not None, meta
    plan = plan_entry(bars2, ev)
    assert "mode" in plan
    assert plan.get("reason") is None or "TypeError" not in str(plan.get("reason"))


if __name__ == "__main__":
    test_yizi_not_gap_yang()
    test_true_yizi_uses_prev_close()
    test_flat_open_yizi_is_yang_bar()
    test_held_lows_no_ratchet_on_lower_lows()
    test_pivots_dedup_limit_up_cluster()
    test_stop_plan_two_layers_and_hard_below_buy()
    test_stop_plan_narrow_break_hard_below_buy_lo()
    test_stop_plan_long_yang_keeps_mid_anchor_name()
    test_too_far_gate()
    test_breakout_in_zone_matches_band()
    test_line_zone_pad_matches_in_zone()
    test_is_live_bar_session()
    test_no_platform_does_not_fallback_to_r1()
    test_plan_entry_no_yang_in_uptrend_does_not_crash()
    print("ok")
