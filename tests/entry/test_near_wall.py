# -*- coding: utf-8 -*-
"""rr_near_wall 顺延口径回归（2026-09-23）。

症状（德邦 688035）：当 **D0 自己创了窗口新高**（D0.high 86.0 > 窗口最高 85.5）时，
`rr_near_wall = (resistance - trigger) / (trigger - hard)` 把「相对**收盘价**找到的墙」
当成了「**触发价之上**的墙」—— 85.5 早在 09-22 就被 86.0 踩在脚下，减出来必然是**负数**
（德邦 −0.11）⇒ 把「刚过 1.5 门槛、勉强可做」误判成「R = 0、不可做」。

修法（A 方案）：D0 创新高导致 `resistance < trigger` 时，顺延到更长历史里找
「**≥ trigger 且价格最低**」的枢轴高 = 触发价之上第一道**未被超越**的墙。
德邦 → 92.70（实盘数据）⇒ R = 1.50。

跑法：`python tests/entry/test_near_wall.py`。
"""
import os
import sys
import unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import rule123 as R


def _mk_bars(d0_high, hist_wall=True):
    """构造 61 根：历史(30) → 平台(26) → 冲高 85.5(1) → 回踩(3) → D0(1)。

    - `hist_wall=True` 时在更早历史第 8 根埋一个 92.70 的枢轴高（左右各 2 根都更低）；
    - D0 收盘固定 84.5（< 窗口最高 85.5 ⇒ 能过 `resistance < last_c` 那道闸门），
      high 由参数控制 —— 传 86.0 即「D0 创窗口新高」，传 85.0 即「未创」。
    """
    bars = []
    for i in range(30):
        h = 92.70 if (hist_wall and i == 8) else 80.5
        bars.append({"d": f"H{i:03d}", "o": 79.5, "h": h, "l": 78.5, "c": 80.0, "v": 1e6})
    for i in range(26):
        bars.append({"d": f"P{i:03d}", "o": 80.0, "h": 81.0, "l": 79.0, "c": 80.0, "v": 1e6})
    bars.append({"d": "W000", "o": 80.5, "h": 85.5, "l": 80.0, "c": 84.0, "v": 2e6})
    for i in range(3):
        bars.append({"d": f"R{i:03d}", "o": 82.5, "h": 83.0, "l": 81.5, "c": 82.0, "v": 1e6})
    bars.append({"d": "D000", "o": 84.0, "h": d0_high, "l": 83.5, "c": 84.5, "v": 1.5e6})
    return bars


class TestNearWallExtend(unittest.TestCase):
    def _run(self, bars):
        return R.ma_reclaim_break(bars, {"ticker": "688035"}, 2.0, bars[-1]["c"])

    def test_d0_breaks_window_high_extends_upward(self):
        """D0 创窗口新高（86.0 > 85.5）⇒ 墙必须顺延到历史枢轴 92.70，且 R 不能是负数。"""
        r = self._run(_mk_bars(86.0))
        self.assertIsNotNone(r)
        self.assertTrue(r["near_wall_extended"])
        self.assertEqual(r["near_wall"], 92.70)
        self.assertEqual(r["near_wall_kind"], "顺延枢轴高")
        self.assertIsNotNone(r["rr_near_wall"])
        self.assertGreater(r["rr_near_wall"], 0)
        # 原「相对收盘价」的墙要保持原样，不被顺延值覆盖（两套口径不得混同）
        self.assertEqual(r["resistance"], 85.5)
        self.assertLess(r["resistance"], 86.0)

    def test_d0_inside_window_keeps_original_wall(self):
        """D0 未创窗口新高 ⇒ 不顺延，near_wall 恒等于 resistance（老行为逐字不变）。"""
        r = self._run(_mk_bars(85.0))
        self.assertIsNotNone(r)
        self.assertFalse(r["near_wall_extended"])
        self.assertEqual(r["near_wall"], r["resistance"])
        self.assertEqual(r["near_wall_kind"], r["resistance_kind"])


