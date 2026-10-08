#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""regime 分层回归（老罗 2026-10-08：「启动之后连续性还挺好的，不能指望回调太深」）。

背景：原 path_habit() 的坑深是**全样本**口径，把震荡/下跌期的深回撤混进「启动后」
样本 ⇒ 坑深 P75 被高估 ⇒ 回踩位被设到 MA20 去。全市场实证
（research_regime_split_2026-10-08.py，600 票 / 8780 信号）：
    上升段触及 MA20 仅 22.7% vs 非上升段 70.8%（差 −48.1%）
    ⇒ 启动段等 MA20 = 77% 概率等不到（违反「等不到的价 = 0」）

本测试覆盖（全部离线合成数据，不联网）：
    1. regime_up() 不含未来函数
    2. path_habit() 的 regime 分层样本数守恒
    3. 触及率 / 坑深在合法区间
    4. _shrink() 随样本量单调逼近个股值
    5. _pullback_anchor() 措辞按触及率分档（防「37% 说成大概率会给到」）

实测锚（人工核对用，非断言）：
    002780 三夫户外（2026-10-08）：当前=启动段；启动段 n=5 MA20 触及 37%，
       非启动段 n=4 MA20 触及 72%
    601975 招商南油（2026-10-08）：启动段 n=13 MA20 触及 28%，非启动段 n=6 触及 76%
    ⇒ 与全市场基准 22.7% / 70.8% 同向，样本外成立

运行：pytest tests/entry/test_stock_character_regime.py
"""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for p in (ROOT, os.path.join(ROOT, "gates")):
    if p not in sys.path:
        sys.path.insert(0, p)

import stock_character as S  # noqa: E402


def _mk_bars(n=120, start=10.0, big_every=10):
    """合成日线：前半段上涨（→ up regime 信号），后半段回落（→ other 信号）。
    每隔 big_every 根放一根 +6% 大阳，保证 path_habit 能取到信号。
    """
    out = []
    d = dt.date(2025, 1, 2)
    c = start
    for i in range(n):
        d += dt.timedelta(days=1)
        if d.weekday() >= 5:
            d += dt.timedelta(days=7 - d.weekday())
        if i > 0 and i % big_every == 0:
            chg = 0.06
        elif i < n * 0.6:
            chg = 0.01
        else:
            chg = -0.012
        o = c
        c = c * (1 + chg)
        out.append({"d": d.isoformat(), "o": o, "h": max(o, c) * 1.006,
                    "l": min(o, c) * 0.994, "c": c, "v": 1e6})
    return out


def test_regime_up_no_future():
    """regime 标签不得含未来信息：同一 index 的判定，不因后续新增 K 线而改变。"""
    bars = _mk_bars()
    i = len(bars) - 30
    before = S.regime_up(bars, i)
    bars2 = bars + [{"d": "2030-01-01", "o": 100.0, "h": 110.0, "l": 99.0,
                     "c": 109.0, "v": 1e6}]
    after = S.regime_up(bars2, i)
    assert before == after, "regime_up 读到了未来数据"


def test_regime_split_conservation():
    """up.n + other.n 必须等于总信号数（分层不丢样本、不重复计数）。"""
    p = S.path_habit(_mk_bars())
    assert p is not None and p["n"] > 0
    rg = p["regime"]
    assert rg["up"].get("n", 0) + rg["other"].get("n", 0) == p["n"]
    assert p["regime_now"] in ("up", "other")


def test_rates_in_bounds():
    """触及率 ∈ [0,1]；坑深为非负百分数。"""
    p = S.path_habit(_mk_bars())
    for key in ("up", "other"):
        d = p["regime"].get(key) or {}
        if not d.get("n"):
            continue
        for k in ("touch_ma5", "touch_ma10", "touch_ma20"):
            assert 0.0 <= d[k] <= 1.0, (key, k, d[k])
        for k in ("depth_p50", "depth_p75"):
            assert 0.0 <= d[k] <= 100.0, (key, k, d[k])


def test_shrink_monotone():
    """经验贝叶斯收缩：n 越大越贴近个股值；n=0 时退回基准。"""
    base = 0.0
    assert S._shrink([], base) == base
    v1 = S._shrink([1.0], base)
    v8 = S._shrink([1.0] * 8, base)
    v100 = S._shrink([1.0] * 100, base)
    assert v1 < v8 < v100 < 1.0
    assert abs(v8 - 0.5) < 1e-9, "n=8, PSEUDO=8 ⇒ w=0.5"
    assert abs(S._shrink([0.0] * 1000, 1.0) - 0.0) < 0.01


def test_pullback_anchor_wording():
    """措辞必须按触及率分档 —— 37% 触及 = 63% 等不到，不许说成「大概率会给到」。"""
    sub = {"n": 20, "touch_ma5": 0.98, "touch_ma10": 0.60, "touch_ma20": 0.20}
    assert "别等 MA20" in S._pullback_anchor(None, "up", sub)

    sub40 = dict(sub, touch_ma20=0.40)
    s40 = S._pullback_anchor(None, "up", sub40)
    assert "别等 MA20" not in s40 and "次要挂单" in s40

    sub70 = dict(sub, touch_ma20=0.70)
    assert "会给到" in S._pullback_anchor(None, "up", sub70)

    # 样本不足 ⇒ 不给锚点，避免拿噪声当个股特性
    assert S._pullback_anchor(None, "up", {"n": 1, "touch_ma5": 1.0,
                                           "touch_ma10": 1.0, "touch_ma20": 1.0}) is None


def test_classify_path_keeps_contract():
    """classify_path 仍返回三元组，且样本不足时给 unknown（向后兼容）。"""
    assert S.classify_path(None)[0] == "unknown"
    assert S.classify_path({"n": 1, "depth_p50": 1.0, "depth_p75": 2.0})[0] == "unknown"
    p = S.path_habit(_mk_bars())
    k, label, advice = S.classify_path(p)
    assert k in ("v", "n", "mixed", "unknown")
    assert isinstance(advice, str) and advice


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
