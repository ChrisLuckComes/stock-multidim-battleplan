#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「T0 启动日」vs「沿某条均线趋势上升」两态判别 + 买法改道的回归测试。

## 背景（用户原话 2026-09-24）
「T0 触发日看距 MA5 的判断，这个也不对吧，我是判断出东材是走五日线上升的，
 你这样判断岂不是和写死没区别，我是想提高判断 T0 和沿趋势上升的模式准确度」

旧口径是 `D0 收盘距 MA5 ≤ 4%` 单一阈值 —— 在 4 只票、约 6 个月样本上拟合出来的，
扩到 53 只 × 500 根后：
  · 「买入持有 5 日收盘」口径：>4% +1.50%% vs ≤4% +0.95%%（旧表是 +1.75%% vs +3.35%%，腰斩）
  · 「引擎真实触发价 + MA5 硬止损」口径：>4% 均R −0.032 vs ≤4% −0.196（**方向反转**）
故改为看**该票与均线的关系状态**（自归一化，无价格阈值）：
  line_ride      MA5 / EMA10 / MA20 任一：20 根斜率 > 0 且价格多数时间在线上（站上 ≥ 12 根）
                 —— 多条成立时取**收盘下方离现价最近**的那条，回踩锚这条（**不写死五日线**）
  fresh_reclaim  三条都不成线，且 MA5 尚未转头（slope20 ≤ 0）且多数时间在 MA5 下（≤ 11）
  mixed          其余
53 只全池回放（引擎真实触发价/止损锚）：line_ride  5 日均R −0.292 / 胜率 19% / 77% 被扫；
fresh_reclaim +0.160 / 26% / 68%。→ line_ride 态把入口从「过昨高追」改成「回踩被选中的那条线挂限价」。

★ 字段口径（2026-09-24 晚统一）：`line*` = 被选中的那条线；`ma5*` / `above20` / `bounce20`
  恒为 MA5 口径。读取方取错会报出完全不同的价位 —— 见 `TestRideAnchorNotHardcodedToMa5`。

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
        """**同样「距 MA5」的两段序列必须判出不同态** —— 这正是旧 4% 阈门的死穴。

        旧口径下两段都是「>4% ⇒ 不按 T0 追」，但一段是沿线上行（改道回踩）、
        一段是刚收复（T0 成立）。判别必须来自状态，而不是那个百分比。
        ⚠️ 距 MA5 的绝对值只在此处取小值（0.5%）—— 因为 2026-09-24 三轮又加了一条
        **独立的**闸门：距最近候选线 > `MA_RIDE_ANCHOR_MAX_ATR` 时不再是 line_ride，
        而是 `new_high`（无锚=新高，见 `TestNewHighNoAnchor`）。两条闸门语义不同：
        这条管「趋势还是刚收复」，那条管「够不够得着、有没有回踩锚」。
        """
        rid = _force_last_dist(riding_series(), 0.005)
        dec = _force_last_dist(declining_series(), 0.005)
        s_rid = R.ma_ride_state(rid, R.atr14(rid))
        s_dec = R.ma_ride_state(dec, R.atr14(dec))
        self.assertAlmostEqual(s_rid["dist_ma5_pct"], 0.5, delta=0.4)
        self.assertAlmostEqual(s_dec["dist_ma5_pct"], 0.5, delta=0.4)
        self.assertEqual(s_rid["state"], "line_ride")
        self.assertEqual(s_dec["state"], "fresh_reclaim",
                         "距 MA5 相同，但 MA5 20 根斜率为负 = 刚收复 ⇒ 两态必须分开")

    def test_ride_can_be_longer_ma(self):
        """沿线上涨不写死五日线。价格落到五日线下面时，仍可贴着更长的均线。"""
        closes = [10.0 + 0.05 * i for i in range(55)]
        peak = closes[-1]
        closes += [peak - 0.04 * i for i in range(1, 7)]
        bars = _bars(closes)
        s = R.ma_ride_state(bars, R.atr14(bars))
        self.assertEqual(s["state"], "line_ride")
        self.assertIn(s["anchor"], ("ema10", "sma20"))
        self.assertNotEqual(s["anchor"], "ma5")


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

    def test_redirect_flag_is_explicit(self):
        """`ride_redirected` 必须显式为真 —— 光有 `t0_held_for_ride` 区分不了「真改道」
        与「当日原有 line_pullback」（东材 601208 09-24 就是后者，报告曾误标「已改道」）。"""
        self.assertTrue(self.plan.get("ride_redirected"))
        self.assertEqual((self.plan.get("ride_priority") or {}).get("winner"), "ride")

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


