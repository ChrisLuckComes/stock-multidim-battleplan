# -*- coding: utf-8 -*-
"""盘中第二次机会：打穿上限武装回踩、回踩进首屏、计划上限交接通道。"""
import probe_intraday as P
import battle_analyze as BA
from gates.session_decide import (
    decide, apply_channel_handoff, target_is_stale,
)


def _day(price, pre, avg, n=8):
    minutes = []
    for i in range(n):
        minutes.append({
            "t": "10:%02d" % (30 + i),
            "o": pre, "c": price, "h": price, "l": min(price, pre),
            "v": 1000, "avg": avg,
        })
    minutes[0]["o"] = pre
    day = {"code": "600000", "name": "t", "date": "2026-10-16",
           "pre": pre, "minutes": minutes, "flows": []}
    summary = {"last": {"c": price, "avg": avg},
               "flow": {"main": 100.0}, "session_done": False,
               "after_share": 0.1}
    return day, summary


def _bar(hm, o, h, l, c, v):
    return {"d": "2026-10-16 %s" % hm, "o": o, "h": h, "l": l, "c": c, "v": v}


class TestPullbackAnchor:
    def test_overshoot_anchors_trigger_not_close(self):
        assert P.pullback_anchor("overshoot", 11.40, 10.60) == 10.60

    def test_hit_anchors_close(self):
        assert P.pullback_anchor("hit", 10.80, 10.60) == 10.80


class TestEvalOvershootArmsPullback:
    def test_overshoot_then_pullback_is_the_action(self):
        # K=20.5，触发 20.6，上限 21.1。第 4 根打穿，随后缩量回踩企稳。
        mins = [
            _bar("10:00", 20.2, 20.4, 20.1, 20.3, 100),
            _bar("10:05", 20.3, 20.45, 20.2, 20.35, 100),
            _bar("10:10", 20.35, 20.5, 20.3, 20.4, 100),
            _bar("10:15", 20.4, 21.6, 20.4, 21.5, 500),
            _bar("10:20", 21.2, 21.2, 20.50, 20.70, 80),
            _bar("10:25", 20.65, 20.90, 20.60, 20.85, 80),
        ]
        kb = {"kind": "测试", "level": 20.5, "dist_atr": 0.5}
        out = P.eval_ash_intraday(
            mins, "600000", 1.0, 20.0, basis_bars=None,
            account=200000, kb=kb)
        assert out["source"] == "回踩确认"
        assert out["action"]["via"] == "overshoot"
        assert out["action"]["S"] == 20.60
        assert out["action"]["entry"] == 20.85
        assert out["action"]["qty"] > 0

    def test_overshoot_without_pullback_does_not_buy(self):
        mins = [
            _bar("10:00", 20.2, 20.4, 20.1, 20.3, 100),
            _bar("10:05", 20.3, 20.45, 20.2, 20.35, 100),
            _bar("10:10", 20.35, 20.5, 20.3, 20.4, 100),
            _bar("10:15", 20.4, 21.6, 20.4, 21.5, 100),
            _bar("10:20", 21.4, 21.8, 21.3, 21.7, 100),
            _bar("10:25", 21.6, 21.9, 21.5, 21.8, 100),
        ]
        kb = {"kind": "测试", "level": 20.5, "dist_atr": 0.5}
        out = P.eval_ash_intraday(
            mins, "600000", 1.0, 20.0, account=200000, kb=kb)
        assert out["action"] is None
        assert "不买" in out["why"] or "尚未回踩" in out["why"]


class TestFrontScreenPriority:
    def test_pullback_surfaces_when_earlier_channels_silent(self):
        act = {"kind": "回踩确认", "entry": 20.85, "stop": 20.40,
               "qty": 500, "est_loss": 225, "pct_account": 10.0}
        got, src = BA.intraday_action_from_probe(
            {"ash_pullback": {"allow": True, "action": act}})
        assert got == act
        assert "回踩确认先手仓" in src

    def test_burst_beats_pullback(self):
        burst = {"kind": "先手1/3仓", "entry": 41.68, "stop": 39.89,
                 "qty": 100, "est_loss": 179, "pct_account": 8.0}
        pull = {"kind": "回踩确认", "entry": 20.0, "stop": 19.0,
                "qty": 100, "est_loss": 100, "pct_account": 4.0}
        got, src = BA.intraday_action_from_probe({
            "intraday": {"action": burst},
            "ash_pullback": {"action": pull},
        })
        assert got["entry"] == 41.68
        assert "量能突变" in src


class TestPlanHandoff:
    def test_gap_cap_hands_off(self):
        day, summary = _day(10.50, 10.0, 10.40)
        d = decide(day, summary, 10.0, 9.5, 15.0, account=100000)
        assert d["handoff"] is True
        assert d["action"] == "不买"
        assert d["arm_price"] is None
        assert d["cap_gates"]["tightest"] == "昨收+2%闸"

    def test_explicit_cap_does_not_hand_off(self):
        day, summary = _day(10.50, 10.0, 10.40)
        d = decide(day, summary, 10.0, 9.5, 15.0, cap=10.10, account=100000)
        assert d["handoff"] is False
        assert "不交接" in d["reason"]

    def test_far_target_r_gate_does_not_hand_off(self):
        # 昨收很高，+2% 闸不绑；R 闸把上限压在现价下方，目标仍远。
        day, summary = _day(10.80, 20.0, 10.70)
        d = decide(day, summary, 10.0, 9.0, 12.0, atr=0.5, account=100000)
        assert d["handoff"] is False
        assert d["cap_gates"]["tightest"] == "R闸"

    def test_stale_target_hands_off(self):
        day, summary = _day(10.05, 10.0, 10.00)
        assert target_is_stale(10.05, 10.80, 2.0) is True
        d = decide(day, summary, 10.0, 9.0, 10.80, atr=2.0, account=100000)
        assert d["handoff"] is True

    def test_vwap_still_blocks_handoff(self):
        day, summary = _day(10.50, 10.0, 9.70)
        d = decide(day, summary, 10.0, 9.5, 15.0, account=100000)
        assert d["handoff"] is False
        assert "远离" in d["reason"] or "追高" in d["reason"]

    def test_channel_rr_must_qualify(self):
        day, summary = _day(10.50, 10.0, 10.40)
        d = decide(day, summary, 10.0, 9.5, 15.0, account=100000)
        weak = {"source": "回踩确认", "why": "",
                "action": {"entry": 10.60, "stop": 10.20, "target1": None,
                           "qty": 100}}
        out = apply_channel_handoff(d, weak, account=100000)
        assert out["action"] == "不买"
        assert "低于" in out["reason"]

    def test_channel_with_room_buys(self):
        day, summary = _day(10.50, 10.0, 10.40)
        d = decide(day, summary, 10.0, 9.5, 15.0, account=200000)
        ok = {"source": "回踩确认", "why": "",
              "action": {"entry": 10.60, "stop": 10.20, "target1": 12.0,
                         "qty": 100}}
        out = apply_channel_handoff(d, ok, account=200000)
        assert out["action"] == "能买"
        assert out["channel_source"] == "回踩确认"
        assert out["limit_buy"] == 10.60
        assert out["channel_stop"] == 10.20
        assert out["stop"] == 9.5
