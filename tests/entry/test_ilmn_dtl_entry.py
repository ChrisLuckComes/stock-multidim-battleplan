#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ILMN / 赛分科技 688758 —— **「牛旗 = 下降趋势线突破」的下沿买点**回归测试。

运行：pytest tests/entry/test_ilmn_dtl_entry.py -q

## 这个测试为什么存在

老罗 2026-09-26 发来 ILMN 日线截图并定下两条口径：

> 「ILMN **0915 就是牛旗买点**。如果形成牛旗，**不在旗形上沿买入，而是在平台顶
>  买入**的话，虽然都能说得通，但**没有了成本优势，盈亏比变得差很多**。赛分科技
>  就是因为没有识别到牛旗或者 T0 的站上均线，导致平台突破才买入 **32.1** 的成本
>  过高，**盈利缩水很多**。」
>
> 「**下降趋势线突破没有问题，牛旗一定是下降趋势线突破**。」

⇒ 牛旗在引擎里的**正落点不是 `detect_bull_flag` 的「标准旗形」，而是下降趋势线
（`down_tl`）通道**。旗面回撤太深（`retrace_too_deep`）或小高点成不了枢轴
（`no_declining_high`）时旗形判无效，**但下倾线依然存在、依然该买** —— 此前
`flag_knife_edge` 只挂在「有效旗形」上，这类票的贴线档整个丢掉。

## 缺口（改前实录）

    ILMN 线锚 08-27@231.81 → 09-03@221.76，线值每日下移 2.01：
      09-11  收 206.45  当日线 211.71  次日线 209.70  ⇒ 正常埋伏单 down_tl @210.13 ✓
      09-14  收 208.21  当日线 209.70  次日线 207.69  ⇒ 收盘已站上次日线值
             （gap **+0.06×ATR**）⇒ 旧守卫 `last_c >= line_next: return None`
             静默吞掉埋伏单，**看起来像「形态消失」**，knife_edge 也不触发
             （`_flag_ev` 是 None：旗形因 `retrace_too_deep` 判无效）
      09-15  开 **208.95**（≥207.69）· 量 3,148,966 = 前 5 日均量的 **1.694×**
             · 收 222.29（**+6.9%**）⇒ 老罗说的「0915 就是牛旗买点」

**成本差**：上沿 207.69 vs 平台突破买位 231.81（09-17 `platform_break`）
⇒ **低 10.4%**。到 09-24 高 277.95：+33.0% vs +19.9%。

赛分科技（A 股，同一形态）：

      09-11  收 26.81  引擎给 down_tl @27.92（hard 25.99）✓ —— 能吃到
      09-14  收 28.56  过线日 → mode=downtrend_tl_break，买区 27.83~29.73
      09-18  收 32.80  platform_break，平台沿 31.97 ⇒ 老罗实际买在 **32.10**
    成本差：27.92 vs 32.10 ⇒ **低 15.0%**（到 09-23 高 34.79：+24.6% vs +8.4%）。

⇒ 本文件钉住三件事：
  ① `dtl_knife_edge` 在「旗形无效但下倾线成立」时照样给贴线预警（ILMN 09-14）；
  ② 贴线档的两条成立条件（开盘不低开 **且** 放量）在 ILMN 09-15 恰好全中；
  ③ 「上沿买」进得了交付面：开盘作战方案 **⓪ 档** + probe 探针都打出来。

## 口径边界（不在本测试范围内）

`detect_bull_flag` 的闸门**一个都没改**（`FLAG_LEN_MAX` 20 仍是动能闸门、
`retrace_too_deep` 仍是标准旗形的判据）—— 本测试只是说明「旗形无效 ≠ 牛旗买法无效」，
两条通道并存。ANET（旗面 28 根）依旧判无效、暂不考虑（老罗 2026-09-26 裁定）。

## 数据

`fixtures/ILMN_bars.json`（275 根，2025-08-22→2026-09-25，yahoo 前复权）、
`fixtures/688758_bars.json`（400 根，2025-02-10→2026-09-24，sina 前复权），
逐根 o/h/l/c/v 原样，不合成、不插值。
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

import open_playbook as OP                                          # noqa: E402
import probe_intraday as PI                                         # noqa: E402
from rule123 import (atr14, build_ev, detect_bull_flag,             # noqa: E402
                     detect_down_trendline, dtl_knife_edge,
                     flag_knife_edge, line_val, pivots, plan_entry,
                     pre_breakout_line_order)

FIX_ILMN = os.path.join(HERE, "fixtures", "ILMN_bars.json")
FIX_SF = os.path.join(HERE, "fixtures", "688758_bars.json")

