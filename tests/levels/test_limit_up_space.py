# -*- coding: utf-8 -*-
"""盈亏比计算方案（2026-10-10 重构，老罗「创新高报高盈亏比是虚假」终极口径）。

依据经典技术分析：
  · Edwards & Magee《股市趋势技术分析》"MEASURING FORMULAE" 章
  · Bulkowski《图表形态百科全书》统计
  · O'Neil《笑傲股市》（买新高、让利润奔跑）

核心不变量：
  ① 有明确平台宽度 / 明确形态 ⇒ 按对应测幅法算真实目标（矩形高度 / 旗杆高度 / 双底深度）；
  ② 创历史新高（阶段新高）且上方无历史阻力、无可量度形态 ⇒ 如实写「🌊 海阔天空」，
     不预设目标价、不造假盈亏比、也不因此降优先级；
  ③ 真涨停封死 ⇒ 标「⛔ 封死」，当日不可追（与海阔天空区分）；
  ④ 废除旧的 `upside_open`（创新高就给 6×ATR）—— 那正是虚假高盈亏比的病根。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "gates"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from rule123 import (  # noqa: E402
    LU_GEM,
    LU_MAIN,
    POKE_ATR,
    RR_GATE,
    attach_stops_targets,
    atr14,
    measured_target,
    pivots,
    targets,
)


def _bar(d, o, h, l, c, v=2e6):
    return {"d": d, "o": o, "h": h, "l": l, "c": c, "v": v}


def _platform_bars(top=44.0, bottom=40.0, n=40):
    """构造 n 根矩形盘整（高≈top、低≈bottom），末根放量突破收盘于 top 之上。"""
    bars = []
    for i in range(n):
        bars.append(_bar("2026-09-%02d" % (i + 1), (top + bottom) / 2,
                         top, bottom, (top + bottom) / 2, 2e6))
    bars.append(_bar("2026-10-09", top, top + 2.0, top - 0.5, top + 1.5, 9e6))
    return bars


def _atr(bars):
    return round(atr14(bars), 2)


class TestMeasuredMove:
    def test_platform_measured_target(self):
        """platform_break + 明确平台 ⇒ 测幅目标 = 上沿 + (上沿−下沿)。"""
        bars = _platform_bars(top=44.0, bottom=40.0)
        atr = _atr(bars)
        ev = {"platform": {"price": 44.0, "i": 0, "kind": "pressing"},
              "ticker": "600882"}
        mt = measured_target(bars, "platform_break", ev, 45.5, atr)
        assert mt is not None, mt
        assert mt["kind"] == "platform", mt
        assert mt["measure"] == 4.0, mt          # 44 − 40
        assert mt["target1"] == 48.0, mt         # 44 + 4
        assert mt["breakout"] == 44.0, mt

    def test_flag_measured_target(self):
        """flag_tl_break + 旗杆 ⇒ 测幅目标 = 突破点 + 旗杆高度（腿进=腿出）。"""
        ev = {"bull_flag": {"pole_lo": 20.0, "pole_hi": 30.0},
              "ticker": "300966"}
        mt = measured_target([_bar("d", 30, 31, 29, 30)], "flag_tl_break",
                             ev, 30.0, 2.0)
        assert mt is not None, mt
        assert mt["kind"] == "flag", mt
        assert mt["measure"] == 10.0, mt
        assert mt["target1"] == 40.0, mt          # entry + pole_h

    def test_w_bottom_measured_target(self):
        """w_bottom_break + 双底 ⇒ 测幅目标 = 颈线 + (颈线−底)。"""
        ev = {"w_bottom": {"neckline": 50.0, "l1": {"price": 40.0},
                           "l2": {"price": 41.0}}, "ticker": "600882"}
        mt = measured_target([_bar("d", 50, 51, 49, 50)], "w_bottom_break",
                             ev, 50.0, 2.0)
        assert mt is not None, mt
        assert mt["kind"] == "w_bottom", mt
        assert mt["measure"] == 10.0, mt          # 50 − 40
        assert mt["target1"] == 60.0, mt          # 50 + 10

    def test_measured_target_needs_ev(self):
        """无 ev / 结构不全 ⇒ 返回 None（回落旧逻辑，不强行给测幅）。"""
        bars = _platform_bars()
        assert measured_target(bars, "platform_break", None, 45.5, 2.0) is None
        # platform 缺 price ⇒ None
        ev = {"platform": {}, "ticker": "600882"}
        assert measured_target(bars, "platform_break", ev, 45.5, 2.0) is None
        # 不在三种可量度模式内（反转类）⇒ None
        assert measured_target(bars, "downtrend_tl_break", ev, 45.5, 2.0) is None


class TestSeaSky:
    def test_new_high_no_structure_sea_sky(self):
        """创阶段新高、上方无墙、无可量度形态 ⇒ 海阔天空，target1/rr 均为 None。"""
        bars = []
        for i in range(25):
            bars.append(_bar("p%d" % i, 98, 100, 96, 99, 1))
        bars.append(_bar("d", 110, 122, 109, 120, 1))   # 收 120，创新高
        atr = _atr(bars)
        ev = {"ticker": "600882"}                          # 无 platform/flag/wbottom
        Hs, _ = pivots(bars, 3)
        tg = targets(bars, "downtrend_tl_break", {}, atr, 120.0, Hs, ev)
        assert tg["sea_sky"] is True, tg
        assert tg["target1"] is None, tg
        assert tg["rr_target1"] is None, tg
        assert tg["measured_kind"] == "sea_sky", tg
        assert tg["no_room_above"] is False, tg

    def test_sea_sky_not_lowered_priority(self):
        """海阔天空 ≠ 缺陷：端到端透传到 plan，不降 recommend、不写「封死」verdict。"""
        bars = []
        for i in range(25):
            bars.append(_bar("p%d" % i, 98, 100, 96, 99, 1))
        bars.append(_bar("d", 110, 122, 109, 120, 1))
        atr = _atr(bars)
        plan = {"mode": "downtrend_tl_break", "recommend": True,
                "buy_zone": {"level": 120.0, "anchor": "hl_trendline"},
                "note": "", "verdict": "下降趋势线突破"}
        plan = attach_stops_targets(plan, bars, atr, [], ev={"ticker": "600882"})
        assert plan.get("sea_sky") is True, plan
        assert plan["recommend"] is True, plan            # 不降优先级
        assert "海阔天空" in (plan.get("note") or ""), plan
        assert "封死" not in (plan.get("verdict") or ""), plan


class TestSealedLimitUp:
    def test_sealed_main_board(self):
        """① 主板真涨停 +10%、收=高、窗口内无更高墙 ⇒ 封死（no_room_above）。"""
        bars = _platform_bars(top=24.0, bottom=22.0, n=20)
        # 末根改为真涨停：昨收 24.0 → 涨停价 26.40，收=高
        bars[-1] = _bar("2026-10-09", 25.5, 26.40, 25.2, 26.40, 9e6)
        atr = _atr(bars)
        ev = {"ticker": "600882"}                          # 主板
        Hs, _ = pivots(bars, 3)
        tg = targets(bars, "downtrend_tl_break", {}, atr, 26.40, Hs, ev)
        assert tg["no_room_above"] is True, tg
        assert tg["target1"] is None, tg
        assert tg["rr_target1"] is None, tg
        assert tg["sea_sky"] is False, tg

    def test_sealed_gem(self):
        """① 创业板真涨停 +20% 同样封死。"""
        bars = _platform_bars(top=24.0, bottom=22.0, n=20)
        bars[-1] = _bar("2026-10-09", 25.5, 28.80, 25.2, 28.80, 9e6)  # 24×1.2=28.8
        atr = _atr(bars)
        ev = {"ticker": "300966"}                          # 创业板
        Hs, _ = pivots(bars, 3)
        tg = targets(bars, "downtrend_tl_break", {}, atr, 28.80, Hs, ev)
        assert tg["no_room_above"] is True, tg
        assert tg["target1"] is None, tg

    def test_real_wall_wins_over_sea_sky(self):
        """④ 真有墙时（即便当日大涨创新高）也报那面墙，不判海阔天空、不判封死。"""
        bars = _platform_bars(top=24.0, bottom=22.0, n=20)
        bars.append(_bar("2026-09-21", 29.0, 34.0, 28.5, 33.0, 5e6))
        bars.append(_bar("2026-09-22", 33.0, 33.5, 29.0, 30.0, 4e6))  # 回落 → 34 成局部高
        bars.append(_bar("2026-10-08", 24.0, 24.2, 23.8, 24.0, 2e6))
        bars.append(_bar("2026-10-09", 25.5, 29.5, 25.2, 29.40, 9e6))  # 创新高
        atr = _atr(bars)
        ev = {"ticker": "600882"}
        Hs, _ = pivots(bars, 3)
        tg = targets(bars, "downtrend_tl_break", {"hard_stop": 27.9}, atr, 29.40, Hs, ev)
        assert tg["no_room_above"] is False, tg
        assert tg["sea_sky"] is False, tg
        assert tg["target1"] is not None, tg
        assert tg["target1"] <= 34.0, tg                  # 报那面墙，不是凭空延伸


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
