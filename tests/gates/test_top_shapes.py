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


class TestBearishContext(unittest.TestCase):
    """大背景层：形态破位（首选）→ 趋势破位兜底（老罗 2026-10-31）。"""

    def test_trend_fallback_when_no_pattern(self):
        """无顶部形态但趋势破位（跌破 MA20 且 MA20 走空）⇒ 兜底给背景。"""
        closes = legs_to_closes([(100, 10), (88, 12), (84, 8), (80, 6)])
        bars = bars_from_closes(closes)
        ctx = TS.bearish_context(bars)
        self.assertIsNotNone(ctx, "下跌趋势应给出兜底背景")
        self.assertTrue(ctx.get("fallback"), "应是兜底背景")
        self.assertIn("兜底", ctx["name"])
        self.assertEqual(ctx["start_i"], ctx["start_i"])   # 成立日存在

    def test_fallback_branch_is_reachable(self):
        """★ 强制走**兜底分支**（形态检测不出时）——该分支曾因 NameError 从未执行过。

        做法：monkeypatch detect_top_shapes 返回空 ⇒ 只剩趋势破位兜底。
        """
        orig = TS.detect_top_shapes
        try:
            TS.detect_top_shapes = lambda bars, **kw: []
            closes = legs_to_closes([(100, 10), (88, 12), (84, 8), (80, 6)])
            bars = bars_from_closes(closes)
            ctx = TS.bearish_context(bars)
            self.assertIsNotNone(ctx, "兜底分支应给出背景")
            self.assertTrue(ctx.get("fallback"))
            self.assertIn("兜底", ctx["name"])
            self.assertIsNotNone(ctx["break_level"])
        finally:
            TS.detect_top_shapes = orig

    def test_uptrend_no_context(self):
        """持续上涨 ⇒ 既无形态背景也无趋势兜底 ⇒ 无背景。"""
        closes = legs_to_closes([(140, 25)])
        bars = bars_from_closes(closes)
        ctx = TS.bearish_context(bars)
        self.assertIsNone(ctx, "纯上涨不应有做空背景")


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

    def test_flag_continuation(self):
        """旗形：陡峭旗杆 + 窄幅下倾旗面 ⇒ 中继形态，**不是**圆顶。

        老罗 2026-10-31 实测：明月镜片这根「旗形」曾被误判成圆顶 ⇒ 顶部闸门误杀。
        """
        closes = legs_to_closes([(100, 10), (132, 8), (131, 1), (129, 1), (127, 1)])
        bars = bars_from_closes(closes)
        got = names(TS.detect_top_shapes(bars))
        self.assertIn("旗形", got, f"应识别出旗形，实际 {got}")
        self.assertNotIn("圆顶", got, f"旗形不得被误判成圆顶，实际 {got}")

    def test_dome_needs_gradual_arc(self):
        """圆顶必须是**渐进多段**推进：单根脉冲不得主导。"""
        closes = legs_to_closes([(112, 8), (108, 4), (120, 8), (116, 4),
                                  (125, 8), (118, 6)])
        bars = bars_from_closes(closes)
        got = names(TS.detect_top_shapes(bars))
        self.assertIn("圆顶", got, f"应识别出圆顶，实际 {got}")

    def test_dome_rejects_single_bar_pulse(self):
        """一根涨停拉完的「顶」不是圆顶（单根涨幅占总涨幅 > 45% ⇒ 旗杆）。"""
        closes = legs_to_closes([(112, 8), (108, 4), (140, 2), (132, 3)])
        bars = bars_from_closes(closes)
        got = names(TS.detect_top_shapes(bars))
        self.assertNotIn("圆顶", got, f"单根脉冲主导不得判成圆顶，实际 {got}")


def _long_seq(legs, base=40, start=100.0):
    """前置 base 根水平基线，再按 (目标价, 根数) 腿展开。

    基线要够长（detect_top_shapes 回看 SHAPE_WINDOW=140、pivots 需要摆动点），
    短线序列根本产不出摆动点 ⇒ 任何形态都检不出（踩过）。
    """
    closes = [start] * base
    cur = start
    for target, n in legs:
        step = (target - cur) / n
        for _ in range(n):
            cur += step
            closes.append(cur)
    return closes


