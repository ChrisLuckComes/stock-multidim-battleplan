# -*- coding: utf-8 -*-
"""高弹性 × 双通过 交集榜 —— 筛选/排序/旁路/渲染逻辑回归。

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
    }


def _scr_row(code, verdict="STRONG", rs=10.0, ma20=1.0, ret=5.0, leader=True,
             chip="集中锁仓中", mode="line_pullback", rvol=1.5, in_zone=True,
             allow=True, scr90=18.0, rr1=2.0, buy_lo=9.0, buy_hi=10.0, spot=10.0):
    return {
        "code": code, "verdict": verdict, "rs_slope": rs, "ma20_slope": ma20,
        "ret20": ret, "leader": leader, "mode": mode, "rvol": rvol,
        "in_zone": in_zone, "spot": spot, "buy_lo": buy_lo, "buy_hi": buy_hi,
        "stop": 9.0, "rr1": rr1, "recommend": True,
        "scr": {"state": chip, "allow_new": allow, "scr90": scr90},
    }


class TestDual(unittest.TestCase):
    def _rows(self):
        return [
            # STRONG 但筹码仅拉升 + 弹性高
            _mk("600001", "主板", 99.0, "高弹性", True, verdict="STRONG",
                rs=20.0, ma20=3.0, ret=10.0, leader=True, chip="拉升脱离成本区中"),
            # NEUTRAL 但筹码锁仓 + 弹性高（意见2：不应压过上面的 STRONG）
            _mk("600002", "主板", 90.0, "高弹性", True, verdict="NEUTRAL",
                rs=8.0, ma20=0.5, ret=2.0, leader=False, chip="集中锁仓中"),
            # 高弹性但非双通过
            _mk("600003", "主板", 98.0, "高弹性", False),
            # 双通过但中弹性（不在交集）
            _mk("600004", "主板", 40.0, "中弹性", True),
        ]

    def _scr(self):
        return {
            "600001": _scr_row("600001", verdict="STRONG", rs=20.0, ma20=3.0, ret=10.0,
                               leader=True, chip="拉升脱离成本区中"),
            "600002": _scr_row("600002", verdict="NEUTRAL", rs=8.0, ma20=0.5, ret=2.0,
                               leader=False, chip="集中锁仓中"),
            "600004": _scr_row("600004", verdict="STRONG"),
        }

    def _stat(self):
        return {"scanned": 4, "pass": 3, "high": 2, "mid": 1,
                "reject_total": 0, "insuff": 0}

    def _run(self):
        with patch.object(ED, "_load_scr_full", return_value=self._scr()), \
             patch.object(ED.ES, "build", return_value=(self._rows(), self._stat())):
            return ED.build_dual("x_close", scr_path="dummy.jsonl")

    def test_intersection_only(self):
        best, by_elastic, bypass, st = self._run()
        codes = {r["code"] for r in best}
        self.assertEqual(codes, {"600001", "600002"})   # 600003 非双通过、600004 中弹性
        self.assertEqual(st["inter"], 2)
        self.assertEqual(st["dual"], 3)

    def test_strength_before_chip(self):
        """意见2：STRONG 拉升 必须排在 NEUTRAL 锁仓 之前。"""
        best, _, _, _ = self._run()
        self.assertEqual(best[0]["code"], "600001")      # STRONG
        self.assertEqual(best[1]["code"], "600002")      # NEUTRAL
        self.assertEqual(best[0]["verdict_tier"], 2)
        self.assertEqual(best[1]["verdict_tier"], 1)

    def test_bypass_filters(self):
        """意见1：旁路只收 STRONG+突破类+发散+放量+区内+scr90<32；缩量/区外/太发散挡外。"""
        scr = {
            # 命中：STRONG + platform_break + 发散 + 放量 + 区内 + scr90<32
            "A": _scr_row("A", verdict="STRONG", mode="platform_break", chip="筹码发散",
                          allow=False, rvol=1.8, in_zone=True, scr90=28.0, rs=40.0, ret=20.0),
            # 挡：缩量假突破 rvol<1
            "B": _scr_row("B", verdict="STRONG", mode="platform_break", chip="筹码发散",
                          allow=False, rvol=0.6, in_zone=True, scr90=24.0, rs=40.0, ret=20.0),
            # 挡：区外追高
            "C": _scr_row("C", verdict="STRONG", mode="flag_tl_break", chip="筹码发散",
                          allow=False, rvol=2.0, in_zone=False, scr90=24.0, rs=40.0, ret=20.0),
            # 挡：太发散 scr90>=32
            "D": _scr_row("D", verdict="STRONG", mode="w_bottom_break", chip="筹码发散",
                          allow=False, rvol=2.0, in_zone=True, scr90=35.0, rs=40.0, ret=20.0),
            # 挡：NEUTRAL
            "E": _scr_row("E", verdict="NEUTRAL", mode="platform_break", chip="筹码发散",
                          allow=False, rvol=2.0, in_zone=True, scr90=24.0, rs=40.0, ret=20.0),
            # 挡：非突破类
            "F": _scr_row("F", verdict="STRONG", mode="line_pullback", chip="筹码发散",
                          allow=False, rvol=2.0, in_zone=True, scr90=24.0, rs=40.0, ret=20.0),
            # 挡：已双通过（非发散否）
            "G": _scr_row("G", verdict="STRONG", mode="platform_break", chip="筹码发散",
                          allow=True, rvol=2.0, in_zone=True, scr90=24.0, rs=40.0, ret=20.0),
        }
        rows = []   # 交集无关，只测旁路
        stat = {"scanned": 0, "pass": 0, "high": 0, "mid": 0, "reject_total": 0, "insuff": 0}
        with patch.object(ED, "_load_scr_full", return_value=scr), \
             patch.object(ED.ES, "build", return_value=(rows, stat)):
            _, _, bypass, _ = ED.build_dual("x_close", scr_path="dummy.jsonl")
        codes = [d["code"] for d in bypass]
        self.assertEqual(codes, ["A"])

    def test_render_md_and_html(self):
        best, by_elastic, bypass, st = self._run()
        md = ED.render_md(best, by_elastic, bypass, st, "x_close", "scr.jsonl")
        html = ED.render_html(best, by_elastic, bypass, st, "x_close", "scr.jsonl")
        self.assertIn("最该上的", md)
        self.assertIn("600001", md)
        self.assertIn("旁路", md)
        self.assertIn("<table", html)
        self.assertIn("600001", html)


if __name__ == "__main__":
    unittest.main()