with open(FIX_ILMN, encoding="utf-8") as _f:
    ILMN_ALL = json.load(_f)["bars"]
with open(FIX_SF, encoding="utf-8") as _f:
    SF_ALL = json.load(_f)["bars"]


def _upto(all_bars, day):
    out = [b for b in all_bars if b["d"] <= day]
    assert out, "夹具里没有 %s 之前的数据" % day
    return out


def ilmn_at(day):
    return _upto(ILMN_ALL, day)


def sf_at(day):
    return _upto(SF_ALL, day)


def plan_of(bars, ticker):
    ev, bb, meta = build_ev(bars, ticker=ticker)
    assert ev is not None, meta
    return plan_entry(bb, ev)


def dtl_of(bars, atr_v):
    return detect_down_trendline(bars, atr_v, start_i=max(1, len(bars) - 45))


def _bar(d, o, h, l, c, v=1_000_000):
    return {"d": d, "o": o, "h": h, "l": l, "c": c, "v": v}


def _flat_bars(n, c=10.2):
    return [_bar("D%d" % i, 10, 10.5, 9.5, c) for i in range(n)]


# ───────────────────────── ① ILMN 形态本体 ─────────────────────────

def test_ilmn_line_is_the_declining_highs_from_the_screenshot():
    """线锚 = 08-27 高 231.81 → 09-03 高 221.76 —— 老罗截图里画的那条。"""
    b = ilmn_at("2026-09-14")
    atr_v = atr14(b)
    alt = dtl_of(b, atr_v)
    assert alt is not None
    assert b[alt["pb"]["i"]]["d"] == "2026-08-27"
    assert b[alt["pa"]["i"]]["d"] == "2026-09-03"
    assert alt["pb"]["price"] == pytest.approx(231.81, abs=0.005)
    assert alt["pa"]["price"] == pytest.approx(221.76, abs=0.005)
    assert alt["pa"]["price"] < alt["pb"]["price"], "必须是下移线"


def test_ilmn_line_moves_down_by_about_two_points_a_day():
    """线每日下移 ≈2.01 —— 正是「贴线死区」的成因（线自己贴上来）。"""
    b = ilmn_at("2026-09-14")
    atr_v = atr14(b)
    alt = dtl_of(b, atr_v)
    p_b = (alt["pb"]["i"], alt["pb"]["price"])
    p_a = (alt["pa"]["i"], alt["pa"]["price"])
    n = len(b)
    d1 = line_val(p_b, p_a, n - 1) - line_val(p_b, p_a, n - 2)
    assert d1 == pytest.approx(-2.01, abs=0.02), d1


def test_ilmn_0914_flag_is_invalid_yet_the_line_stands():
    """09-14 `detect_bull_flag` 判 `retrace_too_deep` —— 旗形无效。

    这正是本案例的**要害**：旗形判无效 ≠ 牛旗买法无效。老罗定的「牛旗一定是
    下降趋势线突破」说明买法挂在下倾线上，与旗形是否「标准」无关。
    """
    b = ilmn_at("2026-09-14")
    atr_v = atr14(b)
    Hs, _ = pivots(b, 3)
    f = detect_bull_flag(b, Hs, atr_v, diagnostic=True)
    assert f and not f.get("valid")
    assert f["invalid_reason"] == "retrace_too_deep", f
    # 但旗形**无效**的同时下倾线是成立的
    assert dtl_of(b, atr_v) is not None


# ───────────────────────── ② 贴线档：改前 vs 改后 ─────────────────────────

def test_ilmn_0911_has_the_normal_ambush_order():
    """09-11：线仍在价上方 ⇒ 正常埋伏单 down_tl @210.13（hard 201.08）。"""
    p = plan_of(ilmn_at("2026-09-11"), "ILMN")
    pb = p.get("pre_breakout") or {}
    assert pb.get("anchor") == "down_tl", pb
    assert pb["trigger"] == pytest.approx(210.13, abs=0.01)
    assert pb["level"] == pytest.approx(209.70, abs=0.01)
    assert pb["hard_stop"] == pytest.approx(201.08, abs=0.01)
    assert p.get("knife_edge") is None, "线在价上方时不该走贴线档"


