# -*- coding: utf-8 -*-
"""★ 2026-11-05「全修 1~6」回归 fixture —— 纯离线、不触网、无全局补丁。

对应老罗「盈亏比计算 / 入场位 / 盘中决策 / 经常踏空」四条主诉的修复：

  ① RR 单一真源：所有门槛常量指向 rr_core.MIN_RR，公式只此一份，T1/T2 不得混用；
  ② 追价授权 next_day_chase 真正被 A股/美股盘中决策消费（改前是死字段），
     并输出「向下回踩 / 向上突破」并列档；
  ③ 盘中现价越过昨晚挂单价时，动态重算结构止损 + cap 基准迁移到突破位；
  ④ 美股止损下限 / 名义仓位上限 / --asof 时区归一；
  ⑤ 报告层赔率矩阵优先沿用引擎 targets.t1，不再用「最近近墙」把突破票压到 R<1.5；
  ⑥ 日线补数拒绝 v=0 平盘占位 bar（停牌/退市票），防污染 MA/ATR/RVOL 与骗过 fail-closed。

⚑ 本文件**不作为独立脚本**运行（不 sys.exit），由 pytest 正常收集。
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gates")))

import pytest  # noqa: E402

import rr_core  # noqa: E402
from screener import bao_store  # noqa: E402
from gates.us_session_decide import (normalize_asof, size_for_us,  # noqa: E402
                                     decide_us, summarize as us_summarize)
from gates.session_decide import dynamic_structure  # noqa: E402
from battle_analyze import odds_matrix  # noqa: E402


# ────────────────────────────── ① RR 单一真源 ──────────────────────────────

def test_rr_core_is_single_source():
    """门槛 1.5 只允许在 rr_core 定义一次；各模块 import 的必须是同一个值。"""
    assert rr_core.MIN_RR == 1.5
    for name in ("RR_QUALIFIED", "RR_GATE", "RR_ON", "METEOR_RR_GATE",
                 "MIN_RR_NOW", "ZONE_CHASE_RR"):
        assert getattr(rr_core, name) == rr_core.MIN_RR, name

    import rule123
    import confluence
    import battle_analyze
    import screener.filters as F
    import gates.us_short as US
    import gates.us_short_watch as WATCH
    import gates.hold_winners as HW
    import open_playbook as OP

    assert rule123.RR_GATE == rr_core.MIN_RR
    assert confluence.RR_ON == rr_core.MIN_RR
    assert battle_analyze.METEOR_RR_GATE == rr_core.MIN_RR
    assert battle_analyze.MIN_RR == rr_core.MIN_RR
    assert F.MIN_RR == rr_core.MIN_RR
    assert US.MIN_RR == rr_core.MIN_RR
    assert WATCH.MIN_RR_NOW == rr_core.MIN_RR
    assert HW.RR_MIN == rr_core.MIN_RR
    assert OP.RR_QUALIFIED == rr_core.MIN_RR
    # 公式必须真的 re-export 同一函数对象，而不是「又抄了一遍」
    assert OP.long_rr is rr_core.long_rr
    assert OP.max_entry_for_rr is rr_core.max_entry_for_rr
    assert US.rr_ratio is rr_core.short_rr
    assert US.min_entry_for_rr is rr_core.min_entry_for_rr


def test_rr_formulas_and_critical_price():
    """做多/做空 R、临界价反解、缺目标价 → None（不是 0，不跳过闸门）。"""
    assert rr_core.long_rr(10.0, 12.0, 9.0) == 2.0
    assert rr_core.long_rr(10.0, 9.0, 9.0) is None       # 目标不在入场上方
    assert rr_core.short_rr(10.0, 8.0, 11.0) == 2.0
    assert rr_core.short_rr(10.0, 12.0, 11.0) is None    # 目标不在入场下方
    assert rr_core.long_rr(10.0, None, 9.0) is None
    assert rr_core.short_rr(10.0, 8.0, None) is None
    assert rr_core.rr_ok(None) is False
    assert rr_core.rr_ok(1.49) is False
    assert rr_core.rr_ok(1.5) is True
    assert [rr_core.grade(x) for x in (None, 1.49, 1.5, 2.0, 3.0)] == [
        "不推荐", "不推荐", "合格", "推荐", "强烈推荐"]

    ce = rr_core.max_entry_for_rr(rr_core.MIN_RR, 12.0, 9.0)
    assert abs(rr_core.long_rr(ce, 12.0, 9.0) - rr_core.MIN_RR) < 1e-9
    se = rr_core.min_entry_for_rr(rr_core.MIN_RR, 8.0, 11.0)
    assert abs(rr_core.short_rr(se, 8.0, 11.0) - rr_core.MIN_RR) < 1e-9


def test_short_t1_t2_must_not_be_mixed():
    """★ 做空：候选 RR 与 RR≥1.5 门槛必须用**同一个目标**（T1），不得拿 T2 放水。"""
    entry, stop, t1, t2 = 100.0, 104.0, 94.0, 88.0
    r_t1 = rr_core.short_rr(entry, t1, stop)   # 1.5 = 门槛线
    r_t2 = rr_core.short_rr(entry, t2, stop)   # 3.0 = 远目标，看着更漂亮
    assert r_t1 == 1.5 and r_t2 == 3.0 and r_t2 > r_t1

    crit_t1 = rr_core.min_entry_for_rr(rr_core.MIN_RR, t1, stop)
    crit_t2 = rr_core.min_entry_for_rr(rr_core.MIN_RR, t2, stop)
    assert abs(crit_t1 - entry) < 1e-9
    assert crit_t2 < crit_t1                  # 用 T2 反解会把临界空点压低 ⇒ 放水
    assert abs(rr_core.short_rr(crit_t2, t1, stop)) > 1e-9
    assert rr_core.short_rr(crit_t2, t1, stop) < rr_core.MIN_RR


# ────────────────────────────── ⑥ 数据层守卫 ──────────────────────────────

def test_flat_placeholder_bar_guard():
    """停牌/退市票的 o=h=l=c、v=0 占位行一律拒；真一字板（v>0）不误伤。"""
    g = bao_store.is_flat_placeholder_bar
    assert g({"d": "2026-11-05", "o": 10, "h": 10, "l": 10, "c": 10, "v": 0}) is True
    assert g({"d": "2026-11-05", "o": 10, "h": 10, "l": 10, "c": 10, "v": 12345}) is False
    assert g({"d": "2026-11-05", "o": 10, "h": 10.5, "l": 9.8, "c": 10.3, "v": 999}) is False
    assert g({"d": "2026-11-05", "o": 10, "h": 10.5, "l": 9.8, "c": 10.3, "v": 0}) is True
    assert g({"d": "2026-11-05", "o": 10, "v": 0}) is True       # 字段残缺
    assert g(None) is False


def test_guard_is_wired_into_all_synth_callers(monkeypatch):
    """守卫收口在对外入口 synth_today_bar_tencent，各补数路径无需各写一遍。"""
    fake = {"d": "2026-11-05", "o": 10, "h": 10, "l": 10, "c": 10, "v": 0}
    good = {"d": "2026-11-05", "o": 10, "h": 10.5, "l": 9.8, "c": 10.3, "v": 9999}
    monkeypatch.setattr(bao_store, "_synth_today_bar_raw", lambda *a, **k: fake)
    assert bao_store.synth_today_bar_tencent("sh600000") is None
    monkeypatch.setattr(bao_store, "_synth_today_bar_raw", lambda *a, **k: good)
    assert bao_store.synth_today_bar_tencent("sh600000") == good


# ────────────────────────────── ④ 美股执行约束 ──────────────────────────────

def test_normalize_asof_et_and_bj():
    """ET 直通；BJ 按美东夏令时/冬令时精确换算（不写死 12/13 小时）。"""
    assert normalize_asof(None) is None
    assert normalize_asof("9:30") == ("09:30", False)
    for bad in ("abc", "25:99", "12:75", "1230"):
        with pytest.raises(ValueError):
            normalize_asof(bad)
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo("America/New_York")
        has_tz = True
    except Exception:
        has_tz = False
    if has_tz:
        # 夏令时差 12h / 冬令时差 13h；固定日期 ⇒ 不随运行当天翻 flaky
        assert normalize_asof("21:30", tz="BJ",
                              et_date=datetime.date(2026, 7, 1)) == ("09:30", False)
        assert normalize_asof("21:30", tz="BJ",
                              et_date=datetime.date(2026, 1, 15)) == ("08:30", False)
        assert normalize_asof("04:00", tz="BJ",
                              et_date=datetime.date(2026, 1, 15))[1] is True  # 跨日
    else:
        # 无 tzdata ⇒ 走冬令时固定 13h 兜底，不得崩
        assert normalize_asof("21:30", tz="BJ")[0] in ("08:30", "09:30")


def test_size_for_us_nominal_cap():
    """极端窄止损（盘前/夜盘 low≈cur）不得让 R→∞ 把仓位买到超过账户现金。"""
    acct = 4694.80
    normal = size_for_us(100.0, 99.5, account=acct)
    tight = size_for_us(100.0, 99.999, account=acct)
    for r in (normal, tight):
        assert r["qty"] * 100.0 <= acct + 1e-6
        assert r["amount"] <= acct + 1e-6
    assert tight["qty"] == normal["qty"]        # 都被名义上限截住
    assert tight["qty_cap_hit"] is True
    assert "名义上限" in tight["warn"]


# ────────────────────────────── ③ 盘中动态结构 ──────────────────────────────

def _mins(n, start=10.2, step=0.02):
    out = []
    for i in range(n):
        c = start + i * step
        o = start + (i - 1) * step if i else start
        out.append({"t": "%05d" % (i + 1), "o": o, "h": max(o, c) * 1.003,
                    "l": min(o, c) * 0.997, "c": c, "v": 1000.0, "avg": c})
    return out


def test_dynamic_structure_migrates_cap_base():
    """现价越过昨晚挂单价 + 日内结构健康 ⇒ 止损上移、cap 基准迁到突破位。"""
    day = {"code": "sz300001", "pre": 10.2, "minutes": _mins(35)}
    healthy = {"ok": True, "lo": [10.6]}
    d = dynamic_structure(day, 11.0, 10.5, 10.0, 13.0, 1.0, healthy, {})
    assert d["applied"] is True
    assert d["stop"] > 10.0 and d["stop"] < 11.0
    assert d["cap_base"] >= 10.5
    assert d["target"] == 13.0                 # 目标不凭空造

    # 默认路径一律不重算：回踩 / 分钟不足 / 结构不健康 / 目标被贴脸
    assert dynamic_structure(day, 10.4, 10.5, 10.0, 13.0, 1.0, healthy, {})["applied"] is False
    assert dynamic_structure({"code": "sz300001", "pre": 10.2, "minutes": _mins(5)},
                             11.0, 10.5, 10.0, 13.0, 1.0, healthy, {})["applied"] is False
    assert dynamic_structure(day, 11.0, 10.5, 10.0, 13.0, 1.0,
                             {"ok": False}, {"ok": False})["applied"] is False
    assert dynamic_structure(day, 11.0, 10.5, 10.0, 11.0, 1.0,
                             healthy, {})["applied"] is False


# ────────────────────────────── ② 追价授权消费 ──────────────────────────────

def test_us_chase_auth_is_consumed():
    """美股：现价越过 entry 但未顶穿授权 limit ⇒ 抬 cap / 收紧止损 / 半仓。"""
    mins = _mins(35, start=10.0, step=0.03)[:35]
    mins[-1]["c"] = 10.8
    mins[-1]["avg"] = 10.7
    day = {"code": "CIEN", "name": "Ciena", "date": "2026-11-05",
           "pre": 10.0, "minutes": mins}
    summary = us_summarize(day)
    auth = {"limit": 11.2, "stop": 10.3, "size_ratio": 0.5, "valid_for": "仅次日"}

    with_auth = decide_us(day, summary, entry=10.2, stop=9.8, target=13.0,
                          atr=1.0, account=4694.80,
                          pulse_enabled=False, chase_auth=auth)
    without = decide_us(day, summary, entry=10.2, stop=9.8, target=13.0,
                        atr=1.0, account=4694.80,
                        pulse_enabled=False, chase_auth=None)
    assert with_auth["chase_applied"] == auth
    assert with_auth["chase_size_ratio"] == 0.5
    assert with_auth["above_entry"] == 11.2
    assert with_auth["stop"] == 10.3            # 收紧（不是放宽）
    assert with_auth["cap"] >= without["cap"]   # 静态 cap 不再是踏空瓶颈
    assert without["chase_applied"] is None


# ────────────────────────────── ⑤ 报告层同尺 ──────────────────────────────

def test_odds_matrix_prefers_engine_t1():
    """引擎 t1 满足 0.5ATR 时优先采用，不再被「买价+0.5ATR 之上最近近墙」压到贴脸。"""
    entries = [{"name": "测试档", "price": 10.0, "kind": "breakout", "dist_pct": 0.0}]
    anchors = [{"name": "结构止损", "price": 9.0}]
    walls = [11.0, 12.0, 15.0]
    kw = dict(entries=entries, anchors=anchors, atr_v=1.0,
              t1=12.0, t2=15.0, last_c=10.0, walls=walls)
    base = odds_matrix(**kw)
    eng = odds_matrix(engine_t1=12.0, **kw)
    assert base[0]["t1_used"] == 11.0               # 旧口径：最近近墙
    assert eng[0]["t1_used"] == 12.0                # 新口径：引擎 t1
    assert "引擎targets.t1" in eng[0]["r1_basis"]
    assert eng[0]["r1"] > base[0]["r1"]             # 突破票不再被系统性压到 R<1.5
    assert odds_matrix(engine_t1=None, **kw) == base  # 不传则完全兼容旧行为
