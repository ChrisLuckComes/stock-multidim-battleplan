#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""反转K线识别 · 4 裸K模板 + 3 过滤 的回归测试

正例：每种模板合成一根「到位 + 动能足 + 收低位」的空头反转棒 ⇒ passed=True。
反例：① 位置不符（阻力在远方）② 动能不足（收盘不够极端）③ 收盘在中间（非低位）
      ⇒ 各自对应的过滤 flag = False，passed = False。
额外：纯上涨趋势中不误触发（无空头反转棒 / 无模板）。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
GATES = os.path.join(ROOT, "gates")
if GATES not in sys.path:
    sys.path.insert(0, GATES)

import reverse_kline_short as T   # noqa: E402


def base_uptrend(n=22, top=100.0):
    """22 根温和上涨（小阳），收盘 90→top，构造可用的 MA20 / 结构支撑。"""
    bs = []
    for k in range(n):
        px = 90.0 + (top - 90.0) * k / (n - 1)
        o = px
        c = px + 0.2
        bs.append({"d": "b%d" % k, "o": o, "h": c + 0.3, "l": o - 0.3,
                   "c": c, "v": 1.0})
    return bs


def run_scenario(tail, ma20_override=None, neckline=100.0, atr=1.0):
    """tail = 追加在 22 根基底之后的 [context..., signal] 棒。
    构造阻力集（MA20 + 头肩顶常量），调用通用 detect_reversal_kline。"""
    bs = base_uptrend()
    for k, b in enumerate(tail):
        bs.append(dict(b, d="t%d" % k))
    idx = len(bs) - 1
    mets = T._metrics(bs)
    ma20 = T._roll_ma([b["c"] for b in bs], 20)
    if ma20_override is not None:
        ma20[idx] = ma20_override
        if idx - 1 >= 0:
            ma20[idx - 1] = ma20_override
    resists = [("MA20", ma20[idx]),
               ("头肩顶颈线", neckline),
               ("头肩顶头部", neckline + 8.0),
               ("头肩顶右肩", neckline + 4.0),
               ("头肩顶左肩", neckline + 3.0)]
    return T.detect_reversal_kline(bs, idx, mets, ma20, resists, atr), idx


class TestTemplatesPositive(unittest.TestCase):
    def test_engulfing(self):
        tail = [
            {"o": 99.5, "h": 100.3, "l": 99.2, "c": 100.0, "v": 1.0},
            {"o": 99.6, "h": 99.8, "l": 97.0, "c": 97.5, "v": 2.0},
        ]
        r, _ = run_scenario(tail, neckline=100.0)
        self.assertIsNotNone(r)
        self.assertIn("吞没反转", r["templates"])
        self.assertTrue(r["passed"], "吞没正例应 passed=True")

    def test_pinbar(self):
        tail = [
            {"o": 99.0, "h": 100.0, "l": 98.8, "c": 99.6, "v": 1.0},
            {"o": 99.0, "h": 101.0, "l": 97.0, "c": 98.0, "v": 2.0},
        ]
        r, _ = run_scenario(tail, neckline=100.0)
        self.assertIsNotNone(r)
        self.assertIn("上影线拒绝", r["templates"])
        self.assertTrue(r["passed"], "PinBar 正例应 passed=True")

    def test_fakeout(self):
        tail = [
            {"o": 99.0, "h": 101.5, "l": 99.0, "c": 100.5, "v": 1.5},
            {"o": 100.2, "h": 100.5, "l": 97.5, "c": 98.0, "v": 2.0},
        ]
        r, _ = run_scenario(tail, neckline=100.0)
        self.assertIsNotNone(r)
        self.assertIn("假突破", r["templates"])
        self.assertTrue(r["passed"], "假突破正例应 passed=True")

    def test_exhaustion(self):
        tail = [
            {"o": 98.0, "h": 99.2, "l": 97.8, "c": 99.0, "v": 1.0},
            {"o": 99.0, "h": 100.0, "l": 98.8, "c": 99.8, "v": 1.0},
            {"o": 99.8, "h": 100.6, "l": 99.6, "c": 100.4, "v": 1.0},
            {"o": 100.5, "h": 100.8, "l": 98.0, "c": 98.5, "v": 2.0},
        ]
        r, _ = run_scenario(tail, neckline=100.0)
        self.assertIsNotNone(r)
        self.assertIn("连续衰竭", r["templates"])
        self.assertTrue(r["passed"], "连续衰竭正例应 passed=True")


