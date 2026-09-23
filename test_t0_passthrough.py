#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""2026-09-22 两处引擎缺陷的回归测试。

## 背景（用户原话）
「为什么我觉得像 T0 的变种，昨天突破了均线，今天继续涨」+「1.2 都修」

### 缺陷 1 —— evaluate() 把 T0 块丢了
`plan_entry()` 一直会算 `ma_reclaim`（T0：均线收复 + 过昨高）并挂在结果上，
但 `rule123.evaluate()` 是**重建** out（逐字段挑），漏了 `ma_reclaim` / `tier_t0`。
后果：`watch_cn` / `pool_us`（直接调 plan_entry）看得到 T0，
而 `battle_analyze`（调 evaluate）写出的报告**看不到** —— 用户看到的是
「09-21 收复全部均线、09-22 过昨高」的票，报告里却只有一条 T2 回踩单。

### 缺陷 2 —— 两个股数
`battle_analyze.pick_recommend` 选的止损是**矩阵原始结构位**（如 688428 的 30.09
= 基准日最低），而 probe 预案单用的是计划硬止损（同锚 − gap = 29.91）。
每股风险 0.81 vs 0.99 ⇒ 925 股 vs 757 股，同屏出现两个数字。
另：`cap_amt=cash` 会把「单笔绝对额硬顶 5 万」顶掉（现金 > 硬顶时）。

跑法：`python -m unittest test_t0_passthrough`（或 `python test_t0_passthrough.py`）。
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import battle_analyze as BA          # noqa: E402
import probe_intraday as P           # noqa: E402
import rule123 as R                  # noqa: E402


def t0_series():
    """合成一段「左侧有墙 + 平台 + 末根收复全部均线」的日线（35 根）。

    形态：10.0 一带来回震荡（每 3 根一个摆动 → 保证有枢轴高低点）→
    第 16 根一根长上影冲到 11.60（左墙）→ 回落到 10.5 一带继续震荡
    → 末根 10.40 开、10.92 收（站上 MA5/10/20），墙的上沿 11.60 在 25 根窗口内。
    """
    bars = []
    d = datetime.date(2026, 6, 1)
    for i in range(34):
        if i == 15:                                  # 左墙：长上影插针
            o, h, l, c = 10.45, 11.60, 10.40, 11.20
        else:
            base = 10.0 + 0.03 * i if i < 15 else 10.45 + 0.005 * (i - 15)
            amp = 0.14 if (i // 3) % 2 == 0 else -0.10
            c = base + amp
            o = c - amp * 0.5
            h = max(o, c) + 0.07
            l = min(o, c) - 0.07
        bars.append({"d": d.isoformat(), "o": round(o, 2), "h": round(h, 2),
                     "l": round(l, 2), "c": round(c, 2), "v": 1.0e6})
        d += datetime.timedelta(days=1)
    # 末根：收复全部均线 + 贴着左墙
    bars.append({"d": d.isoformat(), "o": 10.40, "h": 11.00, "l": 10.35,
                 "c": 10.92, "v": 1.6e6})
    return bars


def _write_snapshot(bars):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"bars": bars, "name": "测试票", "spot": bars[-1]["c"]}, f)
    return path