class TestBreakLevelNeckline(unittest.TestCase):
    """⚑ 老罗 2026-11-05 拍板「改法 A」：V顶/圆顶的 break_level 必须是**真颈线**。

    bug 原状：`_det_v_top` 传 `break_level=hi_p`、`_det_dome_top` 传 `break_level=H3[1]`，
    都是**峰价本身** ⇒ 只要收盘在峰价下方就恒判「已破位(反转确认)」。
    全市场实测：V顶 1,312/1,312、圆顶 43/43 全是假确认，
    并连带让 `bearish_context`（做空背景）在「刚从峰价回落」就成立。
    """

    def test_v_top_break_level_is_true_neckline(self):
        bars = bars_from_closes(_long_seq([(132, 8), (118, 5)]))
        vts = [s for s in TS.detect_top_shapes(bars) if s["pattern"] == "V顶"]
        self.assertTrue(vts, "应识别出 V顶")
        vt = vts[0]
        self.assertLess(vt["break_level"], vt["peak"],
                        f"V顶 break_level({vt['break_level']}) 必须低于峰价({vt['peak']})，"
                        "否则「已破位」恒真")
        # 真颈线 = 峰后回落最低价
        after = bars[vt["peak_i"] + 1:vt["peak_i"] + 1 + 8]
        self.assertAlmostEqual(vt["break_level"], min(b["l"] for b in after), places=4,
                              msg="V顶 break_level 应等于峰后回落最低价")

    def test_dome_break_level_is_true_neckline(self):
        bars = bars_from_closes(legs_to_closes([(112, 8), (108, 4), (120, 8), (116, 4),
                                               (125, 8), (118, 6)]))
        domes = [s for s in TS.detect_top_shapes(bars) if s["pattern"] == "圆顶"]
        self.assertTrue(domes, "应识别出圆顶（本用例的构造见 test_dome_needs_gradual_arc）")
        d = domes[0]
        self.assertIsNotNone(d["break_level"], "圆顶必须给出可用的颈线，不能是 None")
        self.assertLess(d["break_level"], d["peak"],
                        f"圆顶 break_level({d['break_level']}) 必须低于峰价({d['peak']})")

    def test_v_top_not_confirmed_until_neckline_breaks(self):
        """价格从峰价回落但**未跌破峰后低点** ⇒ 不得标 confirmed（原 bug 会标 True）。"""
        bars = bars_from_closes(_long_seq([(132, 8), (118, 5)]))
        vts = [s for s in TS.detect_top_shapes(bars) if s["pattern"] == "V顶"]
        self.assertTrue(vts)
        vt = vts[0]
        # 末根收在峰价下方（118 < 132）但颈线之上（117.29），应是「未完成」
        self.assertLess(bars[-1]["c"], vt["peak"], "本用例末根应低于峰价")
        self.assertGreater(bars[-1]["c"], vt["break_level"], "本用例末根应仍在颈线之上")
        self.assertFalse(vt["confirmed"],
                         "收盘未跌破真颈线就不得判「已破位」，原 bug 这里恒为 True")


class TestFlagCoversTop(unittest.TestCase):
    """⚑ 老罗 2026-11-05 拍板「改法 B」：旗形豁免的匹配口径。

    原判据「顶部形态 peak_i 与旗杆顶相距 ≤2 根」——在真实数据里只覆盖到 7%
    的豁免；改为「**顶部形态的峰落在旗杆区间 [l, e] 内**」后覆盖到 93%。
    语义：峰值出现在急速上攻那一段上，说明那个「顶」本身就是旗杆的一部分。
    """

    def test_v_top_covered_when_peak_inside_pole(self):
        bars = bars_from_closes(_long_seq([(132, 8), (118, 5)]))
        shapes = TS.detect_top_shapes(bars)
        self.assertIn("旗形", names(shapes), "应识别出旗形（旗杆 32% / 旗面 5 根窄幅下倾）")
        vts = [s for s in shapes if s["pattern"] == "V顶"]
        self.assertTrue(vts, "应识别出 V顶（与旗形同位）")
        vt = vts[0]
        self.assertTrue(vt.get("covered_by_flag"),
                        "V顶 的峰落在旗杆区间内 ⇒ 必须被旗形覆盖，改判中继")
        self.assertEqual(vt["bias"], "continuation",
                         "被覆盖后 bias 必须改成 continuation，否则仍会进顶部候选池")

    def test_flag_not_detected_when_band_too_wide(self):
        """旗面回撤超过旗杆 50% ⇒ 不是旗形（不能把下跌当整理）。"""
        bars = bars_from_closes(_long_seq([(132, 8), (90, 6)]))   # 回撤 32% vs 旗杆 32%
        got = names(TS.detect_top_shapes(bars))
        self.assertNotIn("旗形", got, f"旗面破掉旗杆一半以上不得算旗形，实际 {got}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