class TestNewHighNoAnchor(unittest.TestCase):
    """「没有锚点就代表是新高，不需要找锚」（老罗 2026-09-24 三轮原话）。

    振华股份 603067 当时被标成 `line_ride`、锚 MA20 36.36 —— 可现价高出该线 **2.20×ATR**，
    离「贴着它走」十万八千里；而它当日是实打实的平台突破（活平台沿 40.89、刚站上 1 根）。
    「沿均线走」这种几乎恒真的描述不该盖住客观形态。
    ⇒ 距最近候选线 > `MA_RIDE_ANCHOR_MAX_ATR`(1.5)×ATR ⇒ 判 `new_high`（无回踩锚），
       `prefer` 走 breakout，不再改道回踩。

    门槛 1.5×ATR 的实证落点（`research_ride_priority.py`，突破日 n=1560）：
      距线 0.5~1.0×ATR ⇒ 5 日内回踩触及率 66%、触及后期望 +1.59%
      距线 >1.5×ATR   ⇒ 触及率跌到 24%、期望转负 −0.22%（这条线已不是可用回踩锚）
      同日突破买（1.5×ATR 止损）在远锚档反而最好（+0.278R / +1.68%）
    """

    def _far(self):
        return _force_last_dist(riding_series(), 0.05)

    def test_close_anchor_is_line_ride(self):
        bars = riding_series()
        s = R.ma_ride_state(bars, R.atr14(bars))
        self.assertEqual(s["state"], "line_ride")
        self.assertLessEqual(s["line_dist_atr"], R.MA_RIDE_ANCHOR_MAX_ATR)
        self.assertEqual(s["prefer"], "line_pullback")

    def test_far_anchor_is_new_high(self):
        bars = self._far()
        s = R.ma_ride_state(bars, R.atr14(bars))
        self.assertEqual(s["state"], "new_high",
                         "距最近候选线 >1.5×ATR ⇒ 没有回踩锚 ⇒ 新高，不该叫「沿线上行」")
        self.assertGreater(s["line_dist_atr"], R.MA_RIDE_ANCHOR_MAX_ATR)
        self.assertEqual(s["prefer"], "breakout")
        self.assertIn("新", s["note"])
        self.assertEqual(s["anchor_max_atr"], R.MA_RIDE_ANCHOR_MAX_ATR)
        # 仍要报出「最近那条候选线」，否则用户看不懂为什么不给锚
        self.assertIsNotNone(s["line"])
        self.assertIsNotNone(s["line_label"])

    def test_far_anchor_does_not_redirect(self):
        """`new_high` 不得触发回踩改道（`t0_held_for_ride` / `line_pullback` 都不该出现）。"""
        bars = self._far()
        ev, bars2, _ = R.build_ev(bars, drop_live=False, ticker="688999")
        plan = R.plan_entry(bars2, ev)
        self.assertIsNone(plan.get("t0_held_for_ride"))
        self.assertNotEqual(plan.get("mode"), "line_pullback")
        self.assertNotIn("line_ride 改道", json.dumps(plan, ensure_ascii=False))

    def test_no_anchor_gate_without_atr(self):
        """不给 ATR 时不做这条闸门 —— 回到纯状态判别（对外接口的向后兼容）。"""
        bars = self._far()
        s = R.ma_ride_state(bars, None)
        self.assertEqual(s["state"], "line_ride")
        self.assertIsNone(s["line_dist_atr"])

    def test_anchor_gate_used_by_engine(self):
        """引擎侧真的接上了这条闸门：`plan["ride_priority"]` 写明突破优先。"""
        bars = self._far()
        ev, bars2, _ = R.build_ev(bars, drop_live=False, ticker="688999")
        plan = R.plan_entry(bars2, ev)
        self.assertEqual(plan["ma_ride"]["state"], "new_high")
        rp = plan.get("ride_priority") or {}
        self.assertEqual(rp.get("winner"), "breakout")
        self.assertEqual(rp.get("ride_role"), "无（不给回踩锚）")


def flat_then_break(lip=10.05, brk=10.15, n=60):
    """60 根在 9.95~10.05 反复触碰、末根收 10.15 站上平台沿 ⇒ 合成 `platform_break`。

    取 brk−lip ≈ 1×ATR，避开 `pack()` 的「突破已延伸·>2×ATR 不追」降级，确保 recommend=True。
    """
    d = datetime.date(2026, 3, 1)
    bars = [{"d": (d + datetime.timedelta(days=i)).isoformat(), "o": 10.00,
             "h": lip, "l": 9.95, "c": 10.00, "v": 1.0e6} for i in range(n)]
    bars.append({"d": (d + datetime.timedelta(days=n)).isoformat(), "o": 10.02,
                 "h": round(brk + 0.03, 2), "l": 10.00, "c": brk, "v": 2.0e6})
    return bars


