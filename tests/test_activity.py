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
    LABEL_PRICE_CRUSH,
    SIZE_HINT,
    TURN_ZOMBIE_LT,
    ZOMBIE_LT,
    activity_label,
    activity_raw_score,
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
    assert a["activity_raw"] == pytest_approx(3.6 * 5.2)
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
    # 没给换手：没有「换手还在」的证据，低分不升成高价压制。
    assert activity_label(ZOMBIE_LT - 0.0001) == "僵尸"
    assert activity_label(ZOMBIE_LT - 0.0001, TURN_ZOMBIE_LT - 0.01) == "僵尸"
    assert activity_label(ZOMBIE_LT - 0.0001, TURN_ZOMBIE_LT) == LABEL_PRICE_CRUSH
    assert activity_label(None) is None
    assert activity_label(None, 6.13) is None
    # 活跃 / 中性不看换手拆档。
    assert activity_label(1.56, 1.0) == "活跃"
    assert activity_label(0.5, 1.0) == "中性"
    assert activity_label(0.5, 6.13) == "中性"
    assert activity_label(ZOMBIE_LT, 1.0) == "中性"
    assert activity_label(ZOMBIE_LT, 6.0) == "中性"


def test_calibration_splits_zombie_from_price_crush():
    """2026-10-23 近似，钉标签，不拉行情。

    四行 activity 都 < 0.3。换手 < 2% 才是僵尸；换手还在标高价压制。
    低价且 activity > 0.8（鼎捷一类）仍是活跃，见 test_user_examples。
    """
    rows = (
        ("688192", 6.3, 1.55, 63.0, "僵尸"),
        ("603198", 5.1, 1.03, 41.0, "僵尸"),
        ("300033", 5.3, 6.13, 214.0, LABEL_PRICE_CRUSH),
        ("603893", 9.0, 5.21, 214.0, LABEL_PRICE_CRUSH),
    )
    for code, amp, turn, px, want in rows:
        score = activity_score(amp, turn, px)
        assert score == pytest_approx(amp * turn / px), code
        assert score < ZOMBIE_LT, code
        assert activity_label(score, turn) == want, code
        assert activity_raw_score(amp, turn) == pytest_approx(amp * turn), code
        r = assess(_const(8, px, amp, turn))
        assert r["activity"] == pytest_approx(score, abs=5e-5), code
        assert r["activity_raw"] == pytest_approx(amp * turn, abs=5e-5), code
        assert r["activity"] == pytest_approx(r["activity_raw"] / px, abs=5e-5), code
        assert r["activity_label"] == want, code
        assert r["hard_gate"] is False, code
        assert "÷" in r["formula"]
        if want == "僵尸":
            assert turn < TURN_ZOMBIE_LT
            assert "不是僵尸" not in r["reason"]
        else:
            assert turn >= TURN_ZOMBIE_LT
            assert "不是僵尸" in r["reason"]
            assert r["activity_label"] != "僵尸"


def test_split_uses_turnover_not_price_or_code():
    """高价但换手死仍是僵尸。不按股价门槛、不按代码名改标签。"""
    dead_high = assess(_const(6, 214, 6.3, 1.2))
    assert dead_high["activity"] < ZOMBIE_LT
    assert dead_high["activity_label"] == "僵尸"
    assert dead_high["hard_gate"] is False
    # 低价、换手达到 2%、分数仍 < 0.3：同一条换手规则，不另开股价特例。
    alive_low = assess(_const(6, 10, 1.0, TURN_ZOMBIE_LT))
    assert alive_low["activity"] < ZOMBIE_LT
    assert alive_low["activity_label"] == LABEL_PRICE_CRUSH


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
    assert r["activity_raw"] is None
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

    crush = assess(_const(8, 214, 5.3, 6.13))
    crush_html = RR.build_activity(
        {"meta": {"market": "CN", "code": "300033"}, "activity": crush}, {})
    assert "badge b-t1'>高价压制 " in crush_html
    assert "不是僵尸" in crush_html
    assert "价无关对照" in crush_html
    assert "b-warn" not in crush_html
    assert "不否决" in crush_html
    assert "不改 recommend" in crush_html

    zombie = assess(_const(8, 63, 6.3, 1.55))
    zombie_html = RR.build_activity(
        {"meta": {"market": "CN", "code": "688192"}, "activity": zombie}, {})
    assert "badge b-warn'>僵尸 " in zombie_html

    _items, crush_badges = RR.build_kpis({
        "meta": {"basis_close": 214, "prev_close": 210, "market": "CN"},
        "struct": {"atr14": 4, "atr_pct": 2},
        "plan": {"buy_zone": {}, "mode": "wait", "verdict": "等待", "recommend": False},
        "activity": crush,
    }, {})
    assert "高价压制" in crush_badges
    assert "软约束" in crush_badges
    assert "b-warn" not in crush_badges
    assert "否决买点" not in crush_badges

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
