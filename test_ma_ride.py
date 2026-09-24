#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「T0 启动日」vs「沿五日线趋势上升」两态判别 + 买法改道的回归测试。

## 背景（用户原话 2026-09-24）
「T0 触发日看距 MA5 的判断，这个也不对吧，我是判断出东材是走五日线上升的，
 你这样判断岂不是和写死没区别，我是想提高判断 T0 和沿趋势上升的模式准确度」

旧口径是 `D0 收盘距 MA5 ≤ 4%` 单一阈值 —— 在 4 只票、约 6 个月样本上拟合出来的，
扩到 53 只 × 500 根后：
  · 「买入持有 5 日收盘」口径：>4% +1.50%% vs ≤4% +0.95%%（旧表是 +1.75%% vs +3.35%%，腰斩）
  · 「引擎真实触发价 + MA5 硬止损」口径：>4% 均R −0.032 vs ≤4% −0.196（**方向反转**）
故改为看**该票与 MA5 的关系状态**（自归一化，无价格阈值）：
  line_ride      MA5 已上行一段（slope20 > 0）且价格多数时间在线上（above20 ≥ 12）
  fresh_reclaim  MA5 尚未转头（slope20 ≤ 0）且价格多数时间在线下（above20 ≤ 11）
  mixed          其余
53 只全池回放（引擎真实触发价/止损锚）：line_ride  5 日均R −0.292 / 胜率 19% / 77% 被扫；
fresh_reclaim +0.160 / 26% / 68%。→ line_ride 态把入口从「过昨高追」改成「回踩 MA5 挂限价」。

跑法：`python test_ma_ride.py`（或 `python -m unittest test_ma_ride`）。
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

import rule123 as R                  # noqa: E402


def _bars(closes, spike_at=None, spike_h=None):
    out = []
    d = datetime.date(2026, 6, 1)
    for i, c in enumerate(closes):
        c = float(c)
        if spike_at is not None and i == spike_at:
            o, h, l = c - 0.12, float(spike_h), c - 0.17
        else:
            o, h, l = c - 0.05, c + 0.07, c - 0.07
        out.append({"d": d.isoformat(), "o": round(o, 2), "h": round(h, 2),
                    "l": round(l, 2), "c": round(c, 2), "v": 1.0e6})
        d += datetime.timedelta(days=1)
    return out


def riding_series(n=60):
    """稳步上行 + 每 8 根一次短回踩（造枢轴）+ 第 50 根一根长上影当墙 + 末根站上均线。

    ⇒ MA5 20 根斜率 > 0、近 20 根 18 根收在线上 = `line_ride`。
    """
    closes = []
    for i in range(n - 1):
        c = 10.0 + 0.02 * i
        if i % 8 == 4:
            c -= 0.10
        elif i % 8 == 0:
            c += 0.06
        closes.append(c)
    bars = _bars(closes, spike_at=50, spike_h=12.30)
    closes.append(11.25)
    last = closes[-1]
    bars.append({"d": (datetime.date.fromisoformat(bars[-1]["d"])
                       + datetime.timedelta(days=1)).isoformat(),
                 "o": 11.20, "h": 11.30, "l": 11.18, "c": 11.25, "v": 1.6e6})
    _ = last
    return bars


def declining_series(n=60):
    """自 12.0 缓降 20+ 根（MA5 20 根斜率为负）+ 末根反抽到均线上 = `fresh_reclaim`。"""
    closes = [12.0 - 0.02 * i + (0.05 if i % 2 else -0.05) for i in range(n)]
    bars = _bars(closes, spike_at=30, spike_h=12.55)
    bars.append({"d": (datetime.date.fromisoformat(bars[-1]["d"])
                       + datetime.timedelta(days=1)).isoformat(),
                 "o": 11.55, "h": 11.75, "l": 11.50, "c": 11.70, "v": 1.6e6})
    return bars


def _force_last_dist(bars, pct):
    """把末根收盘调到「距 MA5 = pct」——解 c = S(1+p)/(4−p)，S = 前 4 根收盘和。"""
    s = sum(b["c"] for b in bars[-5:-1])
    c = s * (1 + pct) / (4 - pct)
    b = dict(bars[-1])
    b["o"], b["h"], b["l"], b["c"] = round(c - 0.04, 2), round(c + 0.06, 2), round(c - 0.08, 2), round(c, 2)
    return bars[:-1] + [b]


