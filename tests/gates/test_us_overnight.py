#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美股夜盘数据层回归 —— 纯离线，不触网、不读真 token。

覆盖：ET 时段边界（含 DST/周五收摊）、Tiingo 快照解析、陈旧判定、
非夜盘零改动、夜盘打标与追加参考价 bar、decide_us fail-closed、render 上屏。
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "gates")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from gates import us_overnight as N
from gates import us_session_decide as U

UTC = dt.timezone.utc
UDT = lambda y, m, d, h, mi: dt.datetime(y, m, d, h, mi, tzinfo=UTC)  # noqa: E731


# ───────────────────────── 时段边界 ─────────────────────────
@pytest.mark.parametrize("now,expect", [
    (UDT(2026, 11, 16, 1, 8), "overnight"),    # 周日 20:08 EST，夜盘开
    (UDT(2026, 11, 16, 8, 59), "overnight"),   # 周一 03:59 EST，夜盘尾
    (UDT(2026, 11, 16, 9, 0), "pre"),          # 周一 04:00 EST，盘前起
    (UDT(2026, 11, 16, 14, 29), "pre"),
    (UDT(2026, 11, 16, 14, 30), "regular"),
    (UDT(2026, 11, 16, 20, 59), "regular"),
    (UDT(2026, 11, 16, 21, 0), "post"),
    (UDT(2026, 11, 17, 1, 0), "overnight"),    # 周一 20:00 EST
    (UDT(2026, 11, 21, 1, 0), "closed"),       # 周五 20:00 EST ⇒ 无夜盘
    (UDT(2026, 11, 21, 12, 0), "closed"),      # 周六
    (UDT(2026, 11, 15, 12, 0), "closed"),      # 周日白天
    (UDT(2026, 7, 14, 14, 30), "regular"),     # 夏令时 10:30 EDT
])
def test_us_session_boundaries(now, expect):
    assert N.us_session_at(now) == expect


def test_is_overnight_helper():
    assert N.is_overnight(UDT(2026, 11, 16, 1, 8)) is True
    assert N.is_overnight(UDT(2026, 11, 16, 14, 30)) is False


# ───────────────────────── 报价解析 ─────────────────────────
def test_parse_quote_list_and_dict():
    row = {"ticker": "NVDA", "timestamp": "2026-11-16T01:08:00Z",
           "tngoLast": 167.83, "lqRefPrice": 167.83,
           "prevClose": 166.19, "prevDate": "2026-11-12"}
    q1 = N.parse_quote([row])
    q2 = N.parse_quote(row)
    q3 = N.parse_quote({"data": [row]})
    assert q1["price"] == 167.83 and q2["price"] == 167.83 and q3["price"] == 167.83
    assert q1["prev_close"] == 166.19


def test_parse_quote_rejects_unusable():
    assert N.parse_quote({}) is None
    assert N.parse_quote([{"ticker": "NVDA", "timestamp": "x"}]) is None
    assert N.parse_quote([{"tngoLast": 0}]) is None
    assert N.parse_quote([{"lqRefPrice": 168.0}])["price"] == 168.0   # 备用字段


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _opener(payload, calls):
    def op(req, timeout=None):
        calls.append(req.full_url)
        return _Resp(payload)
    return op


def test_fetch_quote_fresh():
    now = UDT(2026, 11, 16, 1, 8)
    calls = []
    op = _opener([{"ticker": "NVDA", "timestamp": now.isoformat().replace("+00:00", "Z"),
                   "tngoLast": 167.83, "prevClose": 166.19}], calls)
    q = N.fetch_quote("NVDA", token="FAKE", opener=op, now_utc=now)
    assert q["ok"] is True and q["price"] == 167.83 and q["stale_sec"] == 0.0
    assert q["source"] == "tiingo_24x5" and len(calls) == 1
    assert "FAKE" not in json.dumps(q)                    # 异常/返回体都不带 token


def test_fetch_quote_stale_and_no_ts(monkeypatch):
    monkeypatch.setenv("TIINGO_MAX_STALE_SEC", "300")
    now = UDT(2026, 11, 16, 1, 8)
    old = (now - dt.timedelta(seconds=600)).isoformat().replace("+00:00", "Z")
    q = N.fetch_quote("NVDA", token="FAKE",
                      opener=_opener([{"timestamp": old, "tngoLast": 167.0}], []),
                      now_utc=now)
    assert q["ok"] is False and q["stale_sec"] == 600.0 and "滞留" in q["note"]
    q2 = N.fetch_quote("NVDA", token="FAKE",
                       opener=_opener([{"tngoLast": 167.0}], []), now_utc=now)
    assert q2["ok"] is False and "时间戳" in q2["note"]


