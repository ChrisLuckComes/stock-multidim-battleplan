#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个股强度前置闸门回归（老罗 2026-10-15 定：前置判断；WEAK 硬中止、不跑完整
报告，但必须给出不通过的理由；A 股/美股同一套通用逻辑，不按市场重复实现）。

覆盖：
- market_regime 美股宽基分流：select_broad_indices 纯函数（A 股板块链 / 美股 QQQ+SPY）
- quick_strength 美股路径（bars + broad_bars 直接给，不联网）：STRONG / WEAK 判定与理由
- battle_analyze 接线源码契约：_strength_gate 必须在 R.evaluate 之前（前置）
- WEAK → aborted 载荷 + 打印必含理由；闸门异常 → NEUTRAL 兜底不阻断
- CLI --no-strength 绕过口存在
"""
import contextlib
import datetime
import inspect
import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import gates.market_regime as MR
import gates.quick_strength as QS
import battle_analyze as BA


def _mk(closes, start="2026-09-01"):
    """由收盘价列表造日线（旧→新），与 tests/test_quick_strength._mk 同口径。"""
    d0 = datetime.datetime.strptime(start, "%Y-%m-%d")
    out = []
    for i, c in enumerate(closes):
        d = (d0 + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        out.append({"d": d, "o": c, "h": c * 1.01, "l": c * 0.99,
                    "c": c, "v": 1e6})
    return out


def _flat_qqq(n=40):
    return _mk([100.0] * n)


# ───────────────────────────── 宽基分流（market_regime） ─────────────────────────────
def test_select_broad_us_chain():
    assert MR.select_broad_indices("ALAB") == ["usQQQ", "usSPY"]
    assert MR.select_broad_indices("600183")[0] == "sh000001"      # 沪主板块链不变
    assert MR.select_broad_indices("301069")[0] == "sz399006"      # 创业板链不变
    assert MR.select_broad_indices("ALAB", regime_index="usSPY")[0] == "usSPY"


def test_is_us_ticker():
    assert MR._is_us_ticker("ALAB") is True
    assert MR._is_us_ticker("QQQ") is True
    assert MR._is_us_ticker("002317") is False
    assert MR._is_us_ticker("688543") is False


# ───────────────────────────── 取数分流契约（2026-10-15 实测修正） ─────────────────────────────
def test_fetch_index_bars_us_routes_to_bars_source():
    """美股宽基不走腾讯日K（实测稀疏：n=140 仅得 1~2 根），转 bars_source.us_quote。

    ⚑ 替身必须复刻 us_quote 的真实签名 —— 它返回 (quote, notes) 二元组
    （battle_analyze 里就是 q, notes = BS.us_quote(...)），按 dict 写替身
    会漏掉「tuple 解包」这个真 bug（2026-10-15 实测踩过：except 吞掉
    AttributeError → 静默取空）。
    """
    import bars_source as BS
    fake = _mk([100.0 + i for i in range(40)])
    orig = BS.us_quote
    BS.us_quote = lambda sym, **k: ({"ticker": sym, "bars": fake}, [])
    try:
        MR._CACHE.pop("usQQQ", None)
        got = MR.fetch_index_bars("usQQQ", n=65)
        assert got == fake, "美股宽基必须转 us_quote 且原样返回其 bars"
    finally:
        BS.us_quote = orig
        MR._CACHE.pop("usQQQ", None)


def test_fetch_stock_bars_us_routes_to_bars_source():
    """CLI 取数：美股 ticker 同样转 us_quote（腾讯美股日K 稀疏不可用）。"""
    import bars_source as BS
    fake = _mk([100.0 + i for i in range(40)])
    orig = BS.us_quote
    BS.us_quote = lambda sym, **k: ({"ticker": sym, "bars": fake}, [])
    try:
        assert QS.fetch_stock_bars("ALAB") == fake
    finally:
        BS.us_quote = orig


# ───────────────────────────── quick_strength 美股路径 ─────────────────────────────
def test_quick_strength_us_strong():
    # 美股个股持续上行 + QQQ 宽基平盘 ⇒ 跑赢大盘 + 自身多头排列 → STRONG
    qqq = _flat_qqq()
    stock = _mk([100.0 * (1 + 0.004 * i) for i in range(40)])
    r = QS.quick_strength("ALAB", stock, broad_bars=qqq, name="ALAB")
    assert r["verdict"] == "STRONG", r
    assert r["rs_state"] == "ok"
    assert r["metrics"]["rs_slope"] > 0


def test_quick_strength_us_weak_with_reasons():
    # 收盘跌破 MA20 + MA20 斜率 ≤ 0 → WEAK，且必须给出不通过的理由
    qqq = _flat_qqq()
    stock = _mk([100.0 * (1 - 0.004 * i) for i in range(40)])
    r = QS.quick_strength("ALAB", stock, broad_bars=qqq, name="ALAB")
    assert r["verdict"] == "WEAK", r
    assert r["reasons"], "WEAK 必须给出不通过的理由（老罗：硬中止也要给理由）"
    assert any("MA20" in x for x in r["reasons"])


def test_quick_strength_ash_unchanged():
    # A 股路径回归：同 tests/test_quick_strength 口径，确认本次改动没碰判定
    broad = _mk([100.0] * 40)
    stock = _mk([100.0 * (1 + 0.003 * i) for i in range(40)])
    r = QS.quick_strength("002317", stock, broad_bars=broad)
    assert r["verdict"] == "STRONG", r


# ───────────────────────────── battle_analyze 接线契约 ─────────────────────────────
def test_gate_before_evaluate_source_contract():
    """前置判断：_strength_gate 调用必须位于 R.evaluate 之前（防回退挪位）。"""
    src = inspect.getsource(BA.analyze)
    i_gate = src.find("_strength_gate(")
    i_ev = src.find("R.evaluate(")
    assert 0 < i_gate < i_ev, "强度闸门必须位于 R.evaluate 之前（前置判断）"


def test_main_cli_and_abort_branch():
    src = inspect.getsource(BA.main)
    assert "--no-strength" in src, "必须提供 --no-strength 绕过口"
    assert "strength_gate=not a.no_strength" in src
    assert 'res.get("aborted")' in src, "WEAK 必须走 aborted 分支（不写 json、不 summarize）"


def test_weak_abort_payload_print_reasons():
    """WEAK → aborted 载荷结构 + 打印必含不通过的理由。"""
    orig = QS.quick_strength
    QS.quick_strength = lambda *a, **k: {
        "verdict": "WEAK", "code": "002317", "name": "众生药业",
        "metrics": {"last_close": 10.0, "ma20": 11.0, "above_ma20": False,
                    "ma20_slope": -1.2, "rs_slope": -3.4, "leader": False},
        "reasons": ["收盘 10.00 < MA20 11.00（价格在中期线下方）",
                    "MA20 斜率 -1.20% ≤ 0（中期趋势向下）"],
        "broad_state": "down",
    }
    try:
        qs = BA._strength_gate("002317", _mk([11.0] * 30 + [10.0]))
        assert qs["verdict"] == "WEAK"
        payload = {"meta": {"code": "002317", "name": "众生药业", "abort": True},
                   "aborted": True, "abort_reason": "strength_weak",
                   "strength_gate": qs}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            BA._print_strength_abort(payload)
        out = buf.getvalue()
        assert "硬中止" in out
        assert "收盘 10.00 < MA20 11.00" in out, "理由必须逐条打出来"
        assert "MA20 斜率 -1.20%" in out
    finally:
        QS.quick_strength = orig


def test_gate_exception_fallback_neutral():
    """闸门自身异常 → NEUTRAL 放行、不阻断主流程（与 market_regime 同哲学）。"""
    orig = QS.quick_strength

    def _boom(*a, **k):
        raise RuntimeError("boom")

    QS.quick_strength = _boom
    try:
        out = BA._strength_gate("002317", _mk([100.0] * 40))
    finally:
        QS.quick_strength = orig
    assert out["verdict"] == "NEUTRAL"
    assert any("异常" in x for x in out["reasons"])


if __name__ == "__main__":
    _fns = [(k, v) for k, v in sorted(globals().items())
            if k.startswith("test_") and callable(v)]
    _fail = 0
    for name, fn in _fns:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as e:
            _fail += 1
            print("FAIL %s: %r" % (name, e))
    print("\n%d/%d passed" % (len(_fns) - _fail, len(_fns)))
    sys.exit(1 if _fail else 0)
