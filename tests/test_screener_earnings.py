# -*- coding: utf-8 -*-
"""screener/earnings.py 回归测试（老罗 2026-10-27「正式报告优先」口径）。

全部用**构造数据**，不打网络。每条用例带真实反例。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from screener import earnings as E  # noqa: E402


def _fc(report_date, ptype="预增", lo=50.0, hi=80.0, code="004", notice="2026-07-01"):
    return {"SECURITY_CODE": "300839", "REPORT_DATE": report_date + " 00:00:00",
            "PREDICT_TYPE": ptype, "PREDICT_FINANCE_CODE": code,
            "PREDICT_FINANCE": "归属于上市公司股东的净利润",
            "ADD_AMP_LOWER": lo, "ADD_AMP_UPPER": hi, "NOTICE_DATE": notice + " 00:00:00"}


def _rp(report_date, sjltz, ystz=10.0, roe=8.0, update="2026-10-21"):
    return {"SECURITY_CODE": "300839", "REPORTDATE": report_date + " 00:00:00",
            "SJLTZ": sjltz, "YSTZ": ystz, "WEIGHTAVG_ROE": roe,
            "PARENT_NETPROFIT": 1e8, "UPDATE_DATE": update + " 00:00:00"}


# ── 正式报告口径 ──────────────────────────────────────────────────────────
def test_tone_from_report_strong():
    t = E.tone_from_report(_rp("2026-09-30", 194.1, 56.2, 7.70))
    assert t["tone"] == "向好" and t["strong"] is True
    assert t["np_yoy"] == pytest.approx(194.1)
    assert t["rev_yoy"] == pytest.approx(56.2)
    assert t["roe"] == pytest.approx(7.70)
    assert t["src"] == "正式报告"


def test_tone_from_report_weak_and_bad():
    assert E.tone_from_report(_rp("2026-09-30", 12.0))["tone"] == "向好(弱)"
    assert E.tone_from_report(_rp("2026-09-30", 12.0))["strong"] is False
    assert E.tone_from_report(_rp("2026-09-30", -5.0))["tone"] == "中性偏弱"
    assert E.tone_from_report(_rp("2026-09-30", -680.7))["tone"] == "向差"
    assert E.tone_from_report(_rp("2026-09-30", -680.7))["strong"] is False


def test_tone_from_report_none():
    assert E.tone_from_report(None) is None


# ── 预告口径 ──────────────────────────────────────────────────────────────
def test_tone_from_forecast():
    t = E.tone_from_forecast([_fc("2026-03-31", "扭亏", 184.0, 205.0)])
    assert t["tone"] == "向好" and t["strong"] is True
    assert t["report_date"] == "2026-03-31"
    assert t["src"] == "业绩预告"


def test_tone_from_forecast_empty():
    t = E.tone_from_forecast([])
    assert t["state"] == "none" and t["tone"] == "未取到"


def test_tone_from_forecast_picks_latest_period():
    """同一票多期预告 ⇒ 必须取最新一期（排序降序时 rows[0] 即最新）。"""
    rows = [_fc("2026-09-30", "预减", -30.0, -10.0), _fc("2026-03-31", "扭亏", 184.0, 205.0)]
    t = E.tone_from_forecast(rows)
    assert t["report_date"] == "2026-09-30"
    assert t["tone"] == "向差"


# ── merge_tone：正式报告优先（核心）──────────────────────────────────────
def test_merge_prefers_official_report_when_newer():
    """300839 实测：预告 3-31 期扭亏 +184~205%（判"向好"），
    但三季报 9-30 期已于 10-21 披露（+194.1%）⇒ 必须以正式报告为准。"""
    m = E.merge_tone([_fc("2026-03-31", "扭亏", 184.0, 205.0)],
                      [_rp("2026-09-30", 194.1, 56.2, 7.70)])
    assert m["src"] == "正式报告"
    assert m["report_date"] == "2026-09-30"
    assert m["np_yoy"] == pytest.approx(194.1)
    assert m["forecast"]["report_date"] == "2026-03-31"
    assert "已让位于正式报告" in m["note"]


def test_merge_official_report_overrides_bad_forecast():
    """反例：一季度预告"预增 +80%"，三季报实际 -45% ⇒ 绝不能判「业绩超预期=加分」。"""
    m = E.merge_tone([_fc("2026-03-31", "预增", 60.0, 80.0)],
                      [_rp("2026-09-30", -45.0, -12.0, 3.1)])
    assert m["src"] == "正式报告"
    assert m["tone"] == "向差"
    assert m["strong"] is False
    assert m["np_yoy"] == pytest.approx(-45.0)


def test_merge_same_period_prefers_report():
    m = E.merge_tone([_fc("2026-09-30", "预增", 60.0, 80.0)],
                      [_rp("2026-09-30", 5.0, 1.0, 2.0)])
    assert m["src"] == "正式报告"
    assert m["np_yoy"] == pytest.approx(5.0)


def test_merge_keeps_forecast_when_forecast_newer():
    """正式报告还没出（最新只到 3-31）而预告已到 9-30 ⇒ 仍用预告，并写明原因。"""
    m = E.merge_tone([_fc("2026-09-30", "预增", 60.0, 80.0)],
                      [_rp("2026-03-31", 10.0, 1.0, 2.0)])
    assert m["src"] == "业绩预告"
    assert m["report_date"] == "2026-09-30"
    assert "仍用预告" in m["note"]


def test_merge_no_report_uses_forecast():
    m = E.merge_tone([_fc("2026-09-30", "预增", 60.0, 80.0)], [])
    assert m["src"] == "业绩预告"
    assert m["state"] == "ok"


def test_merge_no_forecast_uses_report():
    m = E.merge_tone([], [_rp("2026-09-30", 194.1)])
    assert m["src"] == "正式报告"
    assert m["state"] == "ok"
    assert m["tone"] == "向好"


def test_merge_both_empty():
    m = E.merge_tone([], [])
    assert m["state"] == "none"


# ── latest_report_row ────────────────────────────────────────────────────
def test_latest_report_row():
    rows = [_rp("2026-09-30", 1.0), _rp("2026-06-30", 2.0)]
    assert E.latest_report_row(rows)["REPORTDATE"].startswith("2026-09-30")
    assert E.latest_report_row([]) is None
    assert E.latest_report_row(None) is None
