# -*- coding: utf-8 -*-
"""买区可执行带 + 次日追价授权（2026-11-05 老罗拍板 A+B）回归。

背景（74 只 A 股池实测）：
  · `in_zone` 只按 [lo,hi] 判，赔率却要 R≥1.5；买区上沿 = level+1.0×ATR 比
    「R≥1.5 的价」宽 ⇒ 挂单价被 (t1+1.5×hard)/2.5 压到现价之下：16/23，
    中位 −3.22%、最深 −6.07%；
  · 次日小高开即落进开盘 ③ 档，而 `next_day_chase` 只发给 in_zone=False 的票
    （0/23 有授权）⇒ 最该追的买区内票反而「0 股等回踩」；
  · 6/23 被压到买区下沿之下，与「跌破买区下沿=突破未成立，不接」矛盾。

A = 买区内 R 不足时补次日追价授权（半仓 + 止损按 R 反推收紧）；
B = 挂单价不得落到买区下沿之下（钉 lo 并标 R；连 R=1.0 都不够才撤单）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from rule123 import attach_zone_exec  # noqa: E402
from probe_intraday import room_and_cap  # noqa: E402


def _bars(last=100.0, n=30):
    out = []
    for i in range(n):
        c = last * (1 - 0.004 * (n - 1 - i))
        out.append({"d": "2026-10-%02d" % ((i % 28) + 1),
                    "o": round(c * 0.998, 2), "h": round(c * 1.008, 2),
                    "l": round(c * 0.992, 2), "c": round(c, 2),
                    "v": 1_000_000})
    return out


def _plan(lo=95.0, hi=102.0, hard=90.0, t1=110.0, mode="platform_break",
          rec=True, chase=None, sea_sky=False, cz=None):
    bz = {"primary_lo": lo, "primary_hi": hi, "in_zone": True, "hard_stop": hard,
          "level": lo}
    if chase is not None:
        bz["next_day_chase"] = chase
    p = {"recommend": rec, "mode": mode, "buy_zone": bz,
         "stop_plan": {"hard": hard},
         "targets": {"target1": t1, "sea_sky": sea_sky}}
    return p


# ------------------------------------------------------------------ A 授权
def test_capped_zone_gets_next_day_chase():
    """cap < hi ⇒ 生成次日追价授权：锚 = min(hi, 前收×1.02)、半仓、止损收紧。"""
    p = _plan()                      # cap = (110+1.5×90)/2.5 = 98 < hi 102
    out = attach_zone_exec(p, _bars(100.0), 2.0)
    z = out["buy_zone"]
    ch = z.get("next_day_chase") or {}
    assert ch, "capped 的买区必须拿到追价授权（否则次日 ③ 档退回 0 股等回踩）"
    assert ch["limit"] == pytest.approx(min(102.0, 102.0)), "锚 = min(买区上沿, 前收×1.02)"
    assert ch["size_ratio"] == 0.5, "追价档只给半仓"
    assert ch["stop"] < ch["limit"], "止损必须落在授权价之下"
    assert ch["stop"] > 90.0 - 1e-9, "止损不得比原硬止损更宽"
    assert ch["valid_for"] == "仅次日"
    assert z["exec_lo"] == pytest.approx(98.0), "可执行带下沿 = max(lo, cap)"


def test_cap_above_hi_needs_no_chase():
    """买区内全部合格（cap ≥ hi）⇒ 不加授权，避免无意义改写六档。"""
    p = _plan(t1=140.0)              # cap = (140+135)/2.5 = 110 > hi 102
    out = attach_zone_exec(p, _bars(100.0), 2.0)
    assert not out["buy_zone"].get("next_day_chase")


def test_ma_reclaim_break_is_skipped():
    """T0 是趋势单、probe 不设买入上限 ⇒ 同口径跳过，不得生「授权价=上沿」的假档。"""
    p = _plan(mode="ma_reclaim_break")
    out = attach_zone_exec(p, _bars(100.0), 2.0)
    assert not out["buy_zone"].get("next_day_chase")
    assert "exec_lo" not in out["buy_zone"]


def test_existing_chase_not_overwritten():
    """强突破/流星砸坑已给授权 ⇒ 不覆盖（它们带自己的止损与结构锚）。"""
    old = {"limit": 101.0, "stop": 97.0, "size_ratio": 0.5, "src": "强突破"}
    p = _plan(chase=old)
    out = attach_zone_exec(p, _bars(100.0), 2.0)
    assert out["buy_zone"]["next_day_chase"] is old


# ------------------------------------------------------------------ B 地板
def test_cap_below_lo_pins_to_zone_floor():
    """cap < lo 但 lo 处 R≥1.0 ⇒ 钉在买区下沿，不再挂买区之外的价。"""
    p = _plan(t1=100.0)              # cap = (100+135)/2.5 = 94 < lo 95；r_lo=1.0
    out = attach_zone_exec(p, _bars(100.0), 2.0)
    z = out["buy_zone"]
    assert out["recommend"] is True, "R=1.0 属「结构失真」区间，不得一刀切撤单"
    assert z["exec_lo"] == pytest.approx(95.0), "下沿必须钉在买区下沿，不得更低"


def test_no_odds_in_zone_cancels_recommend():
    """连 lo 处都够不上 R=1.0 ⇒ 买区内确无赔率价，撤单并写明所需价。"""
    p = _plan(t1=98.0)               # cap = 93.2 < 95；r_lo = 3/5 = 0.6 < 1.0
    out = attach_zone_exec(p, _bars(100.0), 2.0)
    assert out["recommend"] is False
    assert "无合格赔率价" in out["verdict"]
    assert out["buy_zone"]["no_odds_in_zone"] == pytest.approx(93.2, abs=0.01)
    assert not out["buy_zone"].get("next_day_chase")


# ------------------------------------------------------- 海阔天空（赔率闸无锚）
def test_sea_sky_carries_to_buy_zone_and_skips_cap():
    """海阔天空没有固定目标 ⇒ 赔率闸无锚，probe 不得用兜底枢轴高压价。"""
    p = _plan(t1=None, sea_sky=True)
    out = attach_zone_exec(p, _bars(100.0), 2.0)
    assert out["buy_zone"].get("sea_sky") is True, "sea_sky 必须带到 buy_zone"
    assert not out["buy_zone"].get("next_day_chase")
    rc = room_and_cap(_bars(100.0), out["buy_zone"], "line_pullback", 2.0)
    assert rc["cap"] is None, "无锚时不得给 cap（视涯 688781 曾因此被压 −6.07%）"
    assert rc.get("sea_sky") is True


def test_probe_uses_rule123_hard_stop():
    """probe 必须复用 z["hard_stop"]，不得自己重跑 stop_plan 造成 hard 分叉。"""
    bars = _bars(100.0)
    z = {"primary_lo": 95.0, "primary_hi": 102.0, "level": 95.0,
         "hard_stop": 90.0, "target1": 110.0, "in_zone": True}
    rc = room_and_cap(bars, z, "platform_break", 2.0)
    assert rc["hard"] == pytest.approx(90.0), "必须沿用 rule123 的 hard_stop"
    assert rc["cap"] == pytest.approx(98.0), "cap = (t1+1.5×hard)/2.5"
