#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""top_shapes.py · 12 种顶部反转形态识别的回归测试

正例：每种形态合成一段价格序列 ⇒ 必须被识别出对应 pattern 名。
反例：纯上涨趋势 ⇒ 不应识别出任何偏空顶部形态（防误报）。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import top_shapes as TS   # noqa: E402


def bars_from_closes(closes, spread=0.006):
    """由收盘序列造 bars（高/低给 ±spread）。"""
    out = []
    for i, c in enumerate(closes):
        out.append({"d": "d%03d" % i, "o": c, "h": c * (1 + spread),
                    "l": c * (1 - spread), "c": c, "v": 1.0})
    return out


def legs_to_closes(legs, n_per_leg=10, start=100.0):
    """把 (目标价, 根数) 腿序列展开成收盘序列（每腿线性插值）。

    前置 15 根水平基线（凑够识别门槛 + 让摆动点更接近真实行情）。
    """
    closes = [start] * 15
    cur = start
    for target, n in legs:
        step = (target - cur) / n
        for _ in range(n):
            cur += step
            closes.append(cur)
    return closes


def names(shapes):
    return [s["pattern"] for s in shapes]


class TestPatterns(unittest.TestCase):
    def _detect(self, closes, expect):
        bars = bars_from_closes(closes)
        got = names(TS.detect_top_shapes(bars))
        self.assertIn(expect, got, f"应识别出「{expect}」，实际得到 {got}")

    def test_head_and_shoulders(self):
        closes = legs_to_closes([(120, 10), (110, 8), (135, 10), (111, 8),
                                  (121, 8), (100, 10), (108, 6), (96, 8)])
        self._detect(closes, "头肩顶")

    def test_double_top(self):
        closes = legs_to_closes([(120, 12), (108, 8), (120.5, 8), (104, 10)])
        self._detect(closes, "双重顶")

    def test_triple_top(self):
        closes = legs_to_closes([(120, 10), (109, 7), (120.5, 7), (109.5, 7),
                                  (120.2, 7), (103, 10)])
        self._detect(closes, "三重顶")

    def test_v_top(self):
        closes = legs_to_closes([(100, 8), (130, 6), (98, 12)])
        self._detect(closes, "V顶")

    def test_dome_top(self):
        # 三峰递增但动能衰竭（涨幅 15→9→4），峰间有回落，且已回落破顶
        closes = legs_to_closes([(100, 10), (115, 8), (110, 6), (124, 8),
                                  (118, 6), (128, 8), (112, 10)])
        self._detect(closes, "圆顶")

    def test_symmetric_triangle(self):
        # 上轨降(132→129→126) + 下轨升(120→123→125)，两侧斜率相近 ⇒ 非平轨 ⇒ 对称
        closes = legs_to_closes([(132, 10), (120, 8), (129, 8), (123, 8),
                                  (126, 8), (125, 8)])
        self._detect(closes, "对称三角形")

    def test_descending_triangle(self):
        # 上轨降(130→124→118) + 下轨平(110)，末腿收在下轨处（不破坏平轨结构）
        closes = legs_to_closes([(130, 10), (110, 8), (124, 8), (110, 8),
                                  (118, 8), (110, 8)])
        self._detect(closes, "下降三角形")

    def test_ascending_triangle(self):
        # 上轨平(120) + 下轨升(110→114→118)
        closes = legs_to_closes([(120, 10), (110, 8), (120, 8), (114, 8),
                                  (120, 8), (118, 8), (115, 8)])
        self._detect(closes, "上升三角形")

    def test_broadening_triangle(self):
        # 上轨升(112→118→126) + 下轨降(106→102→98) = 喇叭口
        closes = legs_to_closes([(112, 10), (106, 8), (118, 8), (102, 8),
                                  (126, 8), (98, 8), (120, 8)])
        self._detect(closes, "扩散三角形")

    def test_diamond(self):
        closes = legs_to_closes([(110, 8), (104, 6), (120, 8), (110, 8),
                                  (115, 8), (100, 8)])
        self._detect(closes, "菱形")

    def test_rising_wedge(self):
        # 两轨上行且收敛：高点 100→105→108→110（涨幅递减），低点 95→101→105→107.5
        closes = legs_to_closes([(100, 8), (95, 6), (105, 6), (101, 6),
                                  (108, 6), (105, 6), (110, 6), (107.5, 6)])
        self._detect(closes, "上升楔形")

    def test_descending_wedge(self):
        # 两轨下行且收敛：高点 130→124→120→117.5（跌速递减），低点 122→118→115→113
        closes = legs_to_closes([(130, 8), (122, 6), (124, 6), (118, 6),
                                  (120, 6), (115, 6), (117.5, 6), (113, 6)])
        self._detect(closes, "下降楔形")

    def test_rectangle(self):
        # 上下沿都平，收在上沿处（矩形未破位）
        closes = legs_to_closes([(110, 8), (120, 6), (110, 6), (120, 6),
                                  (110, 6), (120, 6)])
        self._detect(closes, "矩形")


class TestNoFalsePositive(unittest.TestCase):
    def test_clean_uptrend_no_bearish_top(self):
        closes = legs_to_closes([(100, 10), (110, 10), (120, 10), (130, 10),
                                  (140, 10)])
        bars = bars_from_closes(closes)
        shapes = TS.detect_top_shapes(bars)
        bearish = [s["pattern"] for s in shapes if s["bias"] == "bearish"]
        self.assertEqual(bearish, [], f"纯上涨不应识别出偏空顶部形态：{bearish}")

    def test_short_series_safe(self):
        bars = bars_from_closes([100, 101, 102])
        self.assertEqual(TS.detect_top_shapes(bars), [], "数据不足应安全返回空")


if __name__ == "__main__":
    unittest.main(verbosity=2)