def _write_snapshot(bars):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"bars": bars, "name": "测试票", "spot": bars[-1]["c"]}, f)
    return path


class TestRideState(unittest.TestCase):
    """状态判别本身：只看该票与 MA5 的关系，不含任何价格距离阈值。"""

    def test_line_ride(self):
        s = R.ma_ride_state(riding_series(), R.atr14(riding_series()))
        self.assertEqual(s["state"], "line_ride")
        self.assertGreater(s["ma5_slope20_pct"], 0)
        self.assertGreaterEqual(s["above20"], R.MA_RIDE_ABOVE_MIN)
        self.assertEqual(s["prefer"], "line_pullback")

    def test_fresh_reclaim(self):
        bars = declining_series()
        s = R.ma_ride_state(bars, R.atr14(bars))
        self.assertEqual(s["state"], "fresh_reclaim")
        self.assertLessEqual(s["ma5_slope20_pct"], 0)
        self.assertLessEqual(s["above20"], R.MA_RIDE_ABOVE_MIN - 1)
        self.assertEqual(s["prefer"], "breakout")

    def test_too_short_returns_none(self):
        bars = _bars([10.0] * 20)
        self.assertIsNone(R.ma_ride_state(bars, 0.2),
                          "不足 20+6 根无法判 20 根斜率，必须返回 None 而不是瞎猜")

    def test_same_dist_ma5_different_state(self):
        """**同样「距 MA5 +5%」的两段序列必须判出不同态** —— 这正是旧 4% 阈门的死穴。

        旧口径下这两段都是「>4% ⇒ 不按 T0 追」，但一段是沿线上行（改道回踩）、
        一段是刚收复（T0 成立）。判别必须来自状态，而不是那个百分比。
        """
        rid = _force_last_dist(riding_series(), 0.05)
        dec = _force_last_dist(declining_series(), 0.05)
        s_rid = R.ma_ride_state(rid, R.atr14(rid))
        s_dec = R.ma_ride_state(dec, R.atr14(dec))
        self.assertAlmostEqual(s_rid["dist_ma5_pct"], 5.0, delta=0.4)
        self.assertAlmostEqual(s_dec["dist_ma5_pct"], 5.0, delta=0.4)
        self.assertEqual(s_rid["state"], "line_ride")
        self.assertEqual(s_dec["state"], "fresh_reclaim",
                         "距 MA5 同为 +5%，但 MA5 20 根斜率为负 = 刚收复 ⇒ 两态必须分开")


