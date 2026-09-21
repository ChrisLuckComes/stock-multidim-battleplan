# -*- coding: utf-8 -*-
"""取数去重与快速降级回归。"""
import json
import os
import tempfile
import threading
import time
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


def test_proxy_never_probes_local_ports_by_default():
    """默认必须只直连 —— 绝不能去连本机常见代理端口。

    背景（2026-09-21 用户反馈「干扰到了我的 clash verge 代理」）：原实现硬编码去连
    127.0.0.1:{7897,7890,7891,10809,1080}，而 7897/7890 正是 Clash / Clash Verge 的
    默认端口，且没有直连兜底 —— 等于脚本反复去连用户自己的代理客户端，每次都等
    20s 超时。这条测试钉死：默认候选里不许出现任何 127.0.0.1 端口。
    """
    keys = ("WB_US_PROXY", "WB_NO_PROXY", "WB_US_PROXY_AUTOPROBE")
    saved = {k: os.environ.pop(k, None) for k in keys}
    saved_file = R.PROXY_FILE
    old_proxy = R._YAHOO_PROXY
    R._YAHOO_PROXY = None
    R.PROXY_FILE = os.path.join(tempfile.gettempdir(), "_no_such_proxy_file.txt")
    try:
        assert R._proxy_list() == [None], "默认必须是「只直连」"
        assert not any("127.0.0.1" in (p or "") for p in R._proxy_list()), \
            "默认候选里不许出现任何本机端口（会干扰用户的代理客户端）"
        os.environ["WB_US_PROXY"] = "http://127.0.0.1:9999"
        assert R._proxy_list() == ["http://127.0.0.1:9999", None], "显式配置要生效且带直连兜底"
        os.environ["WB_US_PROXY"] = "off"
        assert R._proxy_list() == [None], "WB_US_PROXY=off = 强制直连"
        del os.environ["WB_US_PROXY"]
        os.environ["WB_NO_PROXY"] = "1"
        assert R._proxy_list() == [None], "WB_NO_PROXY=1 = 强制直连"
        del os.environ["WB_NO_PROXY"]
        os.environ["WB_US_PROXY_AUTOPROBE"] = "1"
        assert any("127.0.0.1" in p for p in R._proxy_list() if p), \
            "显式开 AUTOPROBE 才允许探端口"
    finally:
        R._YAHOO_PROXY = old_proxy
        R.PROXY_FILE = saved_file
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)


def test_pool_us_quote_cache():
    calls = []
    quote = {"bars": [{"c": 1}, {"c": 2}], "src": "net"}
    pool_us._QUOTE_CACHE.clear()
    old_dirs, old_snap, old_cache = pool_us.SNAP_DIRS, pool_us.USE_SNAP, pool_us.USE_CACHE
    # 关掉快照/磁盘缓存，单独验进程内去重（否则会读到真实 data/ 下的快照）
    pool_us.SNAP_DIRS, pool_us.USE_SNAP, pool_us.USE_CACHE = [], False, False

    def fake_fetch(sym):
        calls.append(sym)
        return quote

    try:
        with patch.object(pool_us.F, "fetch_us", fake_fetch):
            assert pool_us.fetch_us("mu") is quote
            assert pool_us.fetch_us("MU") is quote
        assert calls == ["MU"]
    finally:
        pool_us.SNAP_DIRS, pool_us.USE_SNAP, pool_us.USE_CACHE = old_dirs, old_snap, old_cache


def test_watch_cn_bars_cache():
    calls = []
    bars = [{"c": 1}, {"c": 2}]
    watch_cn._BARS_CACHE.clear()
    old_dirs, old_snap, old_cache = watch_cn.SNAP_DIRS, watch_cn.USE_SNAP, watch_cn.USE_CACHE
    watch_cn.SNAP_DIRS, watch_cn.USE_SNAP, watch_cn.USE_CACHE = [], False, False

    def fake_ash(prefix, code, n=140, **_kw):
        calls.append((prefix, code, n))
        return bars, "net", []

    try:
        with patch.object(watch_cn, "ash_bars", fake_ash):
            assert watch_cn.get_bars("sh", "600000") is bars
            assert watch_cn.get_bars("sh", "600000") is bars
        assert calls == [("sh", "600000", 140)]
    finally:
        watch_cn.SNAP_DIRS, watch_cn.USE_SNAP, watch_cn.USE_CACHE = old_dirs, old_snap, old_cache


def test_fetch_ash_quote_and_kline_overlap():
    inflight = []
    max_inflight = [0]
    lock = threading.Lock()

    def fake_fallback(url, timeout=20, retries=2):
        with lock:
            inflight.append(1)
            max_inflight[0] = max(max_inflight[0], len(inflight))
        time.sleep(0.05)
        with lock:
            inflight.pop()
        if "kline" in url:
            return {"data": {"klines": ["2026-01-02,10,10.5,11,9.5,1000"]}}
        return {"data": {
            "f43": 10.5, "f44": 11, "f45": 9.5, "f46": 10, "f47": 1000,
            "f48": 1, "f57": "600000", "f58": "TEST", "f60": 10,
        }}

    with patch.object(F, "fetch_json_fallback", fake_fallback):
        q = F.fetch_ash("1.600000")
    assert max_inflight[0] == 2
    assert q["spot"] == 10.5
    assert len(q["bars"]) == 1


def test_fetch_us_nasdaq_bars_and_info_overlap():
    inflight = []
    max_inflight = [0]
    lock = threading.Lock()

    def fake_json(url):
        with lock:
            inflight.append(1)
            max_inflight[0] = max(max_inflight[0], len(inflight))
        time.sleep(0.05)
        with lock:
            inflight.pop()
        if "historical" in url:
            return {"data": {"tradesTable": {"rows": [{
                "date": "09/18/2026", "open": "10", "high": "11", "low": "9",
                "close": "10.5", "volume": "1000"}]}}}
        if "/info?" in url:
            return {"data": {
                "companyName": "TEST", "exchange": "NASDAQ",
                "marketStatus": "Closed",
                "primaryData": {
                    "lastSalePrice": "$10.50", "percentageChange": "5",
                    "lastTradeTimestamp": "Closed", "isRealTime": False,
                    "bidPrice": "10", "askPrice": "11",
                },
                "secondaryData": {"lastSalePrice": "$10.00"},
            }}
        raise AssertionError(url)

    with patch.object(F, "_nasdaq_json", fake_json):
        q = F.fetch_us_nasdaq("NET")
    assert max_inflight[0] == 2
    assert q["spot"] == 10.5
    assert q["prev_close"] == 10.0
    assert len(q["bars"]) == 1


if __name__ == "__main__":
    test_eastmoney_http_first()
    test_eastmoney_https_is_single_fallback()
    test_yahoo_proxy_is_remembered()
    test_proxy_never_probes_local_ports_by_default()
    test_pool_us_quote_cache()
    test_watch_cn_bars_cache()
    test_fetch_ash_quote_and_kline_overlap()
    test_fetch_us_nasdaq_bars_and_info_overlap()
    print("ok")
