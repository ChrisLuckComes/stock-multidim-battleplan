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


def test_no_setup_branch_has_empty_buy_zone():
    """「未形成任何买法」的兜底分支不得给出买区。

    原先该分支不传 buy_zone，pack() 便回退到 bz_line（活平台沿线位），
    于是 verdict=等待 的票照样打印出一个可挂单区间 —— 与 note
    「未形成平台/W底/…」自相矛盾，也是「看到买区、其实没有买点」的误判源。
    不变式：buy_zone 带下单带 ⇔ 该 verdict 确实给出了可挂单价位。
    """
    from rule123 import build_ev
    bars = _yang_today_bars()
    # 大阳之后深度破位：结构不成立 → 落到「未形成任何买法」兜底分支
    bars.append(_bar("2026-08-26", 18.85, 18.95, 18.40, 18.50, 8e6))
    ev, bars2, _meta = build_ev(bars, drop_live=False)
    plan = plan_entry(bars2, ev)
    z = plan.get("buy_zone") or {}
    assert plan["recommend"] is False, plan
    assert "未形成" in (plan.get("note") or ""), plan
    assert z.get("primary_lo") is None, z
    assert z.get("primary_hi") is None, z
    # 空区仍须保留完整 schema，消费方按 key 取值不应 KeyError
    for k in ("invalid", "invalid_reason", "invalidation", "in_zone", "chase_only"):
        assert k in z, (k, z)


def test_key_break_cap_never_below_anchor():
    """追高上限必须以 K 为锚，绝不能低于突破位本身。

    旧口径 min(K+0.60ATR, 开盘+0.80ATR) 在「跳空不足 + 突破位较远」时会把
    上限压到 K 以下 —— 上限低于突破位，触发价必然越线，通道永远不成交。
    ILMN 2026-09-16：K=231.81、开盘 223.70、ATR 9.181
        旧 = min(237.32, 231.05) = 231.05 < K   ← bug
        新 = 231.81 + 1.20×9.181 = 242.83
    """
    from probe_intraday import key_break_cap
    K, op, atr = 231.81, 223.70, 9.181
    us = key_break_cap(op, K, atr, "US")
    ash = key_break_cap(op, K, atr, "ASH")
    assert us > K, (us, K)                      # 必须高于突破位本身
    assert abs(us - (K + 1.20 * atr)) < 0.02, us
    assert abs(ash - (K + 0.60 * atr)) < 0.02, ash
    assert ash < us, (ash, us)                  # A 股口径必须比美股严
    assert 234.94 <= us, (234.94, us)           # 该触发价在美股上限内（旧口径会误判追高）
    # 跳空已越过 K：改用开盘锚，与 K 锚不等价
    gap = key_break_cap(240.0, K, atr, "US")
    assert abs(gap - (240.0 + 0.60 * atr)) < 0.02, gap
    assert gap != us, (gap, us)


def test_ash_limit_price_board_pct():
    """A 股涨停价：主板 ±10%，创业板 / 科创板 ±20%。"""
    from probe_intraday import ash_limit_price
    assert ash_limit_price("002961", 19.06) == 20.97
    assert ash_limit_price("600000", 10.00) == 11.00
    assert ash_limit_price("300723", 40.00) == 48.00
    assert ash_limit_price("301000", 40.00) == 48.00
    assert ash_limit_price("688002", 100.00) == 120.00


