# -*- coding: utf-8 -*-
"""短线多头战法（ma_bull_align）回归测试 —— 2026-10-13 老罗定稿。

战法口径（老罗原话）：「20，60天均线在下面支撑，5，7，10三短均线组多头排列就买进，
不要去等突破形态高点。三短均线多头排列一打乱就卖出。」

优先级裁决（老罗 10-13 补齐）：「如果优先符合其他战法，那就是其他，
最后再来判断是否符合短线多头战法」⇒ 本 mode 排在 _plan_entry_core 链尾：
所有有效买点（平台/W底/旗形/下降趋势线突破、沿线回踩、大阳后缩量回踩）
与反追高降级（突破已延伸等）全部优先；仅当链尾只剩空等/等待时才补位。

合成数据说明：
  · sine_series：px(t)=base+drift·t+amp·sin(2πt/period)。drift=0.035 <
    amp·2π/period=0.052 ⇒ 有下跌日 ⇒ pivots 有料；峰高按 drift·period=0.63
    展开 ⇒ 高低点不重复、活平台判定不触发。n=60 收在上升段（价格低于最后
    摆动高点且距之 >2.5×ATR）⇒ 既非 fresh_break 也非 pressing。
  · sawtooth_flat_break：规律锯齿顶出一个活平台后末根突破 ⇒ platform_break。
  · two_peak_recovery：长跌后三段反弹 ⇒ 价格收复 MA5/MA10 但 MA60 仍压在
    头上（上海机场 2026-10-13 同款：MA60=23.20 > 收 23.18）。
"""
import datetime
import json
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for _p in (ROOT, os.path.join(ROOT, "gates")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rule123 as R                                          # noqa: E402

FIX = os.path.join(HERE, "fixtures")


def _fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)["bars"]


def _bars(rows):
    d = datetime.date(2026, 1, 5)
    return [{"d": (d + datetime.timedelta(days=i)).isoformat(),
             "o": round(o, 3), "h": round(h, 3), "l": round(l, 3),
             "c": round(c, 3), "v": v}
            for i, (o, h, l, c, v) in enumerate(rows)]


def sine_series(n=60, drift=0.035, amp=0.15, period=18, base=10.0):
    """正弦上行（见模块 docstring）。n=60 收在上升段：排列成立、无其他买点。"""
    rows = []
    for t in range(n):
        c = base + drift * t + amp * math.sin(2 * math.pi * t / period)
        o = c - 0.012
        rows.append((o, c + 0.010, o - 0.010, c, 1.0e6))
    return rows


def sine_series_extended(n=71):
    """同族序列但收在平台突破后的延伸段（>2×ATR）⇒ 反追高降级 wait。"""
    return sine_series(n)


def sawtooth_up(cycles=12, base=10.0, up=0.05, drop=0.12):
    """规律锯齿：3 根 +up、1 根 −drop；末段收 3 阳。高低点重复成层 ⇒ 活平台。"""
    rows = []
    px = base
    for _ in range(cycles):
        for _j in range(3):
            o = px
            c = px + up
            rows.append((o, max(o, c) + 0.008, min(o, c) - 0.008, c, 1.0e6))
            px = c
        o = px
        c = px - drop
        rows.append((o, o + 0.008, c - 0.008, c, 1.0e6))
        px = c
    for _j in range(3):
        o = px
        c = px + up
        rows.append((o, max(o, c) + 0.008, min(o, c) - 0.008, c, 1.0e6))
        px = c
    return rows


def sawtooth_flat_break():
    """锯齿顶出平台 ⇒ 横 10 根 ⇒ 末根放量突破：platform_break 与排列同一天成立。"""
    rows = sawtooth_up(cycles=12)
    flat = rows[-1][3]
    for _i in range(10):
        rows.append((flat, flat + 0.010, flat - 0.010, flat, 1.0e6))
    rows.append((flat, flat + 0.052, flat - 0.006, flat + 0.045, 2.0e6))
    return rows


def two_peak_recovery(n_down=60, base=12.5, per=0.06):
    """长跌 + 三段反弹（两浅回撤造出第二个枢轴高）：MA60 压在价格上方。"""
    rows = []
    px = base
    for _i in range(n_down):
        o = px
        c = px - per
        rows.append((o, o + 0.006, c - 0.006, c, 1.0e6))
        px = c
    for seg, pull in ((6, 3), (6, 3), (6, 0)):
        for _i in range(seg):
            o = px
            c = px + 0.026
            rows.append((o, c + 0.006, o - 0.006, c, 1.0e6))
            px = c
        for _i in range(pull):
            o = px
            c = px - 0.030
            rows.append((o, o + 0.006, c - 0.006, c, 1.0e6))
            px = c
    return rows