def test_fetch_quote_no_token_and_network_error(monkeypatch):
    # ★ 必须隔离本机 .env.local：老罗本机已配 TIINGO_TOKEN，否则「无 token」分支永远测不到
    monkeypatch.setattr(N, "read_token", lambda: "")
    # 不传 token 且进程无 token ⇒ 一次请求都不发
    def boom(req, timeout=None):
        raise AssertionError("不应发起请求")
    q = N.fetch_quote("NVDA", opener=boom)
    assert q["ok"] is False and "未配置" in q["note"]

    def dead(req, timeout=None):
        raise OSError("offline")
    q2 = N.fetch_quote("NVDA", token="FAKE", opener=dead)
    assert q2["ok"] is False and "取数失败" in q2["note"]
    assert "FAKE" not in q2["note"]


# ───────────────────────── 统一入口 / 打标 ─────────────────────────
def test_non_overnight_never_touches_network():
    calls = []

    def fetcher(symbol, now_utc=None):
        calls.append(symbol)
        return {"ok": True, "price": 1.0}
    q = N.resolve_night_quote("NVDA", 100.0,
                              now_utc=UDT(2026, 11, 16, 14, 30), fetcher=fetcher)
    assert calls == [] and q["fresh"] is True and q["price"] == 100.0
    assert q["source"] == "nasdaq_static"


def test_overnight_fresh_quote_wins():
    def fetcher(symbol, now_utc=None):
        return {"ok": True, "price": 101.5, "stale_sec": 2.0,
                "raw_ts": "x", "note": "ok"}
    q = N.resolve_night_quote("NVDA", 100.0,
                              now_utc=UDT(2026, 11, 16, 1, 8), fetcher=fetcher)
    assert q["fresh"] is True and q["price"] == 101.5 and q["source"] == "tiingo_24x5"


def test_overnight_stale_is_fail_closed():
    def fetcher(symbol, now_utc=None):
        return {"ok": False, "note": "未配置 TIINGO_TOKEN"}
    q = N.resolve_night_quote("NVDA", 100.0,
                              now_utc=UDT(2026, 11, 16, 1, 8), fetcher=fetcher)
    assert q["fresh"] is False and q["price"] == 100.0
    assert q["source"] == "nasdaq_static"


def _mkday(n=30, price=100.0):
    mins = [{"t": "%05d" % (i + 1), "o": price, "h": price, "l": price,
             "c": price, "v": 1000.0, "amt": 0.0, "avg": price}
            for i in range(n)]
    return {"code": "NVDA", "name": "NVDA", "date": "2026-11-16",
            "pre": 99.0, "minutes": mins}


def test_apply_night_quote_appends_zero_volume_bar(monkeypatch):
    monkeypatch.setattr(N, "is_overnight", lambda now_utc=None: True)
    day = _mkday()

    def resolver(symbol, fallback, now_utc=None):
        return {"session": "overnight", "price": 101.5, "source": "tiingo_24x5",
                "fresh": True, "stale_sec": 2.0, "note": "ok"}
    N.apply_night_quote(day, "NVDA", resolver=resolver,
                        now_utc=UDT(2026, 11, 16, 1, 8))
    assert len(day["minutes"]) == 31 and day["minutes"][-1]["c"] == 101.5
    assert day["minutes"][-1]["v"] == 0.0 and day["minutes"][-1]["amt"] == 0.0
    assert day["quote_fresh"] is True and day["night_quote"]["source"] == "tiingo_24x5"


def test_apply_night_quote_stale_does_not_append(monkeypatch):
    monkeypatch.setattr(N, "is_overnight", lambda now_utc=None: True)
    day = _mkday()

    def resolver(symbol, fallback, now_utc=None):
        return {"session": "overnight", "price": 100.0, "source": "nasdaq_static",
                "fresh": False, "note": "未配置 TIINGO_TOKEN"}
    N.apply_night_quote(day, "NVDA", resolver=resolver)
    assert len(day["minutes"]) == 30 and day["quote_fresh"] is False


def test_apply_night_quote_disabled_for_asof(monkeypatch):
    monkeypatch.setattr(N, "is_overnight", lambda now_utc=None: True)

    def resolver(symbol, fallback, now_utc=None):
        raise AssertionError("--asof 回放绝不能用实时价")
    day = _mkday()
    N.apply_night_quote(day, "NVDA", allow=False, resolver=resolver)
    assert day["quote_fresh"] is True and day["night_quote"] is None


def test_load_us_day_wires_gate(monkeypatch):
    """数据层单一真源：所有美股调用方都拿到 quote_fresh / night_quote 标记。"""
    mins = _mkday()["minutes"]
    monkeypatch.setattr(U, "fetch_intraday_nasdaq",
                        lambda s: (list(mins), None, "NVDA"))
    monkeypatch.setattr(N, "is_overnight", lambda now_utc=None: False)
    day, pre = U.load_us_day("NVDA")
    assert day["quote_fresh"] is True and day["night_quote"] is None
    assert len(day["minutes"]) == 30                      # 非夜盘零改动