def test_pullback_confirm_states():
    """通道 C（分钟级回踩确认）：五个状态 + 两个已修 bug 的回归。"""
    from probe_intraday import pullback_confirm
    atr, S = 10.0, 100.0        # 回踩带 = 97.0 ~ 103.0

    def bar(t, o, h, l, c, v):
        return {"d": f"2026-09-15 {t}", "o": o, "h": h, "l": l, "c": c, "v": v}

    # ① ok：启动 → 回踩缩量阴线 → 收阳且低点抬高
    mins = [bar("09:40", 99.0, 101.0, 98.5, 100.0, 1000),
            bar("09:45", 100.2, 100.5, 99.2, 99.6, 900),
            bar("09:50", 99.5, 100.3, 99.4, 100.1, 800)]
    r = pullback_confirm(mins, 0, S, atr)
    assert r["state"] == "ok", r
    assert r["entry"] == 100.10 and r["low"] == 99.2, r
    # 止损 = 回踩低 − 0.10×ATR（用 low 而非 entry 做锚，风险 1.90/2.00≈1.43 同量级）
    assert abs((r["low"] - 0.10 * atr) - 98.20) < 1e-9, r

    # ② bug 回归：**上涨**中的根不算回踩（收在带内也不行）
    #    实测 ILMN 9/15 21:45 就是这种根（放量继续上攻），旧逻辑误判 heavy
    mins2 = [bar("09:40", 99.0, 101.0, 98.5, 100.0, 1000),
             bar("09:45", 100.2, 102.5, 100.0, 102.0, 5000),
             bar("09:50", 102.1, 103.0, 101.8, 102.8, 4000)]
    assert pullback_confirm(mins2, 0, S, atr)["state"] == "pending"

    # ③ bug 回归：低量启动根不能当缩量基数
    #    当日此前均量 2333 > 启动根 1000 → base = 2333，门槛 4667
    #    回踩根 3200 若拿启动根当分母（1000）会被误判 heavy
    mins3 = [bar("09:30", 98.0, 98.5, 97.5, 98.2, 3000),
             bar("09:35", 98.2, 99.2, 98.0, 99.0, 3000),
             bar("09:40", 99.0, 101.0, 98.5, 100.0, 1000),
             bar("09:45", 100.4, 100.6, 99.5, 99.7, 3200),
             bar("09:50", 99.7, 100.4, 99.6, 100.2, 2600)]
    assert pullback_confirm(mins3, 2, S, atr)["state"] == "ok"

    # ④ 真爆量下跌 → heavy（出货，不是回踩）
    mins4 = [bar("09:40", 99.0, 101.0, 98.5, 100.0, 1000),
             bar("09:45", 100.4, 100.6, 99.5, 99.7, 9999)]
    assert pullback_confirm(mins4, 0, S, atr)["state"] == "heavy"

    # ⑤ 跌破带下沿 → failed（突破失败）
    mins5 = [bar("09:40", 99.0, 101.0, 98.5, 100.0, 1000),
             bar("09:45", 97.5, 97.6, 96.5, 96.8, 900)]
    assert pullback_confirm(mins5, 0, S, atr)["state"] == "failed"

    # ⑥ 启动即最后一根 → pending（还没有回踩 K 线）
    assert pullback_confirm(mins[:1], 0, S, atr)["state"] == "pending"


def test_pullback_requires_launch():
    """通道 C 没有「已启动」前置时必须不出信号（防止在没突破时凭空给回踩单）。"""
    import probe_intraday as P
    src = open(P.__file__, encoding="utf-8").read()
    # 两处调用点都必须由 pb_ctx（A股）/ pb_ctx_us（美股）守卫
    assert src.count("if live and mins and pb_ctx:") == 1, "A 股调用点缺前置守卫"
    assert src.count("if mins and pb_ctx_us:") == 1, "美股调用点缺前置守卫"
    # pb_ctx 必须在分支外初始化，否则 kb 为空时 UnboundLocalError（实测 601233 崩过）
    assert "pb_ctx = None                     # 通道 C 的前置状态" in src


