# -*- coding: utf-8 -*-
"""站上均线跟进：T0 尾盘补救 + 沿线限价回升。运行：python tests/entry/test_momentum_follow.py"""
import json
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
import rule123 as R
from tests.entry.replay_momentum_cases import fill_of, orders_of, plan_of

SCRATCH = os.path.join(ROOT, "scratch")


def _bars(code):
    path = os.path.join(SCRATCH, "%s_bars.json" % code)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            bars = json.load(f).get("bars") or []
        if bars:
            return bars
    bars, _ = R.bars_from_em(R.secid_of(code), lmt=400)
    os.makedirs(SCRATCH, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"code": code, "bars": bars}, f, ensure_ascii=False)
    return bars


class T0TailFeice(unittest.TestCase):
    """中科飞测 2026-06-15：过 245 摸不到，尾盘站上均线仍应买中。"""

    @classmethod
    def setUpClass(cls):
        cls.bars = _bars("688361")
        cls.by = {b["d"][:10]: i for i, b in enumerate(cls.bars)}

    def test_plan_has_t0_tail(self):
        i = self.by["2026-06-15"]
        plan = plan_of(self.bars[:i], "688361")
        self.assertEqual(plan.get("mode"), "ma_reclaim_break")
        self.assertTrue(plan.get("recommend"))
        tail = plan.get("t0_tail")
        self.assertIsNotNone(tail)
        self.assertEqual(tail["fill"], "d1_close")
        self.assertEqual(float(tail["trigger"]), 245.0)

    def test_fill_at_close_not_wall(self):
        i = self.by["2026-06-15"]
        plan = plan_of(self.bars[:i], "688361")
        day = self.bars[i]
        self.assertLess(day["h"], 245.0)
        fills = []
        for kind, px, how in orders_of(plan, self.bars[i - 1]["c"]):
            f = fill_of(kind, px, how, day, bars=self.bars, i=i, plan=plan)
            if f is not None:
                fills.append((kind, f))
        self.assertTrue(fills, "应走 T0 尾盘腿")
        kind, px = min(fills, key=lambda x: x[1])
        self.assertEqual(kind, "T0尾盘")
        self.assertAlmostEqual(px, day["c"], places=2)


class LimitReclaimZhaoyi(unittest.TestCase):
    """兆易 2026-04-29：低开在买区下，日内回升摸到五日线应限价成交。"""

    @classmethod
    def setUpClass(cls):
        cls.bars = _bars("603986")
        cls.by = {b["d"][:10]: i for i, b in enumerate(cls.bars)}

    def test_ride_redirect_limit_policy(self):
        i = self.by["2026-04-29"]
        plan = plan_of(self.bars[:i], "603986")
        self.assertTrue(plan.get("ride_redirected"))
        z = plan.get("buy_zone") or {}
        self.assertEqual(z.get("fills_policy"), "limit_reclaim")
        self.assertIsNotNone(z.get("limit_px"))

    def test_fill_on_reclaim_not_open_in_zone(self):
        i = self.by["2026-04-29"]
        plan = plan_of(self.bars[:i], "603986")
        z = plan.get("buy_zone") or {}
        day = self.bars[i]
        lo = z["primary_lo"]
        self.assertLess(day["o"], lo, "开盘在买区下：旧口径会漏")
        fills = []
        for kind, px, how in orders_of(plan, self.bars[i - 1]["c"]):
            f = fill_of(kind, px, how, day, bars=self.bars, i=i, plan=plan)
            if f is not None:
                fills.append((kind, f))
        self.assertTrue(fills)
        kind, px = min(fills, key=lambda x: x[1])
        self.assertEqual(kind, "沿线限价")
        self.assertLessEqual(px, z["limit_px"] + 1e-9)


if __name__ == "__main__":
    unittest.main()
