#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""C 口径回归：否决权只留给「强拉≥15% + 无旗形结构」（老罗 2026-11-05 批）。

背景（全量 900 票 / 71,972 时点，20 日收益中位，对照＝无信号组 −1.61%）：
  · 旧口径：所有「已确认」顶部形态都 block ⇒ 6,843 次，Δ 仅 −1.20pp、MAE −8.73%
  · C 口径：只保留「20 日涨幅 ≥15% 且 无旗形结构」⇒ 642 次，Δ −10.77pp、MAE −18.31%
    降级组（6,201 次）Δ −0.80pp —— 信息量不足以拿走 100% 否决权，故降为缩仓 0.5。

guard 的四件事：
  ① 非强拉（20 日 <15%）的已确认顶部 ⇒ 降 alert，size_factor 0.5，且**不禁买**；
  ② 强拉（≥15%）且无旗形 ⇒ **保留 block**（判别力全在这组，不许顺手放开）；
  ③ 强拉但当日存在旗形结构 ⇒ 同样降级；
  ④ `veto_scope="legacy"` 可完整回退旧口径（对照/回测用），筛选层 `screen_exclude` 同步。

fixture：`tests/fixtures/top_veto_scope_c.json`（301101 真实历史时点，80 根窗口，离线）。
跑法：`python tests/gates/test_top_veto_scope_c.py`
"""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import top_judgment as TJ       # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "top_veto_scope_c.json")

_CACHE = {}


def cases():
    if not _CACHE:
        with open(FIXTURE, encoding="utf-8") as f:
            _CACHE.update(json.load(f))
    return _CACHE


def bars_of(tag):
    return [dict(b) for b in cases()[tag]["bars"]]


class TestVetoScopeC(unittest.TestCase):
    """C 口径的四条边界。"""

    def test_fixture_expectations_hold(self):
        """fixture 自带的期望值必须与当前代码一致（防止 fixture 漂移）。"""
        for tag, c in cases().items():
            with self.subTest(tag=tag):
                v = TJ.top_verdict(bars_of(tag))
                e = c["expect"]
                self.assertEqual(v["level"], e["level"], f"{tag} level 漂移")
                self.assertEqual(bool(v["veto_downgraded"]), e["downgraded"],
                                 f"{tag} 降级状态漂移")
                self.assertEqual(v["size_factor"], e["size_factor"],
                                 f"{tag} 仓位系数漂移")
                self.assertEqual(v["pattern_cn"], e["pattern"])

    def test_nonstrong_is_downgraded_not_blocked(self):
        """① 非强拉（301101 2026-10-22，20 日 +3.19%）⇒ 降级缩仓，不再禁买。

        这天的实际后续：次日 10-23 **+19.99% 涨停**（旧口径的典型误杀）。
        """
        v = TJ.top_verdict(bars_of("downgrade_nonstrong"))
        self.assertEqual(v["level"], "alert")
        self.assertTrue(v["veto_downgraded"])
        self.assertEqual(v["size_factor"], 0.5)
        self.assertAlmostEqual(v["gain20"], 3.19, places=2)
        self.assertLess(v["gain20"], TJ.VETO_MIN_GAIN20)
        # 文案不得再说「未确认」（这批是已确认后被降级的）
        self.assertIn("降级", v["level_cn"])
        self.assertIn("可以买", v["advice"])
        self.assertIn("降级", v["reason"])
        # 关键：不再构成否决
        self.assertFalse(v["top"]["block"])

    def test_strong_without_flag_keeps_block(self):
        """② 强拉且无旗形 ⇒ 保留 block（上升楔形 / V顶 各一例）。"""
        for tag in ("block_keep_wedge", "block_keep_vtop"):
            with self.subTest(tag=tag):
                v = TJ.top_verdict(bars_of(tag))
                self.assertEqual(v["level"], "block", f"{tag} 不该被放开")
                self.assertFalse(v["veto_downgraded"])
                self.assertEqual(v["size_factor"], 0.0)
                self.assertGreaterEqual(v["gain20"], TJ.VETO_MIN_GAIN20)
                self.assertFalse(v["flag_seen"])

    def test_strong_with_flag_is_downgraded(self):
        """③ 强拉但当日有旗形结构 ⇒ 也降级（数据：该组 Δ+2.70pp，否决即误杀）。

        真实样本里「强拉 + 有旗形 + 恰好已确认」极其稀少（n=70 / 71,972），
        短窗下难以稳定复现，故注入一个旗形条目锁定分支逻辑。
        """
        real = TJ.detect_top_shapes
        v0 = TJ.top_verdict(bars_of("block_keep_wedge"))
        self.assertEqual(v0["level"], "block")          # 前提：无旗形时是 block

        def with_flag(b, *a, **kw):
            return list(real(b, *a, **kw)) + [{"pattern": "旗形"}]

        with mock.patch.object(TJ, "detect_top_shapes", side_effect=with_flag):
            v1 = TJ.top_verdict(bars_of("block_keep_wedge"))
        self.assertTrue(v1["flag_seen"])
        self.assertTrue(v1["veto_downgraded"])
        self.assertEqual(v1["level"], "alert")
        self.assertEqual(v1["size_factor"], 0.5)

    def test_legacy_scope_restores_old_behaviour(self):
        """④ `veto_scope="legacy"` 完整回退：同一根 bar 从 alert 回 block。"""
        b = bars_of("downgrade_nonstrong")
        v_new = TJ.top_verdict(b)
        v_old = TJ.top_verdict(b, veto_scope=TJ.VETO_SCOPE_LEGACY)
        self.assertEqual(v_new["level"], "alert")
        self.assertEqual(v_old["level"], "block")
        self.assertEqual(v_old["size_factor"], 0.0)
        self.assertFalse(v_old["veto_downgraded"])
        self.assertEqual(v_old["veto_scope"], TJ.VETO_SCOPE_LEGACY)

    def test_strict_mode_not_trimmed_by_c(self):
        """`--strict`（信号当日即避雷）是另立口径，C 不裁它。"""
        v = TJ.top_verdict(bars_of("downgrade_nonstrong"), strict=True)
        self.assertFalse(v["veto_downgraded"])

    def test_screen_exclude_follows_c(self):
        """筛选层同步：降级的不剔除，legacy 下照旧剔除。"""
        b = bars_of("downgrade_nonstrong")
        ex_new, why_new, _ = TJ.screen_exclude(b)
        ex_old, why_old, _ = TJ.screen_exclude(b, veto_scope=TJ.VETO_SCOPE_LEGACY)
        self.assertFalse(ex_new, "降级后不该在筛选层被剔除")
        self.assertTrue(ex_old, "legacy 口径应照旧剔除")
        self.assertIn("已确认", why_old)


class TestGateNoteNotSilent(unittest.TestCase):
    """rule123 出口：降级不许静默放行——note 必须写明「已确认、缩仓 0.5、不禁买」。"""

    def test_downgraded_keeps_recommend_and_writes_note(self):
        import rule123 as R
        fake = {"recommend": True, "verdict": "测试买点", "note": ""}
        out = R.apply_top_signal_gate(dict(fake), bars_of("downgrade_nonstrong"))
        self.assertTrue(out["recommend"], "降级后不得压掉 recommend")
        self.assertTrue(out.get("top_signal_downgraded"))
        self.assertIn("缩仓 0.5", out["note"])
        self.assertIn("不禁买", out["note"])
        self.assertEqual(out["top_signal"]["size_factor"], 0.5)
        self.assertFalse(out["top_signal"]["block"])

    def test_blocked_still_vetoes(self):
        import rule123 as R
        fake = {"recommend": True, "verdict": "测试买点", "note": ""}
        out = R.apply_top_signal_gate(dict(fake), bars_of("block_keep_vtop"))
        self.assertFalse(out["recommend"], "强拉无旗形必须保持否决")
        self.assertTrue(out.get("top_signal_veto"))
        self.assertEqual(out["top_signal"]["size_factor"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
