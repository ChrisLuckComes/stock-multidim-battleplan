#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""stock_character.py 回归测试。

运行：python test_stock_character.py
    或 python -m pytest test_stock_character.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stock_character as sc  # noqa: E402


def mk(closes, spread=0.01):
    """造日线：给定收盘序列，高低按 spread 展开，日期递增（跳过周末由调用者负责）。"""
    bars = []
    for i, c in enumerate(closes):
        bars.append({
            "d": "2026-%02d-%02d" % (i // 28 + 1, i % 28 + 1),
            "o": c, "h": c * (1 + spread), "l": c * (1 - spread),
            "c": c, "v": 1.0,
        })
    return bars


def test_prefix_of():
    assert sc.prefix_of("688002") == "sh"
    assert sc.prefix_of("603067") == "sh"
    assert sc.prefix_of("900001") == "sh"
    assert sc.prefix_of("301165") == "sz"
    assert sc.prefix_of("300404") == "sz"
    assert sc.prefix_of(688428) == "sh"


def test_normalize_dict_and_list():
    d = [{"d": "2026-01-01", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 9}]
    assert sc.normalize(d)[0]["c"] == 1.5
    # list 形式：按 _BAR_KEYS 顺序 zip
    lst = [["2026-01-01", 1, 2, 0.5, 1.5, 9]]
    assert sc.normalize(lst)[0]["h"] == 2
    # 兼容 date/open/... 别名
    alias = [{"date": "2026-01-01", "open": 1, "high": 2, "low": 0.5,
              "close": 1.5, "volume": 9}]
    assert sc.normalize(alias)[0]["c"] == 1.5


def test_ma():
    bars = mk([1, 2, 3, 4, 5])
    assert sc.ma(bars, 4, 5) == 3.0        # (1+2+3+4+5)/5
    assert sc.ma(bars, 4, 3) == 4.0        # (3+4+5)/3
    assert sc.ma(bars, 1, 5) is None       # 样本不足


def test_atr_positive():
    bars = mk([10, 11, 12, 11, 13, 14, 13, 15, 16, 15, 17, 18, 17, 19, 20, 21])
    a = sc.atr(bars, 15, 14)
    assert a is not None and a > 0


def test_max_up_streak():
    bars = mk([1, 2, 3, 4, 3, 4, 5, 6, 7, 8])
    assert sc.max_up_streak(bars) == 5     # 4,5,6,7,8


def test_classify_breakout():
    a = {"odds": 2.0, "cont_rate": 0.6, "shock_rate": 0.2}
    k, label, _ = sc.classify(a)
    assert k == "breakout" and "加速" in label


def test_classify_grind_by_odds():
    a = {"odds": 0.87, "cont_rate": 0.6, "shock_rate": 0.8}
    k, label, _ = sc.classify(a)
    assert k == "grind" and "消化" in label


def test_classify_grind_by_low_cont():
    a = {"odds": 1.2, "cont_rate": 0.3, "shock_rate": 0.4}
    k, _, _ = sc.classify(a)
    assert k == "grind"


def test_classify_mixed():
    a = {"odds": 1.2, "cont_rate": 0.45, "shock_rate": 0.4}
    k, label, _ = sc.classify(a)
    assert k == "mixed" and "混合" in label


def test_classify_unknown():
    a = {"odds": None, "cont_rate": None, "shock_rate": None}
    k, _, _ = sc.classify(a)
    assert k == "unknown"


def test_analyze_grind_synthetic():
    """造「大阳后一路阴跌」：赔率应 < 1，判为消化型。"""
    closes = []
    v = 10.0
    for i in range(80):
        closes.append(v)
        v *= 1.0002
    # 塞三根大阳，其后都横盘回落
    for k in (30, 50, 70):
        closes[k] = closes[k - 1] * 1.08
        for j in range(k + 1, min(k + 6, len(closes))):
            closes[j] = closes[k] * 0.97   # 回落
    bars = mk(closes)
    a = sc.analyze(bars, big=5.0, hold=5)
    assert a["big_count"] >= 3
    assert a["odds"] is not None and a["odds"] < 1.0
    assert a["class"] == "grind"


def test_analyze_breakout_synthetic():
    """造「大阳后继续上行」：赔率应 > 1.5，判为加速型。"""
    closes = []
    v = 10.0
    for i in range(80):
        closes.append(v)
        v *= 1.0002
    for k in (30, 50, 70):
        closes[k] = closes[k - 1] * 1.08
        for j in range(k + 1, min(k + 6, len(closes))):
            closes[j] = closes[k] * (1 + 0.03 * (j - k))
    bars = mk(closes)
    a = sc.analyze(bars, big=5.0, hold=5)
    assert a["big_count"] >= 3
    assert a["odds"] is not None and a["odds"] > 1.5
    assert a["class"] == "breakout"


def test_analyze_insufficient():
    assert sc.analyze(mk([1, 2, 3])) is None


def test_render_smoke():
    bars = mk([10 + i * 0.1 for i in range(80)])
    a = sc.analyze(bars)
    a["code"] = "000000"
    txt = sc.render(a)
    assert "股性体检" in txt and "股性判定" in txt


def test_real_ruichuang_regression():
    """睿创微纳 688002 回归：应为拉高消化型（网络不可用时跳过）。"""
    try:
        bars = sc.load_bars("688002", n=300)
    except Exception:
        print("  [skip] 无网络，跳过真实数据回归")
        return
    if not bars or len(bars) < 100:
        print("  [skip] 数据不足，跳过真实数据回归")
        return
    a = sc.analyze(bars, big=5.0, hold=5)
    print("  睿创 odds=%.2f class=%s big_count=%d"
          % (a["odds"], a["class"], a["big_count"]))
    assert a["class"] in ("grind", "mixed")     # 不得判为加速型


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = bad = 0
    for fn in fns:
        try:
            fn()
            print("PASS  %s" % fn.__name__)
            ok += 1
        except AssertionError as e:
            print("FAIL  %s  %s" % (fn.__name__, e))
            bad += 1
        except Exception as e:
            print("ERROR %s  %s: %s" % (fn.__name__, type(e).__name__, e))
            bad += 1
    print("\n%d passed, %d failed" % (ok, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
