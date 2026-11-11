# -*- coding: utf-8 -*-
"""gates/reclaim_watch.py 回归：三态判定 + 基准日 fail-closed + 池过滤。

纯离线：monkeypatch ash_bars，不触网、不写真源。
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from gates import reclaim_watch as RW  # noqa: E402
from gates.stopout_reclaim import (  # noqa: E402
    STATUS_PENDING, STATUS_RECLAIMED, STATUS_REENTERED,
)

# 构造一段「11-02 收复」的日线（末根 2026-11-03，隔出局日 2026-10-29 共 3 根）
_BARS = [
    {"d": "2026-10-28", "o": 43.0, "h": 44.2, "l": 42.6, "c": 43.58, "v": 1000},
    {"d": "2026-10-29", "o": 43.0, "h": 43.1, "l": 42.0, "c": 42.11, "v": 900},
    {"d": "2026-10-30", "o": 42.0, "h": 42.5, "l": 40.2, "c": 40.20, "v": 1100},
    {"d": "2026-11-02", "o": 41.0, "h": 46.5, "l": 40.0, "c": 46.29, "v": 2600},  # 2.36×
    {"d": "2026-11-03", "o": 46.0, "h": 47.0, "l": 45.0, "c": 46.80, "v": 1500},
]


def _item(status=STATUS_PENDING, signal=None, sold=42.11, sold_date="2026-10-29"):
    return {"code": "688781", "name": "视涯科技", "prefix": "sh",
            "reclaim": {"status": status, "sold_price": sold, "sold_date": sold_date,
                        "signal_date": (signal or {}).get("date")}}


def _patch_bars(monkeypatch, bars=_BARS, src="net"):
    monkeypatch.setattr(RW, "ash_bars",
                        lambda *a, **k: ([dict(b) for b in bars], src, []))


def test_scan_one_fresh_when_signal_on_asof(monkeypatch):
    """信号日 == 扫描基准日 ⇒ fresh（用 11-02 末根的短序列）。"""
    _patch_bars(monkeypatch, _BARS[:4])            # 末根 11-02 = 信号日
    r = RW.scan_one(_item(), asof="2026-11-02")
    assert r["state"] == "fresh" and not r["stale"]
    assert "当日新收复" in r["action"]


def test_scan_one_historical_when_signal_recorded(monkeypatch):
    """已记录历史信号日 ⇒ historical，绝不报当日新收复。"""
    sig = {"date": "2026-11-02"}
    _patch_bars(monkeypatch)
    r = RW.scan_one(_item(status=STATUS_RECLAIMED, signal=sig), asof="2026-11-03")
    assert r["state"] == "historical"
    assert "历史已收复" in r["action"] and "当日新收复" not in r["action"]
    assert r["chg_vs_sold_pct"] == round((46.80 / 42.11 - 1) * 100, 2)


def test_scan_one_pending_when_below_sold(monkeypatch):
    """末根收盘未站上卖出价 ⇒ pending，报还差多少。"""
    _patch_bars(monkeypatch, _BARS[:3])            # 末根 10-30 收 40.20 < 42.11
    r = RW.scan_one(_item(), asof="2026-10-30")
    assert r["state"] == "pending"
    assert "距卖出价" in r["action"] and "收盘站上" in r["action"]


def test_scan_one_fail_closed_on_stale_asof(monkeypatch):
    """★ 基准日陈旧 ⇒ stale=True 且不出任何收复判定（fail-closed）。"""
    _patch_bars(monkeypatch)
    r = RW.scan_one(_item(), asof="2026-11-10")    # 末根 11-03 ≠ 期望 11-10
    assert r["stale"] is True
    assert r["state"] == "skip"
    assert "fail-closed" in r["action"]
    assert "当日新收复" not in r["action"]


def test_scan_one_fail_closed_on_empty_bars(monkeypatch):
    """日线为空 ⇒ stale，不出判定。"""
    _patch_bars(monkeypatch, [])
    r = RW.scan_one(_item(), asof="2026-11-03")
    assert r["stale"] is True and r["state"] == "skip"


def test_scan_one_fetch_exception_degrades(monkeypatch):
    """取数抛异常 ⇒ 单票降级 stale，不拖垮整批。"""
    def boom(*a, **k):
        raise RuntimeError("net down")
    monkeypatch.setattr(RW, "ash_bars", boom)
    r = RW.scan_one(_item(), asof="2026-11-03")
    assert r["stale"] is True and "取数失败" in r["action"]


def test_scan_one_skips_reentered(monkeypatch):
    """已重入（按新持仓管）⇒ skip。"""
    _patch_bars(monkeypatch)
    r = RW.scan_one(_item(status=STATUS_REENTERED), asof="2026-11-03")
    assert r["state"] == "skip" and not r["stale"]


def test_scan_all_respects_asof(monkeypatch):
    """scan_all 把 asof 透传给每票，全部 T-1 数据同日陈旧。"""
    _patch_bars(monkeypatch)
    rows = RW.scan_all([_item(), _item()], asof="2026-11-09")
    assert len(rows) == 2 and all(r["stale"] for r in rows)


def test_load_watch_pool_filters_no_reclaim_block():
    """只读带 reclaim 块的票；pos=0 但没有块的候选不进跟踪。"""
    rows = RW.load_watch_pool()
    codes = {r["code"] for r in rows}
    assert "688781" in codes and "688608" in codes   # 已在本地池落块
    assert "603198" in codes and "600267" in codes
    assert "688071" not in codes                       # 华依持仓、无 reclaim 块


def test_render_three_sections():
    """报告分段：当日新收复 / 历史勿追 / 待收复 / 陈旧，各自独立。"""
    rows = [
        {"code": "A", "name": "新收复", "state": "fresh", "stale": False,
         "sold_price": 10.0, "signal_date": None, "action": "★当日新收复"},
        {"code": "B", "name": "历史", "state": "historical", "stale": False,
         "signal_date": "2026-11-02", "chg_vs_sold_pct": 25.0, "action": "历史已收复勿追"},
        {"code": "C", "name": "待收复", "state": "pending", "stale": False,
         "signal_date": None, "action": "距卖出价还差2%"},
        {"code": "D", "name": "陈旧", "state": "skip", "stale": True,
         "signal_date": None, "action": "基准日陈旧"},
    ]
    txt = RW.render(rows, asof="2026-11-11")
    assert "★ 当日新收复" in txt and "新收复" in txt
    assert "历史已收复" in txt and "勿当新机会追" in txt
    assert "○ 待收复中" in txt
    assert "基准日陈旧" in txt
