# -*- coding: utf-8 -*-
"""取数去重与快速降级回归。"""
import json
from unittest.mock import patch

import fetch_market as F
import pool_us
import rule123 as R
import watch_cn


def test_eastmoney_http_first():
    for module in (F, R):
        calls = []

        def fake_fetch(url, timeout=20, retries=2):
            calls.append((url, retries))
            return {"data": {}}

        with patch.object(module, "fetch_json", fake_fetch):
            module.fetch_json_fallback(
                "https://push2.eastmoney.com/api/qt/stock/get?secid=1.600000")
        assert calls == [
            ("http://push2.eastmoney.com/api/qt/stock/get?secid=1.600000", 2)
        ]


def test_eastmoney_https_is_single_fallback():
    calls = []

    def fake_fetch(url, timeout=20, retries=2):
        calls.append((url, retries))
        if url.startswith("http://"):
            raise OSError("http unavailable")
        return {"data": {}}

    with patch.object(F, "fetch_json", fake_fetch):
        F.fetch_json_fallback(
            "https://push2.eastmoney.com/api/qt/stock/get?secid=1.600000")
    assert calls == [
        ("http://push2.eastmoney.com/api/qt/stock/get?secid=1.600000", 2),
        ("https://push2.eastmoney.com/api/qt/stock/get?secid=1.600000", 1),
    ]


def test_yahoo_proxy_is_remembered():
    first = "http://127.0.0.1:10809"
    second = "http://127.0.0.1:7897"
    payload = {
        "chart": {
            "result": [{
                "timestamp": [1],
                "indicators": {"quote": [{
                    "open": [1], "high": [2], "low": [0.5],
                    "close": [1.5], "volume": [100],
                }]},
            }]
        }
    }
    calls = []

    def fake_proxy(url, proxy=None, timeout=20):
        calls.append(proxy)
        if proxy == first:
            raise OSError("closed")
        return json.loads(json.dumps(payload))

    old_proxy = R._YAHOO_PROXY
    R._YAHOO_PROXY = first
    try:
        with patch.object(R, "_proxy_list", lambda: [first, second]):
            with patch.object(R, "fetch_json_proxy", fake_proxy):
                R.bars_from_yahoo_min("MU")
        assert calls == [first, second]
        assert R._YAHOO_PROXY == second
        assert R._proxy_list()[0] == second
    finally:
        R._YAHOO_PROXY = old_proxy


def test_pool_us_quote_cache():
    calls = []
    quote = {"bars": [{"c": 1}, {"c": 2}]}
    pool_us._QUOTE_CACHE.clear()

    def fake_fetch(sym):
        calls.append(sym)
        return quote

    with patch.object(pool_us.F, "fetch_us", fake_fetch):
        assert pool_us.fetch_us("mu") is quote
        assert pool_us.fetch_us("MU") is quote
    assert calls == ["MU"]


def test_watch_cn_bars_cache():
    calls = []
    bars = [{"c": 1}, {"c": 2}]
    watch_cn._BARS_CACHE.clear()

    def fake_kline(prefix, code, n=140):
        calls.append((prefix, code, n))
        return bars

    with patch.object(watch_cn.scanner, "sina_kline", fake_kline):
        assert watch_cn.get_bars("sh", "600000") is bars
        assert watch_cn.get_bars("sh", "600000") is bars
    assert calls == [("sh", "600000", 140)]


if __name__ == "__main__":
    test_eastmoney_http_first()
    test_eastmoney_https_is_single_fallback()
    test_yahoo_proxy_is_remembered()
    test_pool_us_quote_cache()
    test_watch_cn_bars_cache()
    print("ok")
