# -*- coding: utf-8 -*-
"""Nasdaq assetclass 探测的回归钉（2026-09-24 修）。

钉死一句话：**ETF（SOXX/SMH/SOXS/SNDQ/MUZ/SKDD…）必须走 assetclass=etf**。

原实现所有 Nasdaq 调用都写死 `assetclass=stocks`，后果（2026-09-24 实测）：
  · `nasdaq_info`  → SNDQ/SOXS/MUZ/SKDD/SOXX/SMH 的 primaryData 为空 ⇒
    **盘前价恒为 None**（做空工具全是 ETF ⇒ 做空腿永远没有盘前价）；
  · `/historical`  → 同批 ETF 的 tradesTable 为 **0 行** ⇒ 日线取不到。
反向也成立：`assetclass=etf` 对 SNDK/ILMN 为空。**两个口径各覆盖一半标的。**

本钉固定两件事：
  1. **正股路径不变** —— stocks 档命中即返回，不再多发一发；
  2. **ETF 路径自动回退** etf 档，并把命中档位缓存下来。

跑法：`python tests/screen/test_nasdaq_assetclass.py`（自带 main，纯离线，无网络）。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
import rule123 as R  # noqa: E402

FAIL = []


def eq(got, want, msg):
    if got != want:
        FAIL.append(f"{msg}：得到 {got!r}，期望 {want!r}")
        print(f"  FAIL {msg} → {got!r} != {want!r}")
    else:
        print(f"  ok   {msg} → {got!r}")


def _info(last, prev, sess="Pre-Market"):
    return {"marketStatus": sess,
            "primaryData": {"lastSalePrice": last,
                            "lastTradeTimestamp": "Sep 24, 2026 4:54 AM ET"},
            "secondaryData": {"lastSalePrice": prev}}


ROW = {"date": "09/22/2026", "open": "$560", "high": "$570", "low": "$555",
       "close": "$565", "volume": "1,000"}


def run():
    print("== 1. _assetclass_order 默认顺序与缓存优先 ==")
    R._US_AC_CACHE.clear()
    eq(R._assetclass_order("ZZZ"), ["stocks", "etf"], "默认 stocks 优先")
    R._US_AC_CACHE["ZZZ"] = "etf"
    eq(R._assetclass_order("ZZZ"), ["etf", "stocks"], "缓存命中档位排前")
    R._US_AC_CACHE.clear()

    orig_raw = R._nasdaq_info_raw
    orig_json = R.fetch_json_nasdaq

    print("== 2. 正股：stocks 档命中即返回（不请求 etf） ==")
    R._US_AC_CACHE.clear()
    calls = []

    def fake_raw(s, ac):
        calls.append(ac)
        return _info("$1,760.72", "$1,816.57") if ac == "stocks" \
            else _info("$0.01", "$0.02")

    R._nasdaq_info_raw = fake_raw
    try:
        spot, meta = R.nasdaq_info("SNDK")
        eq(spot, 1760.72, "正股现价取自 stocks 档")
        eq(calls, ["stocks"], "只发一发")
        eq(R._US_AC_CACHE.get("SNDK"), "stocks", "命中档位被缓存")
        eq(meta["prev_close"], 1816.57, "昨收取自 secondaryData")
        eq(meta["session"], "Pre-Market", "session 透传")
    finally:
        R._nasdaq_info_raw = orig_raw

    print("== 3. ETF：stocks 档空 → 自动回退 etf ==")
    R._US_AC_CACHE.clear()
    calls = []

    def fake_raw_etf(s, ac):
        calls.append(ac)
        return _info(None, None) if ac == "stocks" else _info("$10.48", "$9.88")

    R._nasdaq_info_raw = fake_raw_etf
    try:
        spot, meta = R.nasdaq_info("SNDQ")
        eq(spot, 10.48, "ETF 回退 etf 档取到现价")
        eq(calls, ["stocks", "etf"], "stocks 空后试 etf")
        eq(R._US_AC_CACHE.get("SNDQ"), "etf", "ETF 命中档位=etf")
        eq(meta["prev_close"], 9.88, "ETF 昨收正确")
    finally:
        R._nasdaq_info_raw = orig_raw

    print("== 4. ETF 二次调用：缓存档位优先，只发一发 ==")
    calls = []
    R._nasdaq_info_raw = fake_raw_etf
    try:
        R.nasdaq_info("SNDQ")
        eq(calls, ["etf"], "缓存命中后 etf 第一档即中")
    finally:
        R._nasdaq_info_raw = orig_raw

    print("== 5. 两档都空 → (None, 兜底 meta)，不抛 ==")
    R._US_AC_CACHE.clear()
    R._nasdaq_info_raw = lambda s, ac: _info(None, None)
    try:
        spot, meta = R.nasdaq_info("NOPE")
        eq(spot, None, "无现价返回 None")
        eq(isinstance(meta, dict), True, "仍返回 meta 字典（保持原形状）")
    finally:
        R._nasdaq_info_raw = orig_raw

    print("== 6. 两档都是网络错 → 如实抛（调用方才好回退） ==")
    R._US_AC_CACHE.clear()

    def boom(s, ac):
        raise RuntimeError("403")

    R._nasdaq_info_raw = boom
    try:
        try:
            R.nasdaq_info("NOPE2")
            eq("no-raise", "raise", "应当抛异常")
        except RuntimeError as e:
            eq(str(e), "403", "抛出网络错原文")
    finally:
        R._nasdaq_info_raw = orig_raw

    print("== 7. bars_from_nasdaq：ETF 回退 etf 档拿日线 ==")
    R._US_AC_CACHE.clear()
    calls = []

    def fake_json(url):
        ac = "etf" if "assetclass=etf" in url else "stocks"
        calls.append(ac)
        if "/historical" in url:
            return {"data": {"tradesTable": {"rows": [ROW] if ac == "etf" else []}}}
        return {"data": _info("$552.77", "$565.72")}

    R.fetch_json_nasdaq = fake_json
    R._nasdaq_info_raw = lambda s, ac: fake_json(f"?assetclass={ac}").get("data")
    try:
        bars, spot, meta = R.bars_from_nasdaq("SOXX")
        eq(len(bars), 1, "拿到 1 根日线")
        eq(bars[0]["c"], 565.0, "收盘解析正确")
        eq("stocks" in calls and "etf" in calls, True, "两档都试过")
        eq(R._US_AC_CACHE.get("SOXX"), "etf", "SOXX 命中档位=etf")
    finally:
        R.fetch_json_nasdaq = orig_json
        R._nasdaq_info_raw = orig_raw

    print("== 8. bars_from_nasdaq：正股只走 stocks 档 ==")
    R._US_AC_CACHE.clear()
    calls = []

    def fake_json2(url):
        ac = "etf" if "assetclass=etf" in url else "stocks"
        calls.append(ac)
        if "/historical" in url:
            return {"data": {"tradesTable": {"rows": [ROW]}}}
        return {"data": _info("$1,760.72", "$1,816.57")}

    R.fetch_json_nasdaq = fake_json2
    try:
        bars, spot, meta = R.bars_from_nasdaq("SNDK")
        eq(len(bars), 1, "正股拿到日线")
        eq("etf" not in calls, True, "正股未请求 etf 档")
    finally:
        R.fetch_json_nasdaq = orig_json
        R._US_AC_CACHE.clear()


if __name__ == "__main__":
    run()
    print()
    if FAIL:
        print(f"[FAIL] {len(FAIL)} 项失败")
        for f in FAIL:
            print("   -", f)
        sys.exit(1)
    print("[OK] 全部通过")
