# -*- coding: utf-8 -*-
"""出水芙蓉(T0) 长上影剔除：2026-10-22 老罗定「长上影不算数」。

运行：python tests/entry/test_t0_long_upper_shadow.py
"""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
import rule123 as R


def _mk_bars(last):
    """构造一条「可过 ma_reclaim_break 全部闸门」的序列：前 29 根收盘平走 100，
    最后一根 = last（dict: o/h/l/c）。D0 收盘必须 > 前收、且站上三均线；
    窗口内 (bars[10]) 留一个前高 110 当作阻力位（主板 600000 上限 10%，
    gap=(110-收)/收<10 通过），平台度=100%。
    """
    bars = []
    for i in range(29):
        bars.append({"d": "2026-01-%02d" % (i + 1), "o": 100.0, "h": 100.5,
                     "l": 99.5, "c": 100.0})
    bars[10]["h"] = 110.0            # 左侧阻力位
    bars.append(dict(last, d="2026-02-01"))
    return bars


class LongUpperShadow(unittest.TestCase):
    def test_shooting_star(self):
        # 上影 9.5，实体 0.5 → 上影/实体 = 19 → 长上影
        self.assertTrue(R.long_upper_shadow(
            {"o": 100.0, "h": 110.0, "l": 99.0, "c": 100.5}))

    def test_doji_with_wick(self):
        # 十字星（实体≈0）+ 上影占振幅 5/7 → 长上影
        self.assertTrue(R.long_upper_shadow(
            {"o": 100.0, "h": 105.0, "l": 98.0, "c": 100.0}))

    def test_clean_close_near_high(self):
        # 实体 0.8，上影 0.2 → 不判长上影
        self.assertFalse(R.long_upper_shadow(
            {"o": 100.0, "h": 101.0, "l": 99.0, "c": 100.8}))

    def test_gap_up_close_near_high(self):
        # 高开大涨收在近最高，上影极小 → 不判长上影（中科飞测类）
        self.assertFalse(R.long_upper_shadow(
            {"o": 100.0, "h": 105.0, "l": 99.5, "c": 104.9}))

    def test_missing_fields(self):
        self.assertFalse(R.long_upper_shadow({"o": 100.0, "c": 100.0}))


class T0LongUpperReject(unittest.TestCase):
    """集成：同一组可通过闸门的序列，D0 换成「长上影」后必须不出信号。"""

    def _ev(self):
        return {"ticker": "600000"}

    def test_clean_d0_passes(self):
        bars = _mk_bars({"o": 100.5, "h": 101.3, "l": 100.2, "c": 101.0})
        res = R.ma_reclaim_break(bars, self._ev(), 2.0, bars[-1]["c"])
        self.assertIsNotNone(res, "干净 D0 应出 T0 信号")
        self.assertEqual(res.get("mode"), "ma_reclaim_break")

    def test_long_upper_d0_rejected(self):
        bars = _mk_bars({"o": 100.0, "h": 108.0, "l": 99.5, "c": 100.5})
        self.assertTrue(R.long_upper_shadow(bars[-1]),
                        "前置：该 D0 确为长上影")
        res = R.ma_reclaim_break(bars, self._ev(), 2.0, bars[-1]["c"])
        self.assertIsNone(res, "长上影 D0 出水芙蓉不算数")


if __name__ == "__main__":
    unittest.main()
