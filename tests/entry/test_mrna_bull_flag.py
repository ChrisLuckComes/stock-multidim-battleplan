#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MRNA 2026-08/09 牛旗（上升旗形）案例 —— **口径特征测试**（characterization test）。

运行：pytest tests/entry/test_mrna_bull_flag.py -q

## 这个测试为什么存在

老罗 2026-09-26 发来 MRNA 日线截图（1D）：「增加识别突破后的旗形整理案例-牛旗的图形
MRNA，过牛旗即是买点」。逐根核对后确认这段结构是**教科书级的牛旗**：

    08-19  旗杆：前收 62.96 → 收 174.38（**+176.97%**），量 199.3M（HVE，20日均量的十几倍）
    08-20 ~ 09-16  旗面：19 根。高点逐级下移（08-25@161.37 → 09-02@156.42 → 09-11@149.73
           → 09-16@148.93），量从 99.5M 一路缩到 **7.9~15.4M**；旗面最低 128.61（08-20）
           回撤 = (176.66−128.61)/(176.66−53.71) = **39.1%** ≤ 旗杆 2/3  ⇒ 形态成立
    09-17  过牛旗：收 158.07（**+8.55%**）站上旗面下降线 145.28，量 19.2M 放大
    09-21  继续：+12.27% 到 172.94

## 引擎当时的两个缺陷（本测试钉住的就是它们）

**① 旗形在突破的第二天整体消失。** `detect_bull_flag` 里 `flag_len = n - 1 - pe`
是「旗杆末端到**今天**」的距离，会随时间无限增长，撞上「旗面 ≤20 根」这道闸门就被判死：

    09-16 flag_len=19 → 通过      09-17（过线当天）flag_len=20 → 通过
    09-18 flag_len=21 → **返回 None**  ⇒ 形态在最该被看见的第二天没了

更要命的是它与下游规则**自相矛盾**：`flag_tl_break` 要求 `days_above_tl ∈ [1,3]`
（3 根新鲜窗口），而要在线上连续 3 根，flag_len 至少 21 ⇒ 对该窗口**不可达**。
修法是把 `flag_len` 改成**旗面自身的长度**（旗杆末端 → 突破前最后一根），
稳定性由 `days_above_tl` 单独负责。改后旗面长度恒为 **19**。

**② 牛旗在 T1 里排在 W底之后。** MRNA 09-17 当日引擎同时识别出两个 T1 形态，
按旧序「平台→W底→旗形→沿线」取了 **W底颈线突破**（颈线 156.42，左底 08-28@133.33 /
右底 09-10@130.38），而「过牛旗」的真实触发位是旗面线 145.28。老罗 2026-09-26 定：
**牛旗与平台突破并列 T1**，排在 W底 之前。技术理由除「同一件事的两种几何」（整理段
上沿水平 vs 下倾）外，还有**形态尺度** —— MRNA 这个 W底（9 根）是**嵌在旗面内部**
的小结构（旗面 19 根），大结构应压小结构。

## 数据