def test_ilmn_0914_is_the_knife_edge_tie():
    """09-14 贴线日：knife_edge 命中 down_tl 207.69，gap +0.06×ATR、hard 199.25。"""
    p = plan_of(ilmn_at("2026-09-14"), "ILMN")
    ke = p.get("knife_edge")
    assert ke, "09-14 是贴线日，必须给「贴线待突破」预警"
    assert ke["kind"] == "knife_edge"
    assert ke["anchor"] == "down_tl"
    assert ke["level"] == pytest.approx(207.69, abs=0.01)
    assert ke["gap_atr"] == pytest.approx(0.06, abs=0.011)
    assert ke["hard_stop"] == pytest.approx(199.25, abs=0.01)
    assert ke["rvol_min"] == pytest.approx(1.5, abs=1e-9)
    assert ke["tier"] == "T1"


def test_ilmn_0914_the_old_ambush_order_is_silently_gone():
    """同一根 K 线：埋伏单**没了**（线贴上来被守卫吃掉）—— 缺口本体。

    这一段是「为什么要新增贴线档」的直接证据：09-14 有埋伏单的**关键位**，
    却报不出来。改前 `pre_breakout` 为 None 且没有 knife_edge ⇒ 报告里
    那天只剩「wait」，读者以为形态消失了。
    """
    p = plan_of(ilmn_at("2026-09-14"), "ILMN")
    assert p.get("pre_breakout") is None, "09-14 埋伏单确实被守卫吞掉了"
    assert p.get("mode") == "wait"
    assert p.get("recommend") is False
    # 但贴线档把「次日怎么买」补回来了
    assert (p.get("knife_edge") or {}).get("level") is not None


def test_ilmn_0914_note_carries_the_cost_advantage():
    """note 必须写出**上沿买**这件事与量能条件，否则又是一句「wait」。"""
    p = plan_of(ilmn_at("2026-09-14"), "ILMN")
    n = p["knife_edge"]["note"]
    assert "下降趋势线" in n
    assert "过牛旗" in n
    assert "量" in n and "近5日均量" in n
    assert "在上沿买" in n and "平台突破" in n, "必须点明成本优势来自上沿买"


def test_ilmn_0915_satisfies_both_conditions():
    """09-15 开盘 208.95 ≥ 207.69 且量 1.694× ≥1.5 —— 老罗说的「0915 就是买点」。"""
    b = ilmn_at("2026-09-15")
    day = b[-1]
    assert day["d"] == "2026-09-15"
    assert day["o"] == pytest.approx(208.95, abs=0.005)
    assert day["c"] == pytest.approx(222.29, abs=0.005)
    # 前一根（09-14）算出的贴线位
    ke = plan_of(b[:-1], "ILMN")["knife_edge"]
    assert day["o"] >= ke["level"], "开盘不低开 —— 条件 ①"
    vol_ma5 = sum(x["v"] for x in b[-6:-1]) / 5.0
    assert day["v"] / vol_ma5 == pytest.approx(1.694, abs=0.01), "放量 —— 条件 ②"
    assert day["v"] / vol_ma5 >= ke["rvol_min"]


def test_ilmn_0915_engine_calls_it_downtrend_tl_break():
    """过线当天引擎给 `downtrend_tl_break` —— 老罗：「牛旗一定是下降趋势线突破」。"""
    p = plan_of(ilmn_at("2026-09-15"), "ILMN")
    assert p["mode"] == "downtrend_tl_break", p.get("mode")
    assert p["recommend"] is True
    # 已经过线，贴线档自然作废（gap 远超 1×ATR）
    assert p.get("knife_edge") is None


def test_ilmn_0917_platform_break_is_the_expensive_fallback():
    """09-17 platform_break 买位 231.81 vs 上沿 207.69 ⇒ 贵 10.4%（成本优势的量化）。"""
    p = plan_of(ilmn_at("2026-09-17"), "ILMN")
    assert p["mode"] == "platform_break"
    z = p["buy_zone"]
    assert z["level"] == pytest.approx(231.81, abs=0.01)
    line = 207.69
    assert (z["level"] - line) / z["level"] == pytest.approx(0.104, abs=0.005)


# ───────────────────────── ③ 赛分科技（A 股同口径） ─────────────────────────

def test_688758_0911_has_the_ambush_order_at_27_92():
    """赛分 09-11 引擎给 down_tl @27.92（hard 25.99）—— 「能吃到的那张单」。"""
    p = plan_of(sf_at("2026-09-11"), "688758")
    pb = p.get("pre_breakout") or {}
    assert pb.get("anchor") == "down_tl", pb
    assert pb["trigger"] == pytest.approx(27.92, abs=0.01)
    assert pb["hard_stop"] == pytest.approx(25.99, abs=0.01)


