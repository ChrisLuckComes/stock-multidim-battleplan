# -*- coding: utf-8 -*-
"""open_playbook.from_analysis 的兜底回归 —— 老罗 2026-09-27 修复，必须钉死。

背景：本函数一度被「INST→DEV 同步」整文件覆盖过，老罗的两处修复被抹掉，
后据 `__pycache__/open_playbook.cpython-311.pyc` 反汇编还原。本文件负责：
**再被覆盖或修改时 pytest 会红**，而不是等报告出不来才发现。

修复一：`odds_recommend` 可能是 **list / 空 dict**。
    旧写法 `a.get("odds_recommend") or a.get("odds_primary") or {}`
    对空 list 尚可取假回落，但**非空 list 取真却没有 .get** ⇒ AttributeError 直接崩。
    改为「必须是非空 dict 才认」。

修复二：T0（`plan.mode == "ma_reclaim_break"`）**没有赔率档**，
    entry/stop 全是 None ⇒ 旧逻辑 `return None` ⇒ 整份报告的
    「开盘作战方案」区块缺席（report_render 静默跳过）。
    改为用触发价 / 硬止损 / 枢轴兜底。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import open_playbook as OP  # noqa: E402


def _base(**kw):
    a = {
        "meta": {"code": "601218", "name": "测试", "market": "CN",
                 "basis_close": 5.50},
        "plan": {"mode": "pullback", "buy_zone": {"level": 5.00}},
        "struct": {"atr14": 0.29, "pivots_up": [5.60, {"price": 6.10}]},
        "targets": {"t1": 5.99},
        "odds_recommend": {"entry": 5.30, "stop": 5.00, "qty": 1000},
    }
    a.update(kw)
    return a


# ------------------------------------------------------------ 修复一：list 兜底
def test_odds_recommend_empty_list_does_not_crash():
    """odds_recommend = [] ⇒ 不得崩，回落到 odds_primary。"""
    a = _base(odds_recommend=[],
              odds_primary={"entry": 5.20, "stop": 4.90, "qty": 800})
    pb = OP.from_analysis(a, {})
    assert pb is not None
    assert pb["entry"] == 5.20
    assert pb["stop"] == 4.90


def test_odds_recommend_nonempty_list_does_not_crash():
    """★ 老罗修的核心 Bug：非空 list 有 .get 才怪 —— 必须回落，不能炸。

    旧写法 `a.get("odds_recommend") or a.get("odds_primary")` 里，
    非空 list 是真值 ⇒ 取它 ⇒ `od.get("entry")` ⇒ AttributeError。
    """
    a = _base(odds_recommend=[{"entry": 9.99, "stop": 8.88}],
              odds_primary={"entry": 5.20, "stop": 4.90, "qty": 800})
    pb = OP.from_analysis(a, {})          # 不得 raise AttributeError
    assert pb["entry"] == 5.20, "非空 list 必须被视为无效档并回落"
    assert pb["stop"] == 4.90


def test_odds_recommend_missing_entirely():
    a = _base(**{})
    del a["odds_recommend"]
    a["odds_primary"] = {"entry": 5.10, "stop": 4.80, "qty": 700}
    pb = OP.from_analysis(a, {})
    assert pb["entry"] == 5.10


def test_no_odds_at_all_returns_none_not_crash():
    """两个档都没有 ⇒ 明确 return None（由渲染层跳过），不得抛异常。"""
    a = _base(**{})
    del a["odds_recommend"]
    a["plan"] = {"mode": "pullback", "buy_zone": {}}   # 非 T0，不触发兜底
    assert OP.from_analysis(a, {}) is None


# ------------------------------------------------------------ 修复二：T0 兜底
def test_t0_mode_falls_back_to_trigger_and_hard_stop():
    """T0（ma_reclaim_break）无赔率档 ⇒ entry/stop 由触发价 + 硬止损兜底。

    修复前：entry/stop 全 None ⇒ return None ⇒ 报告缺开盘作战方案。
    """
    a = _base(**{})
    del a["odds_recommend"]
    a["plan"] = {"mode": "ma_reclaim_break",
                 "ma_reclaim": {"trigger": 5.45},
                 "buy_zone": {"primary_hi": 5.60, "hard_stop": 5.05}}
    pb = OP.from_analysis(a, {})
    assert pb is not None, "T0 必须能出方案，不得 return None"
    assert pb["entry"] == 5.45, "优先取 ma_reclaim.trigger"
    assert pb["stop"] == 5.05, "止损取 buy_zone.hard_stop"


def test_t0_entry_falls_back_to_primary_hi():
    a = _base(**{})
    del a["odds_recommend"]
    a["plan"] = {"mode": "ma_reclaim_break",
                 "ma_reclaim": {},
                 "buy_zone": {"primary_hi": 5.60, "hard_stop": 5.05}}
    pb = OP.from_analysis(a, {})
    assert pb["entry"] == 5.60, "trigger 缺失时取 buy_zone.primary_hi"


def test_t0_stop_prefers_buy_zone_hard_stop_then_ma_reclaim():
    a = _base(**{})
    del a["odds_recommend"]
    a["plan"] = {"mode": "ma_reclaim_break",
                 "ma_reclaim": {"trigger": 5.45, "hard_stop": 4.95},
                 "buy_zone": {"primary_hi": 5.60}}
    pb = OP.from_analysis(a, {})
    assert pb["stop"] == 4.95, "buy_zone 无 hard_stop 时回落 ma_reclaim.hard_stop"


def test_t0_target_from_pivots_up():
    """T0 目标：上方第二个枢轴 pivots_up[1]（dict 形式取 price）。"""
    a = _base(**{})
    del a["odds_recommend"]
    del a["targets"]
    a["plan"] = {"mode": "ma_reclaim_break",
                 "ma_reclaim": {"trigger": 5.45},
                 "buy_zone": {"primary_hi": 5.60, "hard_stop": 5.05}}
    pb = OP.from_analysis(a, {})
    assert pb["target"] == 6.10


def test_t0_target_falls_back_to_entry_plus_six_atr():
    """枢轴也没有 ⇒ round(entry + 6×ATR, 2) = 5.45 + 6×0.29 = 7.19。"""
    a = _base(**{})
    del a["odds_recommend"]
    del a["targets"]
    a["struct"] = {"atr14": 0.29}
    a["plan"] = {"mode": "ma_reclaim_break",
                 "ma_reclaim": {"trigger": 5.45},
                 "buy_zone": {"primary_hi": 5.60, "hard_stop": 5.05}}
    pb = OP.from_analysis(a, {})
    assert pb["target"] == pytest.approx(7.19)


def test_t0_fallback_not_applied_to_normal_modes():
    """非 T0 模式不得误用 T0 兜底（pullback 缺档就该明确 None）。"""
    a = _base(**{})
    del a["odds_recommend"]
    a["plan"] = {"mode": "pullback",
                 "ma_reclaim": {"trigger": 5.45},
                 "buy_zone": {"primary_hi": 5.60, "hard_stop": 5.05}}
    assert OP.from_analysis(a, {}) is None