`fixtures/MRNA_bars.json`：2025-11-03 → 2026-09-25 共 225 根，取自 nasdaq 源，
逐根 o/h/l/c/v 原样，不合成、不插值。
"""
import io
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from rule123 import (atr14, build_ev, detect_bull_flag, detect_w_bottom,  # noqa: E402
                     plan_entry, pivots)

FIX = os.path.join(HERE, "fixtures", "MRNA_bars.json")

with open(FIX, encoding="utf-8") as _f:
    _SNAP = json.load(_f)
BARS_ALL = _SNAP["bars"]


def bars_upto(day):
    """到 `day` 收盘为止的日线（含当日）。"""
    out = [b for b in BARS_ALL if b["d"] <= day]
    assert out, "夹具里没有 %s 之前的数据" % day
    return out


def flag_at(day):
    bars = bars_upto(day)
    Hs, _ = pivots(bars, w=3)
    return detect_bull_flag(bars, Hs, atr14(bars))


def plan_at(day):
    bars = bars_upto(day)
    ev, bars, meta = build_ev(bars, ticker="MRNA")
    assert ev is not None, meta
    return plan_entry(bars, ev), bars


# ───────────────────────── 形态本体 ─────────────────────────

def test_pole_is_the_0819_explosion():
    """旗杆 = 08-06 → 08-19，涨幅 +228.9%（起 53.71 → 止 176.66）。"""
    f = flag_at("2026-09-17")
    assert f is not None
    assert f["pole_d1"] == "2026-08-19", f["pole_d1"]
    assert f["pole_hi"] == pytest.approx(176.66, abs=0.01)
    assert f["pole_hi"] / f["pole_lo"] - 1 > 1.5          # 至少 +150%


def test_flag_retrace_is_shallow():
    """旗面回撤 39.1% ≤ 旗杆 2/3（「回撤深度是旗形与通道的分水岭」）。

    注意旗面最低是 **08-20 的 128.61**，不是 09-10 的 130.38 —— 旗杆次日的长上影
    回吐最深，取 min(low) 必须含它。
    """
    f = flag_at("2026-09-17")
    lows = [b["l"] for b in bars_upto("2026-09-16")
            if b["d"] > f["pole_d1"]]
    assert min(lows) == pytest.approx(128.61, abs=0.01)
    retrace = (f["pole_hi"] - min(lows)) / (f["pole_hi"] - f["pole_lo"])
    assert retrace < 2 / 3
    assert retrace == pytest.approx(0.391, abs=0.005)


def test_flag_line_is_drawn_from_declining_highs():
    """旗面线锚在「下降摆动高」上：09-02 高 156.42 → 09-11 高 149.73（后高低于前高）。"""
    f = flag_at("2026-09-17")
    assert f["pb"]["d"] == "2026-09-02" and f["pa"]["d"] == "2026-09-11"
    assert f["pa"]["price"] < f["pb"]["price"]
    assert f["flag_end"] is not None


# ─────────────────── 缺陷①：突破后不再消失、flag_len 稳定 ───────────────────

@pytest.mark.parametrize("day", ["2026-09-16", "2026-09-17", "2026-09-18",
                                 "2026-09-21", "2026-09-22", "2026-09-25"])
def test_flag_len_is_stable_at_19(day):
    """★ 修的就是这条：09-16 起旗面长度恒为 19，不再随突破后的站线根数增长。"""
    f = flag_at(day)
    assert f is not None, "%s：旗形不应消失（旧实现在 09-18 起返回 None）" % day
    assert f["flag_len"] == 19, (day, f["flag_len"])


def test_flag_survives_after_breakout():
    """★ 旧实现：09-18 flag_len=21 > 20 ⇒ None。今天必须还在。"""
    for day in ("2026-09-18", "2026-09-21", "2026-09-22", "2026-09-25"):
        assert flag_at(day) is not None, day


@pytest.mark.parametrize("day,days", [
    ("2026-09-16", 0), ("2026-09-17", 1), ("2026-09-18", 2),
    ("2026-09-21", 3), ("2026-09-22", 4), ("2026-09-25", 7),
])
def test_days_above_line_progresses(day, days):
    """过线根数逐日递进；这就是「新鲜窗口 ∈ [1,3]」的输入。"""
    f = flag_at(day)
    assert f["days_above_tl"] == days


# ─────────────────── 缺陷②：过牛旗 = 买点，且压过 W底 ───────────────────

def test_breakout_day_fires_flag_tl_break():
    """09-17 收 158.07 站上旗面线 145.28 ⇒ `flag_tl_break`，买区锚 = flag_tl。"""
    plan, bars = plan_at("2026-09-17")
    assert bars[-1]["c"] == pytest.approx(158.07, abs=0.01)
    assert plan["mode"] == "flag_tl_break", plan["mode"]
    assert plan["recommend"] is True
    assert plan["buy_zone"]["anchor"] == "flag_tl"
    assert plan["buy_zone"]["level"] == pytest.approx(145.28, abs=0.01)


def test_flag_outranks_w_bottom_on_the_same_bar():
    """★ 同一根 K 线上 W底**确实存在**，但牛旗必须赢（老罗 2026-09-26 定的 T1 内序）。"""
    bars = bars_upto("2026-09-17")
    Hs, Ls = pivots(bars, w=3)
    w = detect_w_bottom(bars, Hs, Ls, atr14(bars))
    assert w is not None, "W底候选应存在，否则本测试证明不了优先级"
    assert w["neckline"] == pytest.approx(156.42, abs=0.02)
    plan, _ = plan_at("2026-09-17")
    assert plan["mode"] == "flag_tl_break", (
        "牛旗应压过 W底；若这里变成 w_bottom_break，说明 T1 内序被改回去了")


def test_day_before_breakout_is_not_a_buy():
    """09-16 收 145.62 仍在旗面线 146.39 之下 ⇒ 未过牛旗，引擎不给出 flag_tl_break。"""
    f = flag_at("2026-09-16")
    assert f["days_above_tl"] == 0
    plan, _ = plan_at("2026-09-16")
    assert plan["mode"] != "flag_tl_break", plan["mode"]


def test_stale_breakout_is_not_re_labelled():
    """09-25 距旗面线已 4.69×ATR、过线第 7 根 ⇒ 超出 3 根新鲜窗口，不再标 flag_tl_break。"""
    f = flag_at("2026-09-25")
    assert f["days_above_tl"] == 7
    plan, _ = plan_at("2026-09-25")
    assert plan["mode"] != "flag_tl_break", plan["mode"]


# ───────────────────────── 独立 CLI 的契约 ─────────────────────────

def _cli(day):
    import bull_flag_check as BF
    return BF.bull_flag_verdict(bars_upto(day), ticker="MRNA")


@pytest.mark.parametrize("day,state,rc", [
    ("2026-09-16", "forming", 1),
    ("2026-09-17", "broken", 2),
    ("2026-09-21", "broken", 2),
    ("2026-09-25", "stale", 1),
])
def test_cli_state_and_exit_code(day, state, rc):
    v = _cli(day)
    assert v["hit"] is True
    assert v["state"] == state, (day, v["state"])
    assert v["exit_code"] == rc


def test_cli_reports_buy_zone_on_breakout_day():
    v = _cli("2026-09-17")
    assert v["state"] == "broken"
    assert v["buy_zone"]["anchor"] == "flag_tl"
    # 09-17 跳空越过旗面线（开 152.50 > 前收 145.62 + 0.2×ATR，低 149.50 > 线 145.28）
    # ⇒ 买区基准上移到缺口上沿，不买回补缺口。
    assert v["buy_zone"]["primary_lo"] == pytest.approx(149.50, abs=0.02)


def test_cli_is_read_only():
    """CLI 只回答「有没有牛旗、过线没有」，不得因为「无旗形」就说不该交易。"""
    import bull_flag_check as BF
    v = BF.bull_flag_verdict(bars_upto("2026-04-20"), ticker="MRNA")
    if not v["hit"]:
        assert "不是「过牛旗」买点" in v["advice"]
