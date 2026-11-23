#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""top_judgment.py · 顶部形态判断（几何形态版）回归测试

覆盖三道闸门：
  ① 形态层：12 种几何形态（判定逻辑在 test_top_shapes.py）
  ② 位置闸门「这一波最高点」：左侧有更高高点 ⇒ 不算顶部信号（排除，不否决）
  ③ 次日确认制：形态完成后次日走弱 ⇒ 确认(block)；反包 ⇒ 推翻(ok)
时效口径：形态必须在最近 scan 根内完成，否则不算数（半年前的顶不该今天拦买点）。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import top_judgment as TJ   # noqa: E402


def bars_from_closes(closes, spread=0.006):
    out = []
    for i, c in enumerate(closes):
        out.append({"d": "d%03d" % i, "o": c, "h": c * (1 + spread),
                    "l": c * (1 - spread), "c": c, "v": 1.0})
    return out


def closes_from_legs(legs, start=100.0, flat_bars=15):
    closes = [start] * flat_bars
    cur = start
    for target, n in legs:
        step = (target - cur) / n
        for _ in range(n):
            cur += step
            closes.append(cur)
    return closes


class TestTrendGateTopJudgment(unittest.TestCase):
    def _double_top_then(self, tail_leg):
        """构造：涨到120 → 回落110 → 再涨120（双顶）→ 尾段按 tail_leg 走。"""
        legs = [(120, 10), (110, 8), (120, 8)] + tail_leg
        return bars_from_closes(closes_from_legs(legs))

    def test_double_top_then_weak_downgrades_without_strong_pull(self):
        """双顶后次日走弱 ⇒ 形态已确认，但**非「强拉 ≥15% + 无旗形」** ⇒ C 口径降级缩仓。

        ⚑ 2026-11-05 C 口径（老罗定）：否决权只留给「20 日涨幅 ≥15% 且无旗形结构」的形态；
        其余已确认形态一律 alert（size_factor 0.5）—— 仍预警、仍缩仓，但**不禁买**。
        强拉版对照见 test_double_top_strong_pull_confirms_block。
        """
        bars = self._double_top_then([(112, 2)])
        v = TJ.top_verdict(bars)
        self.assertEqual(v["state"], "confirmed", f"形态应已确认：{v.get('summary')}")
        self.assertEqual(v["level"], "alert",
                         f"非强拉应降级为 alert，实际 {v['level']} / {v.get('summary')}")
        self.assertEqual(v["exit_code"], 1)
        self.assertTrue(v["veto_downgraded"])
        self.assertAlmostEqual(v["size_factor"], 0.5)
        self.assertLess(v["gain20"], TJ.VETO_MIN_GAIN20)
        self.assertEqual(v["signal"]["pattern_cn"], "双重顶")
        self.assertTrue(v["signal"]["is_top"], "峰值应是这一波最高点")

    def test_double_top_strong_pull_confirms_block(self):
        """同一形态 + 20 日涨幅 ≥15% 且无旗形 ⇒ 保留否决权，level=block / 退出码 2。"""
        closes = closes_from_legs([(120, 6), (110, 3), (120, 4), (112, 2)],
                                  start=97.0, flat_bars=20)
        v = TJ.top_verdict(bars_from_closes(closes))
        self.assertEqual(v["level"], "block",
                         f"强拉应保留否决，实际 {v['level']} / {v.get('summary')}")
        self.assertEqual(v["exit_code"], 2)
        self.assertFalse(v["veto_downgraded"])
        self.assertGreaterEqual(v["gain20"], TJ.VETO_MIN_GAIN20)
        self.assertFalse(v["flag_seen"])
        self.assertEqual(v["signal"]["pattern_cn"], "双重顶")

    def test_double_top_then_rebound_invalidated(self):
        """双顶后收盘反包（收复信号棒最高价）⇒ 推翻，level=ok。

        场景：120/120 双顶 → 回调 112 → 再拉 132（明显更高 ⇒ 反包推翻，
        且不会把 120/132 误认成新双顶）。
        """
        legs = [(120, 10), (110, 8), (120, 8), (112, 2), (132, 3)]
        bars = bars_from_closes(closes_from_legs(legs))
        v = TJ.top_verdict(bars)
        self.assertEqual(v["level"], "ok", f"反包应推翻，实际 {v['level']}")
        self.assertEqual(v["state"], "invalidated")

    def test_old_pattern_not_counted(self):
        """时效口径：形态完成于很久以前 ⇒ 不作数（不拦今天的买点）。"""
        legs = [(120, 10), (110, 8), (120, 8), (112, 2)]
        closes = closes_from_legs(legs) + [110] * 30      # 形态后走弱并沉寂 30 根
        bars = bars_from_closes(closes)
        v = TJ.top_verdict(bars)
        self.assertEqual(v["level"], "ok",
                         f"陈旧形态不该拦买点，实际 {v['level']}")

    def test_not_wave_high_rejected(self):
        """位置闸门：左侧有更高高点 ⇒ 该形态不算顶部信号（被排除，不否决）。

        场景：左侧先冲到 150，之后 120/120 双顶 ⇒ 双顶的峰值不是这一波最高点。
        """
        legs = [(150, 10), (110, 8), (120, 8), (115, 4), (120, 6), (112, 2)]
        bars = bars_from_closes(closes_from_legs(legs))
        v = TJ.top_verdict(bars)
        self.assertGreaterEqual(v["n_rejected"], 1,
                                "非波峰形态应进 rejected 列表")
        self.assertNotEqual(v["level"], "block", "非波峰不应触发否决")

    def test_clean_uptrend_ok(self):
        bars = bars_from_closes(closes_from_legs([(130, 20)]))
        v = TJ.top_verdict(bars)
        self.assertEqual(v["level"], "ok", "纯上涨不应报顶部")
        self.assertEqual(v["n_signals"], 0)

    def test_month_super_yin_ban(self):
        """月线 50% 超大阴线 ⇒ 拉黑（老罗 2026-09-26 口径：相对**上月收盘**跌≥50%）。"""
        bars = []
        for i in range(10):                       # 2026-01：收 200
            bars.append({"d": "2026-01-%02d" % (i + 1), "o": 200.0, "h": 205.0,
                         "l": 199.0, "c": 200.0, "v": 1.0})
        for i in range(10):                       # 2026-02：开 205 → 收 90（腰斩）
            c = 205.0 - 12.5 * i
            bars.append({"d": "2026-02-%02d" % (i + 1), "o": c + 3.0, "h": c + 4.0,
                         "l": c - 1.0, "c": c, "v": 1.0})
        for i in range(10):                       # 2026-03：低位平（不创新高 ⇒ 拉黑有效）
            bars.append({"d": "2026-03-%02d" % (i + 1), "o": 90.0, "h": 92.0,
                         "l": 88.0, "c": 90.0, "v": 1.0})
        sy = TJ.month_super_yin(bars)
        self.assertIsNotNone(sy, "月线腰斩应触发拉黑")
        self.assertGreaterEqual(sy["drop"], 0.5)
        # 引擎出口：拉黑后 recommend 必须为 False
        res = TJ.apply_month_blacklist({"recommend": True, "verdict": "x",
                                         "note": "y"}, bars)
        self.assertFalse(res["recommend"])
        self.assertTrue(res.get("blacklist"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
