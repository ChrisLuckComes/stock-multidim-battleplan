#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""market_regime 闸门回归测试（不联网：monkeypatch fetch_index_bars）。

覆盖：
- 指数三态判定（up / caution / down / unknown）
- 板块→宽基指数兜底映射 + 美股 QQQ/SPY 分流（老罗 2026-10-15：美股同套 RS 逻辑）
- 个股 RS 脱钩（leader）判定
- 复合 gate：up 不干预；caution+RS弱→仅禁突破类；down+RS弱→全禁；RS强→豁免
- 开关关闭不干预
"""
import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import gates.market_regime as MR

# ⚑ 本文件自己钉宽基取数（`_patch_index` 直接改 MR.fetch_index_bars、用例内不还原），
#   故标 market_regime —— conftest 不再重复钉；同时用 autouse 夹具**兜底还原**，
#   免得补丁顺着 pytest 会话污染后续模块（2026-10-17 修：此前泄漏把
#   tests/test_strength_gate.py 的 usQQQ 分流断言打红）。
pytestmark = pytest.mark.market_regime


@pytest.fixture(autouse=True)
def _restore_index_fetch():
    orig = MR.fetch_index_bars
    try:
        yield
    finally:
        MR.fetch_index_bars = orig


def _dates(n, start="2026-09-01"):
    d = datetime.datetime.strptime(start, "%Y-%m-%d")
    return [(d + datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def _bars(closes, dates=None):
    if dates is None:
        dates = _dates(len(closes))
    out = []
    for i, c in enumerate(closes):
        out.append({"d": dates[i], "o": c, "h": c * 1.01, "l": c * 0.99,
                    "c": c, "v": 1e6})
    return out


def _patch_index(closes):
    """把 fetch_index_bars 替换成返回指定指数收盘序列（忽略传入的代码）。"""
    dates = _dates(len(closes))
    bars = _bars(closes, dates)

    def fake(code, n=65):
        return bars

    MR.fetch_index_bars = fake
    return bars, dates


# ───────────────────────────── 指数三态 ─────────────────────────────
def test_index_regime_up():
    bars = _bars(list(range(100, 131)))          # 持续上行
    r = MR.index_regime(bars)
    assert r["state"] == "up", r


def test_index_regime_down():
    bars = _bars(list(range(130, 99, -1)))        # 持续下行
    r = MR.index_regime(bars)
    assert r["state"] == "down", r


def test_index_regime_caution():
    # 上行后轻微回踩：MA10<=MA20 但 MA20 仍向上 ⇒ caution
    bars = _bars(list(range(100, 120)) + [105] * 6)
    r = MR.index_regime(bars)
    assert r["state"] == "caution", r


def test_index_regime_unknown():
    r = MR.index_regime(_bars([100, 101, 102]))   # 数据不足
    assert r["state"] == "unknown", r


# ───────────────────────────── 板块→宽基映射 + 豁免 ─────────────────────────────
def test_board_selection():
    assert MR.select_broad_indices("688152") == ["sh000688", "sh000680"]   # 科创板
    assert MR.select_broad_indices("300199") == ["sz399006", "sz399001"]   # 创业板
    assert MR.select_broad_indices("601218")[0] == "sh000001"             # 沪主
    assert MR.select_broad_indices("000858")[0] == "sz399001"             # 深主
    assert MR._board_of("CF") is None                                   # 美股豁免


# ───────────────────────────── RS 脱钩判定 ─────────────────────────────
def test_stock_rs_leader_false():
    dates = _dates(31)
    idx = _bars(list(range(130, 99, -1)), dates)      # 指数下行
    stock = _bars([c * 0.98 for c in range(130, 99, -1)], dates)  # 个股更弱
    rs = MR.stock_rs(stock, idx)
    assert rs["ok"] is True
    assert rs["leader"] is False


def test_stock_rs_leader_true():
    dates = _dates(31)
    idx = _bars(list(range(130, 99, -1)), dates)      # 指数下行
    stock = _bars(list(range(100, 131)), dates)       # 个股上行 ⇒ 相对指数脱钩
    rs = MR.stock_rs(stock, idx)
    assert rs["ok"] is True
    assert rs["leader"] is True


# ───────────────────────────── 复合 gate 行为 ─────────────────────────────
def _mkt(broad_state, leader):
    return {"enabled": True, "broad_index": "sz399006", "broad_state": broad_state,
            "rs": {"ok": True, "leader": leader}, "leader": leader,
            "exempt": leader}


def test_gate_up_no_block():
    res = {"mode": "platform_break", "recommend": True, "pre_breakout": {"trigger": 1}}
    out = MR.apply_market_regime_gate(res, _mkt("up", False))
    assert out["recommend"] is True
    assert out["mode"] == "platform_break"


def test_gate_caution_weak_blocks_breakout_only():
    res = {"mode": "platform_break", "recommend": True}
    out = MR.apply_market_regime_gate(res, _mkt("caution", False))
    assert out["recommend"] is False
    assert out["mode"] == "wait"
    # 低吸类不放行约束（caution 只拦突破类）
    res2 = {"mode": "line_pullback", "recommend": True}
    out2 = MR.apply_market_regime_gate(res2, _mkt("caution", False))
    assert out2["recommend"] is True


def test_gate_down_weak_blocks_all():
    res = {"mode": "line_pullback", "recommend": True}
    out = MR.apply_market_regime_gate(res, _mkt("down", False))
    assert out["recommend"] is False
    assert out["mode"] == "wait"


def test_gate_leader_exempt_even_in_down():
    # RS 强（龙头/防御）⇒ 宽基 down 也豁免，绝不误杀
    res = {"mode": "platform_break", "recommend": True, "pre_breakout": {"trigger": 1}}
    out = MR.apply_market_regime_gate(res, _mkt("down", True))
    assert out["recommend"] is True
    assert out["mode"] == "platform_break"
    assert out.get("market_regime_veto") is not True


def test_gate_disabled_no_block():
    res = {"mode": "platform_break", "recommend": True}
    out = MR.apply_market_regime_gate(res, _mkt("down", False), enabled=False)
    assert out["recommend"] is True


def test_gate_suppress_prebreakout():
    res = {"mode": "platform_break", "recommend": True,
           "pre_breakout": {"trigger": 10, "status": "预备"}}
    out = MR.apply_market_regime_gate(res, _mkt("caution", False))
    assert out["pre_breakout"]["suppressed_by"] == "market_regime"


# ───────────────────────────── 端到端（monkeypatch 取数） ─────────────────────────────
def test_e2e_down_weak_blocks():
    idx_bars, dates = _patch_index(list(range(130, 99, -1)))   # 指数下行
    stock = _bars([c * 0.98 for c in range(130, 99, -1)], dates)  # 个股更弱
    mkt = MR.compute_market_regime("301069", stock, enabled=True)
    assert mkt["broad_state"] == "down"
    assert mkt["leader"] is False
    res = {"mode": "platform_break", "recommend": True,
           "pre_breakout": {"trigger": 1}}
    out = MR.apply_market_regime_gate(res, mkt, enabled=True)
    assert out["recommend"] is False


def test_e2e_down_leader_exempt():
    idx_bars, dates = _patch_index(list(range(130, 99, -1)))   # 指数下行
    stock = _bars(list(range(100, 131)), dates)                # 个股上行（脱钩）
    mkt = MR.compute_market_regime("301069", stock, enabled=True)
    assert mkt["broad_state"] == "down"
    assert mkt["leader"] is True
    assert mkt["exempt"] is True
    res = {"mode": "platform_break", "recommend": True}
    out = MR.apply_market_regime_gate(res, mkt, enabled=True)
    assert out["recommend"] is True


def test_e2e_switch_off():
    _patch_index(list(range(130, 99, -1)))
    mkt = MR.compute_market_regime("301069", _bars(list(range(100, 131))), enabled=False)
    assert mkt["enabled"] is False
    assert mkt["broad_state"] == "unknown"  # 关闭时不取数


def test_e2e_us_broad():
    # 美股 ticker（老罗 2026-10-15）：宽基走 QQQ/SPY（腾讯 usXXXX 同一取数通道），
    # 与 A 股同一套 RS 逻辑，不再「非A股豁免」
    idx_bars, dates = _patch_index(list(range(130, 99, -1)))   # 宽基下行
    stock = _bars(list(range(100, 131)), dates)                # 个股上行（对 QQQ 脱钩）
    mkt = MR.compute_market_regime("ALAB", stock, enabled=True)
    assert mkt["broad_index"] == "usQQQ"
    assert mkt["broad_state"] == "down"
    assert mkt["leader"] is True
    assert mkt["rs"]["ok"] is True


def test_select_broad_us_chain():
    # 分流纯函数：A 股板块链 / 美股 QQQ+SPY 链 / 显式覆盖
    assert MR.select_broad_indices("ALAB") == ["usQQQ", "usSPY"]
    assert MR.select_broad_indices("600183")[0] == "sh000001"      # 沪主板块链不变
    assert MR.select_broad_indices("301069")[0] == "sz399006"      # 创业板链不变
    assert MR.select_broad_indices("ALAB", regime_index="usSPY")[0] == "usSPY"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
            passed += 1
        except AssertionError as e:
            print("FAIL", t.__name__, "->", e)
        except Exception as e:
            print("ERR ", t.__name__, "->", type(e).__name__, e)
    print("\n%d/%d passed" % (passed, len(tests)))
