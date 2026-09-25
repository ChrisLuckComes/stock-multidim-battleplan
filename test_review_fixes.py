# -*- coding: utf-8 -*-
"""审查修复回归：P0/P1 买卖点专项。"""
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rule123 import (
    SKILL_HARD_ANCHORS,
    attach_stops_targets,
    atr14,
    find_impulse_pause,
    held_lows_3d,
    is_live_bar,
    is_yizi,
    ma_anchor_trend_ok,
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


def test_hard_stop_widens_out_of_noise_band():
    """硬止损落进单日噪声带（距买区下沿 < 0.25×ATR）时，让位给同族更宽的合法锚。

    实证：INTC 2026-09-17 盘中突破，买区 107.57-113.53，大阳中点算出硬止损 107.32
    —— 距买区下沿仅 0.04×ATR，比当日振幅（1.05×ATR）还窄，等于没有止损。
    用户当时正准备隔夜建仓（「无法盯盘到常规时间结束，要睡觉了」），若不换锚，
    他会以为有保护，实际一次正常波动就被扫。铁律一/二不变：数值仍恒 = 锚价 − gap，
    仍落在买区下沿之下，只是不选那条塞在噪声带里的锚。
    """
    bars = [_bar("2026-01-01", 104.78, 111.14, 104.70, 111.06, 5e7)]
    atr_v = 5.96
    z = {
        "level": 107.57,
        "primary_lo": 107.57,
        "primary_hi": 113.53,
        "anchor": "platform_lip",
        "ma5": 101.88,
    }
    sp = stop_plan(bars, "platform_break", z, atr_v)
    assert sp["hard_anchor"] == "阳线下沿", sp
    assert abs(sp["hard"] - (104.70 - 0.10 * atr_v)) < 0.011, sp
    assert sp["hard"] < z["primary_lo"], sp
    assert sp["hard_dist_atr"] >= 0.25, sp
    assert sp["hard_noise"] is False, sp
    assert sp.get("hard_note"), "换锚必须留痕，不许静默改锚"


def test_tight_stop_flagged_when_no_wider_anchor():
    """同族没有更宽合法锚时不许硬凑宽度 —— 但必须如实标 hard_noise。

    跳空光脚大阳（SKILL:101 防守仍是大阳低点）：买区已被缺口抬到 125，硬止损
    124.75 距买区下沿只有 0.10×ATR。这是形态本身决定的，不编造第三个锚，
    而是在输出里把「名义止损≈没有」标出来。
    """
    bars = [_bar(f"2026-07-{i + 1:02d}", 112.5, 113.0, 112.0, 112.5, 1e6) for i in range(30)]
    bars.append(_bar("2026-08-25", 125.0, 135.0, 125.0, 135.0, 8e6))
    atr_v = atr14(bars)
    z = zone_at_level(112.5, atr_v, 135.0, "平台突破(优先T1)", {}, bars)
    sp = stop_plan(bars, "platform_break", z, atr_v)
    assert sp["hard_anchor"] == "阳线下沿", sp
    assert sp["hard_noise"] is True, sp


def test_pullback_tight_stop_is_not_noise_flagged():
    """回踩类的紧硬止损是设计（毛刺滤网），不得按突破类口径报「硬止损贴噪声带」。

    2026-09-18 收尾：拿真实全市场样本跑报表，44 个候选里 21 个被标红，其中 15 个是
    line_pullback（锚=MA5，买区下沿≈线−0.05×ATR，硬止损=线−0.10×ATR）—— 用突破类的
    「距买区下沿 <0.25×ATR」去量，回踩类几乎必然命中。警告一旦 1/3 命中率就退化成噪声，
    人就不看了；而回踩类的主风控本来就是**收盘破线**的结构止损，不需要盯盘。

    注意这是「按 mode 分口径」，不是把警告关掉 —— 同一几何换成突破类必须仍然报警。
    """
    bars = [_bar(f"2026-07-{i + 1:02d}", 100, 100.5, 99.5, 100.0, 1e6) for i in range(30)]
    bars.append(_bar("2026-08-25", 100.2, 101.0, 100.0, 100.8, 1.5e6))
    atr_v = atr14(bars)
    ma5 = round(sum(b["c"] for b in bars[-5:]) / 5, 2)
    z = {"level": ma5, "ma5": ma5, "anchor": None,
         "primary_lo": round(ma5 - 0.05 * atr_v, 2),
         "primary_hi": round(ma5 + 0.05 * atr_v, 2)}

    sp = stop_plan(bars, "line_pullback", z, atr_v)
    assert sp["hard_anchor"] == "MA5", sp
    assert sp["hard_dist_atr"] < 0.25, sp
    assert sp["hard_noise"] is False, "回踩类不得按突破类口径报噪声带"

    # 同一几何、突破类：买区下沿就是成交价，必须如实报警
    sp2 = stop_plan(bars, "platform_break", dict(z, anchor="platform_lip"), atr_v)
    assert sp2["hard_dist_atr"] < 0.25, sp2
    assert sp2["hard_noise"] is True, sp2
    # 距离字段不因分家而丢失：回踩类也要能看到「距下沿多少 ATR」
    assert sp["hard_dist_atr"] is not None


def test_pullback_struct_stop_is_the_line_not_lagging_ma5():
    """回踩单的结构止损 = 回踩锚本身，不能用滞后的 MA5 顶替（SNDK 2026-09-16）。

    价格从高位急跌时 MA5 还停在高处，会**高过当日收盘**：SNDK 9/14~9/16 收
    1551.99 / 1530.89 / 1519.97，而 MA5 依次是 1676.02 / 1634.60 / 1585.76。
    旧代码在 anchor=hl_trendline 时把结构止损换成 MA5，于是「收盘破 MA5」在买入
    那一刻就已成立（买入即止损），stop_plan 还会把买区抬到硬止损之上 —— 输出的
    买区 1580.16~1604.72 整个在收盘 1519.97 之上，recommend 却仍为 True。
    回踩单的定义是「回踩到这条线买、收盘破这条线走」，止损锚必须是这条线。
    """
    bars = [_bar(f"2026-07-{i + 1:02d}", 100, 100.5, 99.5, 100.0, 1e6) for i in range(26)]
    bars += [
        _bar("2026-08-01", 100.0, 112.0, 100.0, 111.0, 2e6),
        _bar("2026-08-02", 111.0, 113.0, 110.0, 112.0, 2e6),
        _bar("2026-08-03", 112.0, 114.0, 111.0, 113.0, 2e6),
        _bar("2026-08-04", 113.0, 115.0, 112.0, 114.0, 2e6),
        _bar("2026-08-05", 114.0, 114.0, 104.0, 105.0, 3e6),  # 急跌
    ]
    atr_v = atr14(bars)
    last_c = bars[-1]["c"]
    ma5 = round(sum(b["c"] for b in bars[-5:]) / 5, 2)
    assert ma5 > last_c, (ma5, last_c)            # 前置：MA5 确实高过现价
    line_px = 100.0                               # 上升趋势线（在现价之下）
    z = {
        "level": line_px, "ma5": ma5, "anchor": "hl_trendline",
        "primary_lo": round(line_px - 0.05 * atr_v, 2),
        "primary_hi": round(line_px + 1.0 * atr_v, 2),
    }
    sp = stop_plan(bars, "line_pullback", z, atr_v)
    assert "MA5" not in sp["struct_anchor"], sp   # 结构止损不许被 MA5 顶替
    assert abs(sp["struct"] - line_px) < 0.011, sp
    assert sp["struct"] < last_c, "结构止损必须在现价之下"
    assert sp["hard"] is not None and sp["hard"] < last_c, sp
    assert sp["hard_anchor"] == "阳线下沿", "MA5 不可用时退到阳线下沿"
    assert "MA5" in (sp.get("hard_note") or ""), "剔除 MA5 必须留痕"
    # 关键：买区不能被抬到现价之上（旧版 9/16 就是这么输出的）
    assert z["primary_lo"] < last_c, (z["primary_lo"], last_c)
    assert z["primary_lo"] <= last_c <= z["primary_hi"], z


def test_stop_above_price_cancels_recommend():
    """出口总闸门：任何止损 ≥ 现价 = 买入即止损，recommend 必须撤销。

    单点补锚总会漏下一个（INTC 昨收口径、赛分盈亏比压穿、BE 陡线外推…共同点是
    「锚点逻辑正确 ≠ 锚点数值可用」），所以在 attach_stops_targets 出口统一拦一道：
    recommend=True 必须自带一个真正位于现价之下的止损。
    """
    bars = [_bar(f"2026-07-{i + 1:02d}", 100, 100.5, 99.5, 100.0, 1e6) for i in range(30)]
    bars.append(_bar("2026-08-25", 100.2, 101.0, 100.0, 100.8, 1.5e6))
    atr_v = atr14(bars)
    last_c = bars[-1]["c"]
    z = {"level": 104.0, "ma5": 103.0, "anchor": "hl_trendline",
         "primary_lo": 103.9, "primary_hi": 105.0}
    sp = stop_plan(bars, "line_pullback", z, atr_v)
    assert sp["struct"] is not None and sp["struct"] >= last_c, sp
    assert sp["hard"] is None, sp
    assert "买入即止损" in sp["warning"], sp

    plan = {"mode": "line_pullback", "recommend": True, "buy_zone": dict(z),
            "note": "", "verdict": "沿线回踩"}
    plan = attach_stops_targets(plan, bars, atr_v, [])
    assert plan["recommend"] is False, plan
    assert plan.get("stop_above_price") is True, plan
    assert "买入即止损" in plan["note"], plan

    # 对照：止损在现价之下时不得误杀
    plan_ok = {"mode": "line_pullback", "recommend": True,
               "buy_zone": {"level": 99.0, "ma5": 100.16, "anchor": "hl_trendline",
                            "primary_lo": 98.9, "primary_hi": 100.0},
               "note": "", "verdict": "沿线回踩"}
    plan_ok = attach_stops_targets(plan_ok, bars, atr_v, [])
    assert plan_ok["recommend"] is True, plan_ok
    assert not plan_ok.get("stop_above_price"), plan_ok


def test_stop_plan_carries_exec_semantics():
    """两档止损必须自带执行口径：哪条腿不用盯盘、哪条腿要盯盘/条件单。

    用户侧硬约束：不能盯整场 + 券商不支持挂止损单。输出若只给两个价，会让人以为
    硬止损随时生效（2026-09-18 用户原话「我无法盯盘到常规时间结束，要睡觉了」）。
    """
    bars = [_bar("2026-01-01", 100.5, 106.0, 100.4, 105.5, 3e6)]
    atr_v = 2.0
    z = zone_at_level(100.0, atr_v, 105.5, "平台突破(优先T1)", {}, bars)
    sp = stop_plan(bars, "platform_break", z, atr_v)
    assert "收盘" in sp["struct_exec"] and "盯盘" in sp["struct_exec"], sp
    assert "盯盘" in sp["hard_exec"] and "不可执行" in sp["hard_exec"], sp
    assert isinstance(sp["hard_dist_atr"], float), sp
    assert isinstance(sp["hard_noise"], bool), sp


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
    """回踩买区：上沿=锚 +1.0×ATR（与 in_zone 门槛同源），下沿只留 0.05×ATR 毛刺。

    2026-09-18 SNDK 修正：旧版是「锚 ±1.0×ATR」双侧对称，下沿落在**破线之后**的区域，
    且必然低于任何「锚 −0.10×ATR」的硬止损 → stop_plan 每次都判「买区与止损锚冲突」
    并把买区下沿抬到硬止损之上；当硬止损锚（MA5）本身高过现价时，抬完的买区整个
    飞到现价上方（9/16 收 1519.97，买区却是 1580.16~1604.72，recommend 仍为 True）。
    下沿收成毛刺余量后，硬止损天然落在买区下沿之下，不再触发那条抬买区的分支。
    """
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
    assert z["primary_lo"] == 99.8      # 100 − 0.05×4：只留毛刺，不下探 1 个 ATR
    assert z["primary_hi"] == 104.0     # 100 + 1.0×4：与贴线门槛同源
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

    ★ 2026-09-20 更新：T0「均线收复+过昨高」定级高于 T1 后，本样本会被 T0 接管
    （其距墙仅 0.48% = 贴墙档，实测胜率 50%/均R+0.80）。接管时原路径的
    gate/relaxed/state 会透传到 `prev_*` 键，故此处接受两种正确结果之一：
      (a) 原「反转态大阳·已放宽」路径直接给出买区（T0 未命中）
      (b) T0 接管，且 `prev_gate == "reversal_yang"` 证明原判定仍是事实
    """
    bars = _reversal_bars()
    plan = plan_entry(bars, _reversal_ev())
    z = plan["buy_zone"]
    if z.get("gate") == "reversal_yang":
        # (a) 原路径
        assert z.get("relaxed") is True, z
        assert z.get("relaxed_reason"), z
        assert z["primary_lo"] is not None and z["primary_hi"] is not None, z
        assert z["state"] == "yang_today", z
        band = z["primary_hi"] - z["primary_lo"]
        assert abs(band - 1.0 * atr14(bars)) < 0.05, (band, z)
        assert "【反转态·放量大阳·已放宽】" in plan["note"], plan["note"]
        return
    # (b) T0 接管：必须证明原判定未丢失，且 T0 给出可执行买区
    assert plan["mode"] == "ma_reclaim_break", plan.get("verdict")
    assert z.get("prev_gate") == "reversal_yang", (z.get("prev_gate"), plan.get("verdict"))
    assert z.get("prev_relaxed") is True, z
    assert z.get("prev_state") == "yang_today", z
    t0 = plan.get("ma_reclaim") or {}
    assert t0.get("setup_kind") == "ma_reclaim_break", t0
    assert t0.get("trigger") is not None and t0.get("hard_stop") is not None, t0
    assert t0["hard_stop"] < t0["trigger"], t0
    assert z["primary_lo"] is not None and z["primary_hi"] is not None, z
    assert plan["recommend"] is True, plan


def test_reversal_yang_requires_volume():
    """反转态 + 缩量大阳 → 原「反转态放宽」路径不认（缺放量确认的破位修复不认）。

    ★ 2026-09-20 更新：T0「均线收复+过昨高」**不设量能条件**（用户定：
    「和量没关系，就是最高点就完了，简单」）。故本样本若被 T0 接管即为正确，
    此时只须校验接管后的可执行性；未被接管则仍走原「等待」分支。
    两者都不得给出「反转态·已放宽」的 gate。
    """
    bars = _reversal_bars(yang_vol=7e5)          # 与大阳前均量持平 → 无放量
    plan = plan_entry(bars, _reversal_ev())
    z = plan["buy_zone"]
    assert z.get("gate") != "reversal_yang", z
    if plan["mode"] == "ma_reclaim_break":
        # T0 接管（与量能无关，符合用户定稿口径）
        t0 = plan.get("ma_reclaim") or {}
        assert t0.get("setup_kind") == "ma_reclaim_break", t0
        assert t0["hard_stop"] < t0["trigger"], t0
        assert z["primary_lo"] is not None and z["primary_hi"] is not None, z
        assert plan["recommend"] is True, plan
        return
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
    """单笔硬顶下的「最高可交易价」：5 万硬顶 → 主板 500 元、科创 250 元。

    这是「点位到了也买不到」的唯一正当来源（物理约束：最小申报单位 × 股价），
    必须在**下单前**就筛掉，不能给了买区再告诉人买不了。
    """
    from probe_intraday import (ash_price_ceiling, no_ash_reason, ash_lots,
                                ash_single_cap)
    # 30% 闸门已移除（老罗 2026-09-17）：门槛 = 单笔硬顶 5 万 ÷ 最小申报单位
    assert ash_single_cap(50000) == 50000
    assert ash_single_cap(200000) == 50000     # 账户再大，单笔仍封顶 5 万
    assert ash_single_cap(20000) == 20000      # 账户小于硬顶 → 以账户为准
    assert ash_price_ceiling("002961", 50000) == 500.0   # 100 股单位（旧 150）
    assert ash_price_ceiling("688002", 50000) == 250.0   # 200 股单位（旧 75）
    assert ash_price_ceiling("002961", 0) is None        # 未给账户 → 不猜
    # 门槛之上：1 手即超硬顶 → 必须 None（不悄悄超标）
    assert ash_lots(50000, 600.0, 560.0, "002961") is None   # 1 手 6 万 > 5 万
    assert ash_lots(50000, 300.0, 280.0, "688002") is None   # 1 手 6 万 > 5 万
    # 门槛之下：正常给股数（旧闸门下这两个都会被判 None）
    assert ash_lots(50000, 160.0, 150.0, "002961") == 100
    assert ash_lots(50000, 76.0, 70.0, "688002") == 200
    # 说明里要出现「结构性不可交易」与门槛价
    s = no_ash_reason("688002", 50000, 300.0)
    assert "结构性不可交易" in s and "250" in s
    s2 = no_ash_reason("002961", 50000, 600.0)
    assert "结构性不可交易" in s2 and "500" in s2
    # 1 手在额度内 → 不冤枉成「结构性不可交易」，只报金额占比
    s3 = no_ash_reason("002961", 2000, 5.0)
    assert "结构性不可交易" not in s3 and "硬约束" in s3


def test_ash_risk_warning_on_min_lot_overshoot():
    """1 手风险超 1.5% 预算时必须出警告（30% 闸门移除后唯一的兜底提示）。

    200 元科创板最小 200 股，配 8% 止损 = 风险 3,200 元 = 账户 6.4%，
    占预算 427%。旧闸门会判「结构性不可交易」挡掉，现在放行 —— 只警告不拦。
    """
    from probe_intraday import ash_risk_warning, ash_lots
    n = ash_lots(50000, 200.0, 184.0, "688981")
    assert n == 200, n                                  # 兜底给 1 手
    w = ash_risk_warning(50000, 200.0, 184.0, n)
    assert w and "超预算" in w and "6.40%" in w, w
    assert "4.3 倍" in w, w
    # 正常路径不得误报：股数由预算算出，必然不超
    n2 = ash_lots(50000, 20.0, 19.0, "002961")
    assert ash_risk_warning(50000, 20.0, 19.0, n2) is None
    # 脏输入不得崩
    assert ash_risk_warning(50000, 20.0, "收盘破19", 100) is None
    assert ash_risk_warning(50000, 20.0, 21.0, 100) is None
    assert ash_risk_warning(0, 20.0, 19.0, 100) is None


def test_ash_lots_is_risk_based():
    """A 股仓位改用风险预算法（与美股 us_lots 同构）。

    旧口径是「计划仓 1/3」—— 一个拍脑袋的比例，与风险无关；也不能没有
    --qty 就不给股数。新口径 = min(账户 × 1.5% / 每股风险, 单笔硬顶 / 价格)。
    """
    from probe_intraday import ash_lots
    # 风险 1.0/股 → 750 股；单笔硬顶 50000/20 = 2500 股 → 风险预算绑定，取 700
    assert ash_lots(50000, 20.0, 19.0, "002961") == 700
    # 止损很近（0.10）→ 风险预算法要 7500 股，被单笔硬顶 50000/100=500 夹住
    # 30% 闸门时代这里只有 100 股 —— 移除闸门后仓位放大 5 倍，正是代价所在
    assert ash_lots(50000, 100.0, 99.9, "002961") == 500
    # 止损很宽（5.0）→ 150 股，未被硬顶干预
    assert ash_lots(50000, 20.0, 15.0, "002961") == 100
    # 结构止损可能是文字 → None，不得崩
    assert ash_lots(50000, 20.0, "收盘破19", "002961") is None
    assert ash_lots(50000, 20.0, None, "002961") is None
    assert ash_lots(50000, 20.0, 21.0, "002961") is None   # 止损在入场之上


def test_ash_lots_min_lot_by_board():
    """最小申报单位：科创板 688/689 = 200 股，主板 / 创业板 = 100 股。

    创业板 300/301 是 100 股 —— 旧代码把 300/301 也当成 200，已修。
    """
    from probe_intraday import first_lot_of, ash_lots, ash_round_qty
    assert first_lot_of("688002") == 200
    assert first_lot_of("689009") == 200
    assert first_lot_of("300723") == 100
    assert first_lot_of("301000") == 100
    assert first_lot_of("002961") == 100
    assert first_lot_of("601233") == 100
    # 科创板 200 起、超出部分 1 股递增；主板/创业板 100 的整数倍
    assert ash_round_qty(750, "688002") == 750
    assert ash_round_qty(201, "688002") == 201
    assert ash_round_qty(199, "688002") == 0
    assert ash_round_qty(750, "002961") == 700
    assert ash_round_qty(750, "300723") == 700
    assert ash_round_qty(99, "002961") == 0
    assert ash_lots(50000, 20.0, 19.0, "688002") == 750
    assert ash_lots(50000, 20.0, 19.0, "002961") == 700
    # 1 手即超单笔硬顶 → None（明确不做，不悄悄超标）
    assert ash_lots(5000, 200.0, 190.0, "688002") is None  # 1 手 4 万 > 账户 5 千
    # 但 1 手在额度内仍要给 —— 否则钱少就永远建不了仓
    assert ash_lots(2000, 10.0, 9.0, "002961") == 100      # 1 手 1000 ≤ 账户 2000
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
                 "ASH_LIMIT_BUFFER", "ASH_RISK_PCT",
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
    """组合层闸门：主力 + 备用 + 单笔硬顶（额度来自 account_config / 默认值）。"""
    import probe_intraday as P
    assert P.ASH_TOTAL == P.ASH_PRIMARY + P.ASH_RESERVE
    assert P.ASH_SINGLE_ABS >= P.ASH_PRIMARY, "单笔硬顶不该小于主力额度"

    # ① 额度充裕 → 放行，归主力层
    g = P.ash_portfolio_gate("600519", 20.0, 1000, held_amt=0.0)
    assert g["allowed"] and g["layer"] == "main", g
    assert g["lots"] == 1000 and g["amt"] == 20000.0, g

    # ② 主力层已满 + 普通信号 → 拒绝，并说清备用不够格动
    g = P.ash_portfolio_gate("600519", 20.0, 1000, held_amt=float(P.ASH_PRIMARY))
    assert not g["allowed"], g
    assert P.ASH_RESERVE_TIER in g["reason"], g

    # ③ 主力层已满 + 够格信号 → 可动备用
    g = P.ash_portfolio_gate("600519", 20.0, 1000, held_amt=float(P.ASH_PRIMARY),
                             use_reserve=True)
    assert g["allowed"] and g["layer"] == "reserve", g

    # ④ 总仓位已满 → 拒绝
    g = P.ash_portfolio_gate("600519", 20.0, 1000, held_amt=float(P.ASH_TOTAL),
                             use_reserve=True)
    assert not g["allowed"] and "总仓位" in g["reason"], g

    # ⑤ 剩余额度买不到 1 手 → 必须说清是「额度」不够，不是规则不让买
    near_full = float(P.ASH_PRIMARY) - 2000.0
    g = P.ash_portfolio_gate("600519", 2000.0, 100, held_amt=near_full)
    assert not g["allowed"] and "买不到 1 手" in g["reason"], g

    # ⑥ 单笔绝对额硬顶：额度再充裕也不越过硬顶
    g = P.ash_portfolio_gate("600519", 10.0, 99999, held_amt=0.0)
    expect_lots = int(P.ASH_SINGLE_ABS // 10)
    assert g["amt"] <= P.ASH_SINGLE_ABS and g["lots"] == expect_lots, g

    # ⑦ 科创板：200 股起，超出部分 1 股递增（201 合法）；不足 200 则买不到
    g = P.ash_portfolio_gate("688981", 60.0, 201, held_amt=0.0)
    assert g["lots"] == 201, g
    g = P.ash_portfolio_gate("688981", 60.0, 199, held_amt=0.0)
    assert not g["allowed"] and "买不到 1 手" in g["reason"], g


def test_probe_us_runs_end_to_end():
    """美股路径冒烟测试：probe_us 曾因语句顺序错误必崩，整条路径是死的。

    rc_us = room_and_cap(bars, z, plan["mode"], ...) 排在 plan/z 赋值之前 →
    UnboundLocalError: 'z'。全部走网络的路径没有测试覆盖，坏了多久都没人知道。
    """
    import io, contextlib
    import probe_intraday as P

    bars, px = [], 100.0
    for i in range(90):
        px += 0.9 if i % 5 < 3 else -0.7
        bars.append(_bar(f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}",
                         px - 0.3, px + 0.5, px - 0.6, px, 1e6))
    orig = (P.bars_from_us, P.bars_from_em_us, P.bars_from_yahoo_min)
    minute_calls = []
    mins = [
        _bar(f"2026-09-21 {21 + i // 6:02d}:{30 + i % 6 * 5:02d}",
             bars[-1]["c"], bars[-1]["c"] + 0.5, bars[-1]["c"] - 0.4,
             bars[-1]["c"] + 0.2, 10000 + i * 100)
        for i in range(6)
    ]

    def _boom(*a, **k):
        raise RuntimeError("stubbed: no network in tests")

    P.bars_from_us = lambda s: (
        bars, bars[-1]["c"],
        {"prev_close": bars[-2]["c"], "session": "Open",
         "as_of": "Sep 21, 2026 10:00 AM ET"})
    P.bars_from_em_us = _boom
    def _minutes(*a, **k):
        minute_calls.append((a, k))
        return mins, None, {}
    P.bars_from_yahoo_min = _minutes
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = P.probe_us("TEST")          # 崩了就是回归
        txt = buf.getvalue()
        assert "一、盘中口径" in txt, txt
        assert "三·A、当日分钟量价复盘" in txt, txt
        assert result["minute_review"]["trade_date"] == "2026-09-21"
        assert "UnboundLocalError" not in txt
        assert len(minute_calls) == 1, "盘中日 K 合成与后续分析应复用同一次分钟线"
    finally:
        P.bars_from_us, P.bars_from_em_us, P.bars_from_yahoo_min = orig


def test_probe_us_reuses_daily_snapshot():
    """--data / market_data 必须跳过 bars_from_us，只再取分钟线。"""
    import io, contextlib
    import probe_intraday as P

    bars, px = [], 100.0
    for i in range(90):
        px += 0.9 if i % 5 < 3 else -0.7
        bars.append(_bar(f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}",
                         px - 0.3, px + 0.5, px - 0.6, px, 1e6))
    orig = (P.bars_from_us, P.bars_from_em_us, P.bars_from_yahoo_min)
    daily_calls = []
    minute_calls = []
    mins = [
        _bar(f"2026-09-21 {21 + i // 6:02d}:{30 + i % 6 * 5:02d}",
             bars[-1]["c"], bars[-1]["c"] + 0.5, bars[-1]["c"] - 0.4,
             bars[-1]["c"] + 0.2, 10000 + i * 100)
        for i in range(6)
    ]

    def _daily(*a, **k):
        daily_calls.append((a, k))
        raise RuntimeError("snapshot 路径不得再取日线")

    def _boom(*a, **k):
        raise RuntimeError("stubbed: no network in tests")

    def _minutes(*a, **k):
        minute_calls.append((a, k))
        return mins, None, {}

    P.bars_from_us = _daily
    P.bars_from_em_us = _boom
    P.bars_from_yahoo_min = _minutes
    market_data = {
        "ticker": "TEST", "market": "US", "name": "TEST",
        "session": "Open", "as_of": "Sep 21, 2026 10:00 AM ET",
        "spot": bars[-1]["c"], "prev_close": bars[-2]["c"], "bars": bars,
    }
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = P.probe_us("TEST", market_data=market_data)
        assert daily_calls == []
        assert len(minute_calls) == 1
        assert result["minute_review"]["trade_date"] == "2026-09-21"
        assert "三·A、当日分钟量价复盘" in buf.getvalue()
    finally:
        P.bars_from_us, P.bars_from_em_us, P.bars_from_yahoo_min = orig


def test_zone_position_is_not_derived_from_recommend():
    """类型标签必须说价位真话：43.72 在买区 43.32-46.09 内就不能写「未到位」。

    旧代码 kind = "突破跟单" if recommend else "回踩单（未到位，等回落）"，
    recommend=False 的真实原因是量能偏大，却对外宣称价格未到位；用户看到
    低开就当「回落到位」，买在买区下沿之下、硬止损之下（300913 9/17 实况）。
    """
    from probe_intraday import zone_position_txt
    assert "已在买区" in zone_position_txt(43.72, 43.32, 46.09)
    assert "未到位" in zone_position_txt(47.00, 43.32, 46.09)      # 高于上沿才叫未到位
    assert "下方" in zone_position_txt(42.80, 43.32, 46.09)        # 今天这笔：跌出买区
    assert "未到位" not in zone_position_txt(42.80, 43.32, 46.09)
    assert zone_position_txt(43.72, None, None) == "买区缺失，无法定位"


def test_in_ash_session_is_independent_of_daily_bar():
    """盘中判定不能依赖日线末根：新浪日线盘中不返回当日半根。

    2026-09-17 10:23 实测：日线末根仍是 09-16 → is_live_bar=False，
    旧代码据此打【已收盘】并跳过盘中确认，整个交易时段工具不可用。
    """
    import datetime
    from rule123 import in_ash_session, is_live_bar
    D = datetime.datetime
    for h, m, exp in ((9, 25, False), (9, 30, True), (11, 40, True),
                      (12, 30, True), (14, 59, True), (15, 0, False), (20, 0, False)):
        assert in_ash_session("2026-09-17", D(2026, 9, 17, h, m)) is exp, (h, m)
    # 休市：快照停在上一交易日 → 不是盘中
    assert in_ash_session("2026-09-16", D(2026, 9, 17, 10, 30)) is False
    # 日线缺当日半根时两者必然分叉，这正是 bug 的成因
    stale = [{"d": "2026-09-16", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]
    assert is_live_bar(stale, now=D(2026, 9, 17, 10, 30)) is False
    assert in_ash_session("2026-09-17", D(2026, 9, 17, 10, 30)) is True


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


def test_ma_anchor_trend_gate():
    """短均线锚须 continuation 或站上 SMA20；hl_trendline 不受限（科华反例）。"""
    bars = []
    px = 40.0
    for i in range(40):
        px -= 0.35
        d = f"2026-08-{(i % 28) + 1:02d}"
        bars.append(_bar(d, px + 0.2, px + 0.4, px - 0.3, px, 1e6))
    dem_ma = {"anchor": "ma5", "level": bars[-1]["c"], "hits": 5, "dist_atr": 0.2}
    dem_tl = {"anchor": "hl_trendline", "level": bars[-1]["c"], "hits": 5, "dist_atr": 0.2}
    assert ma_anchor_trend_ok(bars, {"regime": "reversal"}, dem_ma) is False
    assert ma_anchor_trend_ok(bars, {"regime": "mixed"}, dem_ma) is False
    assert ma_anchor_trend_ok(bars, {"regime": "continuation"}, dem_ma) is True
    assert ma_anchor_trend_ok(bars, {"regime": "reversal"}, dem_tl) is True

    up = []
    px = 20.0
    for i in range(40):
        px += 0.4
        d = f"2026-09-{(i % 28) + 1:02d}"
        up.append(_bar(d, px - 0.2, px + 0.3, px - 0.35, px, 1e6))
    dem_up = {"anchor": "ema10", "level": up[-1]["c"], "hits": 4, "dist_atr": 0.1}
    assert ma_anchor_trend_ok(up, {"regime": "mixed"}, dem_up) is True


def test_us_lots_account_cap_only():
    """美股无比例上限：风险股数可吃满账户，买不起 1 股则 None。"""
    from probe_intraday import us_lots
    # 止损很紧 → 风险法给很多股，但天花板 = 账户/价
    n = us_lots(5000, 25.0, 24.9, risk_pct=0.015)
    assert n == 200, n  # 5000/25
    assert us_lots(5000, 6000.0, 5900.0) is None
    # 止损宽 → 风险法股数更小，不受旧 50% 砍半
    n2 = us_lots(5000, 20.0, 18.0, risk_pct=0.015)
    # risk: 5000*0.015/(2)=37；cap=250；→ 37（旧 50% 会 cap 到 125，但不砍到一半风险单）
    assert n2 == 37, n2


_CONF_KEYS = ("ASH_ACCOUNT", "US_ACCOUNT", "ASH_PRIMARY", "ASH_RESERVE",
              "ASH_TOTAL", "ASH_SINGLE_ABS", "ASH_RISK_PCT", "US_RISK_PCT", "ASH_CASH")


def _conf_ctx():
    """临时清掉相关 env，退出时原样还回去。"""
    class _Ctx:
        def __enter__(self):
            self.saved = {k: os.environ.get(k) for k in _CONF_KEYS}
            for k in _CONF_KEYS:
                os.environ.pop(k, None)
            from account_config import _reset_for_tests
            _reset_for_tests()
            return self

        def __exit__(self, *exc):
            for k, v in self.saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            from account_config import _reset_for_tests
            _reset_for_tests()
            return False
    return _Ctx()


def test_account_config_env_override():
    """人与钱走 env；未设时回落到 DEFAULTS；总上限可推导也可显式声明。

    2026-09-21 口径：A 股 总上限 100,000 = 主力 50,000 + 后备 50,000。
    """
    from account_config import DEFAULTS, load_account_config

    with _conf_ctx():
        c0 = load_account_config(dotenv=False)
        assert c0["ash_primary"] == DEFAULTS["ash_primary"]
        assert c0["ash_reserve"] == DEFAULTS["ash_reserve"]
        assert c0["ash_total"] == c0["ash_primary"] + c0["ash_reserve"]
        assert c0["ash_total_source"] == "derived"

        # ① 只改主力 → 总上限自动跟着变（后备不动）
        os.environ["ASH_PRIMARY"] = "80000"
        c1 = load_account_config(dotenv=False)
        assert c1["ash_primary"] == 80000
        assert c1["ash_total"] == 80000 + c1["ash_reserve"]

        # ② 显式声明总上限且自洽 → 用它，来源标 env
        os.environ["ASH_RESERVE"] = "60000"
        os.environ["ASH_TOTAL"] = "140000"
        c2 = load_account_config(dotenv=False)
        assert c2["ash_total"] == 140000 and c2["ash_total_source"] == "env"


def test_ash_total_mismatch_is_loud():
    """显式总上限与「主力 + 后备」矛盾时必须**报错**，不许静默取一个。

    静默取值的代价：.env 里写着 10 万、实际闸门按 13 万走，而报告里印的是
    「上限 10 万」—— 配置与行为分叉且无人知道。
    """
    from account_config import load_account_config

    with _conf_ctx():
        os.environ.update(ASH_PRIMARY="40000", ASH_RESERVE="40000", ASH_TOTAL="100000")
        try:
            load_account_config(dotenv=False)
            raise AssertionError("矛盾配置必须报错")
        except ValueError as e:
            assert "ASH_TOTAL" in str(e) and "ASH_PRIMARY" in str(e), e

        # 改对（或干脆删掉 ASH_TOTAL 让它推导）就正常
        os.environ["ASH_TOTAL"] = "80000"
        assert load_account_config(dotenv=False)["ash_total"] == 80000
        os.environ.pop("ASH_TOTAL")
        c = load_account_config(dotenv=False)
        assert c["ash_total"] == 80000 and c["ash_total_source"] == "derived"


def test_config_is_read_dynamically():
    """改进程 env 后**不 reload** 也要生效 —— 这是「不写死在代码里」的判据。

    旧实现 `P.ASH_PRIMARY = _CFG[...]` 是 import 时快照：配置改了，报告里印的
    数变了、闸门却还按旧值走。
    """
    import probe_intraday as PI

    with _conf_ctx():
        os.environ.update(ASH_PRIMARY="60000", ASH_RESERVE="30000", ASH_TOTAL="90000")
        assert PI.ASH_PRIMARY == 60000, PI.ASH_PRIMARY
        assert PI.ASH_RESERVE == 30000
        assert PI.ASH_TOTAL == 90000
        # 闸门必须用**新**额度：主力层 6 万，已持 5.5 万 + 本笔 0.2 万 → 放行
        g = PI.ash_portfolio_gate("600519", 20.0, 100, held_amt=55000.0)
        assert g["allowed"] is True, g
        # 已持 5.9 万 → 主力层 6 万只剩 1000 元，买不到 1 手（100 股 × 20 元 = 2000）
        g2 = PI.ash_portfolio_gate("600519", 20.0, 100, held_amt=59000.0)
        assert g2["allowed"] is False and "买不到 1 手" in g2["reason"], g2
        # 主力层正好打满 → 明确说「主力层已满 + 备用只在突破预案单启用」
        g3 = PI.ash_portfolio_gate("600519", 20.0, 100, held_amt=60000.0)
        assert g3["allowed"] is False and "主力层" in g3["reason"], g3
        assert "突破预案单" in g3["reason"], g3
        # 同价位换成「突破预案单」级信号 → 动后备（总 9 万 − 已持 6 万 = 3 万额度）
        g4 = PI.ash_portfolio_gate("600519", 20.0, 100, held_amt=60000.0,
                                   use_reserve=True)
        assert g4["allowed"] is True and g4["layer"] == "reserve", g4

        os.environ.update(ASH_PRIMARY="50000", ASH_RESERVE="50000", ASH_TOTAL="100000")
        assert PI.ASH_PRIMARY == 50000 and PI.ASH_TOTAL == 100000


def test_no_bare_account_globals_in_probe():
    """probe_intraday 里不得**裸用** ASH_*/US_* 配置名。

    模块级 __getattr__ 只对属性访问（P.ASH_X）生效；函数体里的裸名是
    LOAD_GLOBAL，不触发它 → NameError。2026-09-21 改成动态属性时就是这么把
    probe / probe_us 全线打挂的（test_battle_report 立刻报 4 个 error）。
    用 AST 钉死：只允许属性访问与 _ASH_DYNAMIC 表里的**字符串**。
    """
    import ast
    import inspect
    import probe_intraday as PI

    names = {k for k in _CONF_KEYS}
    tree = ast.parse(inspect.getsource(PI))
    bad = [(n.lineno, n.id) for n in ast.walk(tree)
           if isinstance(n, ast.Name) and n.id in names]
    assert not bad, "函数体内裸用配置名（会 NameError）：%s" % bad


def test_probe_module_attrs_are_dynamic():
    """P.ASH_* / P._CFG 仍可按属性读（向后兼容），但每次取的是当前配置。"""
    import probe_intraday as PI
    from account_config import cfg

    assert PI.ASH_TOTAL == cfg()["ash_total"]
    assert PI.ASH_PRIMARY == cfg()["ash_primary"]
    assert PI.ASH_SINGLE_ABS == cfg()["ash_single_abs"]
    assert PI._CFG["ash_account"] == cfg()["ash_account"]
    # 真常量（策略档位）不受影响
    assert PI.ASH_RESERVE_TIER == "突破预案单"


def _intraday_fixture():
    """上升（含回调形成枢轴）+ 顶部平台 + 突破前夜，末根 = 2026-09-16。

    几何对齐 INTC 2026-09-17 实况：平台沿 80.60 尚未被收盘打穿，突破位就是它；
    昨收口径的买区基准落在回踩线 79.1，而不是突破位。
    """
    rows, px = [], 50.0
    for _ in range(5):
        for _ in range(5):
            px += 0.9
            rows.append((px - 0.3, px + 0.5, px - 0.7, px, 1e6))
        for _ in range(3):
            px -= 0.7
            rows.append((px + 0.3, px + 0.6, px - 0.9, px, 1e6))
    for _ in range(9):
        px += 2.0
        rows.append((px - 0.6, px + 0.6, px - 1.1, px, 1.3e6))
    for k in range(8):
        c = px - 0.4 - 0.1 * k
        rows.append((c + 0.3, c + 0.7, c - 0.7, c, 0.8e6))
    d0 = datetime.date(2026, 9, 16) - datetime.timedelta(days=len(rows) - 1)
    return [_bar((d0 + datetime.timedelta(days=i)).isoformat(), *r)
            for i, r in enumerate(rows)]


def _intraday_m5():
    """今日 5 分钟线（跨北京零点，ET 日期仍是 09-17）+ 一根突破根。"""
    return [{"d": "2026-09-17 21:30", "o": 79.8, "h": 82.0, "l": 79.5, "c": 81.5,
             "v": 1e6},
            {"d": "2026-09-18 00:20", "o": 81.5, "h": 84.4, "l": 81.0, "c": 84.0,
             "v": 1e6}]


def test_intraday_bar_uses_et_date_not_beijing():
    """合成 bar 的日期取 Nasdaq 快照的 ET 日；5 分钟线跨北京零点不能改成 09-18。

    旧实现只把 spot 当附带字段，盘中缺口根本不产生 —— INTC 2026-09-17 已站上
    平台沿 107.57，买区却还是昨收的 99.45~104.03 回踩位。
    """
    from rule123 import intraday_bar, us_session_clock
    bars = _intraday_fixture()
    assert bars[-1]["d"] == "2026-09-16"
    meta = {"session": "Open", "as_of": "Sep 17, 2026 12:19 PM ET"}
    b = intraday_bar("INTC", meta, bars, fetch=lambda s: _intraday_m5())
    assert b["d"] == "2026-09-17"
    assert (b["o"], b["h"], b["l"], b["c"]) == (79.8, 84.4, 79.5, 84.0)
    # v_raw = 当日累计量；v = 按已走时段折算的预计全日量（量比口径）
    from rule123 import project_session_volume
    assert b["v_raw"] == 2e6
    assert abs(b["v"] - project_session_volume(2e6, meta)) < 1e-6
    assert b["v"] > b["v_raw"], "12:19 ET 只走了 176/390 分钟，折算应放大"
    assert abs(project_session_volume(2e6, meta) - 2e6 * 390 / 169) < 1e-6
    assert project_session_volume(2e6, {}) == 2e6   # 取不到时钟则原样返回
    assert us_session_clock(meta) == ("2026-09-17", 739)


def test_intraday_merge_refreshes_zone_to_breakout_level():
    """并入今日未收盘 bar 后，买区基准必须从回踩线抬到突破位（平台沿）。"""
    from rule123 import build_ev, merge_intraday_bar
    bars = _intraday_fixture()
    ev0, b0, _ = build_ev(bars)
    p0 = plan_entry(b0, ev0)
    assert p0["mode"] != "platform_break"
    assert p0["buy_zone"]["level"] < 80.5, "昨收口径的买区基准应是回踩线"

    meta = {"session": "Open", "as_of": "Sep 17, 2026 12:19 PM ET"}
    merged, live = merge_intraday_bar("INTC", bars, meta, "US",
                                      fetch=lambda s: _intraday_m5())
    assert live is not None and len(merged) == len(bars) + 1
    ev1, b1, _ = build_ev(merged)
    p1 = plan_entry(b1, ev1)
    assert p1["mode"] == "platform_break" and p1["recommend"] is True
    assert abs(p1["buy_zone"]["level"] - 80.6) < 0.1, p1["buy_zone"]["level"]
    assert p1["buy_zone"]["primary_lo"] > p0["buy_zone"]["level"]


def test_intraday_bar_only_in_regular_session():
    """盘前/盘后/已收盘都不合成 —— 稀疏成交不能当「收盘站上」；A 股路径也不合成。"""
    from rule123 import intraday_bar, merge_intraday_bar
    bars = _intraday_fixture()
    f = lambda s: _intraday_m5()  # noqa: E731
    for sess in ("Closed", "Pre-Market", "After-Hours", ""):
        assert intraday_bar("INTC", {"session": sess,
                                     "as_of": "Sep 17, 2026 12:19 PM ET"},
                            bars, fetch=f) is None, sess
    # 日线已含当日 → 不重复追加
    today = _bar("2026-09-17", 80, 84, 79, 84)
    assert intraday_bar("INTC", {"session": "Open",
                                 "as_of": "Sep 17, 2026 12:19 PM ET"},
                        bars + [today], fetch=f) is None
    # 解不出 ET 日期 → 宁可不判，也不造一根日期错的 bar
    assert intraday_bar("INTC", {"session": "Open", "as_of": "garbage"},
                        bars, fetch=f) is None
    assert merge_intraday_bar("600519", bars, {"session": "Open"}, "ASH")[1] is None


def _report_row(**kw):
    r = {
        "code": "600000", "name": "测试股", "market": "sh", "regime": "uptrend",
        "tier": "tier1", "spot": 105.0, "R1": 104.0, "platform": 104.5,
        "support_lo": 104.8, "support_hi": 110.0,
        "stop": 103.2, "struct_stop": 104.5, "hard_stop": 103.2,
        "hard_anchor": "阳线下沿", "stop_warning": None,
        "hard_dist_atr": 0.55, "hard_noise": False,
        "chase_only": False, "intraday": False, "confirm_at": None,
        "pre_breakout": None, "T1": 118.0, "T2": 130.0, "rvol": 1.8,
        "dd_from_high": -1.2, "buy_type": "platform", "mode": "platform_break",
        "candidate": True,
        # 顶部标志 K 线（2026-09-25 新增列；缺省 = 无信号）
        "top_state": None, "top_block": False, "top_veto": False,
        "top_pattern": None, "top_date": None, "top_vol": None, "top_rvol": None,
        "top_prob": None, "top_invalidation": None, "top_size_factor": None,
        "top_summary": None,
    }
    r.update(kw)
    return r


def test_report_renders_pre_breakout_and_two_tier_stop():
    """扫描报表必须渲染预案单 / 两档止损执行口径 / 盘中口径（2026-09-18）。

    CLI 和盘中入口连打了三轮 `pre_breakout`，HTML 报表一个字段都不显示 ——
    而报表才是全市场扫描结果唯一的消费口，不渲染等于这三轮修复对扫描无效。
    """
    import importlib.util
    import json
    import os
    import tempfile

    root = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("scan_report",
                                                 root / "scan_all" / "report.py")
    rep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rep)
    # 不出网：图表用的日线直接用合成序列顶替
    rep.sina_kline = lambda prefix, code, n=140: [      # noqa: E731
        _bar(f"2026-08-{(i % 28) + 1:02d}", 100 + i * 0.1,
             101 + i * 0.1, 99 + i * 0.1, 100.5 + i * 0.1) for i in range(60)]

    pb = {
        "order": "buy-stop", "anchor": "down_tl", "level": 105.4, "trigger": 106.68,
        "hard_stop": 102.9, "risk_per_share": 3.78, "dist_atr": 0.31,
        "line_from": "8/13高107.57→9/9高106.69", "triggered": True, "fill_px": 106.68,
        "status": "已触发 → 该埋伏单已成交，按计划持有，不再回头等回踩",
        "note": "下降趋势线（8/13高107.57→9/9高106.69）下移中",
    }
    rows = [
        _report_row(code="600001", name="已触发股", pre_breakout=pb, intraday=True,
                    confirm_at="尾盘（北京时间 03:45–04:00 / ET 15:45–16:00）",
                    hard_noise=True, hard_dist_atr=0.08, tier="tier2"),
        _report_row(code="600002", name="待挂单股", pre_breakout=dict(
            pb, anchor=None, triggered=False, fill_px=None, status=None,
            level=106.0, trigger=106.30)),
        # 顶部标志 K 线三态各来一只：已确认（否决）/ 待确认（预警）/ 已推翻（作废）
        _report_row(code="600003", name="顶部确认股", recommend=False,
                    top_state="confirmed", top_block=True, top_veto=True,
                    top_pattern="上吊线", top_date="2026-09-24", top_vol="巨量",
                    top_rvol=3.42, top_prob="极高", top_invalidation=108.60,
                    top_size_factor=0.0),
        _report_row(code="600004", name="顶部待确认股",
                    top_state="pending", top_block=False, top_veto=False,
                    top_pattern="墓碑线", top_date="2026-09-25", top_vol="巨量",
                    top_rvol=2.71, top_prob="中高", top_invalidation=112.30,
                    top_size_factor=0.5),
        _report_row(code="600005", name="顶部被推翻股",
                    top_state="invalidated", top_block=False, top_veto=False,
                    top_pattern="射击之星", top_date="2026-09-22", top_vol="放量",
                    top_rvol=1.62, top_prob="作废", top_invalidation=109.10,
                    top_size_factor=1.0),
    ]

    old = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "scan_all"))
        with open(os.path.join(td, "scan_all", "results.jsonl"),
                  "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.chdir(td)
        try:
            rep.main()
            outdir = os.path.join(td, "output")
            html = open(os.path.join(outdir, os.listdir(outdir)[0]),
                        encoding="utf-8").read()
        finally:
            os.chdir(old)

    # 表头两列必须存在，否则列数与行数不匹配（新增列最容易漏的一处）
    assert "预案单<br>" in html
    assert html.count("<th>") == 16, html.count("<th>")
    assert html.count("</td>") == 5 * 16, html.count("</td>")
    # 预案单一：触发价 / 挂法 / 风险都要落盘，且「已触发」要显式区别于挂单
    assert "挂 106.68" in html, "预案单触发价没渲染"
    assert "斜线 105.40" in html, "预案单挂法（斜线/平台沿）没渲染"
    assert "已触发 @ 106.68" in html, "已触发的预案单必须显式声明是持仓而非挂单"
    assert "不再是挂单，是持仓" in html
    # 预案单二：未触发也要给出挂单价与距多少 ATR
    assert "挂 106.30" in html
    assert "尚未触发" in html and "不必市价追" in html
    # 两档止损的执行口径（从 rule123 取常量，不在报表里另抄）
    assert "结构止损：收盘口径" in html and "硬止损：盘中口径" in html
    assert "结构 104.50（收盘破）" in html
    assert "距下沿 0.08×ATR" in html
    # 顶部标志 K 线（2026-09-25）：三态必须各自渲染，且「已确认」要写出否决语义
    assert "顶部信号<br>" in html, "顶部信号列头没渲染"
    assert "已确认·否决" in html and "上吊线" in html and "巨量 3.42×" in html
    assert "推翻线 108.60" in html
    assert "待确认·预警" in html and "中高" in html and "仓位×0.5" in html
    assert "已推翻" in html and "射击之星" in html
    # 「已确认」的票必须显式写明「顶上无买点」，不能只标个红字
    assert "已否决当日买点" in html and "收盘收复即作废" in html
    # 计数徽标：1 只否决、1 只待确认
    assert "顶部标志K线·否决" in html and "顶部标志K线·待确认" in html
    assert "硬止损贴噪声带" in html, "硬止损落进噪声带必须标红"
    assert "压低买入价" in html, "噪声带正解是压买价，必须写在报表上"
    # 盘中口径：盘中价非收盘价，必须给复核时点
    assert "盘中·未收盘" in html
    assert "尾盘（北京时间 03:45–04:00" in html
    # 量能口径：RVOL 降级为参考，不得再暗示「低 RVOL = 假突破」
    assert "仅参考" in html
    assert "低 RVOL ≠ 假突破" in html or "低 RVOL 不等于假突破" in html
    # 图上也必须画出预案单触发线（否则报表里唯一的可执行价只存在于文字里）
    svg = rep.svg_chart(rows[1], [_bar(f"2026-08-{(i % 28) + 1:02d}",
                                       100 + i * 0.1, 101 + i * 0.1,
                                       99 + i * 0.1, 100.5 + i * 0.1) for i in range(60)],
                        "t")
    assert "预案单 挂 106.30" in svg, svg[-400:]


def test_skill_requires_volume_price_report():
    root = Path(__file__).resolve().parent
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    report = (root / "references" / "research-report.md").read_text(encoding="utf-8")
    required = (
        "## 量价分析（报告强制专章）",
        "### 一、当日分钟走势复盘",
        "### 二、近 20 日量价分析",
        "当日判定：派发 / 派发警告 / 趋势延续 / 中性 / 未收盘不定性",
        "近20日判定：派发 / 派发警告 / 趋势延续 / 中性",
    )
    for text in required:
        assert text in report, f"research-report.md 缺少量价报告约束：{text}"
    assert "research-report.md" in skill
    assert "主标的日线只取一次" in skill


def test_skill_uses_progressive_disclosure():
    root = Path(__file__).resolve().parent
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    refs = (
        "strategy-modes.md",
        "entry-odds.md",
        "research-report.md",
        "intraday.md",
        "risk-exit.md",
        "data-operations.md",
        "output-pitfalls.md",
    )
    assert len(skill.splitlines()) < 500
    assert "不要预加载全部 references" in skill
    for name in refs:
        assert f"references/{name}" in skill
        path = root / "references" / name
        assert path.exists() and path.stat().st_size > 200
        assert len(path.read_text(encoding="utf-8").splitlines()) < 500


def test_skill_uses_lightweight_peer_comparison_by_default():
    root = Path(__file__).resolve().parent
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    data_ops = (root / "references" / "data-operations.md").read_text(encoding="utf-8")
    assert "同行默认轻量对照" in skill
    assert "不得对同行运行完整单票流程" in skill
    assert "不跑同行 `rule123`、分钟线、完整基本面与逐项扫雷" in data_ops
    assert "显式要求同行完整分析时才升级" in data_ops
    assert "`probe_intraday.py --data`" in data_ops
    assert "必须同时启动" in data_ops
    assert "默认不对同行检索财报" in data_ops
    source = (root / "probe_intraday.py").read_text(encoding="utf-8")
    assert 'ap.add_argument("--us-account", type=float' in source
    assert 'ap.add_argument("--data"' in source


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
    test_hard_stop_widens_out_of_noise_band()
    test_tight_stop_flagged_when_no_wider_anchor()
    test_pullback_tight_stop_is_not_noise_flagged()
    test_pullback_struct_stop_is_the_line_not_lagging_ma5()
    test_stop_above_price_cancels_recommend()
    test_stop_plan_carries_exec_semantics()
    test_breakout_zone_not_below_level()
    test_too_far_gate()
    test_breakout_zone_starts_at_level()
    test_breakout_extended_band_still_actionable()
    test_line_zone_pad_matches_in_zone()
    test_is_live_bar_session()
    test_intraday_bar_uses_et_date_not_beijing()
    test_intraday_merge_refreshes_zone_to_breakout_level()
    test_intraday_bar_only_in_regular_session()
    test_report_renders_pre_breakout_and_two_tier_stop()
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
    test_ash_risk_warning_on_min_lot_overshoot()
    test_ash_lots_is_risk_based()
    test_ash_lots_min_lot_by_board()
    test_ash_structural_ceiling()
    test_ash_late_is_not_a_ban()
    test_ash_limit_anchor_is_basis_not_snap_prev()
    test_room_and_cap_propagates_stop_conflict()
    test_backtest_uses_probe_constants()
    test_channel_a_uses_ma_baseline()
    test_ash_portfolio_gate()
    test_probe_us_runs_end_to_end()
    test_probe_us_reuses_daily_snapshot()
    test_zone_position_is_not_derived_from_recommend()
    test_in_ash_session_is_independent_of_daily_bar()
    test_ash_t1_struct_stop()
    test_breakout_preorder_has_no_strong_tier()
    test_band_pos_is_signal_time_not_full_day()
    test_vp_regime_collect()
    test_vp_regime_distribute()
    test_vp_regime_ma5_reclaim()
    test_vp_regime_needs_full_window()
    test_skill_requires_volume_price_report()
    test_skill_uses_progressive_disclosure()
    test_skill_uses_lightweight_peer_comparison_by_default()
    test_ma_anchor_trend_gate()
    test_us_lots_account_cap_only()
    test_account_config_env_override()
    test_ash_total_mismatch_is_loud()
    test_config_is_read_dynamically()
    test_no_bare_account_globals_in_probe()
    test_probe_module_attrs_are_dynamic()
    print("ok")