class TestEvaluateCarriesT0(unittest.TestCase):
    """缺陷 1：evaluate() 必须把 T0 块透传给下游（报告/分析 json）。"""

    @classmethod
    def setUpClass(cls):
        cls.bars = t0_series()
        cls.path = _write_snapshot(cls.bars)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.path)

    def test_plan_entry_has_t0_block(self):
        """前提：plan_entry 一直算得出 T0（这条守着「前提没变」）。"""
        ev, bars2, _ = R.build_ev(self.bars, drop_live=False, ticker="688999")
        plan = R.plan_entry(bars2, ev)
        self.assertIsNotNone(plan.get("ma_reclaim"),
                             "合成序列应满足 T0 判据（站上全部均线 + 上方有墙 + 平台度≥50%）")

    def test_evaluate_passes_t0_through(self):
        out = R.evaluate("688999", data_file=self.path)
        self.assertIn("ma_reclaim", out,
                      "evaluate() 必须透传 ma_reclaim —— 否则 battle_analyze 的报告看不到 T0")
        self.assertEqual(out.get("tier_t0"), "T0")
        t0 = out["ma_reclaim"]
        assert t0["trigger"] == self.bars[-1]["h"], "触发价 = D0 最高价"
        assert t0["hard_stop"] is not None and t0["hard_stop"] < t0["trigger"]

    def test_evaluate_t0_not_required_to_change_mode(self):
        """T0 是并行执行方案，不许因为透传就改掉当日 mode 的语义。"""
        ev, bars2, _ = R.build_ev(self.bars, drop_live=False, ticker="688999")
        plan = R.plan_entry(bars2, ev)
        out = R.evaluate("688999", data_file=self.path)
        self.assertEqual(out["mode"], plan["mode"])


class TestSizingStopUnified(unittest.TestCase):
    """缺陷 2：仓位分母必须用「实际会执行的止损腿」。"""

    def test_plan_hard_stop_wins_when_lower(self):
        # 688428 实况：矩阵止损 30.09（基准日最低）vs 计划硬止损 29.91（同锚 − gap）
        rec = {"entry": 30.90, "stop": 30.09, "stop_name": "基准日最低"}
        z = {"hard_stop": 29.91, "hard_anchor": "阳线下沿"}
        stop, basis = BA.sizing_stop_of(rec, z)
        self.assertEqual(stop, 29.91, "更低的计划硬止损必须优先（否则超配）")
        self.assertIn("硬止损", basis)

    def test_deeper_matrix_stop_kept(self):
        # 矩阵止损本来就更低（更深）→ 原样保留（更保守）
        rec = {"entry": 30.90, "stop": 28.66, "stop_name": "前一日最低"}
        z = {"hard_stop": 29.91, "hard_anchor": "阳线下沿"}
        stop, _ = BA.sizing_stop_of(rec, z)
        self.assertEqual(stop, 28.66)

    def test_stop_above_entry_is_not_used(self):
        # 计划硬止损在买价之上 = 开仓即止损，不能进仓位计算
        rec = {"entry": 29.00, "stop": 28.66, "stop_name": "前一日最低"}
        z = {"hard_stop": 29.91, "hard_anchor": "阳线下沿"}
        stop, _ = BA.sizing_stop_of(rec, z)
        self.assertEqual(stop, 28.66)

    def test_no_plan_leg_falls_back(self):
        rec = {"entry": 30.90, "stop": 30.09, "stop_name": "基准日最低"}
        stop, basis = BA.sizing_stop_of(rec, {})
        self.assertEqual(stop, 30.09)
        self.assertIn("矩阵", basis)

    def test_qty_matches_probe_preorder(self):
        """同一入场 + 同一止损腿 ⇒ 两个接口必须给同一个股数（688428 的 925/757 之争）。"""
        entry, stop = 30.90, 29.91
        code, account, cash = "688428", 50000.0, 76265.0
        qty_odds = P.ash_lots(account, entry, stop, code,
                              cap_amt=BA.single_cap_with_cash(account, cash))
        qty_probe = P.ash_lots(account, entry, stop, code)
        self.assertEqual(qty_odds, qty_probe)
        self.assertEqual(qty_odds, 757)          # 750 / 0.99 = 757.6 → 757


class TestSingleCapWithCash(unittest.TestCase):
    """缺陷 2 的附带项：现金不能顶掉 5 万单笔绝对额硬顶。"""

    def test_cash_above_hard_cap_is_clamped(self):
        self.assertEqual(BA.single_cap_with_cash(50000, 76265), 50000,
                         "可用现金 > 单笔硬顶时，硬顶不能被顶掉")

    def test_cash_below_hard_cap_wins(self):
        self.assertEqual(BA.single_cap_with_cash(50000, 30000), 30000)

    def test_no_cash_uses_hard_cap(self):
        self.assertEqual(BA.single_cap_with_cash(50000, None), 50000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