def _fake_ride(state="line_ride", **kw):
    d = {"state": state, "anchor": "ma5", "line": 10.02, "line_label": "五日线",
         "line_slope20_pct": 3.1, "line_above20": 14, "line_bounce20": 6,
         "line_dist_pct": 1.3, "line_dist_atr": 1.1, "anchor_max_atr": 1.5,
         "ma5_slope20_pct": 3.1, "above20": 14, "ma5": 10.02,
         "prefer": "line_pullback", "note": "假状态（测试注入）"}
    d.update(kw)
    return d


class TestRidePriorityOverBackground(unittest.TestCase):
    """沿线是**背景**、当日形态是**事件** ⇒ 事件优先（老罗 2026-09-24 三轮）。

    他原话：「但平台突破是客观存在的，这个优先级，你要想想到底怎么判定。
    否则所有股大部分时间都是沿均线走的。」—— 全池 83 只里 42 只 line_ride（占一半），
    若它能随意抢位，客观形态全被吃掉。
    """

    def _plan(self, ride_state="line_ride", **ride_kw):
        """用「合成平台突破 + 注入 ride 状态」把两条路摆在同一天。"""
        bars = flat_then_break()
        ev, bars2, _ = R.build_ev(bars, drop_live=False, ticker="688999")
        orig = R.ma_ride_state
        R.ma_ride_state = lambda *a, **k: _fake_ride(ride_state, **ride_kw)
        try:
            return R.plan_entry(bars2, ev)
        finally:
            R.ma_ride_state = orig

    def test_breakout_beats_line_ride_on_same_day(self):
        """当日已有 platform_break 且 recommend=True ⇒ mode 不被沿线改道，且写明突破优先。

        数据依据（`research_ride_priority.py`，突破日 n=1560）：同日突破买（1.5×ATR 止损）
        均R +0.212 / 收益 +1.16% / 胜 44%；而「挂限价等回踩」只有 31% 的日子等得到
        （每机会 +0.39%）⇒ 拿沿线去改道客观突破是净损失。
        """
        plan = self._plan()
        self.assertEqual(plan.get("mode"), "platform_break",
                         "前提：当日确实是平台突破且 recommend=True")
        self.assertTrue(plan.get("recommend"))
        rp = plan.get("ride_priority") or {}
        self.assertEqual(rp.get("winner"), "breakout",
                         "沿线上行是背景，不能改道掉当日的客观突破")
        self.assertEqual(rp.get("mode"), "platform_break")
        self.assertIsNone(plan.get("t0_held_for_ride"),
                          "T0 未接管：突破优先（这里也没有 T0 可接管）")
        self.assertIn("次选", rp.get("ride_role") or "")

    def test_line_ride_wins_when_no_other_setup(self):
        """当日别无买点时，沿线回踩锚是**唯一入口** ⇒ winner=ride，不牺牲任何突破机会。

        `riding_series()` 就是这形状：沿线上行 + T0 成立 + 当日原本无买点 ⇒ 改道
        `line_pullback`（有研硅 688432 09-24 实盘即此）。
        """
        bars = riding_series()
        ev, bars2, _ = R.build_ev(bars, drop_live=False, ticker="688999")
        plan = R.plan_entry(bars2, ev)
        self.assertEqual(plan.get("mode"), "line_pullback",
                         "前提：沿线上行 + T0 成立 + 当日无其他买点 ⇒ 改道回踩")
        rp = plan.get("ride_priority") or {}
        self.assertEqual(rp.get("winner"), "ride")
        self.assertEqual(rp.get("line"), plan["ma_ride"]["line"])


