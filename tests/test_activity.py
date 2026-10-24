# -*- coding: utf-8 -*-
"""股性活跃度回归。

公式：活跃度 = (近5日日均振幅% × 当日换手率%) ÷ 股价
软约束：僵尸 / 价升活降都不单独否决。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gates.activity import (  # noqa: E402
    ACTIVE_GT,
    SIZE_HINT,
    ZOMBIE_LT,
    activity_label,
    activity_score,
    assess,
    cn_code,
    day_amplitude_pct,
    main,
)

import report_render as RR  # noqa: E402


def _const(n, price, amp_pct, turn):
    """n 根横盘。振幅按前收（首根无前收，用自身收盘，数值相同）。"""
    span = price * amp_pct / 100.0
    bars = []
    for i in range(n):
        bars.append({
            "d": "2026-01-%02d" % (i + 1),
            "o": price, "c": price,
            "h": price + span / 2.0,
            "l": price - span / 2.0,
            "v": 1e6,
            "turnover": turn,
        })
    return bars


def _path(n, price0, drift, amp_pct, turn0, turn_step):
    """旧→新。drift 为每日涨跌幅（相对前收）。振幅相对前收固定。"""
    bars = []
    price = price0
    for i in range(n):
        prev = price
        if i:
            price = prev * (1.0 + drift)
        span = prev * amp_pct / 100.0
        turn = turn0 + turn_step * i
        bars.append({
            "d": "2026-03-%02d" % (i + 1),
            "o": price, "c": price,
            "h": price + span / 2.0,
            "l": price - span / 2.0,
            "v": 1e6,
            "turnover": turn,
        })
    return bars


def test_user_examples():
    active = activity_score(3.6, 5.2, 12)
    zombie = activity_score(4.1, 1.1, 68)
    assert active == pytest_approx(1.56)
    assert zombie == pytest_approx(4.1 * 1.1 / 68)
    assert activity_label(active) == "活跃"
    assert activity_label(zombie) == "僵尸"

    a = assess(_const(6, 12, 3.6, 5.2))
    assert a["activity"] == pytest_approx(1.56)
    assert a["activity_label"] == "活跃"
    assert a["hard_gate"] is False
    assert a["activity_divergent"] is False

    z = assess(_const(6, 68, 4.1, 1.1))
    assert z["activity"] == pytest_approx(0.0663, abs=0.0001)
    assert z["activity_label"] == "僵尸"
    assert z["hard_gate"] is False
    assert z["size_hint"] is None


def test_thresholds_are_strict():
    assert activity_label(ACTIVE_GT) == "中性"
    assert activity_label(ACTIVE_GT + 0.0001) == "活跃"
    assert activity_label(ZOMBIE_LT) == "中性"
    assert activity_label(ZOMBIE_LT - 0.0001) == "僵尸"
    assert activity_label(None) is None


def test_missing_turnover_is_not_zero():
    assert activity_score(3.6, None, 12) is None
    assert activity_score(3.6, 0, 12) is None
    assert activity_score(3.6, -1, 12) is None
    assert activity_score(3.6, 5.2, 0) is None
    assert activity_score(None, 5.2, 12) is None

    bars = _const(6, 12, 3.6, 5.2)
    for b in bars:
        b["turnover"] = 0
    r = assess(bars)
    assert r["activity"] is None
    assert r["activity_label"] is None
    assert r["activity_divergent"] is False
    assert r["hard_gate"] is False
    assert "缺换手" in r["reason"]

    for b in bars:
        del b["turnover"]
    r2 = assess(bars)
    assert r2["activity"] is None
    assert r2["activity_label"] is None


def test_turnover_map_without_field_on_price_bars():
    """战术日线不带换手时，用日期对齐的换手表。股价仍用传入的收盘。"""
    bars = _const(6, 12, 3.6, 5.2)
    turns = {}
    for b in bars:
        turns[b["d"].replace("-", "")] = b.pop("turnover")
    r = assess(bars, turnovers=turns)
    assert r["activity"] == pytest_approx(1.56)
    assert r["price"] == pytest_approx(12)
    assert r["turnover"] == pytest_approx(5.2)


def test_amplitude_uses_prev_close():
    bar = {"h": 11.0, "l": 10.0, "c": 10.5}
    assert day_amplitude_pct(bar, 10) == pytest_approx(10.0)
    # 无前收才退到收盘
    assert day_amplitude_pct(bar, None) == pytest_approx(1.0 / 10.5 * 100.0)
    assert day_amplitude_pct({"h": 9, "l": 10, "c": 9.5}, 10) is None


def test_short_history_skipped():
    r = assess(_const(4, 12, 3.6, 5.2))
    assert r["activity"] is None
    assert r["hard_gate"] is False


def test_divergence_price_up_activity_down():
    bars = _path(12, 10.0, 0.01, 4.0, 10.0, -0.7)
    r = assess(bars)
    assert r["activity"] is not None
    assert r["divergence_known"] is True
    assert r["activity_divergent"] is True
    assert r["size_hint"] == SIZE_HINT
    assert r["hard_gate"] is False
    assert "7 成" in r["reason"]


def test_divergence_not_when_price_falls():
    bars = _path(12, 20.0, -0.01, 4.0, 10.0, -0.7)
    r = assess(bars)
    assert r["divergence_known"] is True
    assert r["activity_divergent"] is False
    assert r["size_hint"] is None


def test_divergence_not_when_activity_rises():
    bars = _path(12, 10.0, 0.005, 4.0, 1.0, 0.8)
    r = assess(bars)
    assert r["divergence_known"] is True
    assert r["activity_divergent"] is False


def test_noise_drop_is_not_divergence():
    bars = _path(12, 10.0, 0.002, 4.0, 5.0, -0.002)
    r = assess(bars)
    assert r["divergence_known"] is True
    assert r["activity_divergent"] is False


def test_hole_in_turnover_skips_divergence_label():
    bars = _path(12, 10.0, 0.01, 4.0, 10.0, -0.7)
    del bars[-3]["turnover"]
    r = assess(bars)
    assert r["activity"] is not None
    assert r["activity_divergent"] is False
    assert r["divergence_known"] is False
    assert "背离未判" in r["reason"]


def test_cn_only_code():
    assert cn_code("600036") == "600036"
    assert cn_code("sh600036") == "600036"
    assert cn_code("CIEN") is None
    assert cn_code("AAPL") is None
    rc = main(["CIEN"])
    assert rc == 2


def test_cli_offline_json(tmp_path):
    path = tmp_path / "bars.json"
    path.write_text(json.dumps(_const(6, 12, 3.6, 5.2)), encoding="utf-8")
    rc = main(["600000", "--data", str(path), "--json"])
    assert rc == 0


def test_kpis_without_activity_unchanged():
    a = {
        "meta": {"basis_close": 12, "prev_close": 11, "market": "CN"},
        "struct": {"atr14": 0.4, "atr_pct": 3},
        "plan": {"buy_zone": {}, "mode": "wait", "verdict": "等待", "recommend": False},
    }
    _html, badges = RR.build_kpis(a, {})
    assert "股性活跃度" not in badges
    assert "recommend" in badges


def test_battle_reads_scr_cache_and_does_not_fetch(tmp_path, monkeypatch):
    """战术日线无换手；换手只从筹码缓存读，不许再打东财。"""
    import datetime as dt
    import battle_analyze as BA
    import scr_chip as SC

    price = _const(6, 12, 3.6, 5.2)
    cache_bars = [{"d": b["d"], "turnover": b.pop("turnover")} for b in price]
    path = tmp_path / "scr_600000.json"
    path.write_text(json.dumps({
        "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
        "bars": cache_bars,
    }), encoding="utf-8")

    def _no(*_a, **_k):
        raise AssertionError("should not fetch")

    monkeypatch.setattr(SC, "_cache_path", lambda code, us_mode=False: str(path))
    monkeypatch.setattr(SC, "fetch_all_meta", _no)
    monkeypatch.setattr(SC, "fetch_bars", _no)
    r = BA._cn_activity("600000", price, {"turnover": 1.0})
    assert r["activity"] == pytest_approx(1.56)
    assert r["turnover"] == pytest_approx(5.2)
    assert r["hard_gate"] is False

    bare = _const(6, 12, 3.6, 5.2)
    for b in bare:
        del b["turnover"]
    skipped = BA._cn_activity("600000", bare, None)
    assert skipped["activity"] is None
    assert skipped["hard_gate"] is False


def test_report_card_and_badge_are_soft():
    r = assess(_const(6, 12, 3.6, 5.2))
    html = RR.build_activity({"meta": {"market": "CN", "code": "600000"}, "activity": r}, {})
    assert "活跃" in html
    assert "软约束" in html
    assert "不否决" in html
    assert "1.56" in html

    us = RR.build_activity({"meta": {"market": "US", "code": "CIEN"}}, {})
    assert "不适用" in us

    missing = RR.build_activity(
        {"meta": {"market": "CN"}, "activity": assess(_const(6, 12, 3.6, 0))}, {})
    assert "缺换手" in missing
    assert "不编造" in missing

    div = assess(_path(12, 10.0, 0.01, 4.0, 10.0, -0.7))
    a = {
        "meta": {"basis_close": 12, "prev_close": 11, "market": "CN"},
        "struct": {"atr14": 0.4, "atr_pct": 3},
        "plan": {"buy_zone": {}, "mode": "wait", "verdict": "等待", "recommend": False},
        "activity": div,
    }
    _items, badges = RR.build_kpis(a, {})
    assert "股性活跃度" in badges
    assert "软约束" in badges
    assert "7成→3成" in badges
    assert "否决买点" not in badges


def pytest_approx(v, abs=1e-9):
    import pytest
    return pytest.approx(v, abs=abs)