class TestPokeWallIsNotTarget(unittest.TestCase):
    def test_near_wall_skipped_for_the_next_one(self):
        """101.5 距 100 只有 0.375×ATR，是门。目标用后面的 112。"""
        bars = [{"d": "d", "o": 99, "h": 112, "l": 90, "c": 100, "v": 1}]
        tg = R.targets(bars, "line_pullback", {}, 4.0, 100.0, [(0, 101.5), (0, 112.0)])
        self.assertEqual(tg["target1"], 112.0)

    def test_only_poke_uses_two_atr(self):
        bars = [{"d": "d", "o": 99, "h": 101.5, "l": 90, "c": 100, "v": 1}]
        tg = R.targets(bars, "line_pullback", {}, 4.0, 100.0, [(0, 101.5)])
        self.assertEqual(tg["target1"], 108.0)

    def test_prior_high_gate_is_not_the_target(self):
        """下降趋势线突破的前高可以超过 0.5×ATR，仍然是门。"""
        bars = [{"d": "d", "o": 99, "h": 112, "l": 90, "c": 100, "v": 1}]
        plain = R.targets(bars, "line_pullback", {}, 4.0, 100.0, [(0, 103.4), (0, 112.0)])
        self.assertEqual(plain["target1"], 103.4)
        gated = R.targets(
            bars, "downtrend_tl_break", {"through_gate": 103.4}, 4.0, 100.0,
            [(0, 103.4), (0, 112.0)],
        )
        self.assertEqual(gated["target1"], 112.0)
        shelf = R.targets(
            bars, "flag_tl_break", {"through_gate": 100.0}, 4.0, 100.0,
            [(0, 100.2), (0, 120.0)],
        )
        self.assertEqual(shelf["target1"], 120.0)

    def test_wall_past_half_atr_stays(self):
        """1.2×ATR 的墙不是一捅就过，仍是目标。"""
        bars = [{"d": "d", "o": 99, "h": 120, "l": 90, "c": 100, "v": 1}]
        tg = R.targets(bars, "line_pullback", {}, 4.0, 100.0, [(0, 104.8), (0, 120.0)])
        self.assertEqual(tg["target1"], 104.8)

    def test_new_high_opens_upside(self):
        """前高 100，收盘 120 且上方无墙 ⇒ 目标 = 120+6×ATR，不是 +2×ATR。"""
        bars = []
        for i in range(25):
            bars.append({"d": "p%d" % i, "o": 98, "h": 100, "l": 96, "c": 99, "v": 1})
        bars.append({"d": "d", "o": 110, "h": 122, "l": 109, "c": 120, "v": 1})
        tg = R.targets(bars, "platform_break", {}, 4.0, 120.0, [])
        self.assertTrue(tg["upside_open"])
        self.assertEqual(tg["target1"], 144.0)
        # 止损放在 2×ATR 下，现价 R 仍然过 1.5
        tg2 = R.targets(bars, "platform_break", {"hard_stop": 112.0}, 4.0, 120.0, [])
        self.assertEqual(tg2["rr_target1"], 3.0)

    def test_platform_measured_not_pulled_back_into_the_door(self):
        """近端 101 是门。门后的 130 才是目标，不用测量涨幅把目标压在 108。"""
        z = {"level": 100, "primary_hi": 102, "primary_lo": 98}
        bars = [{"d": "d", "o": 99, "h": 130, "l": 90, "c": 100, "v": 1}]
        tg = R.targets(bars, "platform_break", z, 4.0, 100.0, [(0, 101.0), (0, 130.0)])
        self.assertEqual(tg["target1"], 130.0)

    def test_breakout_without_further_wall_opens_space(self):
        """突破后没有更远的墙，目标用入场+6×ATR，不用 2×ATR。"""
        bars = [{"d": "d", "o": 99, "h": 101.5, "l": 90, "c": 100, "v": 1}]
        tg = R.targets(bars, "flag_tl_break", {"through_gate": 103.4}, 4.0, 100.0, [(0, 103.4)])
        self.assertTrue(tg["space_open"])
        self.assertEqual(tg["target1"], 124.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