def test_breakout_preorder_grades():
    """突破预案单 setup（老罗 2026-09-16 SDGR 案例驱动；09-17 删掉分级）。

    setup：上方有明确突破位 K，且价格贴着它收 —— 此时盘前就该给出
    「站上 K 买入」的条件单，而不是只给一个永不成交的回踩价。
    分级（strong/normal）已删：全样本 121 条 A 股突破单里 strong 一次未触发，
    分级没产生信息；现在只剩「距 K ≤ 门槛」一道，两轨门槛不同。
    """
    from probe_intraday import breakout_preorder

    def bar(d, o, h, l, c, v):
        return {"d": d, "o": o, "h": h, "l": l, "c": c, "v": v}

    # 12 根：idx5 的 21.40 是唯一的摆动高点（左右各 3 根都更低）
    pre = [bar(f"2026-08-{10 + i:02d}", 19.4, h, 19.2, 19.5, 900) for i, h in
           enumerate([19.6, 19.5, 19.4, 19.3, 19.5, 21.40, 19.8, 19.9,
                      19.7, 19.6, 19.5])]
    atr_v = 1.0

    # ① 距 0.65×ATR（原 strong 档的形态）：**分级已删**（老罗 2026-09-17）——
    #    同一个样本，两轨结论不同：美股档（门槛 0.80）放行，A 股档（0.50）判 far。
    #    这一条钉住「删 strong」：不再有「放量大阳」这条晋级条件，只剩距离。
    bo = pre + [bar("2026-08-24", 19.03, 21.40, 18.84, 20.75, 1900)]
    r = breakout_preorder(bo, atr_v, 20.75, profile="us")
    assert r["grade"] == "normal", r
    assert r["K"] == 21.40 and r["trigger"] == 21.50, r
    assert r["stop"] == 21.10 and r["risk"] == 0.4, r
    # 近窗口内无更高阻力 → 目标退回 K + 2.0×ATR
    assert r["target"] == 23.40, r
    assert r["cap"] == 22.60, r                  # K + 1.20×ATR（美股档）
    assert "big_yang" not in r, r                # 分级字段已删干净

    r2 = breakout_preorder(bo, atr_v, 20.75, profile="ash")
    assert r2["grade"] == "far", r2              # 0.65 > BO_NEAR_ASH 0.50
    assert "trigger" not in r2, r2

    # ② 距 0.45×ATR → A 股档放行（≤0.50），追高上限收到 0.60×ATR
    ash_ok = pre + [bar("2026-08-24", 20.50, 21.00, 20.40, 20.95, 900)]
    r3 = breakout_preorder(ash_ok, atr_v, 20.95, profile="ash")
    assert r3["grade"] == "normal", r3
    assert r3["trigger"] == 21.50, r3
    assert r3["cap"] == 22.00, r3

    # ③ 距 0.90×ATR → 两轨都只报位置，不给可挂价
    far = pre + [bar("2026-08-24", 20.20, 20.60, 20.10, 20.50, 900)]
    r4 = breakout_preorder(far, atr_v, 20.50, profile="us")
    assert r4["grade"] == "far", r4
    assert "trigger" not in r4, r4
    assert breakout_preorder(far, atr_v, 20.50, profile="ash")["grade"] == "far"


def test_near_resistance_window():
    """目标位必须取自**近期**阻力。

    bug 回归：pivots 在全历史上找摆动高点，会把一年前的价位当目标。
    SDGR 2026-09-15 实测 —— 目标被算成 22.03（来自 2025-10-02），
    盈亏比显示 1.30:1 而搁置；当日实际冲到 23.71。
    """
    from probe_intraday import near_resistance

    def bar(d, o, h, l, c, v=900):
        return {"d": d, "o": o, "h": h, "l": l, "c": c, "v": v}

    # 前 10 根：深处有一年高点 22.034（idx3，左右各 3 根更低 → 合法 pivot）
    old = [bar(f"s{i:03d}", 21.0, h, 20.9, 21.0) for i, h in
           enumerate([21.0, 21.1, 21.2, 22.034, 21.5, 21.4, 21.3, 21.2,
                      21.1, 21.0])]
    # 后 60 根：全部 19.5 附近（无任何 > 21.60 的高点）
    recent = [bar(f"r{i:03d}", 19.4, 19.5, 19.2, 19.45) for i in range(60)]
    bars = old + recent
    # 窗口 60：只看得到 recent → 找不到阻力（正确行为）
    assert near_resistance(bars, 21.40, 1.0, window=60) is None
    # 放到全历史，才会把一年前的价位翻出来（这就是被修掉的行为）
    got = near_resistance(bars, 21.40, 1.0, window=999)
    assert got is not None and abs(got - 22.034) < 1e-9, got