class TestRideRedirect(unittest.TestCase):
    """`line_ride` 态：T0 让位给「回踩 MA5 低吸」，且必须给出可挂的限价买区。"""

    @classmethod
    def setUpClass(cls):
        cls.bars = riding_series()
        cls.ev, cls.bars2, _ = R.build_ev(cls.bars, drop_live=False, ticker="688999")
        cls.plan = R.plan_entry(cls.bars2, cls.ev)

    def test_ride_state_reaches_plan(self):
        t0 = self.plan.get("ma_reclaim")
        self.assertIsNotNone(t0, "前提：该序列满足 T0 判据（站上全部均线 + 上方有墙 + 平台度）")
        self.assertEqual(t0.get("ride_state"), "line_ride")

    def test_mode_redirected_to_line_pullback(self):
        self.assertEqual(self.plan["mode"], "line_pullback",
                         "line_ride 态下不许按 T0 过昨高追 —— 应改道回踩")
        self.assertTrue(self.plan["recommend"], "改道不是否决：必须仍然给可执行买点")

    def test_held_marker_and_superseded_mode(self):
        self.assertIn("t0_held_for_ride", self.plan)
        self.assertEqual(self.plan["t0_held_for_ride"]["preferred"], "line_pullback")
        self.assertEqual(self.plan["t0_superseded_mode"], "wait")

    def test_buy_zone_is_ma5_pullback(self):
        z = self.plan["buy_zone"]
        ride = self.plan["ma_reclaim"]["ride"]
        self.assertEqual(z["anchor"], "ma5")
        self.assertEqual(z["level"], ride["ma5"])
        # 下沿 = MA5 −0.05×ATR，上沿 = MA5 +1.0×ATR
        self.assertLess(z["primary_lo"], z["level"])
        self.assertGreater(z["primary_hi"], z["level"])
        self.assertEqual(self.plan["path"], "A")
        self.assertEqual(self.plan["setup"], "pullback")

    def test_hard_stop_below_zone_floor(self):
        z = self.plan["buy_zone"]
        sp = self.plan["stop_plan"]
        self.assertLess(sp["hard"], z["primary_lo"],
                        "硬止损必须落在买区下沿之下，否则会出现「按买区成交即已破止损」")
        self.assertEqual(sp["struct"], z["level"])
        self.assertFalse(sp["t0"], "改道后这条腿不再是 T0 腿")

    def test_trigger_still_reported_as_secondary(self):
        t0 = self.plan["ma_reclaim"]
        self.assertIsNotNone(t0.get("trigger"))
        self.assertIsNotNone(t0.get("entry_redirect"))
        self.assertEqual(t0["entry_redirect"]["to"], "line_pullback")
        self.assertIn("更宽", t0["entry_redirect"]["alt"])

    def test_note_warns_about_ma5_stop_too_tight(self):
        note = self.plan["note"]
        self.assertIn("沿五日线上升", note)
        self.assertIn("阳线下沿", note)


class TestMixedStateKeepsTakeover(unittest.TestCase):
    """`mixed` / `fresh_reclaim` 态不许被改道 —— 回归保护，防止误伤既有 T0 样本。"""

    def test_mixed_still_takes_over(self):
        import test_t0_passthrough as T
        ev, bars2, _ = R.build_ev(T.t0_series(), drop_live=False, ticker="688999")
        plan = R.plan_entry(bars2, ev)
        t0 = plan.get("ma_reclaim") or {}
        self.assertEqual(t0.get("ride_state"), "mixed")
        self.assertEqual(plan["mode"], "ma_reclaim_break",
                         "mixed 态下 T0 仍按用户定级接管（不改既有行为）")
        self.assertNotIn("t0_held_for_ride", plan)


class TestRideDecoupledFromT0(unittest.TestCase):
    """`ma_ride` 必须与 T0 是否成立**解耦**。

    反例就是老罗点名的东材 601208 09-24：收 56.44 已创 25 日新高（头上无墙）⇒ T0 判据
    不成立，但它恰恰就是 `line_ride`。若只在 T0 成立时才算 ride，这只票反而看不到答案。
    """

    def test_ride_reported_when_t0_absent(self):
        bars = riding_series()
        b = dict(bars[-1])
        b.update({"h": 12.60, "c": 12.45, "o": 12.30, "l": 12.20})
        bars = bars[:-1] + [b]
        ev, bars2, _ = R.build_ev(bars, drop_live=False, ticker="688999")
        plan = R.plan_entry(bars2, ev)
        self.assertIsNone(plan.get("ma_reclaim"),
                          "前提：收盘创 25 根窗口新高 ⇒ 头上无墙 ⇒ T0 不成立")
        self.assertIn("ma_ride", plan, "T0 不成立时仍必须给出模式判别")
        self.assertEqual(plan["ma_ride"]["state"], "line_ride")


class TestEvaluatePassThrough(unittest.TestCase):
    """`evaluate()` 是重建 out（逐字段挑）—— 新字段漏了就只在 report 里消失。"""

    @classmethod
    def setUpClass(cls):
        cls.bars = riding_series()
        cls.path = _write_snapshot(cls.bars)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.path)

    def test_t0_held_for_ride_passes_through(self):
        out = R.evaluate("688999", data_file=self.path, eod=True)
        self.assertIn("t0_held_for_ride", out,
                      "evaluate 重建 out 时必须透传 t0_held_for_ride，否则报告看不出改道原因")
        self.assertEqual(out["mode"], "line_pullback")


if __name__ == "__main__":
    unittest.main(verbosity=2)
