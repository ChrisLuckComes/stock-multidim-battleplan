# -*- coding: utf-8 -*-
"""固定文案折叠回归（老罗 2026-11-18「优化掉废话/提示/公式」）。

钉死的口径：
  · 每次都一样的说明/公式/纪律 note → 包进 <details>（默认收起，点开看全文）；
  · 数据驱动的 note（现价/主力行为/追价授权/板块状态）→ 绝不折叠，直接可见；
  · 折叠块标签配对，check_html 必须覆盖 details/summary；
  · 无匹配的 note 原样保留（旧 analysis / 美股口径不误伤）。

全部纯函数：直接喂 HTML 片段给 RR.fold_fixed_notes，不联网、不落盘。
"""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import unittest  # noqa: E402

import report_render as RR  # noqa: E402


class TestFixedNoteFold(unittest.TestCase):

    def test_scr_caliber_is_folded(self):
        html = "<p class='note'>口径：三角分布（峰＝当日成交均价）+ 换手衰减 k=1.000 ...</p>"
        out = RR.fold_fixed_notes(html)
        self.assertIn("<details class='fold-note'>", out)
        self.assertIn("SCR 筹码算法口径与校准说明", out)
        self.assertIn("口径：三角分布", out)          # 全文仍在，可展开看到
        self.assertIn("</details>", out)

    def test_activity_formula_is_folded(self):
        html = "<p class='note'>公式：(近5日日均振幅% × 当日换手率%) ÷ 收盘价(元)。&gt;0.8 活跃。</p>"
        out = RR.fold_fixed_notes(html)
        self.assertIn("活跃度算法公式与分档说明", out)
        self.assertIn("&gt;0.8 活跃", out)

    def test_t1_discipline_is_folded(self):
        html = "<p class='note'>⚠ A 股 T+1：当日买入当日不能卖出 ⇒ 当日买入 = 当日不存在止损腿。</p>"
        out = RR.fold_fixed_notes(html)
        self.assertIn("A 股 T+1 与止损腿纪律", out)

    def test_data_driven_note_not_folded(self):
        for txt in (
            "主力行为：锁仓/持平 存量保留峰值 14%｜主力浮盈 +5.8%",
            "主力成本 21.74：现价 23.00（+5.8%）——主力有浮盈",
            "基准收盘 23.00 ｜ 挂单 22.61 ｜ 止损 21.39",
            "本票即板块龙头（20日 +8.6%），无后排寄生问题",
            "追价授权 仅次日 有效（生成日 2026-11-18）",
        ):
            html = "<p class='note'>%s</p>" % txt
            out = RR.fold_fixed_notes(html)
            self.assertNotIn("fold-note", out, "数据 note 被误折：%s" % txt)
            self.assertIn(txt, out)

    def test_non_note_element_untouched(self):
        # 表格里的固定说明（时点清单）不是 <p class='note'>，不得被后置处理改动
        html = "<tr><td>09:20–09:25</td><td><b>不可撤</b>。此段下单必成交。</td></tr>"
        out = RR.fold_fixed_notes(html)
        self.assertEqual(out, html)

    def test_no_match_kept_verbatim(self):
        html = "<p class='note'>一段没有任何固定特征的普通说明</p>"
        self.assertEqual(RR.fold_fixed_notes(html), html)

    def test_folded_block_passes_check_html(self):
        html = "<div class='card'><p class='note'>口径：三角分布测试</p></div>"
        folded = RR.fold_fixed_notes(html)
        ok, _ = RR.check_html(folded)
        self.assertTrue(ok, "折叠后标签必须配对")

    def test_details_and_summary_in_check_list(self):
        # 配对的 details/summary 必须通过
        ok_paired, _ = RR.check_html("<details><summary>x</summary></details>")
        self.assertTrue(ok_paired)
        # 缺闭合的 details 必须被判为不通过，且明细要点名 details
        ok_broken, detail = RR.check_html("<details><summary>x</summary>")
        self.assertFalse(ok_broken)
        self.assertTrue(any("details" in line for line in detail))


if __name__ == "__main__":
    unittest.main(verbosity=2)