def test_bo_gap_cancel():
    """突破单的开盘跳空保护。"""
    from probe_intraday import bo_gap_cancel
    bo = {"K": 21.40, "trigger": 21.50, "stop": 21.10, "cap": 22.60}
    atr = 1.0
    assert bo_gap_cancel(20.78, bo, atr) is None          # SDGR 实况：有效
    assert bo_gap_cancel(21.60, bo, atr) is None          # 略高，仍 < 触发+0.6
    assert "跳空" in bo_gap_cancel(22.20, bo, atr)         # ≥22.10 → 取消
    assert "结构" in bo_gap_cancel(20.30, bo, atr)         # ≤20.40 → 取消
    assert bo_gap_cancel(None, bo, atr) is None           # 无开盘价 → 不误判


def test_ash_structural_ceiling():
    """闸门下的「最高可交易价」：5 万账户 / 30% → 主板 150 元、科创 75 元。

    这是「点位到了也买不到」的唯一正当来源（物理约束：最小申报单位 × 股价），
    必须在**下单前**就筛掉，不能给了买区再告诉人买不了。
    """
    from probe_intraday import ash_price_ceiling, no_ash_reason, ash_lots
    assert ash_price_ceiling("002961", 50000) == 150.0   # 100 股单位
    assert ash_price_ceiling("688002", 50000) == 75.0    # 200 股单位
    assert ash_price_ceiling("002961", 0) is None        # 未给账户 → 不猜
    # 门槛之上：1 手即超闸门 → 必须 None（不悄悄超标）
    assert ash_lots(50000, 160.0, 150.0, "002961") is None
    assert ash_lots(50000, 76.0, 70.0, "688002") is None
    # 门槛之下：正常给股数
    assert ash_lots(50000, 140.0, 130.0, "002961") == 100
    assert ash_lots(50000, 74.0, 70.0, "688002") == 200
    # 说明里要出现「结构性不可交易」与门槛价
    s = no_ash_reason("688002", 50000, 200.0)
    assert "结构性不可交易" in s and "75" in s
    s2 = no_ash_reason("002961", 50000, 160.0)
    assert "结构性不可交易" in s2 and "150" in s2
    # 1 手在额度内 → 不冤枉成「结构性不可交易」，只报金额占比
    # （2000 账户 / 30% → 主板门槛仅 6 元，取 5 元才是门槛内）
    s3 = no_ash_reason("002961", 2000, 5.0)
    assert "结构性不可交易" not in s3 and "硬约束" in s3


def test_ash_lots_is_risk_based():
    """A 股仓位改用风险预算法（与美股 us_lots 同构）。

    旧口径是「计划仓 1/3」—— 一个拍脑袋的比例，与风险无关；也不能没有
    --qty 就不给股数。新口径 = min(账户 × 1.5% / 每股风险, 账户 × 30% / 价格)。
    """
    from probe_intraday import ash_lots
    # 风险 1.0/股 → 750 股；仓位闸 50000×30%/20 = 750 股 → 取 700（向下整手）
    assert ash_lots(50000, 20.0, 19.0, "002961") == 700
    # 止损很近（0.10）→ 风险预算法给 7500 股，被仓位闸 150 夹住 → 100 股
    assert ash_lots(50000, 100.0, 99.9, "002961") == 100
    # 止损很宽（5.0）→ 150 股，未被闸门干预
    assert ash_lots(50000, 20.0, 15.0, "002961") == 100
    # 结构止损可能是文字 → None，不得崩
    assert ash_lots(50000, 20.0, "收盘破19", "002961") is None
    assert ash_lots(50000, 20.0, None, "002961") is None
    assert ash_lots(50000, 20.0, 21.0, "002961") is None   # 止损在入场之上