def test_688758_0914_is_the_cross_day_not_a_knife_edge():
    """赛分 09-14 已过线（收 28.56 > 次日线值 27.69）⇒ 走 downtrend_tl_break，
    不该再给贴线档（贴线档只在**没有其他买点**时补位）。"""
    p = plan_of(sf_at("2026-09-14"), "688758")
    assert p["mode"] == "downtrend_tl_break"
    assert p["recommend"] is True
    z = p["buy_zone"]
    assert z["primary_lo"] == pytest.approx(27.83, abs=0.01)


def test_688758_0918_platform_break_costs_15_percent_more():
    """平台突破（老罗实际买 32.10）比下沿单 27.92 贵 15.0% —— 「盈利缩水」的来源。"""
    p = plan_of(sf_at("2026-09-18"), "688758")
    assert p["mode"] == "platform_break"
    plat = p["buy_zone"]["level"]
    assert plat == pytest.approx(31.97, abs=0.01)
    ambush = 27.92
    assert (32.10 - ambush) / 32.10 == pytest.approx(0.130, abs=0.005), "32.10 vs 27.92"
    assert (plat - ambush) / plat == pytest.approx(0.127, abs=0.005), "平台沿 vs 下沿"


# ───────────────────────── ④ 交付面：⓪ 档 + probe ─────────────────────────

_KE_ILMN = {"level": 207.69, "gap_atr": 0.06, "hard_stop": 199.25,
            "rvol_min": 1.5, "label": "下降趋势线（牛旗面线）",
            "line_from": "2026-08-27高231.81→2026-09-03高221.76"}


def test_open_playbook_inserts_band_zero():
    """开盘作战方案必须有 ⓪ 档，且写明两条条件 + 成本优势。"""
    pb = OP.build(last=208.21, entry=231.81, stop=227.22, target=277.95,
                  atr=8.44, code="ILMN", name="Illumina", market="US",
                  knife_edge=_KE_ILMN)
    b0 = pb["bands"][0]
    assert b0["key"] == "knife_edge"
    assert "贴线待突破" in b0["title"]
    assert b0["tone"] == "on"
    cond = OP._strip(b0["cond"])
    assert "207.69" in cond and "1.5" in cond and "近5日均量" in cond
    # 上界 = 异常跳空参照线（美股 1.08×），免与涨停档重叠
    assert "224.87" in cond
    act = OP._strip(b0["act"])
    assert "低 10.4%" in act, "必须给出相对原挂单的成本优势"
    assert "在上沿买" in act and "平台突破" in act
    assert "缩量" in act, "条件档必须写明缩量的处理"
    assert "199.25" in OP._strip(b0["stop"])


def test_open_playbook_band_zero_yields_to_a_lower_manual_entry():
    """人工把挂单价改到比贴线位更低时，⓪ 档自动让位（不打架、不重复报价）。"""
    pb = OP.build(last=208.21, entry=200.00, stop=195.00, target=277.95,
                  atr=8.44, code="ILMN", name="Illumina", market="US",
                  knife_edge=_KE_ILMN)
    assert pb["bands"][0].get("key") != "knife_edge"
    assert pb.get("knife_edge") is None


def test_open_playbook_without_knife_edge_is_unchanged():
    """不传 knife_edge 时行为与老版一致（纯增量）。"""
    pb = OP.build(last=208.21, entry=231.81, stop=227.22, target=277.95,
                  atr=8.44, code="ILMN", name="Illumina", market="US")
    assert pb.get("knife_edge") is None
    assert all(b.get("key") != "knife_edge" for b in pb["bands"])


def test_from_analysis_reads_knife_edge_from_plan():
    """`from_analysis` 直接取 `plan["knife_edge"]`（A股/美股同一入口）。"""
    a = {"plan": {"knife_edge": _KE_ILMN, "last": 208.21, "sym": "ILMN"},
         "meta": {"code": "ILMN", "market": "US", "basis_close": 208.21},
         "odds_recommend": {"entry": 231.81, "stop": 227.22, "qty": 0},
         "struct": {"atr14": 8.44}}
    pb = OP.from_analysis(a, {})
    assert pb["bands"][0]["key"] == "knife_edge"
    assert pb["knife_edge"]["level"] == pytest.approx(207.69, abs=0.01)


def test_probe_prints_the_condition(capsys=None):
    """probe 探针要打出「贴线待突破」三行（条件档不能只活在引擎里）。"""
    p = plan_of(ilmn_at("2026-09-14"), "ILMN")
    import contextlib
    import io as _io
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        PI._print_knife_edge(p)
    txt = buf.getvalue()
    assert "贴线待突破" in txt
    assert "207.69" in txt and "1.5" in txt and "近5日均量" in txt
    assert "199.25" in txt
    assert "在上沿买" in txt


