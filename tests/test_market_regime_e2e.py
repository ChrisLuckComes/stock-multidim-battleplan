#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""market_regime 端到端接线验证：evaluate → plan_entry → market_regime 透传与 gate 触发。

不联网：monkeypatch rule123.get_bars + gates.market_regime.compute_market_regime。
目的：证明「老罗拍板的 RS 线方案」已从 gates 模块真实接入引擎主流程，
      而非只有单测覆盖的孤立函数。
"""
import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rule123
import gates.market_regime as MR


def _make_bars(n=120):
    """合成涨跌交替 K 线（每 6 根反转），保证 build_ev 能取到足够枢轴点。"""
    bars = []
    base = datetime.date(2024, 1, 1)
    price = 20.0
    direction = 1
    for i in range(n):
        if i > 0 and i % 6 == 0:
            direction *= -1
        price += direction * 0.5
        c = round(price, 2)
        d = (base + datetime.timedelta(days=i)).isoformat()
        bars.append({
            "d": d, "o": round(c - 0.1, 2), "h": round(c + 0.4, 2),
            "l": round(c - 0.4, 2), "c": c, "v": 1_000_000,
        })
    return bars


# 受控的 _plan_entry_core 替身（隔离下游各层 wrapper，只验证 market_regime 闸门接线）
_CORE_HOLDER = {"mode": "platform_break", "recommend": True}


def _fake_core(bars, ev):
    return {
        "mode": _CORE_HOLDER["mode"],
        "recommend": _CORE_HOLDER["recommend"],
        "verdict": "合成结构",
        "buy_zone": {"zone_low": 19.0, "zone_high": 20.0},
        "note": "synthetic",
        "setup": "platform",
        "path": "synthetic",
        "priority": 1,
        "days_above_r1": 0,
        "days_above_platform": 0,
    }


def _fixed_mkt(state, leader=False, exempt=False, enabled=True):
    return {
        "enabled": enabled,
        "broad_index": "sh000001", "broad_state": state,
        "broad_ma10": 3000.0, "broad_ma20": 3000.0, "broad_slope": -1.0,
        "rs": {"ok": True, "leader": leader,
               "rs_slope": 2.0 if leader else -2.0,
               "stock_ma_state": "up" if leader else "down",
               "stock_ma10": 21.0, "stock_ma20": 20.0},
        "leader": leader, "exempt": exempt,
        "breadth_marker": None, "note": "fixed",
    }


def test_plan_entry_gate_triggers_on_down_weak():
    rule123._plan_entry_core = _fake_core
    MR.compute_market_regime = lambda *a, **k: _fixed_mkt("down", leader=False)
    bars = _make_bars()
    ev, _, _ = rule123.build_ev(bars, ticker="301069")
    assert ev is not None, "build_ev 应成功"
    res = rule123.plan_entry(bars, ev, sym="301069")
    assert res["market_regime"]["broad_state"] == "down"
    assert res["mode"] == "wait", res.get("mode")
    assert res["recommend"] is False
    assert res.get("market_regime_veto") is True
    print("OK  down+weak → mode=wait / recommend=False / veto=True")


def test_plan_entry_exempt_on_leader():
    rule123._plan_entry_core = _fake_core
    MR.compute_market_regime = lambda *a, **k: _fixed_mkt("down", leader=True, exempt=True)
    bars = _make_bars()
    ev, _, _ = rule123.build_ev(bars, ticker="301069")
    res = rule123.plan_entry(bars, ev, sym="301069")
    assert res["mode"] == "platform_break", res.get("mode")  # 豁免，不被压
    assert res.get("market_regime_veto") is not True
    print("OK  down+leader(豁免) → mode 保持 platform_break / 无 veto")


def test_plan_entry_caution_blocks_breakout_only():
    rule123._plan_entry_core = _fake_core
    MR.compute_market_regime = lambda *a, **k: _fixed_mkt("caution", leader=False)
    bars = _make_bars()
    ev, _, _ = rule123.build_ev(bars, ticker="301069")
    res = rule123.plan_entry(bars, ev, sym="301069")
    assert res["mode"] == "wait", res.get("mode")            # 突破类被压
    _CORE_HOLDER["mode"] = "ma_bull_align"
    res2 = rule123.plan_entry(bars, ev, sym="301069")
    assert res2["mode"] == "ma_bull_align", res2.get("mode")  # 低吸类不压
    _CORE_HOLDER["mode"] = "platform_break"
    print("OK  caution → 仅禁突破类(platform_break→wait)，低吸(ma_bull_align)放行")


def test_evaluate_forwards_market_regime_to_out():
    rule123._plan_entry_core = _fake_core
    fixed = _fixed_mkt("up", leader=False)  # up 不拦截，纯验证透传
    MR.compute_market_regime = lambda *a, **k: fixed
    bars = _make_bars()
    rule123.get_bars = lambda sym, data_file=None: (bars, {"price": 20.0}, None)
    out = rule123.evaluate("301069", eod=True)
    assert "market_regime" in out, list(out.keys())
    assert out["market_regime"]["broad_state"] == "up"
    print("OK  evaluate → out 透传 market_regime(broad_state=up)")


def test_evaluate_disabled_flag():
    rule123._plan_entry_core = _fake_core
    MR.compute_market_regime = lambda *a, **k: _fixed_mkt("down", leader=False, enabled=False)
    bars = _make_bars()
    rule123.get_bars = lambda sym, data_file=None: (bars, {"price": 20.0}, None)
    out = rule123.evaluate("301069", eod=True, market_regime_enabled=False)
    assert out["market_regime"]["enabled"] is False
    assert out["mode"] == "platform_break", out.get("mode")  # 关闭时不拦截
    print("OK  market_regime_enabled=False → 不拦截(mode 保持 platform_break)")


if __name__ == "__main__":
    test_plan_entry_gate_triggers_on_down_weak()
    test_plan_entry_exempt_on_leader()
    test_plan_entry_caution_blocks_breakout_only()
    test_evaluate_forwards_market_regime_to_out()
    test_evaluate_disabled_flag()
    print("\n全部端到端接线验证通过 ✅")