def test_ash_lots_min_lot_by_board():
    """最小申报单位：科创板 688/689 = 200 股，主板 / 创业板 = 100 股。

    创业板 300/301 是 100 股 —— 旧代码把 300/301 也当成 200，已修。
    """
    from probe_intraday import first_lot_of, ash_lots
    assert first_lot_of("688002") == 200
    assert first_lot_of("689009") == 200
    assert first_lot_of("300723") == 100
    assert first_lot_of("301000") == 100
    assert first_lot_of("002961") == 100
    assert first_lot_of("601233") == 100
    # 同一笔单，科创板取整到 200 的倍数
    assert ash_lots(50000, 20.0, 19.0, "688002") == 600
    assert ash_lots(50000, 20.0, 19.0, "002961") == 700
    # 1 手即超仓位上限 → None（明确不做，不悄悄超标）
    assert ash_lots(5000, 200.0, 190.0, "688002") is None
    assert ash_lots(2000, 10.0, 9.0, "002961") is None     # 1 手 1000 元 = 50%
    # 但 1 手在额度内仍要给 —— 否则钱少就永远建不了仓
    assert ash_lots(5000, 10.0, 9.0, "002961") == 100


def test_ash_late_is_not_a_ban():
    """14:30 不得再作为禁买线（老罗 2026-09-16 明确否掉）。

    旧规则「14:30 后不再新开仓」会让「点位到了却买不到」。挂单式的通道
    （突破单 / 回踩确认）只看价不看钟 —— 时间不是否决理由，风险提示即可。
    """
    src = (Path(__file__).resolve().parent / "probe_intraday.py").read_text(
        encoding="utf-8")
    assert '"state": "late"' not in src, "14:30 拦截的分支又回来了"
    assert "已过 14:30" not in src, "14:30 否决文案又回来了"
    assert "14:30 后不再新开仓" not in src, "14:30 禁令文案又回来了"


def test_room_and_cap_propagates_stop_conflict():
    """room_and_cap 必须把 stop_plan 的「铁律二」修正回写到调用方的 z。

    历史缺陷（601233 2026-09-01 实证，mode=wait）：room_and_cap 只把
    `dict(z)` 副本交给 stop_plan，于是「上抬买区下沿」和 stop_warning
    全被丢弃 —— 报告上买区仍是 25.52~26.89，而硬止损算成 26.84，
    **落在买区之内**，挂单价 26.89 距硬止损仅 0.05（0.19%）：
        工具自己打出「风险 0.05 / 收益 1.21 = 24.20:1」
    仓位闸门与盈亏比闸门全部建在这个假风险上。
    修好后：买区下沿必须被抬到硬止损之上，并落 stop_warning。
    """
    from probe_intraday import room_and_cap
    y = _bar("2026-09-01", 27.40, 27.60, 26.98, 27.17, 6e7)   # 收阴 → 只取阳线下沿
    bars = [_bar("2026-08-%02d" % d, 26, 27, 25.8, 26.2, 2e6)
            for d in (10, 11, 12, 13, 14)] + [y]
    atr_v = 1.375
    z = zone_at_level(26.98, atr_v, 27.17, "平台突破(优先T1)", {}, bars)
    z["primary_lo"], z["primary_hi"] = 25.52, 26.89
    z["anchor"] = "platform_lip"
    z["invalidation"] = 25.52
    rc = room_and_cap(bars, z, "wait", atr_v)
    assert rc["hard"] == 26.84, rc
    assert z["primary_lo"] > rc["hard"], (z["primary_lo"], rc["hard"])
    assert z["primary_lo"] == 26.91, z["primary_lo"]      # hard + 0.05×ATR
    assert z.get("stop_warning"), "冲突必须落 stop_warning"
    assert z.get("buy_lo_adjusted") is True
    # 冲突解决后，挂单价到硬止损的距离不再是 0.19% 的假风险
    assert round((z["primary_lo"] - rc["hard"]) / z["primary_lo"] * 100, 2) > 0.2