# ───────────────────────── 决策闸 / 上屏 ─────────────────────────
def _decide(**kw):
    day = _mkday()
    from tests.us_wyckoff_fixtures import with_verified_markup
    return with_verified_markup(U.decide_us)(day, {"last": day["minutes"][-1]},
                       99.0, 97.0, 105.0, pulse_enabled=False, **kw)


def test_decide_us_fail_closed_on_stale_night_quote():
    d = _decide(quote_fresh=False, quote_note="未配置 TIINGO_TOKEN")
    assert d["action"] == "不买"
    assert d["limit_buy"] is None and d["size"] is None
    assert "夜盘" in d["reason"] and "未配置 TIINGO_TOKEN" in d["reason"]
    assert d["arm_price"] is None                          # 不给可执行盯价单


def test_decide_us_default_unchanged():
    """不传 quote_fresh 的既有调用方（含全部老测试）行为零改动。"""
    d = _decide()
    assert d["action"] in ("能买", "不买")
    assert "夜盘" not in d["reason"]


def test_render_us_shows_night_quote_block():
    d = _decide(quote_fresh=False, quote_note="未配置 TIINGO_TOKEN")
    d["night_quote"] = {"session": "overnight", "source": "nasdaq_static",
                        "fresh": False, "stale_sec": None,
                        "note": "未配置 TIINGO_TOKEN"}
    txt = U.render_us(d)
    assert "夜盘数据" in txt and "实时 否" in txt and "未配置 TIINGO_TOKEN" in txt
    assert "结论  不买" in txt


# ───────────────────────── 真实返回体回归 fixture ─────────────────────────
# 2026-11-16 夜盘（ET 周日 21:17 / 北京周一 10:17）Tiingo 免费 Starter 实测抓回，
# 字段一个不改，作为「文档 vs 真实」的回归锚：文档没写 volume / lqBid* / lqAsk*。
LIVE_NVDA = [{
    "ticker": "NVDA", "timestamp": "2026-11-15T21:17:52.013355036-05:00",
    "open": 165.08, "high": 165.72, "low": 161.03, "tngoLast": 163.2,
    "prevClose": 162.85, "prevDate": "2026-11-13", "volume": 935222.0,
    "lqSpread": 0.000429, "lqBidPrice": 163.185, "lqBidSize": 280,
    "lqRefPrice": 163.2, "lqAskPrice": 163.255, "lqAskSize": 220}]

LIVE_ARKG = [{
    "ticker": "ARKG", "timestamp": "2026-11-15T21:04:45.672816750-05:00",
    "open": 42.92, "high": 42.92, "low": 42.64, "tngoLast": 42.66,
    "prevClose": 43.2, "prevDate": "2026-11-13", "volume": 0.0,
    "lqSpread": 0.011199, "lqBidPrice": 42.515, "lqBidSize": 1520,
    "lqRefPrice": 42.66, "lqAskPrice": 42.995, "lqAskSize": 180}]

LIVE_NOW = dt.datetime.fromisoformat(LIVE_NVDA[0]["timestamp"]).astimezone(UTC)


def test_live_fixture_fields():
    q = N.parse_quote(LIVE_NVDA)
    assert q["price"] == 163.2 and q["prev_close"] == 162.85
    assert q["volume"] == 935222.0                 # session 累计量，不是分钟量
    assert q["bid"] == 163.185 and q["ask"] == 163.255
    assert abs(q["spread"] - 0.000429) < 1e-9
    assert q["open"] == 165.08 and q["high"] == 165.72 and q["low"] == 161.03


def test_live_fixture_fetch_fresh():
    q = N.fetch_quote("NVDA", token="FAKE",
                      opener=_opener(LIVE_NVDA, []), now_utc=LIVE_NOW)
    assert q["ok"] is True and q["price"] == 163.2 and q["stale_sec"] == 0.0
    assert q["bid"] == 163.185 and q["volume"] == 935222.0
    assert q["raw_ts"] == LIVE_NVDA[0]["timestamp"]


def test_live_fixture_thin_name_is_stale():
    """ARKG 夜盘 volume=0、时间戳比 NVDA 早 13 分钟 ⇒ 陈旧闸必须拦下。"""
    q = N.fetch_quote("ARKG", token="FAKE",
                      opener=_opener(LIVE_ARKG, []), now_utc=LIVE_NOW)
    assert q["ok"] is False and q["stale_sec"] > 300
    assert "滞留" in q["note"]


def test_night_quote_book_reaches_render():
    d = _decide(quote_fresh=False, quote_note="x")
    d["night_quote"] = {"session": "overnight", "source": "nasdaq_static",
                        "fresh": True, "stale_sec": 2.0, "note": "ok",
                        "bid": 753.885, "ask": 753.965, "spread": 0.000106,
                        "volume": 63960.0, "prev_close": 754.37}
    txt = U.render_us(d)
    # render 用 %.2f 显示盘口 ⇒ 753.885 / 753.965 显示为 753.88 / 753.97
    assert "夜盘参考盘口" in txt and "753.88" in txt and "753.97" in txt
    assert "0.011%" in txt and "session累计量 63960" in txt