class TestFiltersNegative(unittest.TestCase):
    def test_position_fail(self):
        tail = [
            {"o": 99.5, "h": 100.3, "l": 99.2, "c": 100.0, "v": 1.0},
            {"o": 99.6, "h": 99.8, "l": 97.0, "c": 97.5, "v": 2.0},
        ]
        r, _ = run_scenario(tail, ma20_override=130.0, neckline=130.0)
        self.assertIsNotNone(r)
        self.assertIn("吞没反转", r["templates"])
        self.assertFalse(r["pos_pass"], "阻力在远方应 pos_pass=False")
        self.assertFalse(r["passed"])

    def test_momentum_fail(self):
        # PinBar 模板成立，但信号收盘(98.5)未低于前一根阳线收盘(98.4) ⇒ 动能过滤失败
        tail = [
            {"o": 98.0, "h": 99.2, "l": 97.8, "c": 99.0, "v": 1.0},
            {"o": 98.0, "h": 99.0, "l": 97.8, "c": 98.4, "v": 1.0},  # 阳，收 98.4
            {"o": 100.2, "h": 104.0, "l": 97.0, "c": 98.5, "v": 2.0},  # PinBar，收 98.5
        ]
        r, _ = run_scenario(tail, neckline=103.0)
        self.assertIsNotNone(r)
        self.assertIn("上影线拒绝", r["templates"])
        self.assertFalse(r["mom_pass"], "收盘不够极端应 mom_pass=False")
        self.assertFalse(r["passed"])

    def test_close_position_fail(self):
        tail = [
            {"o": 97.5, "h": 100.0, "l": 96.0, "c": 100.0, "v": 1.0},
            {"o": 99.5, "h": 100.0, "l": 92.0, "c": 96.5, "v": 2.0},
        ]
        r, _ = run_scenario(tail, neckline=100.0)
        self.assertIsNotNone(r)
        self.assertIn("吞没反转", r["templates"])
        self.assertFalse(r["cp_pass"], "收在中间应 cp_pass=False")
        self.assertFalse(r["passed"])


class TestNoFalseTrigger(unittest.TestCase):
    def test_clean_uptrend_no_signal(self):
        bs = base_uptrend(n=40, top=110.0)
        mets = T._metrics(bs)
        ma20 = T._roll_ma([b["c"] for b in bs], 20)
        resists = [("MA20", ma20[i]) for i in range(len(bs))]
        hs = {"detected": False}
        atr = 1.0
        hits = 0
        for i in range(21, len(bs)):
            r = T.detect_reversal_kline(bs, i, mets, ma20, resists, atr)
            if r is not None and r["passed"]:
                hits += 1
        self.assertEqual(hits, 0, "纯上涨趋势不应产出做空信号")


class TestTrendGate(unittest.TestCase):
    """趋势背景闸门 _in_decline：MA50 非强制，靠 头肩顶 / 近端回撤 判主跌浪。"""

    @staticmethod
    def _bars_with_high_then_fall(high=100.0, last=93.0, n=30):
        bs = []
        for k in range(n):
            if k < 10:                      # 前段冲高到 high
                px = 90.0 + (high - 90.0) * k / 9.0
                bs.append({"d": "d%d" % k, "o": px - 0.2, "h": px + 0.3,
                           "l": px - 0.5, "c": px, "v": 1.0})
            else:                           # 后段回落到 last
                bs.append({"d": "d%d" % k, "o": last + 0.3, "h": last + 0.6,
                           "l": last - 0.3, "c": last, "v": 1.0})
        return bs

    def test_new_stock_in_decline_allowed(self):
        """新股（无头肩顶）主跌浪：近端回撤≥6% ⇒ 放行（不依赖 MA50）。"""
        bs = self._bars_with_high_then_fall(high=100.0, last=93.0)
        hs = {"detected": False}
        ok, reason = T._in_decline(bs[-1]["c"], hs, bs, len(bs) - 1)
        self.assertTrue(ok, "新股主跌浪应放行，不应因无 MA50 被卡")
        self.assertIn("回撤", reason)

    def test_at_high_uptrend_blocked(self):
        """上升途中、近端未回撤（close≈近期高点）⇒ 拦下（提前抄顶伪信号）。"""
        bs = base_uptrend(n=30, top=100.0)   # 收 100，近 30 日高≈100
        hs = {"detected": False}
        ok, _ = T._in_decline(bs[-1]["c"], hs, bs, len(bs) - 1)
        self.assertFalse(ok, "仍在高位未回撤不应放行")

    def test_hst_head_only_not_enough(self):
        """仅低于头部、颈线未破、且 20 日未回撤 ⇒ 不放行（避免提前抄顶）。"""
        # 第5根冲到 100（头），其后 20+ 根在 99 横盘：低于头部但未进入主跌浪
        bs = []
        for k in range(30):
            px = 100.0 if k == 5 else 99.0
            bs.append({"d": "d%d" % k, "o": px - 0.2, "h": px + 0.3,
                       "l": px - 0.3, "c": px, "v": 1.0})
        hs = {"detected": True, "neckline": None, "head": (5, 100.0)}
        ok, _ = T._in_decline(bs[-1]["c"], hs, bs, len(bs) - 1)
        self.assertFalse(ok, "仅低于头部、未破颈线、未回撤不应放行")

    def test_hst_neckline_broken_allowed(self):
        """头肩顶颈线已破 ⇒ 放行（最强顶部确认）。"""
        bs = self._bars_with_high_then_fall(high=100.0, last=93.0)
        hs = {"detected": True, "neckline": 95.0, "head": (5, 100.0)}
        ok, reason = T._in_decline(bs[-1]["c"], hs, bs, len(bs) - 1)
        self.assertTrue(ok)
        self.assertIn("颈线", reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