class TestRideDecoupledFromT0(unittest.TestCase):
    """`ma_ride` 必须与 T0 是否成立**解耦**。

    反例就是老罗点名的东材 601208 09-24：收 56.44 已创 25 根窗口新高（头上无墙）⇒ T0 判据
    不成立，但它恰恰就是 `line_ride`。若只在 T0 成立时才算 ride，这只票反而看不到答案。

    本用例把末根直接跳到远高于均线的新高 ⇒ 按三轮的锚闸门判为 `new_high`
    （东材实盘距 MA5 只有 0.55×ATR，所以它是 `line_ride`）；要断言的是
    **「(ma_ride) 必须在」** 这条不变 —— 只在 T0 成立时才算 ride，等于这只票看不到答案。
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
        self.assertIn(plan["ma_ride"]["state"], ("line_ride", "new_high"))
        self.assertEqual(plan["ma_ride"]["prefer"], "breakout")


class TestRideAnchorNotHardcodedToMa5(unittest.TestCase):
    """沿线不一定是沿五日线 —— 锚可能是 EMA10 / MA20（老罗 2026-09-24 晚改）。

    `ma_ride_state` 改成「MA5/EMA10/MA20 各算一遍，取收盘下方离现价最近的那条」之后，
    下游还有三处在取 `ride["ma5"]`：`t0_held_for_ride["line"]`、改道买区的 `hits`
    （回踩次数）、报告徽标/脚注的名称与斜率。全池 83 只里 42 只 line_ride，其中
    **21 只锚非 MA5**（CRWD −9.2%、HPE −10.2%、振华 603067 −12.6% 量级）——
    取 ma5 会报出**完全不同**的价位，不是显示瑕疵。
    """

    @classmethod
    def setUpClass(cls):
        cls.bars = riding_series()
        ev, bars2, _ = R.build_ev(cls.bars, drop_live=False, ticker="688999")
        cls.base = R.ma_ride_state(bars2, R.atr14(bars2))
        cls.fake = dict(cls.base)
        cls.fake.update({
            "state": "line_ride", "anchor": "sma20", "line_label": "MA20",
            "line": 10.00, "line_slope20_pct": 7.5, "line_above20": 16,
            "line_bounce20": 4, "line_dist_pct": -1.8, "line_dist_atr": -0.6,
        })
        _orig = R.ma_ride_state
        R.ma_ride_state = lambda *a, **k: dict(cls.fake)
        try:
            cls.plan = R.plan_entry(bars2, ev)
        finally:
            R.ma_ride_state = _orig

    def test_redirect_anchor_follows_chosen_line(self):
        z = self.plan["buy_zone"]
        self.assertEqual(self.plan["mode"], "line_pullback")
        self.assertEqual(z["anchor"], "sma20")
        self.assertEqual(z["level"], self.fake["line"])
        self.assertNotEqual(z["anchor"], "ma5")

    def test_zone_ma5_stays_real_ma5(self):
        z = self.plan["buy_zone"]
        self.assertEqual(z["ma5"], self.fake["ma5"],
                         "买区 ma5 字段必须留真 MA5 —— 池表（watch_cn / pool_us / "
                         "scanner）拿它当 MA5 列显示，写成锚线价位会串价")

    def test_hits_uses_chosen_line_bounce(self):
        self.assertEqual(self.plan["buy_zone"]["hits"], self.fake["line_bounce20"],
                         "回踩次数必须取**被选中那条线**的，不是 MA5 的 bounce20")

    def test_t0_held_for_ride_reports_chosen_line(self):
        th = self.plan["t0_held_for_ride"]
        self.assertEqual(th["line"], self.fake["line"],
                         "t0_held_for_ride.line 必须是被选中的锚线，不是 MA5")
        self.assertEqual(th["line_label"], "MA20")
        self.assertEqual(th["anchor"], "sma20")
        self.assertIn("沿MA20上升", th["reason"])

    def test_verdict_names_chosen_line(self):
        self.assertIn("MA20", self.plan["verdict"])
        self.assertNotIn("五日线", self.plan["verdict"])

    def test_line_fields_track_chosen_line_not_ma5(self):
        """`line_*` 与 `ma5_*` 是两套口径，不得互相顶替。

        构造「收盘跌破 MA5、但贴着更长的均线」的序列：此时锚不是 MA5，
        `line_slope20_pct`（所选线）与 `ma5_slope20_pct` 必然不同 —— 报告若把
        所选线的斜率配上 MA5 的 `above20`，会输出「斜率达标但站上根数不足 12」的
        自相矛盾文案。
        """
        closes = [10.0 + 0.05 * i for i in range(55)]
        peak = closes[-1]
        closes += [peak - 0.04 * i for i in range(1, 7)]
        bars = _bars(closes)
        s = R.ma_ride_state(bars, R.atr14(bars))
        self.assertEqual(s["state"], "line_ride")
        self.assertNotEqual(s["anchor"], "ma5")
        self.assertGreaterEqual(s["line_above20"], R.MA_RIDE_ABOVE_MIN,
                                "被选中那条线必须自己过 ≥12 根闸门")
        self.assertEqual(s["line_label"],
                         {"ema10": "EMA10", "sma20": "MA20"}[s["anchor"]])
        self.assertEqual(s["line"], round(s["line"], 2))
        self.assertNotEqual(s["line_slope20_pct"], s["ma5_slope20_pct"],
                            "所选线与 MA5 的斜率不能是同一个值（否则等于没换锚）")


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
