# -*- coding: utf-8 -*-
"""★ 2026-11-06 深度 review 二次修复回归 fixture —— 纯离线、不触网、无全局补丁。

覆盖回测报告清单上本轮补修的 P1 #7 与 P2 #14：
  P1 #7  targets() 回踩类 RR 分母用买区下沿 primary_lo（实际挂单价），不用 last_c，
         否则 lo<现价 ⇒ 计划 rr_target1 与挂单 R 两套分母。
  P2 #14 report_render 推荐档高亮 / 「不再首选」判定改用稳定键值（entry_name/entry/
         stop_name/stop），不再用 `o is rec`（JSON 反序列化后对象同一性恒 False ⇒ 高亮失效）。
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "gates"))

from rule123 import rr_target1_with_entry, attach_stops_targets  # noqa: E402
from report_render import build_odds_rows, _rec_identity_key, pick_rec  # noqa: E402


def _rising_dip_bars(n=30, hi=12.0, last=11.0):
    """末收 last、前段冲到 hi 再回落 ⇒ last < prior_high ⇒ 不触发海阔天空；非涨停。"""
    bars = []
    for i in range(n - 1):
        c = 10.0 + (hi - 10.0) * (i / (n - 2))
        bars.append({"d": "2026-%02d-01" % ((i % 12) + 1), "o": round(c, 2),
                     "c": round(c, 2), "h": round(c * 1.01, 2),
                     "l": round(c * 0.99, 2), "v": 1.0})
    bars.append({"d": "2026-12-01", "o": round(last, 2), "c": round(last, 2),
                 "h": round(last * 1.01, 2), "l": round(last * 0.99, 2), "v": 1.0})
    return bars


# ───────────────────────────── P1 #7 单测 ─────────────────────────────

def test_p1_rr_uses_entry_denominator():
    """rr_target1_with_entry 必须用 entry（挂单价）当分母重算，而非 last_c。"""
    tg = {"target1": 15.0, "target2": 17.0, "rr_target1": 2.0}
    ok = rr_target1_with_entry(tg, 10.0, 9.0)
    assert ok is True
    # last_c=11 时 rr=(15-11)/(11-9)=2.0；用 entry=10 ⇒ (15-10)/(10-9)=5.0
    assert tg["rr_target1"] == 5.0, tg
    assert tg.get("rr_entry") == 10.0


def test_p1_rr_skips_when_entry_le_hard():
    """entry <= hard（分母非正）时不改 rr_target1。"""
    tg = {"target1": 15.0, "rr_target1": 2.0}
    assert rr_target1_with_entry(tg, 9.0, 9.0) is False
    assert tg["rr_target1"] == 2.0


def test_p1_rr_skips_when_no_target1():
    """target1 为 None（海阔天空/封死）时不改。"""
    tg = {"target1": None, "rr_target1": None}
    assert rr_target1_with_entry(tg, 10.0, 9.0) is False


def test_p1_attach_pullback_uses_primary_lo():
    """attach_stops_targets 对回踩类（非突破）用 primary_lo 重算 rr_target1。"""
    bars = _rising_dip_bars()
    z = {"level": 10.0, "primary_lo": 10.0, "primary_hi": 11.5,
         "hard_stop": 9.0, "hard": 9.0, "struct_stop": 9.0}
    plan = {"mode": "line_pullback", "recommend": True, "buy_zone": z, "note": ""}
    attach_stops_targets(plan, bars, 1.0, [(k, 50.0) for k in range(5)], ev=None)
    tg = plan.get("targets") or {}
    t1 = tg.get("target1")
    hard_final = z.get("hard_stop")
    assert t1 is not None and hard_final is not None
    # 分母必须是 primary_lo(10.0)，不是 last_c(11.0)
    assert tg["rr_target1"] == round((t1 - 10.0) / (10.0 - hard_final), 2), tg
    assert tg["rr_target1"] != round((t1 - 11.0) / (11.0 - hard_final), 2), tg


def test_p1_attach_breakout_keeps_last_c():
    """attach_stops_targets 对突破类不改用 primary_lo，rr_target1 维持 last_c 分母。"""
    bars = _rising_dip_bars()
    z = {"level": 10.0, "primary_lo": 10.0, "primary_hi": 11.5,
         "hard_stop": 9.0, "hard": 9.0, "struct_stop": 9.0}
    plan = {"mode": "platform_break", "recommend": True, "buy_zone": z, "note": ""}
    attach_stops_targets(plan, bars, 1.0, [(k, 50.0) for k in range(5)], ev=None)
    tg = plan.get("targets") or {}
    t1 = tg.get("target1")
    hard_final = z.get("hard_stop")
    # 突破类：分母仍是 last_c(11.0)
    assert tg["rr_target1"] == round((t1 - 11.0) / (11.0 - hard_final), 2), tg


# ───────────────────────────── P2 #14 单测 ─────────────────────────────

def _row(entry_name, entry, stop_name, stop, r1, risk_atr=0.6):
    return {"entry_name": entry_name, "entry": entry, "stop_name": stop_name,
            "stop": stop, "r1": r1, "risk": 1.0, "risk_atr": risk_atr,
            "need_pct": 0.0, "r2": None, "r1_basis": "t1", "prehang": True, "warn": []}


def test_p2_identity_key_equal_for_same_values():
    a = _row("回踩最优", 10.0, "结构止损", 9.0, 2.5)
    b = dict(a)                       # 同值不同对象（模拟 json.load）
    assert _rec_identity_key(a) == _rec_identity_key(b)
    assert a is not b


def test_p2_odds_rows_highlight_uses_key_not_identity():
    """odds_recommend 与 odds 中某行「同值不同对象」⇒ 推荐档仍能高亮、不被误判。"""
    rec = _row("回踩最优", 10.0, "结构止损", 9.0, 2.5)
    a = {
        "odds": [_row("其他档", 11.0, "结构止损", 9.5, 1.0), rec],
        "odds_primary": [_row("其他档", 11.0, "结构止损", 9.5, 1.0),
                         _row("回踩最优", 10.0, "结构止损", 9.0, 2.5)],
        "odds_top": None,
        "odds_recommend": rec,
    }
    assert pick_rec(a) is rec
    html = build_odds_rows(a)
    assert 'style="background:#fffdf0"' in html, "推荐档未高亮（P2 #14 失效）"
    # 「推荐」徽标只应出现一次（对应 rec 行，其它行是「可行」）
    assert html.count("推荐") == 1, html


def test_p2_best_top_note_uses_key_not_identity():
    """build_best 的「名义最高 R 不再首选」注记：top 与 rec 同值时不再误报。"""
    from report_render import build_best
    rec = {"entry_name": "回踩最优", "entry": 10.0, "stop_name": "结构止损",
           "stop": 9.0, "r1": 2.5, "r2": None, "risk": 1.0, "risk_atr": 0.6,
           "risk_pct": 2.0, "need_pct": 0.0, "r1_basis": "t1", "prehang": True,
           "qty": 100, "amount": 1000, "pct_cash": 10, "warn": []}
    other = {"entry_name": "其他档", "entry": 11.0, "stop_name": "结构止损",
             "stop": 9.5, "r1": 1.0, "r2": None, "risk": 1.5, "risk_atr": 0.7,
             "risk_pct": 3.0, "need_pct": 0.0, "r1_basis": "t1", "prehang": True,
             "qty": 50, "amount": 550, "pct_cash": 5, "warn": []}
    a = {
        "meta": {"code": "T", "name": "T", "account": 50000, "market": "CN"},
        "plan": {},
        "odds": [rec, other],
        "odds_primary": [rec, other],
        "odds_top": None,
        "odds_recommend": rec,
    }
    title, kpi_html, note_html = build_best(a, {})
    # top（odds[0]=rec）与 rec 同值 ⇒ 不应出现「不作为首选」的误导注记
    assert "不作为首选" not in note_html, "top 与 rec 同值时误报「不再首选」"
    # 而 top 与 rec 不同值时应出现（反向校验）
    X = {"entry_name": "最高R档", "entry": 12.0, "stop_name": "结构止损",
         "stop": 9.0, "r1": 5.0, "r2": None, "risk": 3.0, "risk_atr": 1.0,
         "risk_pct": 2.0, "need_pct": 0.0, "r1_basis": "t1", "prehang": True,
         "qty": 100, "amount": 1200, "pct_cash": 10, "warn": []}
    a2 = dict(a)
    a2["odds"] = [X, other, rec]
    a2["odds_primary"] = [X, other, rec]
    a2["odds_recommend"] = other
    _t, _k, note2 = build_best(a2, {})
    assert "不作为首选" in note2, "top 与 rec 不同值时应提示「不再首选」"
