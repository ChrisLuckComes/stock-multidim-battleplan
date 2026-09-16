# -*- coding: utf-8 -*-
"""审查修复回归：P0/P1 买卖点专项。"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rule123 import (
    SKILL_HARD_ANCHORS,
    atr14,
    find_impulse_pause,
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
    """贴沿窄幅突破：阳线下沿高于突破位时，锚价−gap 会落在买区内。
    处理方式是抬买区下沿（硬止损让路给锚），不是改锚名、也不是偷偷下移数值。"""
    bars = [_bar("2026-01-01", 101.4, 102.0, 101.2, 101.5, 2e6)]
    atr_v = 1.785
    z = zone_at_level(101.0, atr_v, 101.5, "平台突破(优先T1)", {}, bars)
    sp = stop_plan(bars, "platform_break", z, atr_v)
    assert sp["hard"] < z["primary_lo"], (sp, z)
    assert sp["hard_anchor"] in ("大阳中点", "阳线下沿")
    assert sp["hard_anchor"] != "买区下沿"
    assert sp.get("warning") or z.get("stop_warning")


def test_stop_plan_long_yang_keeps_mid_anchor_name():
    """长阳突破：锚名保持 SKILL 合法锚，且数值必须严格来自该锚（名值绑定）。"""
    bars = [_bar("2026-01-01", 100.5, 106.0, 100.4, 105.5, 3e6)]
    atr_v = 2.0
    z = zone_at_level(100.0, atr_v, 105.5, "平台突破(优先T1)", {}, bars)
    sp = stop_plan(bars, "platform_break", z, atr_v)
    y = bars[-1]
    px = {"大阳中点": (y["h"] + y["l"]) / 2.0, "阳线下沿": y["l"],
          "MA5": z.get("ma5"), "缺口下沿": None}[sp["hard_anchor"]]
    assert sp["hard_anchor"] in SKILL_HARD_ANCHORS
    assert px is not None
    assert abs(sp["hard"] - (px - 0.10 * atr_v)) < 0.011, (sp, px)
    assert sp["hard"] < z["primary_lo"]
    assert sp.get("warning") or z.get("stop_warning")


def test_hard_stop_name_matches_value():
    """不变量：hard 恒等于「锚价 − 0.10×ATR」，且锚价落在买区下沿之下。

    守护第五轮那条「锚名写大阳中点、数值却差 7.5×ATR」的坑。
    """
    cases = [
        ("跳空光脚大阳", _bar("2026-08-25", 125.0, 135.0, 125.0, 135.0, 8e6)),
        ("无跳空长阳", _bar("2026-08-25", 103.0, 113.0, 102.5, 112.6, 8e6)),
        ("普通阳线突破", _bar("2026-08-25", 111.0, 115.0, 110.5, 114.8, 5e6)),
    ]
    for label, k in cases:
        bars = [_bar(f"2026-07-{i + 1:02d}", 112.5, 113.0, 112.0, 112.5, 1e6) for i in range(30)]
        bars.append(k)
        atr_v = atr14(bars)
        y, prev_c = bars[-1], bars[-2]["c"]
        z = zone_at_level(112.5, atr_v, y["c"], "平台突破(优先T1)", {}, bars)
        sp = stop_plan(bars, "platform_break", z, atr_v)
        assert sp["hard_anchor"] in SKILL_HARD_ANCHORS, (label, sp)
        px = {"大阳中点": (y["h"] + y["l"]) / 2.0, "阳线下沿": y["l"],
              "MA5": z.get("ma5"), "缺口下沿": prev_c}[sp["hard_anchor"]]
        assert abs(sp["hard"] - (px - 0.10 * atr_v)) < 0.011, (label, sp, px, atr_v)
        assert sp["hard"] < z["primary_lo"], (label, sp, z)


def test_gap_breakout_zone_above_gap():
    """跳空突破：买区基准上移到缺口上沿（= 突破根低点），不得落进缺口。"""
    bars = [_bar(f"2026-07-{i + 1:02d}", 112.5, 113.0, 112.0, 112.5, 1e6) for i in range(30)]
    bars.append(_bar("2026-08-25", 125.0, 135.0, 125.0, 135.0, 8e6))
    atr_v = atr14(bars)
    z = zone_at_level(112.5, atr_v, 135.0, "平台突破(优先T1)", {}, bars)
    assert z["gap_hi"] == 125.0
    assert z["primary_lo"] >= 125.0, z          # 缺口 112.5→125 内不得挂买单
    sp = stop_plan(bars, "platform_break", z, atr_v)
    assert sp["hard_anchor"] == "阳线下沿"        # SKILL:101「防守仍是大阳低点」
    assert abs(sp["hard"] - (125.0 - 0.10 * atr_v)) < 0.011, sp
    assert sp["hard"] < z["primary_lo"]


def test_breakout_zone_not_below_level():
    """突破买区不得落在「还没突破」的区域：下沿 ≥ 突破位。"""
    bars = [_bar(f"2026-07-{i + 1:02d}", 100, 100.4, 99.6, 100.0, 1e6) for i in range(40)]
    bars.append(_bar("2026-08-25", 100.2, 101.6, 100.1, 101.5, 3e6))
    atr_v = atr14(bars)
    z = zone_at_level(100.0, atr_v, 101.5, "平台突破(优先T1)", {}, bars)
    assert z["primary_lo"] >= 100.0, z
    assert z["primary_hi"] > z["primary_lo"]


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


def test_breakout_zone_starts_at_level():
    """突破买区自突破位【单边向上】1.0×ATR，in_zone 同步。"""
    bars = [_bar("2026-01-01", 100, 101, 99, 100)]
    atr_v = 4.0
    z_in = zone_at_level(100, atr_v, 101.5, "平台突破(优先T1)", {}, bars)
    assert z_in["primary_lo"] == 100.0
    assert z_in["primary_hi"] == 104.0
    assert z_in["in_zone"] is True
    # 上沿之内仍算在区内；越过上沿即出区
    assert zone_at_level(100, atr_v, 104.0, "平台突破(优先T1)", {}, bars)["in_zone"] is True
    assert zone_at_level(100, atr_v, 104.1, "平台突破(优先T1)", {}, bars)["in_zone"] is False
    # 跌破突破位 = 没突破，不算在区内
    assert zone_at_level(100, atr_v, 99.9, "平台突破(优先T1)", {}, bars)["in_zone"] is False


def _breakout_at(k_target, plat=100.0):
    """构造：40 根基座（平台沿=plat）+ 一根突破根，使现价距突破位 ≈ k_target×ATR。"""
    base = [_bar(f"2026-06-{i + 1:02d}", 100.0, 100.4, 99.6, 100.0, 1e6) for i in range(40)]
    c, bars, a = 100.9, None, None
    for _ in range(80):
        bars = base + [_bar("2026-08-25", 100.2, c + 0.2, 100.05, c, 4e6)]
        a = atr14(bars)
        d = (c - plat) / a
        if abs(d - k_target) < 0.005:
            break
        c += (k_target - d) * a * 0.7
    ev = {
        "rvol20": 2.0,
        "platform": {"i": 39, "price": plat},
        "P0": {"i": 10, "price": 95.0},
        "P1": {"i": 30, "price": 96.0},
        "R1": {"i": 39, "price": plat},
        "c2": True,
        "w_bottom": None,
        "bull_flag": None,
        "down_tl": None,
    }
    return bars, c, a, plan_entry(bars, ev)


def test_breakout_extended_band_still_actionable():
    """出买区上沿、但距突破位 ≤2×ATR：仍可执行（只挂回踩单）；>2×ATR 才 wait。

    守护「拿买区半宽去卡可执行闸门」——SKILL 唯一的不追线是 2×ATR，
    买区位（≤1.0×ATR）与可执行闸门（≤2.0×ATR）是两件事。
    """
    # 1.5×ATR：已出买区上沿、仍在闸门内
    bars, c, a, plan = _breakout_at(1.5)
    z = plan["buy_zone"]
    assert z["in_zone"] is False, (c, z)
    assert c > z["primary_hi"]
    assert 1.0 < (c - z["level"]) / a <= 2.0
    assert plan["recommend"] is True, plan
    assert z.get("chase_only") is True, z
    assert plan["mode"] == "platform_break"
    assert "挂" in plan["verdict"], plan["verdict"]
    assert "禁止市价追" in plan["note"], plan["note"]

    # 买区内不受影响
    _, _, _, plan_in = _breakout_at(0.5)
    assert plan_in["recommend"] is True
    assert plan_in["buy_zone"]["in_zone"] is True
    assert not plan_in["buy_zone"].get("chase_only")

    # >2×ATR：不追
    _, _, _, plan_far = _breakout_at(2.5)
    assert plan_far["recommend"] is False, plan_far
    assert plan_far["mode"] == "wait"
    assert plan_far["verdict"] == "突破已延伸·等回踩"


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


def _yang_today_bars():
    """上升趋势（锯齿，有摆动高低点）+ 一根大阳（低 19.09 / 高 20.97，约 2.4×ATR）。"""
    bars = []
    px = 17.5
    for i in range(78):
        px += 0.06 if (i % 6 < 3) else -0.02
        d = f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}"
        bars.append(_bar(d, px - 0.30, px + 0.42, px - 0.36, px, 8e5))
    bars.append(_bar("2026-08-25", 19.12, 20.97, 19.09, 20.97, 1.9e7))
    return bars


def test_impulse_pause_zone_is_tight_band():
    """大阳后缩量回踩：买区必须是「贴大阳低点 1.0×ATR」的下单带。

    守护「把大阳体（低点~高点）当买区」——大阳体常宽达 2~2.5×ATR，
    落在这样的区间里等于没有价位（挂下沿和挂上沿是两笔不同的交易）。
    """
    bars = _yang_today_bars()
    atr_v = atr14(bars)
    imp = find_impulse_pause(bars, atr_v)
    assert imp["state"] == "yang_today", imp
    z = imp.get("zone")
    assert z is not None, "yang_today 必须带 zone，否则调用方回退到别的买区、同屏两个买区"
    band = z["primary_hi"] - z["primary_lo"]
    assert abs(band - 1.0 * atr_v) < 0.02, (band, atr_v, z)
    assert abs(z["primary_lo"] - 19.09) < 0.02, z
    # 上沿不得落在大阳上半部（大阳 19.09~20.97，中点 20.03）
    assert z["primary_hi"] <= 20.03, z
    # 大阳体另存，供「是否仍在调整区」判定
    assert z["body_lo"] == 19.09 and z["body_hi"] == 20.97, z


def test_impulse_pause_body_mid_is_not_in_zone():
    """回踩到大阳中部不算到位：买区贴大阳低点，in_zone 必须为 False。"""
    bars = _yang_today_bars()
    for j, c in enumerate((20.30, 20.20, 20.10, 20.05), start=1):
        bars.append(_bar(f"2026-08-{25 + j}", c + 0.05, c + 0.12, c - 0.10, c, 3e5))
    atr_v = atr14(bars)
    imp = find_impulse_pause(bars, atr_v)
    z = imp.get("zone")
    assert z is not None, imp
    assert z["primary_hi"] < 20.05, z
    assert z["in_zone"] is False, (z, imp["state"])
    assert imp["state"] != "digest", imp
    assert imp["state"] in ("waiting_back", "no_shrink", "extended"), imp


def test_yang_today_plan_keeps_single_zone():
    """大阳当日：buy_zone 必须来自 impulse_pause，且与 note 里的区间一致。

    守护「yang_today 漏传 zone → buy_zone 回退成沿线回踩的 EMA10 买区」，
    那会让同一张计划卡上出现两个互相矛盾的买区。
    """
    bars = _yang_today_bars()
    n = len(bars)
    ev = {
        "rvol20": 2.0,
        "platform": None,                        # 无活平台沿 → 不做平台突破
        "P0": {"i": 10, "price": min(b["l"] for b in bars[8:14])},
        "P1": {"i": n - 25, "price": min(b["l"] for b in bars[n - 28:n - 20])},
        "R1": {"i": 5, "price": max(b["h"] for b in bars[:10])},
        "c2": True,
        # 把 W底 / 旗形判死，隔离出 impulse_pause 这条路径
        "w_bottom": {"neckline": 100.0},
        "bull_flag": {"tl_now": 100.0, "days_above_tl": 0},
        "down_tl": None,
    }
    plan = plan_entry(bars, ev)
    z = plan["buy_zone"]
    assert z["type"] == "大阳后缩量回踩(次优先T2)", (plan["mode"], plan["verdict"], z)
    assert z["anchor"] in ("yang_digest", "yang_gap"), z
    lo, hi = z["primary_lo"], z["primary_hi"]
    assert lo is not None and hi is not None, z
    assert f"{lo}-{hi}" in plan["note"], (plan["note"], lo, hi)
    # note 里不得再把整条大阳体当买区印出来
    assert f"{z['body_lo']}-{z['body_hi']}" not in plan["note"], plan["note"]


def _reversal_bars(yang_vol=1.9e7, tail=None):
    """反转态：缓降通道（自 P1 曾创新低 → c2=False），末根放量大阳收复 P1。

    末根：O19.70 H20.97 L19.09 C20.97（body 1.27 ≈ 2×ATR，涨幅 6.4%）。
    """
    bars = []
    px = 24.0
    for i in range(60):
        px -= 0.08
        d = f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}"
        bars.append(_bar(d, px + 0.10, px + 0.35, px - 0.30, px, 7e5))
    bars.append(_bar("2026-08-25", 19.70, 20.97, 19.09, 20.97, yang_vol))
    for j, (c, v) in enumerate(tail or []):
        bars.append(_bar(f"2026-08-{26 + j}", c + 0.05, c + 0.12, c - 0.10, c, v))
    return bars


def _reversal_ev():
    """反转态 ev：P1=20.50（低于大阳收盘 → 结构已修复），c2=False。"""
    return {
        "rvol20": 2.0,
        "P0": {"i": 10, "price": 23.00},
        "P1": {"i": 40, "price": 20.50},
        "c2": False,
        "R1": None,
        "platform": None,
        "w_bottom": {"neckline": 100.0},
        "bull_flag": {"tl_now": 100.0, "days_above_tl": 0},
        "down_tl": None,
    }


def test_reversal_yang_gate_relaxes():
    """反转态 + 放量大阳收复 P1 → 放宽门控放行，且必须给出买区。

    守护「ILMN 型信号被整段丢弃」：c2=False 但已站上 P1 的放量大阳，
    原 uptrend 门控会把整套大阳回踩分支跳过，产出 wait 且买区为空。
    """
    bars = _reversal_bars()
    plan = plan_entry(bars, _reversal_ev())
    z = plan["buy_zone"]
    assert z.get("gate") == "reversal_yang", (z.get("gate"), plan["verdict"])
    assert z.get("relaxed") is True, z
    assert z.get("relaxed_reason"), z
    assert z["primary_lo"] is not None and z["primary_hi"] is not None, z
    assert z["state"] == "yang_today", z
    # 买区宽度仍须是贴防守位 1.0×ATR 的下单带（松绑不得复辟「整条大阳体当买区」）
    band = z["primary_hi"] - z["primary_lo"]
    assert abs(band - 1.0 * atr14(bars)) < 0.05, (band, z)
    assert "【反转态·放量大阳·已放宽】" in plan["note"], plan["note"]


def test_reversal_yang_requires_volume():
    """反转态 + 缩量大阳 → 不放宽（缺放量确认的破位修复不认）。"""
    bars = _reversal_bars(yang_vol=7e5)          # 与大阳前均量持平 → 无放量
    plan = plan_entry(bars, _reversal_ev())
    assert plan["buy_zone"].get("gate") != "reversal_yang", plan["buy_zone"]
    assert plan["verdict"] == "等待", plan["verdict"]


def test_reversal_yang_rejects_pullback_below_mid():
    """反转态大阳后回吐到大阳体下半部 → 不放宽（反转已失效）。"""
    bars = _reversal_bars(tail=[(19.50, 3e5), (19.45, 2.8e5), (19.50, 2.7e5)])
    plan = plan_entry(bars, _reversal_ev())
    z = plan["buy_zone"]
    assert z.get("gate") != "reversal_yang", (z.get("gate"), plan["note"])
    assert plan["verdict"] == "等待", (plan["verdict"], plan["note"])


if __name__ == "__main__":
    test_yizi_not_gap_yang()
    test_true_yizi_uses_prev_close()
    test_flat_open_yizi_is_yang_bar()
    test_held_lows_no_ratchet_on_lower_lows()
    test_pivots_dedup_limit_up_cluster()
    test_stop_plan_two_layers_and_hard_below_buy()
    test_stop_plan_narrow_break_hard_below_buy_lo()
    test_stop_plan_long_yang_keeps_mid_anchor_name()
    test_hard_stop_name_matches_value()
    test_gap_breakout_zone_above_gap()
    test_breakout_zone_not_below_level()
    test_too_far_gate()
    test_breakout_zone_starts_at_level()
    test_breakout_extended_band_still_actionable()
    test_line_zone_pad_matches_in_zone()
    test_is_live_bar_session()
    test_no_platform_does_not_fallback_to_r1()
    test_plan_entry_no_yang_in_uptrend_does_not_crash()
    test_impulse_pause_zone_is_tight_band()
    test_impulse_pause_body_mid_is_not_in_zone()
    test_yang_today_plan_keeps_single_zone()
    test_reversal_yang_gate_relaxes()
    test_reversal_yang_requires_volume()
    test_reversal_yang_rejects_pullback_below_mid()
    print("ok")
