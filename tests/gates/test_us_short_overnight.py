#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""做空卡夜盘现价接入回归（2026-11-16）—— 纯离线，不触网、不读真 token。

钉死：
  1. refine_night_spot 非夜盘 / --asof ⇒ 零请求、spot 原样、fresh=True；
  2. 夜盘有新鲜 Tiingo 价 ⇒ spot 被覆盖、spot_source=tiingo_24x5、日线 bars 不碰；
  3. 夜盘取数失败 / 陈旧 ⇒ 保留原 spot、fresh=False + note（fail-closed 依据）；
  4. us_short_watch.actionable 在无实时报价时拒进「今日可做空」；
  5. 两个做空卡确实调用了统一入口（接线回归）。
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from gates import us_overnight as N
from gates import us_short_watch as W

UTC = dt.timezone.utc
NIGHT = dt.datetime(2026, 11, 16, 1, 30, tzinfo=UTC)   # 周日 20:30 EST 夜盘
DAY = dt.datetime(2026, 11, 16, 15, 0, tzinfo=UTC)     # 周一 10:00 EST 盘中


def _fresh(symbol, fallback, now_utc=None):
    return {"fresh": True, "ok": True, "price": 101.5, "source": "tiingo_24x5",
            "note": "合成参考价", "stale_sec": 1.0, "volume": 1234.0,
            "bid": 101.4, "ask": 101.6}


def _stale(symbol, fallback, now_utc=None):
    return {"fresh": False, "ok": False, "price": 105.0, "source": "nasdaq_static",
            "note": "Tiingo 报价已滞留 900 秒", "stale_sec": 900.0}


# ───────────────────────── 时段与 asof ─────────────────────────
def test_day_session_is_untouched():
    calls = []

    def fetcher(sym, fallback, now_utc=None):
        calls.append(sym)
        return _fresh(sym, fallback)

    px, meta = N.refine_night_spot("NVDA", 100.0, {"spot_source": "nasdaq"},
                                   resolver=fetcher, now_utc=DAY)
    assert px == 100.0 and calls == []
    assert meta["spot_source"] == "nasdaq"
    assert meta["quote_fresh"] is True and meta["night_quote"] is None


def test_asof_disables_live_spot():
    calls = []

    def fetcher(sym, fallback, now_utc=None):
        calls.append(sym)
        return _fresh(sym, fallback)

    px, meta = N.refine_night_spot("NVDA", 100.0, {}, allow=False,
                                   resolver=fetcher, now_utc=NIGHT)
    assert px == 100.0 and calls == [] and meta["quote_fresh"] is True


# ───────────────────────── 夜盘取价 ─────────────────────────
def test_night_fresh_price_overwrites_spot_only():
    meta_in = {"spot_source": "yahoo_fullday", "session": "Overnight", "foo": "bar"}
    px, meta = N.refine_night_spot("NVDA", 100.0, meta_in,
                                   resolver=_fresh, now_utc=NIGHT)
    assert px == 101.5
    assert meta["spot_source"] == "tiingo_24x5"
    assert meta["quote_fresh"] is True
    assert meta["foo"] == "bar" and meta_in["spot_source"] == "yahoo_fullday"  # 不改入参
    # ★ 只碰 spot/meta，绝不产出 bars（日线结构/ATR 不被污染）
    assert "bars" not in meta


def test_night_stale_keeps_original_spot_and_marks_stale():
    px, meta = N.refine_night_spot("NVDA", 100.0, {"spot_source": "yahoo_fullday"},
                                   resolver=_stale, now_utc=NIGHT)
    assert px == 100.0                         # 静态价保留，不拿 stale 105 覆盖
    assert meta["quote_fresh"] is False
    assert "滞留" in meta["quote_note"]
    assert meta["spot_source"] == "yahoo_fullday"


def test_resolver_exception_is_swallowed():
    def boom(sym, fallback, now_utc=None):
        raise RuntimeError("network down")

    px, meta = N.refine_night_spot("NVDA", 100.0, {}, resolver=boom, now_utc=NIGHT)
    assert px == 100.0 and meta["quote_fresh"] is False
    assert "异常" in meta["quote_note"]


# ───────────────────────── 可执行闸门 ─────────────────────────
def test_actionable_rejects_stale_night_quote():
    base = {"verdict": "推荐(优先)", "ctx_age": 3}
    assert W.actionable(dict(base, quote_fresh=True)) is True
    assert W.actionable(dict(base)) is True                       # 老数据无字段 ⇒ 兼容放行
    assert W.actionable(dict(base, quote_fresh=False,
                             quote_note="Tiingo 取数失败")) is False


# ───────────────────────── 接线回归 ─────────────────────────
def test_both_short_cards_import_unified_entry():
    import gates.us_short as S
    assert S.refine_night_spot is N.refine_night_spot
    assert W.refine_night_spot is N.refine_night_spot
    # CLI 破例开关存在（显式才允许拿静态价判）
    src = open(S.__file__, encoding="utf-8").read()
    assert "--allow-stale-night-quote" in src and "--asof" in src
    src2 = open(W.__file__, encoding="utf-8").read()
    assert "--asof" in src2 and "quote_fresh" in src2