def test_backtest_uses_probe_constants():
    """回测不得复制阈值 —— 必须直接引用 probe_intraday 的模块常量。

    否则「线上改一处、回测改一处」，参数敏感性扫描会慢慢变成在扫描一份
    已经和线上无关的影子参数。这里挡住的是复制，不是数值本身。
    """
    import backtest_intraday as BT
    import probe_intraday as PI
    assert BT.P is PI, "回测必须 import probe_intraday 本体"
    for name in ("BREAK_DMAX_ASH", "BREAK_DMAX_US", "BREAK_STOP_ATR",
                 "BREAK_BUF_ATR", "BREAK_GAP_ATR", "ASH_BREAK_MIN_OFF",
                 "ASH_LIMIT_BUFFER", "ASH_MAX_POS", "ASH_RISK_PCT",
                 "PULLBACK_VOL_MULT", "PULLBACK_BAND_ATR", "PULLBACK_STOP_ATR",
                 "PULLBACK_FRESH", "BURST_MULT", "BURST_MA_BARS", "BURST_AFTER",
                 "PRE_AMP_MAX", "CHASE_ATR", "BO_NEAR_ASH", "BO_NEAR_US",
                 "BO_TGT_ATR", "RES_WINDOW", "ASH_PRIMARY", "ASH_RESERVE",
                 "ASH_TOTAL", "ASH_SINGLE_ABS", "ASH_RESERVE_TIER"):
        assert not hasattr(BT, name), (
            f"回测复制了 {name}；应改成直接用 P.{name}，否则会与线上漂移")


def test_band_pos_is_signal_time_not_full_day():
    """诊断用的分位必须是「截至信号那一刻」的口径，不得用全日高低点。

    全日高低点含未来信息：买完就跌的票事后必然落进「高位桶」——那是「跌」的
    机械结果，不是「买贵了」的原因。用它切分会把结果当原因（实测：预案单
    高位桶胜率 0% 是假的，换成信号时刻口径后是 9.3%，梯度仍在但不再夸张）。
    全日口径只允许出现在标注了「仅供对照」的对照表里。
    """
    import backtest_intraday as BT
    bars = [{"h": 11.0, "l": 10.0}, {"h": 10.8, "l": 10.2}]
    assert BT._band_pos(10.0, bars) == 0.0       # 最低
    assert BT._band_pos(10.5, bars) == 0.5       # 中位
    assert BT._band_pos(11.0, bars) == 1.0       # 最高
    assert BT._band_pos(10.5, [{"h": 10.5, "l": 10.5}]) is None   # 区间未展开
    assert BT._band_pos(10.5, []) is None
    # 诊断函数必须把全日口径标成对照、信号时刻口径标成主口径
    src = (Path(__file__).resolve().parent / "backtest_intraday.py").read_text(
        encoding="utf-8")
    assert "【3a】" in src and "仅供对照" in src
    assert "含未来信息" in src


def test_ash_limit_anchor_is_basis_not_snap_prev():
    """涨停锚必须用 basis[-1] 收盘，不能用 snap["prev"]。

    收盘后跑的是「次日预案」，snap["prev"] 是昨天的价 —— 涨停价会退回
    今天已封住的那个价，次日略高开就被误判「涨停买不到」而撤单。
    002961：9/16 涨停收 20.97 → 次日涨停价应是 23.07，不是 20.97。
    """
    from probe_intraday import ash_limit_price
    assert ash_limit_price("002961", 20.97) == 23.07     # 次日口径（正确）
    assert ash_limit_price("002961", 19.06) == 20.97     # 昨日口径（今天已封住）
    src = (Path(__file__).resolve().parent / "probe_intraday.py").read_text(
        encoding="utf-8")
    assert 'ash_limit_price(code, basis[-1]["c"])' in src
    assert 'ash_limit_price(code, snap' not in src, \
        "涨停锚又用了 snap —— 回放时它是「此刻」的快照，与回放日期无关"


