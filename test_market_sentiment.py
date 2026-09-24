# -*- coding: utf-8 -*-
"""大盘情绪打分回归（2026-09-24）。

背景：情绪分是**软约束**（只缩放仓位），但总分档位对「宽度中性点」极度敏感——
中性点从 45% 挪到 50%，同一盘面（上涨占比 27%）总分从 34.4 掉到 27.3，
**跨过 30 分那条档位线**，结论从「小仓试错」变成「禁开新仓」⇒ 等于让软约束行使了否决权。
故把三个映射常数与档位边界钉在这里：

  - 宽度中性点 WIDTH_CENTER 必须 < 50%（A 股跌家数常年多于涨家数，用 50% 会让该维度全年负偏）
  - 普跌区间（上涨占比 22%~34%）必须保持区分度，不能被压平到 0 分附近
  - 资金斜率 MONEY_SLOPE=1000 ⇒ -5% 归零，保留「中等流出 vs 重度流出」的区分

跑法：`python test_market_sentiment.py`
"""
import datetime
import unittest

import market_sentiment as ms


class TestWidthMapping(unittest.TestCase):
    def test_center_must_be_below_50pct(self):
        """中性点不得 ≥50%：否则普跌日常态被打成低分，情绪分长期偏负。"""
        self.assertLess(ms.WIDTH_CENTER, 0.50,
                        "宽度中性点 ≥50%：A股跌家数常年多于涨家数，会系统性把宽度维度压低")
        self.assertGreater(ms.WIDTH_CENTER, 0.30)

    def test_center_gives_50_points(self):
        s, r = ms.score_width(45, 55)
        self.assertAlmostEqual(s, 50.0, places=6)
        self.assertAlmostEqual(r, 0.45, places=6)

    def test_today_like_reading(self):
        """涨1375 / 跌3709（占比 27.0%）→ 14.1 分（中性点 50% 时会掉到 3.8 分）。"""
        s, r = ms.score_width(1375, 3709)
        self.assertAlmostEqual(r * 100, 27.0, delta=0.2)
        self.assertAlmostEqual(s, 14.1, delta=0.3)

    def test_weak_range_keeps_resolution(self):
        """上涨占比 22%→34% 必须单调递增且跨度 ≥15 分（v2 口径下这段几乎全挤在 0 分）。"""
        vals = [ms.score_width(int(x * 1000), int((1 - x) * 1000))[0]
                for x in (0.22, 0.25, 0.28, 0.31, 0.34)]
        for a, b in zip(vals, vals[1:]):
            self.assertLess(a, b, f"宽度分不再单调递增：{vals}")
        self.assertGreaterEqual(vals[-1] - vals[0], 15.0, f"普跌区间被压平：{vals}")

    def test_clamped(self):
        self.assertEqual(ms.score_width(0, 100)[0], 0.0)
        self.assertEqual(ms.score_width(100, 0)[0], 100.0)
        self.assertEqual(ms.score_width(0, 0)[0], 50.0)


class TestMoneyMapping(unittest.TestCase):
    def test_zero_is_neutral(self):
        self.assertAlmostEqual(ms.score_money(0, 100)[0], 50.0)

    def test_minus_5pct_hits_zero(self):
        self.assertAlmostEqual(ms.score_money(-5, 100)[0], 0.0)

    def test_keeps_resolution_between_flows(self):
        """-2% 与 -4% 必须区分开（MONEY_SLOPE=2000 时两者都是 0 分）。"""
        s2 = ms.score_money(-2, 100)[0]
        s4 = ms.score_money(-4, 100)[0]
        self.assertGreaterEqual(s2 - s4, 15.0, f"资金维度失去区分度：{s2} vs {s4}")

    def test_today_like_reading(self):
        """-3.37% → 16.3 分。"""
        self.assertAlmostEqual(ms.score_money(-3.37, 100)[0], 16.3, delta=0.2)


class TestTempVolumeTrend(unittest.TestCase):
    def test_temp_scales_with_day_frac(self):
        """涨停中性值随当日进度缩放：净+35 家，全天口径 = 68 分，进度 44% 口径 ≈ 81.4 分。"""
        self.assertAlmostEqual(ms.score_temp(39, 4, 1.0)[0], 68.0, delta=0.1)
        self.assertAlmostEqual(ms.score_temp(39, 4, 0.44)[0], 81.4, delta=0.2)

    def test_temp_missing_pool_is_neutral(self):
        self.assertEqual(ms.score_temp(None, None)[0], 50.0)

    def test_volume_is_direction_aware(self):
        """放量上涨加分、放量下跌扣分（同一 ratio 必须给出相反方向）。"""
        up = ms.score_volume(1.20, +1.0)
        dn = ms.score_volume(1.20, -1.0)
        self.assertGreater(up, 50.0)
        self.assertLess(dn, 50.0)
        self.assertAlmostEqual(up - 50, 50 - dn, places=6)

    def test_trend_on_ma20(self):
        self.assertAlmostEqual(ms.score_trend([0.0]), 50.0)


class TestLevels(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(ms.level_of(70)[0], "强")
        self.assertEqual(ms.level_of(69.9)[0], "偏强")
        self.assertEqual(ms.level_of(55)[0], "偏强")
        self.assertEqual(ms.level_of(45)[0], "中性")
        self.assertEqual(ms.level_of(34.4)[0], "偏弱")
        self.assertEqual(ms.level_of(30)[0], "偏弱")
        self.assertEqual(ms.level_of(29.9)[0], "极弱")

    def test_soft_note_present(self):
        """输出必须声明软约束性质，避免被当成开/不开仓的开关。"""
        self.assertIn("软约束", ms.SOFT_NOTE)
        self.assertIn("硬约束", ms.SOFT_NOTE)


class TestTimeAccounting(unittest.TestCase):
    def _t(self, h, m):
        return datetime.datetime(2026, 9, 24, h, m)

    def test_traded_minutes(self):
        self.assertEqual(ms.traded_minutes(self._t(9, 0)), 0)
        self.assertEqual(ms.traded_minutes(self._t(9, 30)), 0)
        self.assertEqual(ms.traded_minutes(self._t(10, 0)), 30)
        self.assertEqual(ms.traded_minutes(self._t(11, 30)), 120)
        self.assertEqual(ms.traded_minutes(self._t(12, 0)), 120)
        self.assertEqual(ms.traded_minutes(self._t(13, 30)), 150)
        self.assertEqual(ms.traded_minutes(self._t(15, 5)), 240)

    def test_amount_share(self):
        self.assertEqual(ms.amount_share(0), 1.0)
        self.assertAlmostEqual(ms.amount_share(120), 0.55, places=6)
        self.assertAlmostEqual(ms.amount_share(240), 1.0, places=6)
        # 上午 95 分钟 = 0.55 * 95/120
        self.assertAlmostEqual(ms.amount_share(95), 0.55 * 95 / 120, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
