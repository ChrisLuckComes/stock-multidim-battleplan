"""买位口径 / 突破延伸不追 / 同行分组 的回归测试（2026-11-19，伟测 688372 实测）。

背景（老罗原话）：
    「现价 92.08，你说买位 77.97，这胡说八道。它是华为的测试，同行应该还有利扬芯片。」

三处必须锁死的行为：
1. `buy_zone.level` 是**突破位/结构失效线**，不是买位 —— 距现价 2×ATR 时绝不能
   让报告说「等回踩到买位 77.97」（等不到的价 = 0）。
2. 引擎判「突破已延伸·等回踩」（recommend=False）时，**不许**再推荐「现价市价追」——
   旧实现靠 need_pct=0 的 attain=1.0 让现价档拿最高分，风险 15.3%、超预算 3.8 倍。
3. 同行表必须区分「独立测试（同商业模式）」与「封测一体（景气参考）」，
   并把同向只数按组分解 —— 混算 1/5 会把「测试环节相对强势」抹成一句「板块 off」。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import report_render as RR  # noqa: E402


def _ext_analysis():
    """伟测 688372 的骨架：突破位 77.97、现价 92.08、硬止损 88.42。"""
    return {
        "meta": {"code": "688372", "name": "伟测科技", "market": "CN",
                 "basis_date": "2026-11-18", "basis_close": 92.08,
                 "account": 50000, "risk_pct": 0.015, "lot": 200},
        "plan": {
            "sym": "688372", "last": 92.08, "mode": "wait",
            "verdict": "突破已延伸·等回踩", "recommend": False,
            "note": ("收盘 92.08 已高出突破位 77.97 共 2.1×ATR（突破段走完），不追；"
                     "回踩参考 MA5 84.17 / MA10 78.91（活均线）；"
                     "结构失效线 77.97（收盘破即出，不是买位）"),
            "buy_zone": {
                "level": 77.97, "primary_lo": 89.10, "primary_hi": 95.91,
                "hard_stop": 88.42, "hard_anchor": "阳线下沿",
                "struct_stop": 77.97, "struct_anchor": "活平台沿@77.97(收盘破)",
                "dist_atr": 2.07, "in_zone": True, "extended": True,
            },
            "ma_ride": {"line": 84.17},
        },
        "struct": {"atr14": 6.81, "atr_pct": 7.39,
                   "ma": [{"name": "MA5", "value": 84.17, "dir_txt": "上行"}],
                   "percentile": {"60": {"pct": 39.1}, "120": {"pct": 20.3},
                                  "250": {"pct": 20.3}},
                   "range_change": {"3d": 9.99, "5d": 19.01, "10d": 28.57,
                                    "20d": 14.53, "60d": -31.80}},
        "odds": [],
        "odds_top": {"entry_name": "基准日收盘（现价）", "entry": 92.08,
                     "stop": 89.10, "risk": 2.98, "risk_atr": 0.44,
                     "r1": 10.75, "r1_basis": "t1@124.12"},
        "odds_recommend": None,
        "rec_up": None,
        "targets": {},
        "peers": [],
    }


class TestPickRecRefusesChase(unittest.TestCase):
    """引擎判不追时，pick_rec 不许把 odds_top 的现价档捞回来。"""

    def test_no_recommend_returns_none(self):
        a = _ext_analysis()
        self.assertIsNone(RR.pick_rec(a))

    def test_recommend_true_keeps_odds_top(self):
        # recommend=True（000034 那种「延伸但仍可挂回踩单」）⇒ 照旧走 odds_top
        a = _ext_analysis()
        a["plan"]["verdict"] = "突破已延伸·只挂回踩单 22.61"
        a["plan"]["recommend"] = True
        r = RR.pick_rec(a)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r["entry"], 92.08, places=2)


class TestExecRefusesWithoutRecommend(unittest.TestCase):
    """无推荐档时，下单执行表必须明说「不做」，不许拼空壳价。"""

    def test_exec_says_no_trade(self):
        html = RR.build_exec(_ext_analysis(), {})
        self.assertIn("不做", html)
        self.assertIn("没有可执行的挂单价", html)
        self.assertIn("不是买位", html)
        # 旧缺陷：空壳「买入价 —（）」「预挂可行性 ✗ 买点在现价上方」
        self.assertNotIn("买点在现价上方", html)
        self.assertNotIn("买入价", html)


class TestOpenPlaybookRefuses(unittest.TestCase):
    """突破延伸且无推荐档 ⇒ 不出作战表（返回空），不许回落 odds_primary。"""

    def test_no_battle_table(self):
        a = _ext_analysis()
        block = RR.build_open_playbook(a, {})
        self.assertIn("本票不出作战表", block)
        self.assertNotIn("★ 刚好符合", block)


class TestPeerGrouping(unittest.TestCase):
    """同行按业务类型分组 + 同向只数分组分解。"""

    def _analysis(self):
        a = _ext_analysis()
        a["peers"] = [
            {"code": "002185", "name": "华天科技", "group": "封测一体", "d20": -0.21},
            {"code": "688135", "name": "利扬芯片", "group": "独立测试", "d20": 3.09},
            {"code": "600584", "name": "长电科技", "group": "封测一体", "d20": -7.46},
            {"code": "002156", "name": "通富微电", "group": "封测一体", "d20": -8.45},
            {"code": "688362", "name": "甬矽电子", "group": "封测一体", "d20": -14.95},
        ]
        return a

    def test_groups_and_split(self):
        html = RR.build_peers(self._analysis(), {})
        self.assertIn("独立测试", html)
        self.assertIn("封测一体", html)
        self.assertIn("同向分解", html)
        # 主标的 +14.53% 上涨 ⇒ 独立测试 1/1 同向、封测一体 0/4
        self.assertIn("独立测试（同商业模式）1/1 同向", html)
        self.assertIn("封测一体（景气参考）0/4 同向", html)

    def test_independent_test_first(self):
        # 排序：独立测试必须排在封测一体之前（读者先看真同行）
        html = RR.build_peers(self._analysis(), {})
        self.assertLess(html.find("利扬芯片"), html.find("华天科技"))


class TestModeCardWording(unittest.TestCase):
    """模式卡不许把 level 标成「回踩锚」。"""

    def test_label_is_structural_not_pullback(self):
        rows = RR.build_mode_rows(_ext_analysis())
        self.assertIn("结构失效锚", rows)
        self.assertNotIn("回踩锚", rows)


if __name__ == "__main__":
    unittest.main()