def test_channel_a_uses_ma_baseline():
    """通道 A 基准改成「前 N 根均量」（老罗 2026-09-17 改定义）。

    旧定义「≥ 当日此前**最大**量 × 2.5」在 5 分钟粒度上几乎不成立：开盘那根
    往往就是全天最大量，基准被顶死后当天再也不触发 —— 全样本 903 个标的日
    只触发 3 次，放宽倍数也救不回来。新定义下同样的行情能正常识别。
    """
    import probe_intraday as P

    def bar(v):
        return {"d": "2026-09-17 10:30:00", "o": 10.0, "h": 10.0,
                "l": 10.0, "c": 10.0, "v": v}

    # 开盘爆量 1000，随后平量 100×5，再来一根 250
    mins = [bar(1000)] + [bar(100)] * 5 + [bar(250)]
    hits = P.vol_bursts(mins)
    assert [h[0] for h in hits] == [6], hits
    assert hits[0][2] == 100.0, hits      # 基准 = 前 5 根均量，不是此前最大量
    assert hits[0][1] == 2.5, hits
    # 旧口径需要 ≥ 1000×2.5 = 2500 才算突变 → 这根 250 永远看不见
    assert P.BURST_MA_BARS == 5 and P.BURST_MULT == 2.0


def test_ash_portfolio_gate():
    """组合层闸门：主力 5 万 + 备用 5 万 + 单笔硬顶 5 万（老罗 2026-09-17）。"""
    import probe_intraday as P
    assert (P.ASH_PRIMARY, P.ASH_RESERVE, P.ASH_TOTAL, P.ASH_SINGLE_ABS) == \
        (50000, 50000, 100000, 50000)
    assert P.ASH_SINGLE_ABS >= P.ASH_PRIMARY, "单笔硬顶不该小于主力额度"

    # ① 额度充裕 → 放行，归主力层
    g = P.ash_portfolio_gate("600519", 20.0, 1000, held_amt=0.0)
    assert g["allowed"] and g["layer"] == "main", g
    assert g["lots"] == 1000 and g["amt"] == 20000.0, g

    # ② 主力层已满 + 普通信号 → 拒绝，并说清备用不够格动
    g = P.ash_portfolio_gate("600519", 20.0, 1000, held_amt=50000.0)
    assert not g["allowed"], g
    assert P.ASH_RESERVE_TIER in g["reason"], g

    # ③ 主力层已满 + 够格信号 → 可动备用
    g = P.ash_portfolio_gate("600519", 20.0, 1000, held_amt=50000.0,
                             use_reserve=True)
    assert g["allowed"] and g["layer"] == "reserve", g

    # ④ 总仓位 10 万已满 → 拒绝
    g = P.ash_portfolio_gate("600519", 20.0, 1000, held_amt=100000.0,
                             use_reserve=True)
    assert not g["allowed"] and "总仓位" in g["reason"], g

    # ⑤ 剩余额度买不到 1 手 → 必须说清是「额度」不够，不是规则不让买
    g = P.ash_portfolio_gate("600519", 2000.0, 100, held_amt=48000.0)
    assert not g["allowed"] and "买不到 1 手" in g["reason"], g

    # ⑥ 单笔绝对额硬顶：额度再充裕也不越过 5 万
    g = P.ash_portfolio_gate("600519", 10.0, 99999, held_amt=0.0)
    assert g["amt"] <= P.ASH_SINGLE_ABS and g["lots"] == 5000, g

    # ⑦ 科创板最小申报单位 200 股仍然生效
    g = P.ash_portfolio_gate("688981", 60.0, 201, held_amt=0.0)
    assert g["lots"] == 200, g


def test_ash_t1_struct_stop():
    """A 股 T+1 的结构止损 = 信号当日日线最低价（老罗 2026-09-17 的 2.a）。"""
    import probe_intraday as P
    day = [{"l": 10.5}, {"l": 10.1}, {"l": 10.4}, {"l": 10.2}]
    assert P.ash_t1_struct_stop(day, 10.6) == 10.1
    # 当日最低价不低于入场价（异常数据）→ 退回原止损，不造出负风险
    assert P.ash_t1_struct_stop(day, 10.0, fallback=9.9) == 9.9
    assert P.ash_t1_struct_stop([], 10.0, fallback=9.9) == 9.9