def test_probe_silent_when_no_knife_edge():
    """没有贴线档时不刷屏。"""
    import contextlib
    import io as _io
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        PI._print_knife_edge({})
        PI._print_knife_edge({"knife_edge": None})
    assert buf.getvalue() == ""


# ───────────────────────── ⑤ 守卫与优先级 ─────────────────────────

def _dtl_ev(a_i, a_px, b_i, b_px):
    return {"down_tl": {"src": "local_highs",
                        "a": {"i": a_i, "price": a_px},
                        "b": {"i": b_i, "price": b_px}}}


def test_dtl_knife_edge_hits_when_pinned():
    """线值 90（b=5@120 → a=10@110 外推到 20 根）、收盘 90.5、ATR 5 ⇒ 命中。"""
    b = _flat_bars(20)
    ev = _dtl_ev(10, 110.0, 5, 120.0)
    ke = dtl_knife_edge(b, ev, 5.0, 90.5)
    assert ke and ke["anchor"] == "down_tl"
    assert ke["level"] == pytest.approx(90.0, abs=0.01)
    assert ke["gap_atr"] == pytest.approx(0.1, abs=0.01)


def test_dtl_knife_edge_guards():
    """守卫：① 线仍在价上方 ⇒ None；② 线掉到价下方 >1×ATR ⇒ None；
    ③ 不是下移线 ⇒ None；④ src 不匹配 / 空 ev ⇒ None。"""
    b = _flat_bars(20)
    ev = _dtl_ev(10, 110.0, 5, 120.0)          # 线值 90
    assert dtl_knife_edge(b, ev, 5.0, 85.0) is None      # ① 85 < 90
    assert dtl_knife_edge(b, ev, 5.0, 100.0) is None     # ② gap = 2.0×ATR
    assert dtl_knife_edge(b, ev, 5.0, 90.5) is not None  # 边界内仍命中
    up = _dtl_ev(10, 130.0, 5, 120.0)                    # ③ a 比 b 高 = 上移线
    assert dtl_knife_edge(b, up, 5.0, 130.0) is None
    bad = {"down_tl": {"src": "other", "a": {"i": 10, "price": 110.0},
                       "b": {"i": 5, "price": 120.0}}}
    assert dtl_knife_edge(b, bad, 5.0, 90.5) is None     # ④ src 不匹配
    assert dtl_knife_edge(b, {}, 5.0, 90.5) is None
    assert dtl_knife_edge(b, None, 5.0, 90.5) is None
    assert dtl_knife_edge(b, ev, 0, 90.5) is None        # ATR 非法


def test_dtl_knife_edge_requires_line_points():
    """只有 src 没有 a/b 时必须安全返回 None，不抛异常。"""
    b = _flat_bars(20)
    assert dtl_knife_edge(b, {"down_tl": {"src": "local_highs"}}, 5.0, 90.5) is None
    assert dtl_knife_edge(b, {"down_tl": {"src": "local_highs",
                                          "a": {"i": 10, "price": 110.0}}},
                          5.0, 90.5) is None


def test_flag_version_wins_when_both_exist():
    """旗面线版（`flag_knife_edge`）优先 —— 它锚的是旗形自己的线，往往更低。"""
    b = ilmn_at("2026-09-14")
    atr_v = atr14(b)
    Hs, _ = pivots(b, 3)
    f = detect_bull_flag(b, Hs, atr_v)          # 无效 ⇒ None
    assert f is None
    # 旗形有效时（300569 已由另一文件覆盖），flag 版先跑；这里只验证
    # 「旗形无效 ⇒ flag 版返回 None，于是轮到 dtl 版补位」这条链路。
    assert flag_knife_edge(b, f, atr_v, b[-1]["c"]) is None
    assert dtl_knife_edge(b, build_ev(b, ticker="ILMN")[0], atr_v, b[-1]["c"]) is not None


def test_ambush_order_still_returns_none_when_tied():
    """`pre_breakout_line_order` 的守卫**不改**（老罗：加预警，不放宽守卫）。"""
    b = ilmn_at("2026-09-14")
    atr_v = atr14(b)
    ev = build_ev(b, ticker="ILMN")[0]
    assert pre_breakout_line_order(b, ev, atr_v, b[-1]["c"]) is None


def test_no_change_to_the_flag_gates():
    """`detect_bull_flag` 的闸门一个都没动（本案例只加通道，不改标准旗形口径）。"""
    import rule123 as R
    assert R.FLAG_LEN_MAX == 20
    assert R.FLAG_LEN_MIN == 3
    assert R.KNIFE_EDGE_RVOL == 1.5
    assert R.FLAG_DRY_BODY_ATR == 0.15
