#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""天能重工 300569（2026-09）牛旗案例 —— **贴线待突破 + 地量小实体**特征测试。

运行：pytest tests/entry/test_300569_bull_flag.py -q

## 这个测试为什么存在

老罗 2026-09-26 发来 300569 日线截图：

> 「另一个牛旗的案例，**2026-09-23 收地量十字星，次日就有极大可能变盘，果然次日就画了 N 字，
>  那么次日过牛旗的点位就是买点**」

逐根核对确认它是**「贴线待突破」**的教科书样本 —— 引擎此前在这一档什么都报不出来：

    09-08 ~ 09-15  旗杆 6 根，4.62 → 5.25（+13.6%）
    09-16 ~ 09-23  旗面 6 根，高点逐级下移（09-16@5.24 → 09-21@5.09），回撤 58.7%
    09-23  收 4.94。旗面线**当日** 4.99 ⇒ 未站上（days_above_tl = 0）；
           但线每日下移 ⇒ **次日线值 4.940 与收盘 4.940 精确相等**。
           量 14,572,010 = 旗面最低量（= 旗面均量的 46.6%）；实体 0.02 = **0.12×ATR**（≤0.15）
    09-24  开盘 4.94（**恰好贴线、不低开**）、量 76,359,457 = 前 5 日均量的 **2.74×**、
           收 5.28（**+6.88%**）⇒ 过线第 1 根

## 引擎当时的缺陷：埋伏单在「最该出现的那一天」消失

`pre_breakout_flag_order` 的守卫是 `if last_c >= line_next: return None`，注释写着
「已站上 → 交给 flag_tl_break」。但**线每天在下移**，于是「收盘贴线」这一档必然踩中：

    旗面线当日 4.99 > 收盘 4.94  ⇒ 真的没站上
    旗面线次日 4.940 = 收盘 4.940 ⇒ 被判成「已站上」，埋伏单整档删掉

**越是贴着线（越接近突破），越容易落进这个死区** —— 也就是说这条守卫恰好在最该
给价的那一天失效。09-23 那天引擎给的是 `wait｜回踩仍在创新低`，报告里**没有任何**
「明天过 4.95 就是过牛旗」的信息。

## 但为什么**不能**简单放宽这条守卫（本案例最反直觉的实证发现）

实测 153 组真实日线、旗面成型未过线日 n=706（去重）：

| 组 | 定义 | n | 次日「过线率」 | 过线后 5 日中位 | 胜率 |
|---|---|---|---|---|---|
| A 正常 | 线仍在价上方，要涨上去才算过线 | 493 | 24.9% | **+1.30%** | 56.1% |
| B 贴线 | 线已下移到价上（本次 09-23） | 213 | **71.8%** | **−1.81%** | 43.8% |

B 组「过线率」高得离谱，是因为**线自己贴上来、不需要价格涨** —— 这是**廉价过线**，
买进去 5 日中位是**负的**。真正区分成败的是**量能**：

| 组 | 量能 | n | 过线后 5 日中位 | 胜率 |
|---|---|---|---|---|
| 贴线 | ≥1.5×近 5 日均量 | 19 | **+2.76%** | 52.6% |
| 贴线 | 缩量 | 134 | −1.83% | 42.5% |
| 正常 | ≥1.5× | 28 | +3.65% | 67.9% |
| 正常 | 缩量 | 95 | +0.70% | 52.6% |

⇒ 老罗 2026-09-26 定口径：**加「贴线待突破」预警 + 放量确认，不放宽守卫**
（`flag_knife_edge`）。buy-stop 表达不了量能条件，所以这一档给的是
「次日开盘不低开 **且** 量 ≥1.5×近 5 日均量」的盯盘条件。本例 09-24 恰好全中。

## 另一条口径：「地量小实体（十字星）」= 变盘临界，但**只是标注**

1511 个「大阳后缩量」实例：实体 ≤0.15×ATR 那组次日 P(涨≥2%) **31.3%** vs 其余 **26.5%**，
过线后 5 日中位 +1.00% vs −0.18%（滞后 5 日 t=+2.4）。方向在各档上单调，但
分档后样本小 ⇒ 只进 note / 报告，**不改 recommend**（`dry_small_body`）。

