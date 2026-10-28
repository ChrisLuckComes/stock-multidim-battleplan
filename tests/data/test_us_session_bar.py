# -*- coding: utf-8 -*-
"""CNBC 日线补丁不全时回退 Nasdaq。

2026-10-28 早间扫描 TEM：
  err = patch incomplete {'d':'2026-10-27','o':None,'h':None,'l':None,'c':None,'v':None}; last_bar=2026-10-26
Nasdaq 当日 OHLC 有效，却被踢出 T0 命中表。ILMN 同轮是 src=nasdaq 且 patched_cnbc=true。

不联网。`python -m pytest tests/data/test_us_session_bar.py -q`
"""
import us_session_bar as U

SESSION = "2026-10-27"
PRIOR = {
    "d": "2026-10-26", "o": 70.0, "h": 72.0, "l": 69.0, "c": 71.0, "v": 1000.0,
}
CNBC_STUB = {
    "d": "2026-10-27", "o": None, "h": None, "l": None, "c": None, "v": None,
}
NASDAQ_DAY = {
    "d": "2026-10-27", "o": 71.2, "h": 75.4, "l": 70.5, "c": 74.1, "v": 2200.0,
}
CNBC_OK = {
    "d": "2026-10-27", "o": 100.0, "h": 105.0, "l": 99.0, "c": 104.0, "v": 500.0,
}


def test_tem_incomplete_cnbc_uses_nasdaq_and_stays_in_scan():
    bars, info = U.resolve_session_bar(
        [PRIOR], SESSION, cnbc_bar=CNBC_STUB, nasdaq_bars=[PRIOR, NASDAQ_DAY])
    assert info["err"] is None
    assert info["patched_cnbc"] is False
    assert info["src"] == "nasdaq"
    assert bars[-1]["d"] == SESSION
    assert bars[-1]["c"] == 74.1
    assert bars[-1]["o"] == 71.2
    assert U.bar_ohlc_complete(bars[-1])


def test_stub_already_appended_is_replaced_by_nasdaq():
    dirty = [dict(PRIOR), dict(CNBC_STUB)]
    bars, info = U.resolve_session_bar(
        dirty, SESSION, cnbc_bar=CNBC_STUB, nasdaq_bars=[NASDAQ_DAY])
    assert info["err"] is None
    assert bars[-1]["c"] == 74.1
    assert all(U.bar_ohlc_complete(b) for b in bars)
    assert dirty[-1]["c"] is None


def test_ilmn_complete_cnbc_stays_optional_enrichment():
    bars, info = U.resolve_session_bar(
        [PRIOR, NASDAQ_DAY], SESSION, cnbc_bar=CNBC_OK,
        nasdaq_bars=[PRIOR, NASDAQ_DAY], src="nasdaq")
    assert info["err"] is None
    assert info["patched_cnbc"] is True
    assert info["src"] == "nasdaq"
    assert bars[-1]["c"] == 104.0


def test_existing_nasdaq_bar_survives_incomplete_cnbc():
    bars, info = U.resolve_session_bar(
        [PRIOR, NASDAQ_DAY], SESSION, cnbc_bar=CNBC_STUB, nasdaq_bars=None)
    assert info["err"] is None
    assert info["patched_cnbc"] is False
    assert bars[-1]["c"] == 74.1


def test_both_sources_missing_still_reports_patch_incomplete():
    bars, info = U.resolve_session_bar(
        [PRIOR], SESSION, cnbc_bar=CNBC_STUB, nasdaq_bars=[PRIOR])
    assert info["err"] == (
        "patch incomplete {'d':'2026-10-27','o':None,'h':None,"
        "'l':None,'c':None,'v':None}; last_bar=2026-10-26"
    )
    assert bars[-1]["d"] == "2026-10-26"
    assert info["bar"] is None
