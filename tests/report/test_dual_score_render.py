# -*- coding: utf-8 -*-
"""形态质量「按需显示」+ 双轨打分机械粗评 的回归（老罗 2026-11-19 两点）。

钉死口径：
  1. 形态质量三项排序器**只在 ≥1 项命中时显示**；0 项命中整块不渲染，
     且表里只列命中项（不是「三项逐个体检」的清单）。
  2. 双轨打分：notes 手写 > dual_score 机械粗评 > 占位；分母随有效项收缩；
     「当下可执行性」是客观事实（现价 vs 买区），独立于谁打分，买区外标红。

纯函数：直接喂 analysis dict，不联网、不落盘。
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import unittest  # noqa: E402

import report_render as RR  # noqa: E402
from gates import dual_score as DS  # noqa: E402


def _mini(**over):
    """最小可用 analysis（结构照 tests/report/test_enrich_render.py）。"""
    a = {
        "meta": {"code": "000034", "name": "神州数码", "market": "CN",
                 "basis_date": "2026-11-18", "basis_close": 23.0,
                 "src": "test", "bars": 10, "first_bar": "2026-11-01",
                 "last_bar": "2026-11-18", "account": 50000, "lot": 100,
                 "generated_at": "2026-11-18", "risk_pct": 0.015},
        "plan": {"buy_zone": {}, "targets": {}, "stop_plan": {}, "last": 23.0,
                 "atr14": 0.868},
        "struct": {"atr14": 0.868, "atr_pct": 3.7,
                   "ma": [{"name": "MA5", "value": 21.83, "dir": 1},
                          {"name": "EMA10", "value": 21.63, "dir": 1},
                          {"name": "MA20", "value": 21.08, "dir": 1}],
                   "levels": {}, "percentile": {}, "range_change": {},
                   "pivots_up": [], "pivots_down": [], "volprice20": {},
                   "tie_line_verdict": {}},
        "targets": {"ath": 46.8, "t1": 25.68}, "valuation": {},
        "top_verdict": {}, "probe": {"zone_hi": 22.61, "zone_lo": 21.74},
        "odds": [], "odds_primary": [], "odds_top": None,
        # 推荐档字段照真实 analysis 的键集补齐（少一个键渲染层就 KeyError）
        "odds_recommend": {"entry_name": "买区上沿", "entry": 22.61, "entry_kind": "below",
                           "stop": 21.39, "stop_name": "引擎结构止损",
                           "risk": 1.22, "risk_atr": 1.41, "risk_pct": 3.85,
                           "risk_amt": 366.0, "r1": 2.52, "r2": 21.96,
                           "r1_basis": "引擎targets.t1（已过RR闸）",
                           "need_pct": -1.7, "t1_used": 25.68, "t2_used": 49.4,
                           "qty": 300, "amount": 6783.0, "cash": 30000.0,
                           "pct_cash": 22.6, "pct_account": 13.6,
                           "open_sky": False, "prehang": True,
                           "warn": [], "weak": False, "attain_rate": 0.476,
                           "touch_rate": 47.6, "touch_px": 22.61,
                           "exec_stop": 21.08, "score": 1.68},
        "rec_up": None, "intraday": {}, "peers": [], "scr": {"vol_ratio": 1.76},
        "activity": {}, "confluence": None, "sector_risk": {},
        "enrich": {"financials": {"status": "ok",
                                  "metrics": {"report_date": "2026-09-30",
                                              "rev_yoy": 33.6, "np_yoy": 16.4,
                                              "roe": 6.85}},
                   "profile": {"status": "ok", "fund_body": "<p>F10 主营</p>"},
                   "catalysts": {"status": "ok", "catalysts": ["回购公告"]},
                   "minesweep": {"status": "ok", "minesweep": ["<b>担保</b>：21 条"]},
                   "sentiment": {"status": "ok", "score": 33.0}},
    }
    a.update(over)
    return a


def _rb(**hits):
    """构造 ranking_bonus。hits 形如 turn_up=True, drawdown_shrink=False。"""
    names = {"turn_up": "回踩企稳·转头向上",
             "drawdown_shrink": "回撤递减·VCP收缩",
             "platform_tightness": "平台紧凑度"}
    items = {}
    for k, v in hits.items():
        items[k] = {"rule": k, "hit": bool(v), "note": "%s说明" % names[k]}
    return {"rule": "ranking_bonus", "advisory_only": True, "computable": True,
            "items": items, "bonus_hits": sum(1 for v in hits.values() if v),
            "bonus_max": 3,
            "line": "形态质量排序器（只加分不否决）：合计 %d/3。"
                    % sum(1 for v in hits.values() if v)}


def _render_min(a, notes=None):
    """算好 dual_score 后渲染。

    渲染前把 struct.ma 清空：结构卡要 dir_txt/dist_pct 等一批字段，本文件的 mini 样本
    不铺那么全，而结构卡不是本测试对象 —— 清空即跳过该卡，不影响第 7 节。
    """
    a["dual_score"] = DS.from_analysis(a)
    a["struct"]["ma"] = {}
    return RR.render(a, notes or {})


class TestRankingBonusConditional(unittest.TestCase):
    """① 只有符合形态才套上去。"""

    def test_zero_hits_block_hidden(self):
        a = _mini(ranking_bonus=_rb(turn_up=False, drawdown_shrink=False,
                                    platform_tightness=False))
        html = RR.build_ranking_bonus(a, {})
        self.assertEqual(html, "")
        self.assertNotIn("形态质量", html)

    def test_missing_key_block_hidden(self):
        self.assertEqual(RR.build_ranking_bonus(_mini(), {}), "")

    def test_partial_hits_only_list_hits(self):
        a = _mini(ranking_bonus=_rb(turn_up=True, drawdown_shrink=False,
                                    platform_tightness=True))
        html = RR.build_ranking_bonus(a, {})
        self.assertIn("形态质量", html)
        self.assertIn("回踩企稳·转头向上", html)
        self.assertIn("平台紧凑度", html)
        # 未命中的项不列（这张卡说的是「符合了什么」，不是逐项体检表）
        self.assertNotIn("回撤递减·VCP收缩", html)
        self.assertIn("2/3 项加分", html)

    def test_all_hits_listed(self):
        a = _mini(ranking_bonus=_rb(turn_up=True, drawdown_shrink=True,
                                    platform_tightness=True))
        html = RR.build_ranking_bonus(a, {})
        for s in ("回踩企稳·转头向上", "回撤递减·VCP收缩", "平台紧凑度"):
            self.assertIn(s, html)
        self.assertIn("3/3 项加分", html)


class TestDualScoreRender(unittest.TestCase):

    def test_dual_score_fills_section(self):
        a = _mini()
        a["valuation"] = {"pe_dynamic": 22.49, "spot": 23.0}
        html = _render_min(a)
        self.assertIn("双轨打分", html)
        self.assertNotIn("未评分 —— 需补财务/估值数据", html)
        self.assertNotIn("未评分 —— 需补量价/赔率数据", html)
        self.assertIn("营收同比", html)
        self.assertIn("+33.6%", html)
        self.assertIn("ROE（年化）", html)

    def test_notes_override_dual_score(self):
        a = _mini()
        a["valuation"] = {"pe_dynamic": 22.49, "spot": 23.0}
        html = _render_min(a, {"score_invest": 8.5,
                             "score_invest_li": ["人工判断：赛道稀缺"],
                                "score_trade": 7.0,
                                "score_trade_li": ["人工判断：等回踩"]})
        self.assertIn("8.5", html)
        self.assertIn("人工判断：赛道稀缺", html)
        self.assertNotIn("营收同比", html)      # notes 手写时不再显示机械项
        self.assertNotIn("机械粗评", html)

    def test_missing_items_not_in_denominator(self):
        a = _mini()
        a["enrich"]["financials"] = {"status": "ok", "metrics": {}}
        a["valuation"] = {}
        a["targets"] = {}
        a["dual_score"] = DS.from_analysis(a)
        inv = a["dual_score"]["invest"]
        # 投资维度 5 项全无数据 ⇒ effective 0；但短线还有数 ⇒ 卡片仍出
        self.assertEqual(inv["effective"], 0)
        html = _render_min(a)
        self.assertIn("无数据，未计入分母", html)

    def test_exec_now_red_when_outside_zone(self):
        a = _mini()
        a["valuation"] = {"spot": 23.0}
        html = _render_min(a)
        self.assertIn("当下可执行性", html)
        self.assertIn("此处不是买点", html)
        self.assertIn("color:#b3261e", html)

    def test_exec_now_ok_inside_zone(self):
        a = _mini()
        a["plan"]["last"] = 22.0
        a["probe"] = {"zone_hi": 22.61, "zone_lo": 21.74}
        html = _render_min(a)
        self.assertIn("在买区内且引擎认可", html)
        self.assertNotIn("此处不是买点", html)

    def test_exec_now_zone_inside_but_reco_false(self):
        """⚑ 2026-11-19（伟测 688372）：现价在追价带内、但引擎 recommend=False
        ⇒ 不得说「可按下单档执行」，买点可达性也不得给满分。"""
        a = _mini()
        a["plan"]["last"] = 22.0
        a["plan"]["recommend"] = False
        a["plan"]["verdict"] = "突破已延伸·等回踩"
        a["plan"]["note"] = "收盘 22.00 高出买位 19.5 已 2.1×ATR（>2），不追"
        a["probe"] = {"zone_hi": 22.61, "zone_lo": 18.0}
        html = _render_min(a)
        self.assertIn("现价不可买", html)
        self.assertNotIn("可按下单档执行", html)
        trd = DS.from_analysis(a)["trade"]
        buy = [it for it in trd["items"] if it["item"] == "买点可达性"][0]
        self.assertLessEqual(buy["pts"], 0.2)


class TestDualScoreCalc(unittest.TestCase):

    def test_roe_annualized_for_q3(self):
        roe, note = DS._annualize_roe(6.85, "2026-09-30")
        self.assertAlmostEqual(roe, 6.85 * 12 / 9, places=4)
        self.assertIn("年化", note)

    def test_roe_full_year_not_annualized(self):
        roe, note = DS._annualize_roe(9.0, "2025-12-31")
        self.assertEqual(roe, 9.0)
        self.assertIn("无需年化", note)

    def test_growth_tier_boundaries(self):
        self.assertEqual(DS._tier(30.0, DS.GROWTH_TIERS), 2.0)
        self.assertEqual(DS._tier(29.99, DS.GROWTH_TIERS), 1.2)
        self.assertEqual(DS._tier(10.0, DS.GROWTH_TIERS), 1.2)
        self.assertEqual(DS._tier(0.0, DS.GROWTH_TIERS), 0.6)
        self.assertEqual(DS._tier(-0.1, DS.GROWTH_TIERS), 0.0)

    def test_missing_metric_gives_none_not_zero(self):
        # 缺 PE 数据不能算 0 分（否则「没数据」被当成「很差」）
        self.assertIsNone(DS._pe_pts(None))
        self.assertIsNone(DS._tier(None, DS.GROWTH_TIERS))

    def test_advisory_only_and_no_mutation(self):
        from gates.dual_score import from_analysis
        a = _mini()
        a["valuation"] = {"pe_dynamic": 22.49, "spot": 23.0}
        before = (a["plan"]["last"], a["odds_recommend"]["entry"])
        ds = from_analysis(a)
        self.assertTrue(ds["advisory_only"])
        self.assertFalse(ds["hard_gate"])
        # 不许改买卖字段
        self.assertEqual((a["plan"]["last"], a["odds_recommend"]["entry"]), before)

    def test_empty_analysis_returns_none(self):
        self.assertIsNone(DS.from_analysis({}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