## 数据

`fixtures/300569_bars.json`：2026-02-12 → 2026-09-24 共 150 根，sina 前复权源，
逐根 o/h/l/c/v 原样，不合成、不插值。

## 月线拉黑（2026-09-26 晚改了范围）

引擎当日给 300569 打了**月线拉黑**（2026-03-31 月线超长射击之星，高 8.95、上影 77%）。
老罗 2026-09-26 早先表示「这个并不是它的真顶部，真顶部在 2021-12 月，现在只是做短线，
我举例而已，不是要买这个股」；**同日晚改了口径**：「**月线只看 50% 大阴线否决，
其他的月线不要干预日线级别的判断**」⇒ `month_one_star` / `month_star_pair` /
`month_merged_star` 只作标注、不再否决；只有 `month_super_yin`（50% 大阴线）仍拉黑。
本文件把**改后**的现状钉住：标注仍在、否决没了、09-24 的突破买点回来了。
"""
import io
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "gates")))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import top_signals as TS                                            # noqa: E402
from rule123 import (FLAG_LEN_MAX, FLAG_DRY_BODY_ATR, atr14,        # noqa: E402
                     build_ev, detect_bull_flag, dry_small_body,
                     flag_knife_edge, pivots, plan_entry,
                     pre_breakout_flag_order)

FIX = os.path.join(HERE, "fixtures", "300569_bars.json")

with open(FIX, encoding="utf-8") as _f:
    _SNAP = json.load(_f)
BARS_ALL = _SNAP["bars"]


def bars_upto(day):
    out = [b for b in BARS_ALL if b["d"] <= day]
    assert out, "夹具里没有 %s 之前的数据" % day
    return out


def snap_at(day):
    bars = bars_upto(day)
    atr_v = atr14(bars)
    Hs, _ = pivots(bars, w=3)
    return bars, atr_v, detect_bull_flag(bars, Hs, atr_v)


def plan_at(day):
    bars = bars_upto(day)
    ev, bars, meta = build_ev(bars, ticker="300569")
    assert ev is not None, meta
    return plan_entry(bars, ev), bars


def _bar(d, o, h, l, c, v=1_000_000):
    return {"d": d, "o": o, "h": h, "l": l, "c": c, "v": v}


# ───────────────────────── 形态本体 ─────────────────────────

def test_pole_is_the_0908_to_0915_rally():
    """旗杆 = 09-08 低 4.62 → 09-15 高 5.25，6 根，+13.6%。"""
    _b, _a, f = snap_at("2026-09-23")
    assert f is not None
    assert f["pole_d0"] == "2026-09-08", f["pole_d0"]
    assert f["pole_d1"] == "2026-09-15", f["pole_d1"]
    assert f["pole_end"] - f["pole_start"] + 1 == 6
    assert f["pole_lo"] == pytest.approx(4.62, abs=0.005)
    assert f["pole_hi"] == pytest.approx(5.25, abs=0.005)


def test_flag_line_is_drawn_from_declining_highs():
    """旗面线 = 09-16 高 5.24 → 09-21 高 5.09（下移）。"""
    _b, _a, f = snap_at("2026-09-23")
    assert f["pb"]["d"] == "2026-09-16" and f["pb"]["price"] == pytest.approx(5.24, abs=0.005)
    assert f["pa"]["d"] == "2026-09-21" and f["pa"]["price"] == pytest.approx(5.09, abs=0.005)
    assert f["pa"]["price"] < f["pb"]["price"]


def test_flag_retrace_is_58_7():
    """回撤 = (5.25−4.88)/(5.25−4.62) = 58.7% ≤ 66% 上限 ⇒ 形态成立。"""
    _b, _a, f = snap_at("2026-09-23")
    retr = (f["pole_hi"] - f["flag_low"]) / (f["pole_hi"] - f["pole_lo"])
    assert retr == pytest.approx(0.587, abs=0.005)
    assert f["flag_low"] == pytest.approx(4.88, abs=0.005)


@pytest.mark.parametrize("day,expect", [
    ("2026-09-22", 5),      # 旗面还在长（未过线）
    ("2026-09-23", 6),      # 突破前最后一根 = 09-23
    ("2026-09-24", 6),      # ★ 09-24 过线后**冻结**在 6，不再随时间增长
])
def test_flag_len_freezes_after_breakout(day, expect):
    """`flag_len` = 旗面自身长度（旗杆末端 → 突破前最后一根），突破后不再增长。"""
    _b, _a, f = snap_at(day)
    assert f is not None and f["flag_len"] == expect, f and f["flag_len"]


def test_hard_condition_1_holds():
    """硬条件①：旗面收盘不破旗杆「最后一根大阳」（09-15）最低点 4.75。

    旗面最低 4.88 ⇒ 余 +0.80×ATR（09-23 基准）；无盘中插破（undercut=False）。
    """
    _b, atr_v, f = snap_at("2026-09-23")
    assert f["anchor"]["d"] == "2026-09-15"
    assert f["anchor"]["price"] == pytest.approx(4.75, abs=0.005)
    assert f["undercut"] is False
    assert (f["flag_low"] - f["anchor"]["price"]) / atr_v == pytest.approx(0.80, abs=0.05)


def test_hard_condition_2_flag_len_within_limit():
    """硬条件②：旗面 6 根 ≪ 上限 20 根。"""
    _b, _a, f = snap_at("2026-09-23")
    assert f["flag_len"] <= FLAG_LEN_MAX == 20


# ───────────────────────── 09-23：地量 + 小实体 ─────────────────────────

def test_0923_is_the_lowest_volume_in_the_flag():
    """09-23 量 14,572,010 = 旗面最低，且只有旗面均量的 46.6%。"""
    b, _a, f = snap_at("2026-09-23")
    flag_bars = b[f["pole_end"] + 1:f["flag_end"] + 1]
    vols = [x["v"] for x in flag_bars]
    assert len(flag_bars) == 6
    assert b[-1]["v"] == min(vols) == 14_572_010
    assert b[-1]["v"] / (sum(vols) / len(vols)) == pytest.approx(0.466, abs=0.005)


def test_0923_is_dry_small_body():
    """09-23 实体 0.02 = 0.12×ATR ≤ 0.15 ⇒ 命中「地量小实体（十字星）」。"""
    b, atr_v, _f = snap_at("2026-09-23")
    d = dry_small_body(b, atr_v)
    assert d is not None
    assert d["d"] == "2026-09-23"
    assert d["body"] == pytest.approx(0.02, abs=0.005)
    assert d["body_atr"] == pytest.approx(0.123, abs=0.005)
    assert d["body_atr"] <= FLAG_DRY_BODY_ATR == 0.15


def test_0922_is_not_dry_small_body():
    """09-22 实体 0.12 = 0.75×ATR ⇒ 不算小实体（这道前兆不是「每天都有」）。"""
    b, atr_v, _f = snap_at("2026-09-22")
    assert dry_small_body(b, atr_v) is None


def test_dry_small_body_threshold_boundary():
    """阈值口径：实体/ATR ≤ 0.15 命中，> 0.15 不命中（边界两侧各一例）。"""
    atr_v = 1.0
    small = [_bar("2026-01-01", 10.0, 10.4, 9.8, 10.10)]     # body 0.10 = 0.10×ATR
    big = [_bar("2026-01-01", 10.0, 10.4, 9.8, 10.20)]       # body 0.20 = 0.20×ATR
    assert dry_small_body(small, atr_v) is not None
    assert dry_small_body(big, atr_v) is None


# ───────────────────────── 09-23：贴线待突破 ─────────────────────────

def test_0923_is_the_knife_edge_tie():
    """★ 案例核心：旗面线当日 4.99、次日 4.940，而收盘 4.940 ⇒ 精确贴线。

    未站上（days_above_tl = 0），但次日线值 = 收盘价 ⇒ 次日只需不低开即算过线。
    """
    b, atr_v, f = snap_at("2026-09-23")
    assert f["days_above_tl"] == 0
    assert f["tl_now"] == pytest.approx(4.99, abs=0.005)
    assert b[-1]["c"] == pytest.approx(4.94, abs=0.005)
    ke = flag_knife_edge(b, f, atr_v, b[-1]["c"])
    assert ke is not None
    assert ke["level"] == pytest.approx(4.94, abs=0.005)      # 次日线值 = 今收
    assert ke["gap_atr"] == pytest.approx(0.0, abs=0.02)
    assert ke["hard_stop"] == pytest.approx(4.78, abs=0.02)   # 线下 1.0×ATR
    assert ke["rvol_min"] == 1.5
    assert ke["dry"] is not None                              # 地量小实体一并标出


def test_0923_knife_edge_replaces_the_ambush_order():
    """同一根 K 线上，埋伏单为 None（被守卫吃掉），贴线预警顶上 —— 两者几何互斥。"""
    b, atr_v, f = snap_at("2026-09-23")
    assert pre_breakout_flag_order(b, f, atr_v, b[-1]["c"], None) is None
    assert flag_knife_edge(b, f, atr_v, b[-1]["c"]) is not None


def test_knife_edge_guards():
    """守卫：① 已站上不给；② 线掉到价下方 >1×ATR 不给（那是形态走完，不是贴线）。"""
    atr_v = 1.0
    # ① days_above_tl > 0
    flag_above = {"pb": {"i": 1, "price": 12.0, "d": "D1"},
                  "pa": {"i": 3, "price": 11.0, "d": "D3"},
                  "days_above_tl": 1, "flag_len": 4}
    b = [_bar("D%d" % i, 10, 10.5, 9.5, 10.2) for i in range(5)]
    assert flag_knife_edge(b, flag_above, atr_v, 10.2) is None
    # ② 线远在价下：pb 12 → pa 2（斜率 −5/根），次日线值远低于收盘
    flag_far = {"pb": {"i": 0, "price": 12.0, "d": "D0"},
                "pa": {"i": 2, "price": 2.0, "d": "D2"},
                "days_above_tl": 0, "flag_len": 4}
    b2 = [_bar("D%d" % i, 10, 10.5, 9.5, 10.2) for i in range(6)]
    assert flag_knife_edge(b2, flag_far, atr_v, 10.2) is None


def test_knife_edge_requires_flag_line_points():
    """测试桩只写 tl_now 时（无 pb/pa）必须安全返回 None，不抛异常。"""
    b = [_bar("D%d" % i, 10, 10.5, 9.5, 10.2) for i in range(5)]
    assert flag_knife_edge(b, {"tl_now": 10.0, "days_above_tl": 0}, 1.0, 10.2) is None
    assert flag_knife_edge(b, None, 1.0, 10.2) is None


def test_engine_note_carries_the_precursor():
    """09-23 引擎 mode 仍是 wait（老罗口径：不创新低属趋势买法，不改），
    但 note 里必须出现「地量小实体」前兆 —— 否则等于把那天当普通阴跌。"""
    p, _b = plan_at("2026-09-23")
    assert p.get("mode") == "wait"
    assert p.get("recommend") is False
    assert "地量小实体" in (p.get("note") or "")


def test_plan_attaches_knife_edge_on_0923():
    """plan_entry 出口挂 knife_edge（与 pre_breakout 同一出口，供报告/CLI 消费）。"""
    p, _b = plan_at("2026-09-23")
    assert p.get("pre_breakout") is None
    ke = p.get("knife_edge")
    assert ke is not None and ke["kind"] == "knife_edge"
    assert ke["level"] == pytest.approx(4.94, abs=0.005)


# ───────────────────────── 09-24：过线第 1 根 ─────────────────────────

def test_0924_is_the_breakout_day():
    """09-24 开 4.94（恰好贴线、不低开）、收 5.28（+6.88%）、量 2.74×前 5 日均量。"""
    b = bars_upto("2026-09-24")
    prev5 = [x["v"] for x in b[-6:-1]]
    assert b[-1]["d"] == "2026-09-24"
    assert b[-1]["o"] == pytest.approx(4.94, abs=0.005)       # 开盘 = 旗面线 ⇒ 不低开
    assert b[-1]["c"] == pytest.approx(5.28, abs=0.005)
    assert b[-1]["h"] == pytest.approx(5.35, abs=0.005)
    assert (b[-1]["c"] / b[-2]["c"] - 1) * 100 == pytest.approx(6.88, abs=0.05)
    assert b[-1]["v"] / (sum(prev5) / len(prev5)) == pytest.approx(2.74, abs=0.05)


def test_0924_is_day_1_above_the_line_and_no_knife_edge():
    """过线第 1 根 ⇒ 新鲜窗口内；贴线预警消失（已站上，交给 flag_tl_break）。"""
    b, atr_v, f = snap_at("2026-09-24")
    assert f["days_above_tl"] == 1
    assert f["tl_now"] == pytest.approx(4.94, abs=0.005)
    assert f["flag_len"] == 6
    assert flag_knife_edge(b, f, atr_v, b[-1]["c"]) is None


# ───────────────────────── CLI ─────────────────────────

@pytest.mark.parametrize("day,state,rc", [
    ("2026-09-22", "knife_edge", 1),
    ("2026-09-23", "knife_edge", 1),
    ("2026-09-24", "broken", 2),
])
def test_cli_state_and_exit_code(day, state, rc):
    import bull_flag_check as BF
    v = BF.bull_flag_verdict(bars_upto(day), ticker="300569")
    assert v["state"] == state, v["state"]
    assert v["exit_code"] == rc


def test_cli_advice_on_0923_states_the_volume_condition():
    """贴线档的处置必须写清「开盘不低开 + 放量」两个条件，并给出硬止损。"""
    import bull_flag_check as BF
    v = BF.bull_flag_verdict(bars_upto("2026-09-23"), ticker="300569")
    a = v["advice"]
    assert "1.5" in a and "量" in a
    assert "4.78" in a                     # 硬止损
    assert "地量小实体" in a
    txt = "\n".join(BF._render("300569", "天能重工", "inline", bars_upto("2026-09-23"), v))
    assert "贴线待突破" in txt


def test_cli_is_read_only(tmp_path, monkeypatch):
    """CLI 只回答「有没有牛旗、过线没有」，不写文件、不下单。"""
    import bull_flag_check as BF
    before = set(os.listdir(HERE))
    v = BF.bull_flag_verdict(bars_upto("2026-09-23"), ticker="300569")
    assert isinstance(v, dict)
    assert set(os.listdir(HERE)) == before


# ───────────────────────── 月线拉黑口径（2026-09-26 晚改了范围）─────────────────────────

def test_monthly_one_star_still_recorded():
    """月线形态本身照旧识别、照旧落库（2026-03 超长射击之星，高 8.95）——**没被删掉**。

    老罗 2026-09-26 早先：「这个并不是它的真顶部，真顶部在 2021-12 月，现在只是做短线，
    我举例而已，不是要买这个股」；**当日晚改口径**：「**月线只看 50% 大阴线否决，
    其他的月线不要干预日线级别的判断**」⇒ `month_one_star` 保留为**标注**，不再否决。
    """
    hit = TS.month_one_star(BARS_ALL)
    assert hit is not None and hit["state"] == "blacklist"
    assert hit["month"] == "2026-03"
    assert hit["peak_high"] == pytest.approx(8.95, abs=0.01)
    assert "只做空不做多" in hit["note"]


def test_monthly_one_star_no_longer_blacklists_the_engine():
    """★ 新口径下 `month_one_star` 不再压 `recommend`：09-24（过牛旗那天）买点回来了。

    改口径前的现状是 `recommend=False` + 「拉黑｜」—— 一个**真实突破买点被月线越权拦掉**。
    撤掉后 09-24 给 `mode=platform_break` / `recommend=True`。
    """
    p, _b = plan_at("2026-09-24")
    assert p.get("recommend") is True
    assert p.get("mode") == "platform_break"
    assert "拉黑" not in (p.get("verdict") or "")
    assert "拉黑" not in (p.get("note") or "")
    # 标注仍在，只是不再否决
    assert isinstance(p.get("month_one_star"), (dict, type(None)))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
