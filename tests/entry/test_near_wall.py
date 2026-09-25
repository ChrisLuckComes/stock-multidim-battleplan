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


if __name__ == "__main__":
    unittest.main(verbosity=2)
