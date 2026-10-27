# -*- coding: utf-8 -*-
"""screener/report_calendar.py 回归测试（老罗 2026-10-27 三条铁律）。

铁律① 窗口按**交易日**算（3 个交易日），不是自然日
铁律② **越晚出报告的越垃圾** ⇒ 晚出降级
铁律③ **早出预报不能漏** ⇒ 预告已出即「业绩已明」
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from screener import report_calendar as RC  # noqa: E402


# ── ① 交易日窗口 ───────────────────────────────────────────────────────
def test_q3_window_is_trade_days_not_calendar_days():
    """三季报法定截止 2026-10-31 是**周六** ⇒ 实际截止交易日 = 10-30（周五）。
    窗口 = 截止前 3 个交易日 ⇒ [10-27, 10-28, 10-29, 10-30]。"""
    w = RC.current_window("2026-10-27")
    assert w is not None
    assert w["name"] == "三季报"
    assert w["report_date"] == "2026-09-30"
    assert w["deadline"] == "2026-10-31"
    assert w["deadline_trade_day"] == "2026-10-30"
    assert w["trade_days"] == ["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]


def test_window_start_boundary():
    """10-26 距截止 4 个交易日 ⇒ 窗口外；10-27 进窗口。"""
    assert RC.current_window("2026-10-26") is None
    assert RC.current_window("2026-10-27") is not None


def test_after_days_grace_trade_day():
    """截止后 1 个交易日仍在宽限内（延期票要标出来），再往后出窗。"""
    assert RC.current_window("2026-11-02") is not None
    assert RC.current_window("2026-11-03") is None


def test_q1_window():
    w = RC.current_window("2026-04-28")
    assert w and w["name"] == "一季报"
    assert w["report_date"] == "2026-03-31"
    assert w["deadline"] == "2026-04-30"


def test_h1_window():
    w = RC.current_window("2026-08-28")
    assert w and w["name"] == "中报"
    assert w["report_date"] == "2026-06-30"
    assert w["deadline"] == "2026-08-31"


def test_annual_window_crosses_year():
    """年报：报告期 2025-12-31，法定截止**次年** 04-30（与一季报同日）。"""
    w = RC.current_window("2026-04-28")
    assert w and w["name"] == "一季报"      # 同距时取报告期更新的（一季报）
    assert "年报" in (w.get("also_in_window") or [])


def test_outside_window_returns_none():
    assert RC.current_window("2026-06-15") is None
    assert RC.current_window("2026-12-30") is None


def test_before_days_is_trade_days():
    """x 天可调（交易日）。"""
    assert RC.current_window("2026-10-26") is None
    assert RC.current_window("2026-10-26", before_days=4) is not None


# ── ③ 披露判据：预告也算「已明」；且必须用实际披露日 ──────────────────
def test_disclosure_map_uses_actual_publish_date(monkeypatch):
    """⚠ 核心反例：601116 的 ACTUAL_PUBLISH_DATE = 2026-10-28（明天）⇒ 今天算**未披露**。
    东财 IS_PUBLISH 会提前一天预填，只能用 ACTUAL_PUBLISH_DATE。"""
    appt = [
        {"SECURITY_CODE": "601116", "ACTUAL_PUBLISH_DATE": "2026-10-28 00:00:00"},
        {"SECURITY_CODE": "000913", "ACTUAL_PUBLISH_DATE": "2026-10-20 00:00:00"},
        {"SECURITY_CODE": "002112", "ACTUAL_PUBLISH_DATE": None},
    ]
    fcst = [{"SECURITY_CODE": "300839", "NOTICE_DATE": "2026-10-15 00:00:00"}]
    monkeypatch.setattr(RC, "_fetch_all",
                        lambda rn, filt, page_size=500: (appt if "APPOIN" in rn else fcst))
    monkeypatch.setattr(RC, "_cache_path",
                        lambda tag, rd, td: os.path.join(str(RC.CACHE_DIR), "_t_%s_%s.json" % (tag, td)))
    m = RC.disclosure_map("2026-09-30", today="2026-10-27", use_cache=False)
    assert m["000913"]["report_date"] == "2026-10-20"
    assert "601116" not in m                    # 披露日是明天 ⇒ 今天未披露
    assert "002112" not in m                    # 无实际披露日 ⇒ 未披露
    assert m["300839"]["forecast_date"] == "2026-10-15"   # 预告已出


def test_forecast_counts_as_shown(monkeypatch):
    """铁律③：早出预告 ⇒ 业绩已明，**不是盲区**。"""
    appt = []
    fcst = [{"SECURITY_CODE": "300839", "NOTICE_DATE": "2026-10-15 00:00:00"}]
    monkeypatch.setattr(RC, "_fetch_all",
                        lambda rn, filt, page_size=500: (appt if "APPOIN" in rn else fcst))
    monkeypatch.setattr(RC, "_cache_path",
                        lambda tag, rd, td: os.path.join(str(RC.CACHE_DIR), "_t2_%s.json" % td))
    rows = [{"code": "300839"}]
    out = RC.annotate(rows, today="2026-10-27")
    assert out is not None
    assert rows[0]["_report_shown"] is True
    assert rows[0]["_report_kind"] == "预告"
    assert rows[0]["_report_date"] == "2026-10-15"
    assert rows[0]["_report_tier"] == "早"       # 早于窗口起点 ⇒ 早出


def test_annotate_outside_window_does_nothing():
    rows = [{"code": "002112"}]
    assert RC.annotate(rows, today="2026-06-15") is None
    assert "_report_shown" not in rows[0]


# ── ② 越晚出报告越垃圾 ─────────────────────────────────────────────────
def test_tier_of_late_vs_early():
    win = {"trade_days": ["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]}
    # 窗口前披露 = 早
    assert RC.tier_of("2026-10-20", win)[0] == "早"
    # 4 天窗口：前 1/3（10-27、10-28）= 早，还剩 2 天缓冲
    assert RC.tier_of("2026-10-27", win)[0] == "早"
    assert RC.tier_of("2026-10-28", win)[0] == "早"
    # 倒数第 2 天 = 中
    assert RC.tier_of("2026-10-29", win)[0] == "中"
    # 窗口最后 1 天 = 晚（赶 deadline）
    assert RC.tier_of("2026-10-30", win)[0] == "晚"
    # 宽限期披露 = 晚
    assert RC.tier_of("2026-11-02", win)[0] == "晚"


def test_late_tail_days_configurable():
    win = {"trade_days": ["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]}
    assert RC.tier_of("2026-10-29", win, late_tail_days=1)[0] == "中"
    assert RC.tier_of("2026-10-29", win, late_tail_days=2)[0] == "晚"
    assert RC.tier_of("2026-10-28", win, late_tail_days=3)[0] == "晚"
    # 窗口 4 天：早 = 前 1/3（off 0,1）｜中 = off 2｜晚 = off 3（默认 late_tail=1）


def test_tier_of_no_date():
    assert RC.tier_of(None, {"trade_days": ["2026-10-27"]}) == (None, None)


# ── 交易日历工具 ────────────────────────────────────────────────────────
def test_trade_days_includes_future_workdays():
    """DB 只到今天，未来工作日要接上（否则 10-28~10-30 丢失、截止交易日错算）。"""
    seq = RC.trade_days("2026-12-31", n=200)
    assert "2026-10-27" in seq
    assert "2026-10-28" in seq
    assert "2026-10-29" in seq
    assert "2026-10-30" in seq
    assert "2026-10-31" not in seq      # 周六 ⇒ 不是交易日
    assert seq == sorted(seq)            # 升序


def test_workdays_skips_weekend():
    wd = RC._workdays("2026-10-26", "2026-11-02")
    assert "2026-10-31" not in wd        # 周六
    assert "2026-11-01" not in wd        # 周日
    assert "2026-10-30" in wd
