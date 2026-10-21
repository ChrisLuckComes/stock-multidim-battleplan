# -*- coding: utf-8 -*-
"""高弹性 × 双通过 交集榜 —— 筛选/渲染逻辑回归。

不扫全市场：直接 mock screener.elasticity_screen.build 的返回值。
"""
import os
import sys
import unittest
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from screener import elastic_dual as ED


def _mk(code, board, score, tier, dual, mode=None, verdict=None,
        surge=10, max_up=10.0, limit=0.10):
    return {
        "code": code, "name": code, "board": board,
        "elastic_score": score, "tier": tier,
        "dual": dual, "dual_mode": mode, "dual_verdict": verdict,
        "surge_days": surge, "max_up": max_up, "limit": limit,
    }


class TestDual(unittest.TestCase):
    def _rows(self):
        return [
            _mk("600001", "主板", 98.0, "高弹性", True, "回踩均线", "STRONG"),   # 交集命中
            _mk("600002", "主板", 50.0, "中弹性", True, "回踩均线", "NEUTRAL"), # 双通过但非高弹性
            _mk("600003", "主板", 99.0, "高弹性", False),                       # 高弹性但未双通过
        ]

    def _stat(self):
        return {"scanned": 3, "pass": 3, "high": 2, "mid": 1,
                "reject_total": 0, "insuff": 0}

    def test_intersection_filters_high_only(self):
        with patch.object(ED.ES, "build", return_value=(self._rows(), self._stat())):
            inter, dual_all, st = ED.build_dual("x_close")
        self.assertEqual(len(inter), 1)              # 仅 600001
        self.assertEqual(inter[0]["code"], "600001")
        self.assertEqual(len(dual_all), 2)           # 双通过全量 2 只
        self.assertEqual(st["inter"], 1)
        self.assertEqual(st["dual"], 2)

    def test_dual_all_sorted_by_score_desc(self):
        rows = [
            _mk("600002", "主板", 50.0, "中弹性", True),
            _mk("600001", "主板", 98.0, "高弹性", True),
        ]
        with patch.object(ED.ES, "build", return_value=(rows, self._stat())):
            inter, dual_all, st = ED.build_dual("x_close")
        self.assertEqual([r["code"] for r in dual_all], ["600001", "600002"])

    def test_render_md_and_html(self):
        with patch.object(ED.ES, "build", return_value=(self._rows(), self._stat())):
            inter, dual_all, st = ED.build_dual("x_close")
        md = ED.render_md(inter, dual_all, st, "x_close", "scr.jsonl")
        html = ED.render_html(inter, dual_all, st, "x_close", "scr.jsonl")
        self.assertIn("交集", md)
        self.assertIn("600001", md)
        self.assertIn("<table", html)
        self.assertIn("600001", html)


if __name__ == "__main__":
    unittest.main()
