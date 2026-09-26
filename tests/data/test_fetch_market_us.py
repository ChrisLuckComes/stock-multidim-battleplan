# -*- coding: utf-8 -*-
"""fetch_market 美股取数的回归钉（2026-09-22 修）。

钉死两条：
  1. **盘中昨收 = 末根已收盘 bar 的收盘**。过去无条件取 bars[-2]，
     而 Nasdaq 已把 secondaryData 改成 null ⇒ 昨收被算成前天的收盘
     （实测 2026-09-22 MRVL 交出 244.25，真值 257.38，误差 −2.8%）。
  2. **当日 open/high/low/volume 不得用上一交易日的值冒充**。
     `/historical` 盘中无当日 bar ⇒ 必须单发 realtime-trades；
     取不到就置 None + `ohlc_basis="unavailable"`。
     （`open/high/low` 与 A 股 fetch_ash 同名同义，下游按「今日」读是合理的。）

跑法：`python tests/data/test_fetch_market_us.py`（自带 main，无需 pytest）。
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
import fetch_market as FM

FAIL = []


def eq(got, want, msg):
    if got != want:
        FAIL.append(f"{msg}：得到 {got!r}，期望 {want!r}")
        print(f"  FAIL {msg} → {got!r} != {want!r}")
    else:
        print(f"  ok   {msg} → {got!r}")


BARS = [
    {"d": "2026-09-18", "o": 242.06, "h": 244.44, "l": 235.96, "c": 244.25, "v": 23635620.0},
    {"d": "2026-09-21", "o": 251.4, "h": 261.18, "l": 244.985, "c": 257.38, "v": 21466200.0},
]
TODAY_BAR = {"d": "2026-09-22", "o": 255.46, "h": 264.25, "l": 254.00,
             "c": 261.60, "v": 4952383.0}
TRADES = {"nlsVolume": "4,952,383", "previousClose": "$257.38",
          "todayHighLow": "$264.25/$254.00", "fiftyTwoWeekHighLow": "$329.88/$70.685"}

_ORIG_JSON, _ORIG_BARS, _ORIG_RANGE = FM._nasdaq_json, FM.nasdaq_bars, FM._nasdaq_day_range


def patch(status="Open", trades=TRADES, bars=None, trades_raises=False):
    info = {
        "marketStatus": status,
        "primaryData": {"lastSalePrice": "$261.60", "percentageChange": "+1.64%",
                        "lastTradeTimestamp": "Sep 22, 2026 10:23 AM ET", "isRealTime": True},
        "secondaryData": None,          # ★ 2026-09-22 起 Nasdaq 真的返回 null
    }

    def fake_json(url, *a, **k):
        if "realtime-trades" in url:
            if trades_raises:
                raise RuntimeError("realtime-trades 不可达")
            return {"data": {"topTable": {"rows": [trades] if trades else []}}}
        if "/info" in url:
            return {"data": info}
        return {"data": {}}

    FM._nasdaq_json = fake_json
    FM.nasdaq_bars = lambda sym, days=400: [dict(b) for b in (bars or BARS)]
    FM._nasdaq_day_range.__globals__["_nasdaq_json"] = fake_json


def teardown_function(function):
    """pytest 每跑完一个用例还原被测模块的全局。

    本文件设计成**独立脚本**（`python tests/data/test_fetch_market_us.py`，靠
    `main()` 的 finally 还原）。但 pytest 也会收集这些 `test_*` 函数，而 pytest
    从不调用 `main()` ⇒ `patch()` 把 `FM._nasdaq_json` / `FM.nasdaq_bars` /
    `_nasdaq_day_range.__globals__["_nasdaq_json"]` 永久留在被替换状态，
    后续用例跟着错：`tests/data/test_performance_paths.py::
    test_fetch_us_nasdaq_bars_and_info_overlap` 断言「历史日线 + info 两个请求真的
    并发重叠（max_inflight == 2）」，而残留的 `FM.nasdaq_bars` 是本地 lambda、根本
    不发请求 ⇒ inflight 只到 1、必挂。还原后单独跑与全量跑结果一致。
    """
    FM._nasdaq_json, FM.nasdaq_bars = _ORIG_JSON, _ORIG_BARS


def test_intraday_uses_today_range():
    print("\n[1] 盘中 + realtime-trades 可用 → 今日区间，昨收取末根已收盘 bar")
    patch()
    q = FM.fetch_us_nasdaq("MRVL")
    eq(q["session"], "Open", "session 透传")
    eq(q["spot"], 261.6, "实时价")
    eq(q["prev_close"], 257.38, "昨收 = 末根已收盘 bar（不是 bars[-2] 的 244.25）")
    eq(q["high"], 264.25, "今日最高")
    eq(q["low"], 254.0, "今日最低（不是昨日的 244.985）")
    eq(q["volume"], 4952383.0, "今日累计量")
    eq(q["ohlc_basis"], "today", "口径标记")
    eq(q["day_high"], 264.25, "day_high")
    eq(q["day_low"], 254.0, "day_low")
    eq(q["open"], None, "Nasdaq 无当日开盘价 → 留空，不用昨天的 251.4 冒充")


def test_intraday_range_unavailable_must_not_lie():
    print("\n[2] 盘中但当日区间取不到 → 一律留空，禁止沿用昨日")
    patch(trades_raises=True)
    q = FM.fetch_us_nasdaq("MRVL")
    eq(q["ohlc_basis"], "unavailable", "口径标记")
    eq((q["open"], q["high"], q["low"], q["volume"]), (None, None, None, None),
       "open/high/low/volume 全 None（不是昨日 251.4/261.18/244.985/21466200）")
    eq(q["prev_close"], 257.38, "昨收仍正确（退到末根已收盘 bar）")


def test_closed_session_keeps_last_bar():
    print("\n[3] 已收盘 → 不合成、不单发，末根 bar 即当日")
    patch(status="Closed", bars=BARS + [TODAY_BAR])
    q = FM.fetch_us_nasdaq("MRVL")
    eq(q["ohlc_basis"], "last_closed", "口径标记")
    eq(q["prev_close"], 257.38, "昨收 = bars[-2]（已收盘时末根才是当天）")
    eq(q["high"], 264.25, "最高取末根 bar")
    eq(q["low"], 254.0, "最低取末根 bar")
    eq(q["open"], 255.46, "开盘取末根 bar（已收盘时它是真值）")


def test_day_range_parser():
    print("\n[4] realtime-trades 解析")
    patch()
    d = FM._nasdaq_day_range("MRVL")
    eq(d["high"], 264.25, "解析 $264.25")
    eq(d["low"], 254.0, "解析 254.00")
    eq(d["prev_close"], 257.38, "解析 previousClose")
    eq(d["volume"], 4952383.0, "解析带千分位的 nlsVolume")
    patch(trades=None)
    eq(FM._nasdaq_day_range("MRVL"), None, "空 rows → None（不抛）")


def main():
    try:
        test_intraday_uses_today_range()
        test_intraday_range_unavailable_must_not_lie()
        test_closed_session_keeps_last_bar()
        test_day_range_parser()
    finally:
        FM._nasdaq_json, FM.nasdaq_bars = _ORIG_JSON, _ORIG_BARS
    print("\n" + "=" * 60)
    if FAIL:
        print(f"FAILED {len(FAIL)} 项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