def test_breakout_preorder_has_no_strong_tier():
    """strong 分级已删（老罗 2026-09-17）：常量与判定分支都不许留。"""
    import probe_intraday as P
    for name in ("BO_NEAR_STRONG", "BO_NEAR_NORMAL", "BO_BODY_ATR", "BO_VOL_REL"):
        assert not hasattr(P, name), f"{name} 应已删除（strong 分级已取消）"
    assert hasattr(P, "BO_NEAR_ASH") and hasattr(P, "BO_NEAR_US")
    assert P.BO_NEAR_ASH < P.BO_NEAR_US, "A 股有 T+1 隔夜风险，门槛应更紧"
    src = (Path(__file__).resolve().parent / "probe_intraday.py").read_text(
        encoding="utf-8")
    assert '"strong"' not in src, "还有 strong 档的判定"
    assert "big_yang" not in src, "big_yang 字段应随分级一起删掉"


def _vp_series(up_vol=3e6, dn_vol=1e6, n=25):
    """量价研判测试序列：交替涨（量 up_vol）/跌（量 dn_vol），净向上。"""
    bars, c = [], 40.0
    for i in range(n):
        up = i % 2 == 0
        c2 = c + 0.5 if up else c - 0.25
        bars.append(_bar(f"2026-08-{i + 1:02d}", c, max(c, c2) + 0.1,
                         min(c, c2) - 0.1, c2, up_vol if up else dn_vol))
        c = c2
    return bars


def test_vp_regime_collect():
    """涨放跌缩 + 上行 → 收集（兆龙互连口径，2026-09-17 固化）。"""
    import probe_intraday as P
    vp = P.vp_regime(_vp_series())
    assert vp["verdict"] == "collect"
    assert vp["ratio"] >= P.VP_COLLECT_RATIO
    assert vp["ret"] > 0


def test_vp_regime_distribute():
    """跌放涨缩 → 派发特征，无论区间涨跌方向。"""
    import probe_intraday as P
    vp = P.vp_regime(_vp_series(up_vol=1e6, dn_vol=3e6))
    assert vp["verdict"] == "distribute"
    assert vp["ratio"] < P.VP_DISTRIB_RATIO


def test_vp_regime_ma5_reclaim():
    """缩量刺破 MA5 收回 = 洗盘特征（兆龙互连 9/16 的形态）。"""
    import probe_intraday as P
    bars = _vp_series()
    ma5 = sum(b["c"] for b in bars[-5:]) / 5
    bars[-1] = _bar(bars[-1]["d"], ma5 - 0.5, ma5 + 0.3, ma5 - 1.0, ma5 + 0.1,
                    1e6)  # 量 < 窗口均量 2e6
    vp = P.vp_regime(bars)
    assert vp["ma5_reclaim"] is True


def test_vp_regime_needs_full_window():
    """K 线不足窗口 → 返回 None，不许拿短窗口硬判。"""
    import probe_intraday as P
    assert P.vp_regime(_vp_series(n=10)) is None


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
    test_no_setup_branch_has_empty_buy_zone()
    test_key_break_cap_never_below_anchor()
    test_ash_limit_price_board_pct()
    test_pullback_confirm_states()
    test_pullback_requires_launch()
    test_breakout_preorder_grades()
    test_near_resistance_window()
    test_bo_gap_cancel()
    test_ash_lots_is_risk_based()
    test_ash_lots_min_lot_by_board()
    test_ash_structural_ceiling()
    test_ash_late_is_not_a_ban()
    test_ash_limit_anchor_is_basis_not_snap_prev()
    test_room_and_cap_propagates_stop_conflict()
    test_backtest_uses_probe_constants()
    test_channel_a_uses_ma_baseline()
    test_ash_portfolio_gate()
    test_ash_t1_struct_stop()
    test_breakout_preorder_has_no_strong_tier()
    test_band_pos_is_signal_time_not_full_day()
    test_vp_regime_collect()
    test_vp_regime_distribute()
    test_vp_regime_ma5_reclaim()
    test_vp_regime_needs_full_window()
    print("ok")
