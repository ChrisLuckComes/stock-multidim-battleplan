# -*- coding: utf-8 -*-
"""高弹性 × 双通过 交集榜 —— 筛选/排序/渲染逻辑回归。

不扫全市场：mock screener.elasticity_screen.build 与 _load_scr_full。
"""
import os
import sys
import unittest
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from screener import elastic_dual as ED


def _mk(code, board, score, tier, dual, verdict="STRONG", mode="line_pullback",
        rs=10.0, ma20=1.0, ret=5.0, leader=True, chip="集中锁仓中",
        surge=10, max_up=10.0, limit=0.10):
    return {
        "code": code, "name": code, "board": board,
        "elastic_score": score, "tier": tier, "dual": dual,
        "dual_mode": mode, "dual_verdict": verdict,
        "surge_days": surge, "max_up": max_up, "limit": limit,
        # 强度/筹码原始字段由 _load_scr_full 并回
        "_rs": rs, "_ma20": ma20, "_ret": ret, "_leader": leader, "_chip": chip,
        "_mode": mode, "_verdict": verdict,
    }


def _scr_row(code, verdict="STRONG", rs=10.0, ma20=1.0, ret=5.0, leader=True,
             chip="集中锁仓中", mode="line_pullback"):
    return {
        "code": code, "verdict": verdict, "rs_slope": rs, "ma20_slope": ma20,
        "ret20": ret, "leader": leader, "mode": mode,
        "spot": 10.0, "buy_lo": 9.0, "buy_hi": 10.0, "in_zone": True,
        "stop": 9.0, "rr1": 2.0, "recommend": True,
        "scr": {"state": chip},
    }


class TestDual(unittest.TestCase):
    def _rows(self):
        return [
            # 弹性最高但筹码仅"拉升"、强度 NEUTRAL
            _mk("600001", "主板", 99.0, "高弹性", True, verdict="NEUTRAL",
                rs=5.0, ma20=0.5, ret=2.0, leader=False, chip="拉升脱离成本区中"),
            # 弹性中等但筹码"锁仓"、强度 STRONG、领头羊 → 应排最前
            _mk("600002", "主板", 80.0, "高弹性", True, verdict="STRONG",
                rs=15.0, ma20=2.0, ret=8.0, leader=True, chip="集中锁仓中"),
            # 高弹性但非双通过
            _mk("600003", "主板", 98.0, "高弹性", False),
            # 双通过但中弹性（不在交集）
            _mk("600004", "主板", 40.0, "中弹性", True),
        ]

    def _scr(self):
        return {
            "600001": _scr_row("600001", verdict="NEUTRAL", rs=5.0, ma20=0.5, ret=2.0,
                               leader=False, chip="拉升脱离成本区中"),
            "600002": _scr_row("600002", verdict="STRONG", rs=15.0, ma20=2.0, ret=8.0,
                               leader=True, chip="集中锁仓中"),
            "600004": _scr_row("600004", verdict="STRONG"),
        }

    def _stat(self):
        return {"scanned": 4, "pass": 3, "high": 2, "mid": 1,
                "reject_total": 0, "insuff": 0}

    def test_intersection_and_best_order(self):
        with patch.object(ED, "_load_scr_full", return_value=self._scr()), \
             patch.object(ED.ES, "build", return_value=(self._rows(), self._stat())):
            best, by_elastic, st = ED.build_dual("x_close", scr_path="dummy.jsonl")
        # 交集只含双通过∩高弹性：600001, 600002（600003 非双通过、600004 中弹性）
        self.assertEqual([r["code"] for r in best], ["600002", "600001"])
        self.assertEqual(len(by_elastic), 2)
        self.assertEqual(st["inter"], 2)
        self.assertEqual(st["dual"], 3)

    def test_best_order_chip_first(self):
        """筹码锁仓+STRONG 必须压过 拉升+NEUTRAL，即使后者弹性分更高。"""
        with patch.object(ED, "_load_scr_full", return_value=self._scr()), \
             patch.object(ED.ES, "build", return_value=(self._rows(), self._stat())):
            best, _, _ = ED.build_dual("x_close", scr_path="dummy.jsonl")
        # 600002(锁仓/STRONG) 第一，600001(拉升/NEUTRAL) 第二
        self.assertEqual(best[0]["code"], "600002")
        self.assertEqual(best[1]["code"], "600001")
        self.assertEqual(best[0]["chip_tier"], 3)
        self.assertEqual(best[1]["chip_tier"], 2)

    def test_render_md_and_html(self):
        with patch.object(ED, "_load_scr_full", return_value=self._scr()), \
             patch.object(ED.ES, "build", return_value=(self._rows(), self._stat())):
            best, by_elastic, st = ED.build_dual("x_close", scr_path="dummy.jsonl")
        md = ED.render_md(best, by_elastic, st, "x_close", "scr.jsonl")
        html = ED.render_html(best, by_elastic, st, "x_close", "scr.jsonl")
        self.assertIn("最该上的", md)
        self.assertIn("600002", md)
        self.assertIn("<table", html)
        self.assertIn("600002", html)


if __name__ == "__main__":
    unittest.main()
