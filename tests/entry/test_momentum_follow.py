# -*- coding: utf-8 -*-
"""站上均线跟进：T0 尾盘补救 + 沿线限价回升。

⚠ 2026-10-22 新增「出水芙蓉长上影不算数」规则后的回归口径调整：
   中科飞测 688361 与兆易 603986 的真实信号日（06-15 / 04-29）前一根 K
   （06-12 / 04-28）都是**长上影**（冲高被砸回），旧代码误把它们当有效 T0 信号日，
   本文件原测试就是守这种「长上影也计数」的旧行为。新规下它们**不算数**。

   故本文件分两类守卫：
     (A) 长上影 D0 经 plan 不再出 T0 —— 负向真实数据守新规（test_*_long_upper_rejected）；
     (B) 干净 D0（06-15 / 04-29）仍由 plan 产出 t0_tail / ride_redirected+limit_reclaim
         —— 正向守「尾部补救 / 沿线限价」逻辑未被误伤（子块断言，不依赖 regime 顶层 vet；
         真实数据下 06-15 切片 regime 向下会顶掉 recommend，但 t0_tail 子块照常计算）。
   成交腿用受控日 K 直接喂 fill_of，避免被 regime 闸门整段吞掉。
运行：python tests/entry/test_momentum_follow.py
"""
import json
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
import rule123 as R
from tests.entry.replay_momentum_cases import fill_of, above_ma, plan_of

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
    """中科飞测 688361：出水芙蓉长上影新规的正/负向守卫。

    真实序列：06-12 长上影（o230.01 h245.00 c226.45，up/body=4.21）⇒ 新规不算数；
             06-15 干净（o226.45 h235.88 c235.0，up/body=0.10，close_pos=0.94）⇒ 仍算数，
             其 T0 触发价 = 当日最高 235.88，次日未过昨高则尾盘按收盘成交。
    """

    @classmethod
    def setUpClass(cls):
        cls.bars = _bars("688361")
        cls.by = {b["d"][:10]: i for i, b in enumerate(cls.bars)}

    def test_long_upper_d0_rejected(self):
        """负向：06-12 长上影经 plan 不再出 T0（新规核心守卫）。"""
        i = self.by["2026-06-15"]
        plan = plan_of(self.bars[:i], "688361")   # D0 = 06-12（长上影）
        self.assertIsNone(plan.get("ma_reclaim"),
                         "长上影 D0 经 2026-10-22 新规后不应再产出 T0")
        self.assertNotEqual(plan.get("mode"), "ma_reclaim_break")

    def test_clean_d0_yields_t0_tail(self):
        """正向：06-15 干净 D0 仍产出 t0_tail（trigger=当日最高 235.88、fill=d1_close）。"""
        i = self.by["2026-06-15"]
        plan = plan_of(self.bars[:i + 1], "688361")   # D0 = 06-15（干净）
        tail = plan.get("t0_tail")
        self.assertIsNotNone(tail, "干净 D0 必须仍产出 T0 尾盘补救子块")
        self.assertEqual(tail["fill"], "d1_close")
        self.assertAlmostEqual(float(tail["trigger"]), 235.88, places=2)

    def test_tail_fills_at_close_when_d1_cannot_break(self):
        """正向：D1 摸不到触发价、收盘仍站上均线且高于硬止损 ⇒ 尾盘按收盘价成交。"""
        i = self.by["2026-06-15"]
        plan = plan_of(self.bars[:i + 1], "688361")
        tail = plan.get("t0_tail")
        self.assertIsNotNone(tail)
        trig = float(tail["trigger"])
        # 受控日 K：高于硬止损、最高摸不到触发价（模拟「次日未能过昨高」）
        day = {"d": "2026-06-16", "o": 230.0, "h": trig - 1.0,
               "l": 229.0, "c": 234.0}
        self.assertTrue(above_ma(self.bars, i + 1), "D1 需站上均线，守住 above_ma 前置")
        self.assertLess(day["h"], trig)
        f = fill_of("T0尾盘", trig, "t0_tail", day,
                    bars=self.bars, i=i + 1, plan={"t0_tail": tail})
        self.assertIsNotNone(f)
        self.assertAlmostEqual(f, day["c"], places=2)


class LimitReclaimZhaoyi(unittest.TestCase):
    """兆易 603986：出水芙蓉长上影新规 + 沿线限价回升的正/负向守卫。

    真实序列：04-28 长上影（o304.25 h318.58 c306.75，up/body=4.73）⇒ 新规不算数；
             04-29 干净（o302.26 h310.91 c308.14，up/body=0.47，close_pos=0.81）⇒ 仍算数，
             低开在买区下、日内回升摸到五日线 ⇒ 沿线限价成交。
    """

    @classmethod
    def setUpClass(cls):
        cls.bars = _bars("603986")
        cls.by = {b["d"][:10]: i for i, b in enumerate(cls.bars)}

    def test_long_upper_d0_rejected(self):
        """负向：04-28 长上影经 plan 不再出 T0。"""
        i = self.by["2026-04-29"]
        plan = plan_of(self.bars[:i], "603986")   # D0 = 04-28（长上影）
        self.assertIsNone(plan.get("ma_reclaim"),
                         "长上影 D0 经 2026-10-22 新规后不应再产出 T0")
        self.assertNotEqual(plan.get("mode"), "ma_reclaim_break")

    def test_ride_redirect_limit_policy(self):
        """正向：04-29 干净 D0 仍触发改道 + 沿线限价政策，limit_px 非空。"""
        i = self.by["2026-04-29"]
        plan = plan_of(self.bars[:i + 1], "603986")   # D0 = 04-29（干净）
        self.assertTrue(plan.get("ride_redirected"), "干净 D0 应仍触发均线改道")
        z = plan.get("buy_zone") or {}
        self.assertEqual(z.get("fills_policy"), "limit_reclaim")
        self.assertIsNotNone(z.get("limit_px"))

    def test_fill_on_reclaim_logic(self):
        """正向：开盘在买区下、日内低点触及限价 ⇒ 沿线限价成交价不高于 limit_px。"""
        i = self.by["2026-04-29"]
        plan = plan_of(self.bars[:i + 1], "603986")
        z = plan.get("buy_zone") or {}
        limit_px = z.get("limit_px")
        self.assertIsNotNone(limit_px)
        lo, hi = z.get("primary_lo"), z.get("primary_hi")
        # 受控日 K：开盘低于买区下沿，日内低点触及限价（模拟「低开回升摸线」）
        day = {"d": "2026-04-30", "o": lo - 3.0, "h": hi,
               "l": limit_px - 0.5, "c": limit_px + 2.0}
        f = fill_of("沿线限价", float(limit_px), "limit", day,
                    bars=self.bars, i=i + 1, plan={"buy_zone": {
                        "hard_stop": limit_px, "fills_policy": "limit_reclaim",
                        "primary_lo": lo, "primary_hi": hi}})
        self.assertIsNotNone(f, "低开回升摸线应触发沿线限价成交")
        self.assertLessEqual(f, float(limit_px) + 1e-9,
                             "沿线限价成交价不得高于 limit_px")


if __name__ == "__main__":
    unittest.main()
