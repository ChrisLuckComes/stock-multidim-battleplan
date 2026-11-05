# -*- coding: utf-8 -*-
"""★ 2026-11-05 深度 review 修复回归 fixture —— 纯离线、不触网、无全局补丁。

对应「主引擎及其分支」深挖后确证的 2 条 P0 + 4 条 P1：

  P0-A  chase 授权用 max() 掀翻「显式 cap / R闸 / 昨收+2%闸」三闸取严 ⇒ 越权抬 cap
        （上一轮 10 例 fixture 只测 dynamic_structure 单元与美股 chase，A股 decide 零覆盖）
  P0-B  engine_t1 只在 walls 新口径分支生效、且被逐档 need 二次否决；
        walls=[] 得「海阔天空」而 walls=None 得「有目标」⇒ 同输入反结论
  P1-1  next_day_chase 的 valid_for 全链无人消费、无生成日 ⇒ 授权无限期生效
  P1-2  并列向下/向上档只是 render 文案，两档没有各自可成交价与执行方式
  P1-3  做空「推荐」徽章（候选位 RR）与门槛行（现价 vs 临界价）双口径
  P1-4  背景硬闸与 docstring「全是旁证」自相矛盾（按铁律保留标的级否决，只修口径）

⚑ 本文件由 pytest 正常收集，不作为独立脚本运行（不 sys.exit）。
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "gates"))

import pytest  # noqa: E402

from battle_analyze import odds_matrix  # noqa: E402
from gates.session_decide import decide, chase_still_valid  # noqa: E402
from gates.us_short_watch import _verdict, brief_line  # noqa: E402


# ─────────────── A 股 decide() 端到端夹具（照 tests/data/test_session_decide.py） ───────────────

def _day(pre=10.0, price=10.80, date="2026-11-05", code="601218"):
    minutes = [
        {"t": "09:31", "o": pre, "h": pre + 0.10, "l": pre - 0.10,
         "c": pre + 0.05, "v": 100, "avg": pre},
        {"t": "10:30", "o": price - 0.10, "h": price + 0.05,
         "l": price - 0.20, "c": price, "v": 100, "avg": price - 0.10},
    ]
    return {
        "code": code, "name": "测试票", "pre": pre, "date": date,
        "minutes": minutes,
        "flows": [{"t": "10:30", "main": 1e7, "super": 1e7,
                   "large": 0, "mid": 0, "small": -1e7}],
        "ticks": [], "bench_minutes": [], "bench_name": "上证指数",
        "quote": {"f170": (price / pre - 1) * 100},
        "session_done": False, "frame": "minute", "indexes": {},
    }


def _summary(day):
    minutes = day["minutes"]
    last = minutes[-1]
    hi = max(minutes, key=lambda x: x["h"])
    lo = min(minutes, key=lambda x: x["l"])
    vol = sum(x["v"] for x in minutes) or 1.0
    after = sum(x["v"] for x in minutes if x["t"] > hi["t"])
    flow = {"main": 1e7, "super": 1e7, "large": 0, "mid": 0, "small": -1e7}
    return {
        "hi": hi, "lo": lo, "last": last, "vol": vol,
        "up": 1, "dn": 1, "above": 1, "below": 1,
        "after": after, "after_share": after / vol,
        "pos": (last["c"] - lo["l"]) / (hi["h"] - lo["l"]) if hi["h"] != lo["l"] else 0.5,
        "bench_at_close": -1.0, "flow": flow,
        "session_done": day["session_done"],
        "buy": 0, "sell": 0, "big": [], "big_buy": 0, "big_sell": 0,
        "outer": None, "inner": None,
        "flow_low": flow, "flow_high": flow, "outflow": None,
        "reading_key": "mixed", "reading": "—",
        "conclusion": "", "conclusion_label": "", "conclusion_buy": "",
    }


def _auth(**kw):
    a = {"limit": 11.2, "stop": 10.5, "size_ratio": 0.5,
         "valid_for": "仅次日", "issued": "2026-11-04",
         "src": "买区内 R 不足·追价授权"}
    a.update(kw)
    return a


def _run(chase=None, cap=None, target=12.0, date="2026-11-05"):
    day = _day(date=date)
    return decide(day, _summary(day), entry=10.0, stop=9.5, target=target,
                  cap=cap, chase=chase, zone_hi=10.6,
                  atr=0.5, pulse_enabled=False)


# ────────────────────────────── P0-A 三闸取严 ──────────────────────────────

def test_p0a_chase_cannot_override_explicit_cap():
    """授权 limit=11.2 不得把用户显式 cap=10.5 掀翻（改前 max() ⇒ cap 变 10.83）。"""
    d = _run(chase=_auth(), cap=10.5)
    assert d["chase_applied"] is not None, "授权应命中（现价 10.8 ≤ limit 11.2）"
    assert d["chase_size_ratio"] == 0.5
    assert d["cap"] <= 10.5 + 1e-9, d["cap"]
    g = d["cap_gates"]
    assert g["tightest"] == "显式cap", g
    assert g["final"] == d["cap"], "归因 final 必须与生效 cap 一致"
    assert g["chase_limit"] == 11.2


def test_p0a_chase_cannot_override_rr_gate():
    """无显式 cap 时，R≥1.5 闸给出的 10.70 必须压住授权 limit 11.2。"""
    d = _run(chase=_auth(), cap=None, target=11.0)
    assert d["chase_applied"] is not None
    assert d["cap"] <= 10.70 + 1e-9, d["cap"]
    assert d["cap_gates"]["tightest"] == "R闸", d["cap_gates"]
    assert d["cap_gates"]["final"] == d["cap"]


def test_p0a_chase_still_applies_when_no_tighter_gate():
    """三闸都不比 limit 更严时，授权仍应生效（收紧止损 + 半仓），不是把授权废掉。"""
    d = _run(chase=_auth(), cap=None, target=13.0)
    assert d["chase_applied"] is not None
    assert d["stop_exec"] >= 10.5, "执行止损必须收紧到授权 stop"
    assert d["chase_size_ratio"] == 0.5
    assert d["cap"] <= 11.2 + 1e-9, "授权 limit 是额外上界"


# ────────────────────────────── P1-1 授权时效 ──────────────────────────────

def test_p1_chase_auth_expires():
    """16 天前的授权不得再抬 cap / 收紧止损 / 半仓。"""
    d = _run(chase=_auth(issued="2026-10-20"), cap=None, target=13.0)
    assert d["chase_applied"] is None
    assert "过期" in d["chase_expired"]
    assert d["cap_gates"].get("chase_limit") is None
    assert d["stop_exec"] < 10.5, d["stop_exec"]


def test_p1_chase_auth_without_date_is_fail_closed():
    """无生成日期的放宽类授权必须停用，且给出可操作理由（不静默踏空）。"""
    d = _run(chase=_auth(issued=None), cap=None, target=13.0)
    assert d["chase_applied"] is None
    assert "缺生成日期" in d["chase_expired"]


def test_chase_still_valid_boundaries():
    assert chase_still_valid(None, "2026-11-05")[0] is True
    assert chase_still_valid(_auth(valid_for="长期"), "2026-11-05")[0] is True
    for off in (0, 1, 3):
        issued = "2026-11-%02d" % (5 - off)
        assert chase_still_valid(_auth(issued=issued), "2026-11-05")[0] is True, off
    ok, why = chase_still_valid(_auth(issued="2026-11-01"), "2026-11-05")
    assert ok is False and "过期" in why
    ok, why = chase_still_valid(_auth(issued="2026-11-06"), "2026-11-05")
    assert ok is False and "尚未生效" in why
    ok, why = chase_still_valid(_auth(issued="not-a-date"), "2026-11-05")
    assert ok is False and "格式非法" in why
    ok, why = chase_still_valid(_auth(), "")
    assert ok is False and "缺生成日期" in why


# ────────────────────────────── P1-2 并列双档 ──────────────────────────────

def test_p1_two_ways_have_real_prices_and_exec_modes():
    d = _run(chase=_auth(), cap=10.5)
    ways = d["two_ways"]
    down, up = ways["down"], ways["up"]
    assert down["price"] == 10.0 and up["price"] == 11.2
    assert down["price"] < up["price"]
    assert "预挂" in down["exec"], "向下档可隔夜预挂限价"
    assert "盯盘" in up["exec"], "A股无 buy-stop，向上档只能盯盘手动打"
    assert up["authorized"] is True
    assert d["limit_buy"] is None or isinstance(d["limit_buy"], (int, float))


# ────────────────────────────── P0-B 报告层同尺 ──────────────────────────────

def test_p0b_empty_walls_equals_none():
    """walls=[] 与 walls=None 必须同结论（改前一个「海阔天空」一个有目标）。"""
    entries = [{"name": "追高档", "price": 11.9, "kind": "breakout", "dist_pct": 0.0}]
    anchors = [{"name": "结构止损", "price": 10.9}]
    kw = dict(entries=entries, anchors=anchors, atr_v=1.0,
              t1=12.0, t2=15.0, last_c=11.9)
    assert odds_matrix(walls=[], **kw) == odds_matrix(walls=None, **kw)


def test_p0b_walls_none_also_reads_engine_t1():
    """engine_t1 判定提到分支外：walls=None 也必须认引擎目标（与 walls=[] 同结论）。"""
    entries = [{"name": "追高档", "price": 11.0, "kind": "breakout", "dist_pct": 0.0}]
    anchors = [{"name": "结构止损", "price": 10.0}]   # 风险 1.0 ≥ 0.25×ATR，避薄止损闸
    kw = dict(entries=entries, anchors=anchors, atr_v=1.0,
              t1=12.0, t2=15.0, last_c=11.0)
    r = odds_matrix(walls=None, engine_t1=12.0, **kw)
    assert r[0]["t1_used"] == 12.0
    assert "引擎targets.t1" in r[0]["r1_basis"]
    assert odds_matrix(walls=None, engine_t1=None, **kw)[0]["r1_basis"] == "t1"


def test_p0b_engine_t1_not_rejected_by_need():
    """engine_t1 给出 ≥ buy+0.5ATR 余量时必须认；贴脸（余量不足）则回落墙/None，
    不得用贴脸 t1 造 R≈0 的虚拟目标（深度 review P1 修 2026-11-05）。"""
    entries = [{"name": "追高档", "price": 11.0, "kind": "breakout", "dist_pct": 0.0}]
    anchors = [{"name": "结构止损", "price": 10.0}]
    # 充足余量：buy=11.0, t1=12.0, atr=1.0 ⇒ need=11.5，12.0≥11.5 ⇒ 认引擎目标
    rows = odds_matrix(entries, anchors, 1.0, 12.0, 15.0, 11.0,
                       walls=[11.0, 12.0, 15.0], engine_t1=12.0)
    assert rows[0]["t1_used"] == 12.0, rows[0]
    assert "引擎targets.t1" in rows[0]["r1_basis"]
    # 贴脸：buy=11.9 距 t1 仅 0.1 < 0.5ATR ⇒ 引擎目标被 margin 闸否决，回落墙逻辑
    entries_tight = [{"name": "追高档", "price": 11.9, "kind": "breakout", "dist_pct": 0.0}]
    rows2 = odds_matrix(entries_tight, anchors, 1.0, 12.0, 15.0, 11.9,
                        walls=[11.0, 12.0, 15.0], engine_t1=12.0)
    assert "引擎targets.t1" not in rows2[0]["r1_basis"], rows2[0]
    assert rows2[0]["t1_used"] != 12.0, rows2[0]


# ──────────────────────── P1-3 / P1-4 做空徽章口径 ────────────────────────

def _short_card(close=100.0, gate=110.0):
    return {
        "sym": "TEST", "date": "2026-11-05", "close": close,
        "ref_close": 100.0, "mode": "反抽结构空", "verdict": "推荐",
        "verdict_reason": "波段内·主决策空点 RR≥1.5 达标",
        "ctx_name": "头肩顶", "ctx_alive": True, "ctx_age": 5, "ctx_grade": "A",
        "main_cands": [{"name": "反抽R1不过空", "entry": gate,
                        "stop": 115.0, "target": 80.0,
                        "rr": (gate - 80.0) / (115.0 - gate),
                        "rr_far": None, "rr_basis": "T1", "target_basis": "T1"}],
        "rr_ladder": [(3.0, 125.0), (2.0, 118.0), (1.5, gate), (1.0, 105.0)],
        "etf": {"code": "TESTS"}, "t1": 80.0, "t2": 75.0, "chase": [], "deep": None,
        "signal": {"templates": ["射击之星"], "date": "2026-11-01", "ctx_age": 5},
        "risk_pct": 2.0, "target_far": 75.0, "chased_pct": -1.0,
    }


def test_p1_verdict_marks_wait_when_spot_below_gate():
    """现价未到临界价 ⇒ 徽章标「等到位」，与门槛行同口径。"""
    tier, why = _verdict(_short_card(close=100.0, gate=110.0))
    assert tier.startswith("推荐") and "等到位" in tier
    assert "候选位" in why
    tier2, _ = _verdict(_short_card(close=112.0, gate=110.0))
    assert "等到位" not in tier2


def test_p4_top_context_stays_hard_gate():
    """P1-4 裁定：顶部破位背景属标的级结构前提，仍是硬否决（铁律），不是旁证。"""
    c = _short_card()
    c["ctx_name"] = None
    assert _verdict(c)[0] == "不推荐"
    c = _short_card()
    c["ctx_alive"] = False
    assert _verdict(c)[0] == "不推荐"


def test_p3_gate_line_states_spot_vs_critical_price():
    """门槛行主语必须是「现价 vs 临界入场价」，不再用含糊的「够/不够」。"""
    card = _short_card(close=100.0, gate=110.0)
    card["verdict"], card["verdict_reason"] = _verdict(card)
    txt = brief_line(card)
    assert "临界入场" in txt
    assert "现价未到" in txt and "反抽" in txt
    assert "不够" not in txt
