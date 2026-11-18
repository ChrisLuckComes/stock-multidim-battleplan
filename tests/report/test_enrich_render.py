# -*- coding: utf-8 -*-
"""事实栏位渲染优先级回归（老罗 2026-11-18「该调接口就调，别留空」）。

钉死口径：
  · 优先级：notes 手写 > enrich 自动拉 > 占位文案（enrich 只是补空缺，不覆盖人工）；
  · enrich 只有 status=ok 才采用，failed/empty/skipped 一律回退并如实标原因；
  · 真正空的 <p class="note"></p> 渲染后清掉，不留白占行；
  · 催化剂层：notes 没写时用 enrich 的公告清单注入 confluence（只排序不否决）。

纯函数测试：直接喂 analysis dict 给 RR._enrich / RR.fold_fixed_notes / RR.render 的
小样本，不联网、不落盘。
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import unittest  # noqa: E402

import report_render as RR  # noqa: E402


class TestEnrichHelpers(unittest.TestCase):

    def _a(self, status="ok", **fields):
        e = {"status": status}
        e.update(fields)
        return {"enrich": {"profile": e}}

    def test_ok_value_used(self):
        self.assertEqual(
            RR._enrich(self._a(fund_body="<p>主营</p>"), "profile", "fund_body"),
            "<p>主营</p>")

    def test_failed_value_ignored(self):
        self.assertIsNone(
            RR._enrich(self._a("failed", fund_body="<p>编造</p>", reason="net down"),
                       "profile", "fund_body"))

    def test_empty_value_ignored(self):
        self.assertIsNone(RR._enrich(self._a(fund_body=""), "profile", "fund_body"))
        self.assertIsNone(RR._enrich(self._a(catalysts=[]), "profile", "catalysts"))

    def test_reason_text(self):
        a = self._a("failed", reason="api down", src="eastmoney")
        self.assertIn("api down", RR._enrich_reason(a, "profile"))
        self.assertIsNone(RR._enrich_reason({}, "profile"))


class TestEmptyNoteCleanup(unittest.TestCase):

    def test_empty_note_removed(self):
        html = "<div><p class='note'></p><p class='note'>  </p><p class='note'>有字</p></div>"
        out = RR.fold_fixed_notes(html)
        self.assertNotIn("<p class='note'></p>", out)
        self.assertIn("<p class='note'>有字</p>", out)

    def test_failed_reason_note_kept(self):
        html = "<p class='note'>财务接口未返回（net down）</p>"
        self.assertEqual(RR.fold_fixed_notes(html), html)


class TestRenderPriority(unittest.TestCase):
    """用真实模板渲染一份最小 analysis，验证四栏位取到 enrich 数据。"""

    def _mini_analysis(self):
        return {
            "meta": {"code": "000034", "name": "神州数码", "market": "CN",
                     "basis_date": "2026-11-18", "basis_close": 23.0,
                     "src": "test", "bars": 10, "first_bar": "2026-11-01",
                     "last_bar": "2026-11-18", "account": 50000, "lot": 100,
                     "generated_at": "2026-11-18", "risk_pct": 0.015},
            "plan": {"buy_zone": {}, "targets": {}, "stop_plan": {}},
            "struct": {"atr14": 0.87, "atr_pct": 3.7, "ma": {}, "levels": {},
                       "percentile": {}, "range_change": {}, "pivots_up": [],
                       "pivots_down": [], "volprice20": {}, "tie_line_verdict": {}},
            "targets": {}, "valuation": {}, "top_verdict": {}, "probe": {},
            "odds": [], "odds_primary": [], "odds_top": None,
            "odds_recommend": None, "rec_up": None, "intraday": {},
            "peers": [], "scr": {}, "activity": {}, "confluence": None,
            "sector_risk": {}, "enrich": {
                "financials": {"status": "ok", "rows": [["报告期", "2026-09-30"],
                                                        ["营业收入", "1367.3 亿"]]},
                "profile": {"status": "ok", "fund_body": "<p>F10 主营事实</p>"},
                "catalysts": {"status": "ok",
                              "catalysts": ["2026-11-07 关于回购股份的公告"]},
                "minesweep": {"status": "ok", "minesweep": ["<b>担保</b>：21 条"]},
                "sentiment": {"status": "ok", "score": 33.0},
            },
        }

    def test_enrich_fills_empty_slots(self):
        html = RR.render(self._mini_analysis(), {})
        self.assertIn("2026-09-30", html)          # 财务
        self.assertIn("1367.3 亿", html)
        self.assertIn("F10 主营事实", html)         # 主营
        self.assertIn("关于回购股份的公告", html)  # 催化
        self.assertIn("<b>担保</b>：21 条", html)  # 扫雷
        # 四个目标栏位不再显示各自的旧占位文案（估值 4.3 是另一个栏位，不在此列）
        self.assertNotIn("财务，接口未返回", html)
        self.assertNotIn("未识别到明确催化剂", html)
        self.assertNotIn("扫雷检索失败", html)

    def test_notes_override_enrich(self):
        html = RR.render(self._mini_analysis(), {
            "fund_body": "<p>人工写的主线叙事</p>",
            "fin_rows": [["人工", "手写财务"]],
        })
        self.assertIn("人工写的主线叙事", html)
        self.assertIn("手写财务", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
