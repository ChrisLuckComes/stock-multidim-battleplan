#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""形态质量排序器回归（2026-11-18 老罗吸收 1/2/3）。

三条铁律逐项验：
  ① 每项都有正例 + 反例（防判据写松）。
  ② hard_gate 恒 False、advisory_only=True，绝不写 recommend。
  ③ 缺数据降级不抛异常。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gates import ranking_bonus as RB  # noqa: E402


def _bar(d, o, h, l, c, v=1000):
    return {"d": d, "o": o, "h": h, "l": l, "c": c, "v": v}


# ── ① 企稳转头向上 ─────────────────────────────────────────────────
def test_turn_up_hit_after_touch_then_reclaim():
    # 锚 10：先探到 9.9（触及），再一根收盘 10.5 > 前收 10.1 且收在上半区
    bars = [
        _bar("d0", 10.2, 10.3, 10.0, 10.1),
        _bar("d1", 10.1, 10.15, 9.9, 10.05),   # 触及锚
        _bar("d2", 10.05, 10.6, 10.0, 10.5),   # 转头向上
    ]
    r = RB.stabilize_turn_up(bars, 10.0)
    assert r["hit"] is True and r["computable"] is True
    assert r["confirm_date"] == "d2" and r["close"] == 10.5


def test_turn_up_reject_when_no_reclaim():
    # 触及后收盘仍低于锚 ⇒ 不算企稳
    bars = [
        _bar("d0", 10.2, 10.3, 10.0, 10.1),
        _bar("d1", 10.0, 10.1, 9.8, 9.85),
        _bar("d2", 9.85, 9.95, 9.7, 9.8),
    ]
    r = RB.stabilize_turn_up(bars, 10.0)
    assert r["hit"] is False


def test_turn_up_reject_close_below_prev():
    # 站回锚但收盘 < 前收 ⇒ 不是"转头向上"
    bars = [
        _bar("d0", 10.4, 10.5, 10.0, 10.4),
        _bar("d1", 10.1, 10.2, 9.9, 10.15),
        _bar("d2", 10.15, 10.2, 10.0, 10.05),
    ]
    r = RB.stabilize_turn_up(bars, 10.0)
    assert r["hit"] is False


def test_turn_up_reject_when_no_touch():
    bars = [_bar("d%d" % i, 11, 11.2, 10.9, 11.1) for i in range(5)]
    r = RB.stabilize_turn_up(bars, 10.0)
    assert r["hit"] is False and "未触及" in r["note"]


# ── ② 回撤幅度递减（VCP） ──────────────────────────────────────────
def _vcp_bars(depths):
    """构造 high→low→high→low 交替，回撤幅度按 depths。

    每轮 5 根：高点(2根给 pivot 留边) → 低点 → 反弹。pivot_w=2 时相邻高点
    必须隔 ≥3 根，故每轮固定 5 根，且首尾各补 2 根中性 bar 让端点可判。
    """
    bars, base = [], 100.0
    bars.append(_bar("pad0", base, base * 1.001, base * 0.999, base))
    bars.append(_bar("pad1", base, base * 1.001, base * 0.999, base))
    for k, depth in enumerate(depths):
        H = base
        L = H * (1 - depth / 100.0)
        bars.append(_bar("h%d" % k, H, H * 1.02, H * 0.99, H * 1.01))
        bars.append(_bar("t%d" % k, H * 1.01, H * 1.015, L * 1.06, H * 1.005))
        bars.append(_bar("l%d" % k, L * 1.02, L * 1.03, L * 0.98, L))
        bars.append(_bar("u%d" % k, L * 1.03, L * 1.05, L * 0.99, L * 1.02))
        bars.append(_bar("v%d" % k, L * 1.05, H * 1.05, L * 1.02, H * 1.02))
        base = H * 1.02
    bars.append(_bar("tail0", base, base * 1.001, base * 0.999, base))
    bars.append(_bar("tail1", base, base * 1.001, base * 0.999, base))
    return bars


def test_vcp_hit_when_drawdowns_shrink():
    r = RB.drawdown_series(_vcp_bars([27.0, 17.0, 7.0]))
    assert r["hit"] is True and r["contracting"] is True
    assert r["n_drawdowns"] >= 2


def test_vcp_reject_when_drawdowns_expand():
    r = RB.drawdown_series(_vcp_bars([7.0, 17.0, 27.0]))
    assert r["hit"] is False and r.get("contracting") is False


def test_vcp_advisory_only_caveat_present():
    r = RB.drawdown_series(_vcp_bars([27.0, 17.0, 7.0]))
    assert r["advisory_only"] is True
    assert "别等深均线" in r["note_caveat"]


def test_vcp_reject_when_insufficient_drawdowns():
    bars = [_bar("d%d" % i, 10, 10.2, 9.9, 10.1) for i in range(8)]
    r = RB.drawdown_series(bars)
    assert r["hit"] is False and r["n_drawdowns"] < 2


# ── ③ 平台紧凑度 ──────────────────────────────────────────────────
def test_tightness_hit_flat():
    bars = [_bar("d%d" % i, 10, 10.1, 9.95, 10.0 + (i % 2) * 0.02) for i in range(20)]
    r = RB.platform_tightness(bars)
    assert r["hit"] is True and r["tightness_pct"] >= 60


def test_tightness_reject_wide():
    bars = [_bar("d%d" % i, 10, 11, 9, 10 + (i - 10) * 0.5) for i in range(20)]
    r = RB.platform_tightness(bars)
    assert r["hit"] is False and r["tightness_pct"] < 60


def test_tightness_does_not_touch_t0_gate():
    # 紧凑度阈值(±5%)与 T0 平台度阈值(±10%/<50)互不影响：本模块不 import rule123
    src = open(RB.__file__, encoding="utf-8").read()
    assert "import rule123" not in src and "from rule123" not in src
    assert "plateau_degree<50" not in src.replace("（RR_GATE、plateau_degree<50、", "")


# ── 聚合器软键铁律 ────────────────────────────────────────────────
def test_from_analysis_is_soft_no_recommend_key():
    bars = [_bar("d%d" % i, 10, 10.1, 9.9, 10.0) for i in range(20)]
    a = {"plan": {"recommend": True, "mode": "line_pullback",
                  "buy_zone": {"primary_lo": 10.0}}}
    out = RB.from_analysis(a, bars=bars)
    assert out["hard_gate"] is False and out["advisory_only"] is True
    assert "ranking_bonus" not in a["plan"]
    assert a["plan"]["recommend"] is True
    assert set(out["items"]) == {"turn_up", "drawdown_shrink", "platform_tightness"}


def test_from_analysis_degrades_without_bars():
    out = RB.from_analysis({}, bars=[])
    assert out["computable"] is False and out["bonus_hits"] == 0
    assert "未评估" in out["line"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