def plan_of(rows, ticker="688999"):
    bars = _bars(rows)
    ev, bb, meta = R.build_ev(bars, drop_live=False, ticker=ticker)
    assert ev is not None, "build_ev 失败：%s" % meta
    return R.plan_entry(bb, ev)


def plan_of_bars(bars, ticker):
    ev, bb, meta = R.build_ev(bars, drop_live=False, ticker=ticker)
    assert ev is not None, "build_ev 失败：%s" % meta
    return R.plan_entry(bb, ev)


class TestAlignFallback(unittest.TestCase):
    """链尾补位：其他战法全部落空时，排列成立 ⇒ mode=ma_bull_align。"""

    def test_alignment_fires_when_chain_falls_through(self):
        plan = plan_of(sine_series(60))
        self.assertEqual(plan["mode"], "ma_bull_align")
        self.assertTrue(plan["recommend"])
        self.assertEqual(plan["priority"], 1)
        self.assertEqual(plan["path"], "A")
        self.assertIn("短均线多头排列", plan["verdict"])

    def test_buy_zone_is_current_price_with_ma20_floor(self):
        plan = plan_of(sine_series(60))
        z = plan["buy_zone"]
        last_c = 12.213
        self.assertAlmostEqual(z["level"], last_c, places=2)
        self.assertTrue(z["in_zone"])
        # 买区 = 现价 ±0.2/0.3 ATR（盘中即触发，不等突破）
        atr = R.atr14(_bars(sine_series(60)))
        self.assertAlmostEqual(z["primary_lo"], round(last_c - 0.20 * atr, 2), places=2)
        self.assertAlmostEqual(z["primary_hi"], round(last_c + 0.30 * atr, 2), places=2)
        # 兜底硬止损 = 长均托底中更近者（本案例 MA20=11.75 > MA60=11.04）
        self.assertAlmostEqual(z["hard_stop"], 11.75, places=2)
        self.assertEqual(z["hard_anchor"], "MA20")
        self.assertLess(z["hard_stop"], z["level"], "止损必须落在买价之下")

    def test_dynamic_exit_rule_is_alignment_break(self):
        """主卖点（动态）= 三短均线多头排列一打乱；硬止损仅兜底。"""
        plan = plan_of(sine_series(60))
        er = plan["buy_zone"].get("exit_rule") or {}
        self.assertIn("MA5<MA7", er.get("condition", ""))
        self.assertIn("MA7<MA10", er.get("condition", ""))
        self.assertFalse(er.get("triggered"), "排列成立时卖出规则不应已触发")
        self.assertFalse(er.get("ma5_lt_ma7"))
        self.assertFalse(er.get("ma7_lt_ma10"))
        self.assertIn("一打乱", plan["buy_zone"].get("stop_basis", ""))

    def test_note_carries_core_discipline(self):
        plan = plan_of(sine_series(60))
        note = plan["note"]
        self.assertIn("弱势行情不追突破高点", note)
        self.assertIn("排列成立即买", note)
        self.assertIn("一打乱", note)

    def test_ma_state_exposed_for_tracking(self):
        plan = plan_of(sine_series(60))
        ms = plan["ma_bull_align"]["ma_state"]
        self.assertEqual(
            [ms["ma5"], ms["ma7"], ms["ma10"]],
            [12.11, 12.03, 11.92],
        )
        self.assertTrue(ms["ma5"] > ms["ma7"] > ms["ma10"])


class TestPriorityOverAlignment(unittest.TestCase):
    """老罗裁决：其他战法优先；反追高纪律同样优先。"""

    def test_platform_break_beats_alignment_same_day(self):
        """当日有新鲜平台突破 ⇒ 走 platform_break，排列状态仅挂载参考。"""
        plan = plan_of(sawtooth_flat_break())
        self.assertEqual(plan["mode"], "platform_break")
        self.assertTrue(plan["recommend"])
        mab = plan.get("ma_bull_align") or {}
        self.assertTrue(mab.get("recommend"),
                        "排列同日成立 ⇒ 状态必须挂载（供卖出跟踪/复盘）")
        self.assertEqual(mab.get("setup_kind"), "ma_bull_align")

    def test_anti_chase_downgrade_beats_alignment(self):
        """平台突破后延伸 >2×ATR ⇒ pack() 降级为「突破已延伸·等回踩」，
        排列成立也不重新开买（反追高纪律优先于本战法）。"""
        plan = plan_of(sine_series_extended(71))
        self.assertEqual(plan["mode"], "wait")
        self.assertFalse(plan["recommend"])
        self.assertIn("突破已延伸", plan["verdict"])
        mab = plan.get("ma_bull_align") or {}
        self.assertTrue(mab.get("recommend"))


