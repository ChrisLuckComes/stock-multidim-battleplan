# -*- coding: utf-8 -*-
"""超级大盘的顶：月线大阴宣布见顶，不看单根日 K。

中际旭创 300308：高点 2026-06-22 的 1416.88。
7 月月线开 1263.50、高 1315.57、低 793.03、收 902.01。

运行：python tests/top/test_month_top.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import top_signals as T


def bar(d, o, h, l, c):
    return {"d": d, "o": o, "h": h, "l": l, "c": c, "v": 1.0}


def zhongji():
    return [
        bar("2026-06-01", 1161.0, 1200.0, 1093.0, 1180.0),
        bar("2026-06-22", 1367.78, 1416.88, 1343.38, 1382.33),
        bar("2026-06-30", 1223.01, 1295.6, 1218.21, 1270.0),
        bar("2026-07-01", 1263.5, 1315.57, 1208.0, 1223.17),
        bar("2026-07-31", 1100.0, 1150.0, 793.03, 902.01),
        bar("2026-09-24", 852.5, 974.99, 804.02, 895.86),
    ]


class TestMonthTop(unittest.TestCase):
    def test_july_big_yin_announces(self):
        mt = T.month_top(zhongji())
        self.assertIsNotNone(mt)
        self.assertEqual(mt["month"], "2026-07")
        self.assertEqual(mt["peak_high"], 1416.88)
        self.assertGreaterEqual(mt["body_r"], 0.55)
        self.assertLessEqual(mt["pos"], 0.30)

    def test_june_high_alone_is_not_the_announcement(self):
        bars = [b for b in zhongji() if b["d"] < "2026-07-01"]
        self.assertIsNone(T.month_top(bars))

    def test_daily_level_ignores_month_yin(self):
        v = T.top_verdict(zhongji(), name="中际旭创")
        self.assertEqual(v["level"], "ok")
        self.assertEqual(v["month_top"]["month"], "2026-07")
        self.assertEqual(v["month_top"]["d"], "2026-07-31")
        self.assertEqual(v["higher_top"]["horizon"], "无大行情")
        self.assertIn("腰斩", v["higher_top"]["expect"])


class TestSeresMonth(unittest.TestCase):
    def test_october_big_yin_announces_before_november(self):
        bars = [
            bar("2025-09-30", 137.79, 173.55, 135.43, 170.46),
            bar("2025-10-09", 172.39, 172.39, 157.66, 158.89),
            bar("2025-10-31", 155.63, 157.6, 153.55, 154.39),
        ]
        mt = T.month_top(bars)
        self.assertEqual(mt["month"], "2025-10")
        self.assertEqual(mt["pattern"], "big_yin")
        self.assertEqual(mt["peak_high"], 173.55)
        self.assertEqual(mt["close"], 154.39)
        self.assertIn("三折", mt["expect"])


class TestMoutaiMonth(unittest.TestCase):
    def test_peak_month_gravestone_announces(self):
        bars = [
            bar("2021-01-29", 100, 120, 90, 110),
            bar("2021-02-26", 130, 200, 125, 128),
        ]
        mt = T.month_top(bars)
        self.assertEqual(mt["month"], "2021-02")
        self.assertEqual(mt["pattern"], "gravestone")
        self.assertEqual(mt["peak_high"], 200)
        self.assertIn("腰斩", mt["expect"])


class TestMonthSuperYin(unittest.TestCase):
    def test_demingli_close_drop_blacklists(self):
        bars = [
            bar("2026-06-30", 641.4, 980.0, 568.18, 942.9),
            bar("2026-07-31", 945.0, 976.0, 334.0, 385.9),
        ]
        hit = T.month_super_yin(bars)
        self.assertEqual(hit["month"], "2026-07")
        self.assertGreaterEqual(hit["drop"], 0.50)
        v = T.top_verdict(bars)
        self.assertEqual(v["level"], "ok")
        self.assertEqual(v["higher_top"]["horizon"], "拉黑")

    def test_sndk_47_close_drop_is_not_blacklisted(self):
        bars = [
            bar("2026-06-30", 1731.15, 2354.39, 1514.36, 2273.73),
            bar("2026-07-31", 2085.17, 2129.98, 998.19, 1214.83),
        ]
        self.assertEqual(T.classify_shape(bars[1])["pattern"], "big_yin")
        self.assertLess(1 - bars[1]["c"] / bars[0]["c"], 0.50)
        self.assertIsNone(T.month_super_yin(bars))
        v = T.top_verdict(bars)
        self.assertNotEqual((v.get("higher_top") or {}).get("horizon"), "拉黑")


class TestMonthOneStar(unittest.TestCase):
    def test_aaoi_may_alone_blacklists(self):
        bars = [
            bar("2026-04-30", 90.15, 173.41, 81.51, 164.36),
            bar("2026-05-29", 162.68, 233.67, 143.58, 158.41),
        ]
        hit = T.month_one_star(bars)
        self.assertEqual(hit["month"], "2026-05")
        self.assertEqual(hit["peak_high"], 233.67)
        self.assertIn("一根即拉黑", hit["note"])
        v = T.top_verdict(bars)
        self.assertEqual(v["level"], "ok")
        self.assertEqual(v["higher_top"]["horizon"], "拉黑")

    def test_new_high_clears_the_blacklist(self):
        bars = [
            bar("2026-05-29", 162.68, 233.67, 143.58, 158.41),
            bar("2026-06-30", 200.0, 250.0, 190.0, 245.0),
        ]
        self.assertIsNone(T.month_one_star(bars))

    def test_orcl_october_alone_blacklists(self):
        bars = [
            bar("2025-09-30", 222.0, 345.72, 218.79, 281.24),
            bar("2025-10-31", 278.8, 322.54, 256.28, 262.61),
        ]
        hit = T.month_one_star(bars)
        self.assertEqual(hit["month"], "2025-10")
        self.assertEqual(hit["pattern"], "shooting_star")
        self.assertEqual(hit["peak_high"], 345.72)


class TestMonthStarPair(unittest.TestCase):
    def test_aaoi_may_june_blacklists_longs(self):
        bars = [
            bar("2026-05-29", 162.68, 233.67, 143.58, 158.41),
            bar("2026-06-30", 149.25, 209.64, 127.01, 148.16),
        ]
        hit = T.month_star_pair(bars)
        self.assertEqual(hit["months"], ["2026-05", "2026-06"])
        self.assertEqual(hit["peak_high"], 233.67)
        self.assertIn("只做空不做多", hit["note"])
        v = T.top_verdict(bars)
        self.assertEqual(v["level"], "ok")
        self.assertEqual(v["higher_top"]["horizon"], "拉黑")

    def test_pair_needs_two_but_one_star_still_blacklists(self):
        # 必须带一根**前序月**（2026-04）：`month_one_star` 要求 i ≥ 1 —— i = 0 时
        # `peak_high` 就是这根自己的高点，「史上最高点」恒成立，没有前序月份就
        # 谈不上「顶部」（次新股/数据窗只有两个月时会把首月误判成顶）。
        bars = [
            bar("2026-04-30", 150.0, 170.0, 140.0, 165.0),
            bar("2026-05-29", 162.68, 233.67, 143.58, 158.41),
            bar("2026-06-30", 149.25, 160.0, 140.0, 155.0),
        ]
        self.assertIsNone(T.month_star_pair(bars))
        self.assertEqual(T.month_one_star(bars)["month"], "2026-05")

    def test_first_month_alone_is_not_a_top(self):
        """数据窗只有两个月、首月即超长射击之星 ⇒ 不宣布（i ≥ 1）。"""
        bars = [
            bar("2026-05-29", 162.68, 233.67, 143.58, 158.41),
            bar("2026-06-30", 149.25, 160.0, 140.0, 155.0),
        ]
        self.assertIsNone(T.month_one_star(bars))


class TestFrameGaps(unittest.TestCase):
    """缺帧 ⇒ 整个周/月线附加项静默关闭，不得宣布任何顶。

    反例原型：tests/report/test_review_fixes.py 的 `_reversal_bars` ——
    2026-03 之后直接跳到 2026-08，只有 4 根日线的「8 月」被当成完整月，
    判出一根假超长射击之星，把整条 plan_entry 打成「拉黑｜等待」。
    """

    def test_missing_month_disables_month_overlay(self):
        bars = [
            bar("2026-01-28", 10.0, 10.5, 9.8, 10.0),
            bar("2026-02-27", 10.0, 12.0, 9.9, 10.1),
            bar("2026-08-28", 10.0, 11.0, 9.9, 10.0),   # 3~7 月缺失
        ]
        self.assertIsNone(T.month_one_star(bars))
        self.assertIsNone(T.month_top(bars))
        self.assertIsNone(T.month_super_yin(bars))
        self.assertIsNone((T.top_verdict(bars).get("higher_top")))

    def test_contiguous_months_still_work(self):
        bars = [
            bar("2026-01-28", 10.0, 10.5, 9.8, 10.0),
            bar("2026-02-27", 10.0, 12.0, 9.9, 10.1),
            bar("2026-03-31", 10.0, 11.0, 9.9, 10.0),
        ]
        self.assertTrue(T._contiguous(T._frame_groups(bars, "month"), "month"))

    def test_iso_week_rollover_is_contiguous(self):
        """跨年 ISO 周不得被判成缺帧（2025 有 53 周）。"""
        bars = [
            bar("2025-12-26", 10.0, 10.5, 9.8, 10.0),   # 2025-W52 (Fri)
            bar("2026-01-02", 10.0, 10.5, 9.8, 10.0),   # 2026-W01 (Fri)
        ]
        g = T._frame_groups(bars, "week")
        self.assertEqual([x["key"] for x in g], ["2025-W52", "2026-W01"])
        self.assertTrue(T._contiguous(g, "week"))


class TestMonthMergedStar(unittest.TestCase):
    def test_orcl_sep_oct_merge_blacklists_longs(self):
        bars = [
            bar("2025-09-30", 222.0, 345.72, 218.79, 281.24),
            bar("2025-10-31", 278.8, 322.54, 256.28, 262.61),
        ]
        hit = T.month_merged_star(bars)
        self.assertEqual(hit["months"], ["2025-09", "2025-10"])
        self.assertEqual(hit["pattern"], "shooting_star")
        self.assertEqual(hit["peak_high"], 345.72)
        self.assertGreater(hit["body_r"], 0.10)
        v = T.top_verdict(bars)
        self.assertEqual(v["level"], "ok")
        self.assertEqual(v["higher_top"]["horizon"], "拉黑")


class TestWeekTop(unittest.TestCase):
    def test_weekly_big_yin_means_short_only(self):
        bars = [
            bar("2026-06-05", 100, 200, 90, 180),
            bar("2026-06-12", 170, 175, 80, 100),
        ]
        wt = T.week_top(bars)
        self.assertIsNotNone(wt)
        self.assertEqual(wt["d"], "2026-06-12")
        self.assertEqual(wt["peak_high"], 200)
        v = T.top_verdict(bars)
        self.assertEqual(v["level"], "ok")
        self.assertEqual(v["higher_top"]["horizon"], "短线")
        self.assertEqual(v["higher_top"]["frames"], ["周线"])

    def test_unfinished_week_does_not_announce(self):
        bars = [
            bar("2026-06-05", 100, 200, 90, 180),
            bar("2026-06-11", 170, 175, 80, 100),
        ]
        self.assertIsNone(T.week_top(bars))


if __name__ == "__main__":
    unittest.main(verbosity=2)
