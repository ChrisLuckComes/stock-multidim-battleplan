# -*- coding: utf-8 -*-
"""飙股体检 / 板块领头羊：纯函数离线回归。运行：python tests/screen/test_surge_pick.py

只测**不联网**的部分：板块标签过滤、动量分口径、领头羊排名与分位、股性分档、
业绩预告口径（同报告期多行取归母净利润）。取数层（东财）不在这里测。
"""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for _p in (ROOT, os.path.join(ROOT, "gates")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import surge_pick as SP  # noqa: E402


def _bars(n, start=100.0, step=0.0, last_date="2026-09-24", vol=1e6):
    """造 n 根匀速日线（只用于股性/动量口径的算术校验）。"""
    import datetime
    d = datetime.date.fromisoformat(last_date) - datetime.timedelta(days=n - 1)
    out = []
    px = start
    for _ in range(n):
        out.append({"d": d.isoformat(), "o": px, "h": px * 1.01, "l": px * 0.99,
                    "c": px, "v": vol})
        px += step
        d += datetime.timedelta(days=1)
    return out


class TestPickBoards(unittest.TestCase):
    """板块过滤：地域/指数/风格/资金/事件标签都不能拿来定领头羊。"""

    def test_keeps_industry_and_concept_drops_tags(self):
        rows = [
            {"f12": "BK1326", "f14": "半导体设备", "f3": -1.69},
            {"f12": "BK1201", "f14": "电子", "f3": -2.47},
            {"f12": "BK0153", "f14": "广东板块", "f3": -1.69},      # 地域
            {"f12": "BK0536", "f14": "基金重仓", "f3": -2.53},      # 资金/风格
            {"f12": "BK0701", "f14": "中证500", "f3": -1.74},       # 指数
            {"f12": "BK1661", "f14": "行业龙头", "f3": -1.27},      # 风格标签
            {"f12": "BK1749", "f14": "2026中报预增", "f3": -2.0},   # 事件
            {"f12": "BK0935", "f14": "中芯概念", "f3": -2.26},
        ]
        got = [b["name"] for b in SP.pick_boards(rows, limit=8)]
        self.assertEqual(got, ["半导体设备", "电子", "中芯概念"])

    def test_limit_keeps_eastmoney_order(self):
        rows = [{"f12": "BK%04d" % i, "f14": "板块%d" % i, "f3": 0} for i in range(1, 6)]
        rows = [dict(r, f14="概念%d" % i) for i, r in enumerate(rows)]
        self.assertEqual([b["name"] for b in SP.pick_boards(rows, limit=2)],
                         ["概念0", "概念1"])

    def test_drops_non_bk_rows(self):
        rows = [{"f12": "not-bk", "f14": "半导体"}, {"f12": "BK0001", "f14": "半导体"}]
        self.assertEqual([b["bk"] for b in SP.pick_boards(rows)], ["BK0001"])


class TestLeaderScore(unittest.TestCase):
    """动量分口径必须与 screen_wind_dual 一致：超额 + 距60日高 + max(0, 距MA20)。"""

    def test_formula(self):
        # 20 日 +30%，板块 +10% ⇒ 超额 +20；距 60 日高 -8（负数直接加）；距 MA20 +5
        self.assertAlmostEqual(SP.leader_score(30.0, 10.0, -8.0, 5.0), 17.0, places=6)

    def test_from_h60_is_added_not_abs(self):
        """距 60 日高是负数 ⇒ 必须扣分，不许取负变加分。"""
        near = SP.leader_score(20.0, 0.0, -1.0, 0.0)
        far = SP.leader_score(20.0, 0.0, -30.0, 0.0)
        self.assertGreater(near, far)
        self.assertAlmostEqual(far, -10.0, places=6)

    def test_below_ma20_does_not_subtract(self):
        """距 MA20 为负时取 0（老罗口径：只有站在 MA20 之上才加分）。"""
        self.assertAlmostEqual(SP.leader_score(10.0, 0.0, 0.0, -12.0), 10.0, places=6)

    def test_missing_chg20_returns_none(self):
        self.assertIsNone(SP.leader_score(None, 0.0, -1.0, 1.0))


class TestRankLeaders(unittest.TestCase):
    def test_order_and_pctile(self):
        rows = [{"code": "a", "mom": 3.0}, {"code": "b", "mom": 30.0},
                {"code": "c", "mom": -5.0}, {"code": "d", "mom": None}]
        got = SP.rank_leaders(rows)
        self.assertEqual([r["code"] for r in got], ["b", "a", "c"])
        self.assertEqual([r["rank"] for r in got], [1, 2, 3])
        self.assertAlmostEqual(got[0]["pctile"], 1.0, places=6)
        self.assertAlmostEqual(got[2]["pctile"], 1 / 3, delta=1e-3)   # 分位是按档四舍五入过的


class TestCharacter(unittest.TestCase):
    def test_limit_up_grid_by_board(self):
        self.assertEqual(SP.limit_up_pct("688361"), 20.0)
        self.assertEqual(SP.limit_up_pct("300308"), 20.0)
        self.assertEqual(SP.limit_up_pct("603986"), 10.0)
        self.assertEqual(SP.limit_up_pct("830799"), 30.0)

    def test_counts_limit_and_big_yang(self):
        bars = _bars(40, start=100.0)
        # 三根 +20% 涨停、一根 +8% 大阳（不到 10%）
        for i, chg in ((10, 20.0), (18, 20.0), (26, 20.0), (34, 8.0)):
            bars[i]["c"] = bars[i - 1]["c"] * (1 + chg / 100)
            bars[i]["h"] = bars[i]["c"]
        c = SP.character(bars, "688361", win=250)
        self.assertEqual(c["limit_ups"], 3)
        self.assertEqual(c["up10"], 3)
        self.assertEqual(c["up5"], 4)
        self.assertTrue(c["grade"].startswith("活"))

    def test_flat_chart_is_dull(self):
        c = SP.character(_bars(60, start=100.0), "603986", win=250)
        self.assertEqual(c["limit_ups"], 0)
        self.assertTrue(c["grade"].startswith("钝"))

    def test_beta_of_perfect_tracker(self):
        """参照指数收益要有波动（常数收益 var=0，回归无解）；个股 = 2× 指数收益 ⇒ beta 2。"""
        ref = _bars(90, start=50.0, step=0.0)
        rs = [0.01, -0.008, 0.015, -0.004, 0.006, -0.012, 0.009, -0.002]
        for i in range(1, len(ref)):
            ref[i]["c"] = ref[i - 1]["c"] * (1 + rs[i % len(rs)])
        stk = [dict(b) for b in ref]
        for i in range(1, len(stk)):
            stk[i]["c"] = stk[i - 1]["c"] * (1 + 2 * rs[i % len(rs)])
        b = SP.beta_of(stk, ref, win=60)
        self.assertIsNotNone(b)
        self.assertAlmostEqual(b, 2.0, delta=0.05)

    def test_beta_needs_overlap(self):
        self.assertIsNone(SP.beta_of(_bars(30), _bars(80), win=60))


class TestEarningsTone(unittest.TestCase):
    """同一报告期多行（扣非/营收/归母）必须取「归母净利润」那一行。"""

    def test_prefers_net_profit_row(self):
        rows = [
            {"REPORT_DATE": "2025-12-31 00:00:00", "NOTICE_DATE": "2026-01-31 00:00:00",
             "PREDICT_FINANCE_CODE": "005", "PREDICT_FINANCE": "扣除非经常性损益后的净利润",
             "PREDICT_TYPE": "续亏", "ADD_AMP_LOWER": -16.84, "ADD_AMP_UPPER": 19.42},
            {"REPORT_DATE": "2025-12-31 00:00:00", "NOTICE_DATE": "2026-01-31 00:00:00",
             "PREDICT_FINANCE_CODE": "004", "PREDICT_FINANCE": "归属于上市公司股东的净利润",
             "PREDICT_TYPE": "扭亏", "ADD_AMP_LOWER": 516.48, "ADD_AMP_UPPER": 724.72},
            {"REPORT_DATE": "2025-06-30 00:00:00", "NOTICE_DATE": "2025-07-15 00:00:00",
             "PREDICT_FINANCE_CODE": "004", "PREDICT_FINANCE": "归属于上市公司股东的净利润",
             "PREDICT_TYPE": "预减", "ADD_AMP_LOWER": -50.0, "ADD_AMP_UPPER": -40.0},
        ]
        e = SP.earnings_tone(rows)
        self.assertEqual(e["report_date"], "2025-12-31")     # 取最新报告期
        self.assertEqual(e["types"], ["扭亏"])
        self.assertEqual(e["tone"], "向好")
        self.assertTrue(e["strong"])                          # ≥30% ⇒ 大加分项

    def test_bad_tone_is_not_bonus(self):
        e = SP.earnings_tone([{"REPORT_DATE": "2025-12-31", "PREDICT_TYPE": "首亏",
                               "PREDICT_FINANCE_CODE": "004", "ADD_AMP_LOWER": -300.0,
                               "ADD_AMP_UPPER": -200.0}])
        self.assertEqual(e["tone"], "向差")
        self.assertFalse(e["strong"])

    def test_empty(self):
        self.assertEqual(SP.earnings_tone([])["state"], "none")

    def test_missing_data_is_not_a_veto(self):
        """业绩取不到 ⇒ 只标「未取到」，不得当否决（老罗：业绩非必要）。"""
        rep = {"character": {"grade": "活（易大涨/涨停，弹性好）"},
               "earnings": {"tone": "未取到"}, "boards": []}
        hits, bonus = SP._factor_conclusion(rep)
        self.assertEqual(dict(hits)["股性"], "好")
        self.assertEqual(bonus, "—")


class TestLeadConclusion(unittest.TestCase):
    def _rep(self, pctile, mom):
        return {"character": {"grade": "中"}, "earnings": {"tone": "未取到"},
                "boards": [{"name": "半导体设备", "sampled": 13,
                            "target": {"pctile": pctile, "mom": mom, "rank": 3}}]}

    def test_high_rank_and_positive_mom_is_leader(self):
        hits, _ = SP._factor_conclusion(self._rep(0.85, 5.0))
        self.assertTrue(dict(hits)["板块领头羊"].startswith("是"))

    def test_high_rank_but_negative_mom_is_not_leader(self):
        """板块一起退潮时「最不差」的那个不是领头羊。"""
        hits, _ = SP._factor_conclusion(self._rep(0.85, -8.3))
        self.assertTrue(dict(hits)["板块领头羊"].startswith("边缘"))

    def test_low_rank(self):
        hits, _ = SP._factor_conclusion(self._rep(0.3, 1.0))
        self.assertTrue(dict(hits)["板块领头羊"].startswith("不是"))


class TestSurgeReportGuards(unittest.TestCase):
    def test_rejects_us(self):
        self.assertIn("只做 A 股", SP.surge_report("PANW")["error"])

    def test_offline_only_character(self):
        rep = SP.surge_report("688361", offline=True, bar_n=320)
        if rep.get("error"):
            self.skipTest("本地无 688361 日线")
        self.assertIn("grade", rep["character"])
        self.assertEqual(rep["boards"], [])


class TestRenderOrder(unittest.TestCase):
    """小节顺序与【结论】顺序必须与老罗的飙股定义一致：
    ① 业绩超预期（大加分项、非必要）→ ② 股性 → ③ 板块领头羊。

    回归：旧版打出来的是 ①股性 ③业绩 ②板块 —— 编号与物理顺序不符，
    读的人第一眼就卡住（老罗原话：「我看不懂，打出来没意义，我要的是结论」）。
    """

    def _rep(self):
        return {
            "code": "688361", "close": 370.3, "span": "2025-06-09 ~ 2026-09-24",
            "bars": 320,
            "character": {"win": 250, "limit_ups": 1, "limit_pct": 20,
                          "up10": 9, "up5": 42, "atr_pct": 5.5, "beta": 1.19,
                          "grade": "活（易大涨/涨停，弹性好）"},
            "earnings": {"state": "ok", "report_date": "2025-12-31",
                         "notice_date": "2026-01-31", "types": ["扭亏"],
                         "finance": "归属于上市公司股东的净利润",
                         "add_amp": (516.5, 724.7), "tone": "向好",
                         "strong": True, "note": "代理口径；非超预期"},
            "boards": [], "board_all": [], "board_note": "未取到",
        }

    def test_section_order_matches_definition(self):
        t = SP.render(self._rep())
        i1, i2, i3 = t.index("① 业绩"), t.index("② 股性"), t.index("③ 板块")
        self.assertLess(i1, i2)
        self.assertLess(i2, i3)

    def test_conclusion_puts_earnings_first(self):
        t = SP.render(self._rep())
        line = [x for x in t.splitlines() if x.startswith("【结论】")][0]
        self.assertTrue(line.index("业绩超预期") < line.index("股性")
                        < line.index("板块领头羊"), line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