class TestAlignBlockers(unittest.TestCase):
    """不成立场景：临界观察（暂不买），reason 必须可解读。"""

    def test_tangled_short_mas_block(self):
        """价格跌回短均线簇（MA5 未在价格与 MA7 之上）⇒ 不成立。"""
        plan = plan_of(sine_series(64))
        mab = plan.get("ma_bull_align") or {}
        self.assertFalse(mab.get("recommend"))
        self.assertIn("短均线未严格多头排列", mab.get("why") or "")
        self.assertNotEqual(plan["mode"], "ma_bull_align")

    def test_ma60_pressing_above_price_blocks(self):
        """上海机场式：价格收复 MA5/MA10 但 MA60 仍压在头上 ⇒ 长均未托底，不成立。"""
        plan = plan_of(two_peak_recovery())
        mab = plan.get("ma_bull_align") or {}
        self.assertFalse(mab.get("recommend"))
        self.assertIn("MA60", mab.get("why") or "")
        self.assertIn("压制", mab.get("why") or "")
        self.assertNotEqual(plan["mode"], "ma_bull_align")

    def test_too_few_bars_returns_none(self):
        bars = _bars(sine_series(59))
        self.assertIsNone(R.ma_bull_align(bars, None, R.atr14(bars), bars[-1]["c"]))


class TestRealCases(unittest.TestCase):
    """老罗 10-13 提供的四只真实案例（前复权日线，截止 2026-10-13）。

    上海机场 = 临界观察（还没到买点）；中车/神华/海控 = 排列成立但当日另有
    W 底突破 ⇒ 按老罗裁决走突破类，排列状态挂载备查。
    """

    def test_600009_critical_observation(self):
        plan = plan_of_bars(_fixture("600009_airport_bars.json"), "600009")
        mab = plan.get("ma_bull_align") or {}
        self.assertFalse(mab.get("recommend"), "上海机场 10-13：临界观察，暂不买")
        why = mab.get("why") or ""
        self.assertIn("短均线未严格多头排列", why)
        self.assertIn("MA60", why)
        self.assertNotEqual(plan["mode"], "ma_bull_align")

    def test_601766_alignment_attached_breakout_wins(self):
        plan = plan_of_bars(_fixture("601766_crrc_bars.json"), "601766")
        mab = plan.get("ma_bull_align") or {}
        self.assertTrue(mab.get("recommend"), "中车 10-13：MA5>MA7>MA10 且价>MA20>MA60")
        # 老罗裁决：当日符合突破类（其他战法）⇒ 走突破类，排列仅挂载。
        # 注：fixture 窗口自 2025-06 起（比冒烟测试多 3 个月历史），枢轴/活平台
        # 判定与短窗略有差异 —— 此处按 fixture 实际形态 platform_break 断言。
        self.assertEqual(plan["mode"], "platform_break")
        ms = mab.get("ma_state") or {}
        self.assertTrue(ms.get("ma5") > ms.get("ma7") > ms.get("ma10"))

    def test_601088_alignment_attached_breakout_wins(self):
        plan = plan_of_bars(_fixture("601088_shenhua_bars.json"), "601088")
        mab = plan.get("ma_bull_align") or {}
        self.assertTrue(mab.get("recommend"), "神华 10-13：排列成立")
        self.assertEqual(plan["mode"], "platform_break")

    def test_601919_alignment_attached_not_align_mode(self):
        plan = plan_of_bars(_fixture("601919_cosco_bars.json"), "601919")
        mab = plan.get("ma_bull_align") or {}
        self.assertTrue(mab.get("recommend"), "海控 10-13：排列成立")
        self.assertNotEqual(plan["mode"], "ma_bull_align",
                            "当日 W 底突破已延伸被降级 ⇒ 仍不走排列 mode（反追高优先）")


if __name__ == "__main__":
    unittest.main()
