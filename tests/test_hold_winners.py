# -*- coding: utf-8 -*-
"""不要轻易卖赢家（hold_winners，2026-10-27）。

软标签不改 recommend。硬纠正只在用户同步卖出、且结构未破时。
盘中刺破不算破。收盘派发、排列打乱、顶部确认，卖出成立。
浅档是买区上沿 / VWAP / −1%，R 不到 1.5 不给单，不放宽止损。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import report_render as RR  # noqa: E402
from gates.hold_winners import (  # noqa: E402
    CITE,
    assess,
    facts_from_analysis,
    format_hold_line,
    from_analysis,
    main,
    review_sell,
    shallow_book,
)


def _leg(**kw):
    base = {
        "close": 40.0,
        "price": 40.2,
        "support": 37.5,
        "stop": 37.5,
        "target": 46.0,
        "zone_lo": 38.5,
        "zone_hi": 40.2,
        "prev_close": 39.0,
        "mode": "line_pullback",
    }
    base.update(kw)
    return base


def test_intact_leg_is_soft_hold():
    block = assess(_leg())
    assert block["label"] == "prefer_hold"
    assert block["hard_gate"] is False
    assert block["intact"] is True
    assert "不要轻易卖出" in block["line"]
    assert CITE in block["line"]
    assert block["pushback"] is None


def test_intraday_pierce_is_a_shake_not_a_break():
    block = review_sell(_leg(low=36.8, price=40.2), sold_px=39.5)
    assert block["shake"] is True
    assert block["verdict"] == "wrong_sell"
    assert block["hard_gate"] is True
    assert "盘中刺破" in block["pushback"]
    assert "纠正" in block["pushback"]
    assert "不许顺着安慰" in block["pushback"]
    assert "40.20" in block["pushback"]
    assert "买区上沿" in block["pushback"]
    assert block["reentry"]["stop"] == 37.5
    assert block["reentry"]["rr"] >= 1.5


def test_close_through_support_sell_stands():
    block = review_sell(_leg(close=37.0, price=37.0), sold_px=37.2)
    assert block["verdict"] == "sell_stands"
    assert block["hard_gate"] is False
    assert block["label"] == "exit_stands"
    assert "卖后观察" in block["line"]
    assert "纠正" not in block["line"]


def test_distribution_close_overrides_a_hold():
    block = review_sell(_leg(distribution_close=True), sold_px=40.0)
    assert block["verdict"] == "sell_stands"
    assert block["hard_gate"] is False
    assert "派发" in block["line"]


def test_align_break_is_the_mode_exit():
    held = assess(_leg(mode="ma_bull_align", align_broken=False))
    assert held["label"] == "prefer_hold"
    sold = review_sell(_leg(mode="ma_bull_align", align_broken=True), sold_px=40.0)
    assert sold["verdict"] == "sell_stands"
    assert "排列已打乱" in sold["line"]


def test_t0_intact_and_lost():
    ok = assess(_leg(mode="ma_reclaim_break", t0=True, ma5=38, ma10=37, ma20=35))
    assert ok["label"] == "prefer_hold"
    assert any("T0" in r for r in ok["reasons"])
    bad = review_sell(
        _leg(mode="ma_reclaim_break", t0=True, close=36.5, price=36.5,
             ma5=38, ma10=37, ma20=35),
        sold_px=36.6,
    )
    assert bad["verdict"] == "sell_stands"
    assert "T0" in bad["line"]


def test_confirmed_top_sell_stands():
    block = review_sell(_leg(top_block=True), sold_px=40.0)
    assert block["verdict"] == "sell_stands"
    assert "顶部已确认" in block["line"]


def test_stretched_day_does_not_chase():
    block = review_sell(_leg(day_pct=6), sold_px=40.0)
    assert block["verdict"] == "wrong_sell"
    assert block["hard_gate"] is True
    assert block["do_not_chase"] is True
    assert block["action"] == "do_not_chase"
    assert "不要追" in block["pushback"]
    assert "5%" in block["pushback"]
    assert block["reentry"]["px"] == 40.2
    assert block["reentry"]["rr"] >= 1.5


def test_price_above_shallow_window_does_not_lift_the_bid():
    block = review_sell(_leg(price=48, close=40), sold_px=40.0)
    assert block["verdict"] == "wrong_sell"
    assert block["do_not_chase"] is True
    assert block["reentry"]["px"] <= 40.2
    assert "离开浅档" in block["pushback"]


def test_shallow_without_rr_is_not_an_order():
    # 目标贴着浅档：买区上沿已在目标上方，−1% 的 R 也不到 1.5。
    block = review_sell(_leg(target=39.0), sold_px=40.0)
    assert block["verdict"] == "wrong_sell"
    assert block["reentry"] is None
    assert block["sold_in_window"] is False
    assert "不要追" in block["pushback"]
    assert "放宽止损" in block["pushback"]
    assert "37.50" in block["pushback"]


def test_missing_structure_does_not_invent_a_price():
    block = review_sell({"mode": "line_pullback"}, sold_px=10)
    assert block["verdict"] == "unknown"
    assert block["hard_gate"] is False
    assert block["reentry"] is None
    assert "未知" in block["line"]
    assert "不编造" in block["line"]


def test_sold_inside_shallow_window_is_named():
    block = review_sell(_leg(), sold_px=40.0)
    assert block["sold_in_window"] is True
    assert "浅档" in block["pushback"]


def test_vwap_chosen_when_it_is_the_highest_qualified():
    block = review_sell(
        _leg(zone_hi=39.0, vwap=40.0, price=40.0),
        sold_px=39.5,
    )
    assert block["reentry"]["name"] == "VWAP"
    assert block["reentry"]["px"] == 40.0


def test_txn_closed_shallow_uses_last_close_not_prior_bar():
    facts = {
        "close": 298.12,
        "price": 296.0,
        "last_close": 298.12,
        "prev_close": 284.69,
        "zone_lo": 280.0,
        "zone_hi": 296.29,
        "stop": 286.0,
        "support": 286.0,
        "target": 340.0,
        "vwap": 293.0,
        "vwap5": 283.29,
        "mode": "platform_break",
    }
    book = shallow_book(facts)
    assert book["chosen"]["name"] == "买区上沿"
    assert abs(book["chosen"]["px"] - 296.29) < 0.001
    names = {}
    for row in book["rows"]:
        names.setdefault(row["name"], []).append(row["px"])
    assert abs(names["−1%"][0] - 295.14) < 0.001
    assert abs(names["VWAP"][0] - 293.0) < 0.001
    assert "5日" not in names
    assert all(abs(px - 281.84) > 1 for pxs in names.values() for px in pxs)
    assert all(row["name"] != "VWAP" or abs(row["px"] - 283.29) > 1
               for row in book["rows"])


def test_txn_wide_stop_hold_does_not_relabel_deep_dip():
    facts = {
        "close": 298.12,
        "price": 296.0,
        "prev_close": 284.69,
        "zone_lo": 280.0,
        "zone_hi": 296.29,
        "stop": 272.39,
        "support": 272.39,
        "target": 310.29,
        "cap": 287.55,
        "vwap": 293.0,
        "vwap5": 283.29,
        "mode": "platform_break",
    }
    book = shallow_book(facts)
    assert book["chosen"] is None
    for row in book["rows"]:
        assert not (row["name"] == "VWAP" and abs(row["px"] - 283.29) < 0.01)
        if row["name"] == "−1%":
            assert abs(row["px"] - 295.14) < 0.001
            assert row["ok"] is False
        assert abs(row["px"] - 281.84) > 0.5


def test_facts_do_not_treat_vwap5_as_session_vwap():
    analysis = {
        "meta": {"basis_close": 298.12, "prev_close": 284.69},
        "plan": {
            "mode": "platform_break",
            "buy_zone": {
                "primary_hi": 296.29,
                "primary_lo": 280.0,
                "struct_stop": 272.39,
                "vwap5": 283.29,
            },
        },
        "targets": {"t1": 340.0},
    }
    facts = facts_from_analysis(analysis)
    assert facts.get("vwap") is None
    assert facts["vwap5"] == 283.29
    assert facts["last_close"] == 298.12
    book = shallow_book(facts)
    assert book["chosen"]["name"] == "买区上沿"
    assert all(row["name"] != "VWAP" for row in book["rows"])
    dip_rows = [row for row in book["rows"] if row["name"] == "−1%"]
    assert len(dip_rows) == 1
    assert abs(dip_rows[0]["px"] - 295.14) < 0.001


def test_from_analysis_stays_soft_until_a_sell_is_synced():
    analysis = {
        "meta": {"basis_close": 40.0, "prev_close": 39.0, "market": "CN"},
        "plan": {
            "mode": "platform_break",
            "buy_zone": {
                "primary_lo": 38.5,
                "primary_hi": 40.2,
                "struct_stop": 37.5,
                "hard_stop": 37.2,
            },
        },
        "struct": {"atr14": 1.2, "ma": [{"name": "MA5", "value": 38.8}]},
        "targets": {"t1": 46.0},
        "top_verdict": {"level": "ok"},
    }
    held = from_analysis(analysis)
    assert held["hard_gate"] is False
    assert held["label"] == "prefer_hold"
    sold = from_analysis(analysis, sold_px=40.0)
    assert sold["verdict"] == "wrong_sell"
    assert sold["hard_gate"] is True


def test_cli_wrong_sell_exits_2(capsys):
    code = main([
        "--close", "40", "--price", "40.2", "--support", "37.5", "--stop", "37.5",
        "--target", "46", "--zone-lo", "38.5", "--zone-hi", "40.2",
        "--prev-close", "39", "--sold", "40", "--json",
    ])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "wrong_sell"
    assert payload["hard_gate"] is True


def test_cli_missing_file_exits_1(capsys):
    code = main(["--analysis", "out_cn/does_not_exist_hold.json"])
    assert code == 1
    assert "不编造" in capsys.readouterr().out


def test_format_line_names_the_rule_when_empty():
    assert CITE in format_hold_line(None)
    assert CITE in format_hold_line({})


def _report_analysis():
    return {
        "meta": {"basis_close": 40.0, "prev_close": 39.0, "market": "CN",
                 "code": "600000", "name": "合成", "basis_date": "2026-10-24"},
        "plan": {
            "mode": "line_pullback",
            "verdict": "沿线回踩",
            "recommend": True,
            "buy_zone": {
                "primary_lo": 38.5, "primary_hi": 40.2,
                "struct_stop": 37.5, "struct_anchor": "MA5",
                "hard_stop": 37.2, "hard_anchor": "MA5",
                "level": 38.8, "anchor": "MA5",
            },
            "targets": {"target1": 46.0, "rr_target1": 2.0},
        },
        "struct": {
            "atr14": 1.2, "atr_pct": 3.0,
            "ma": [{"name": "MA5", "value": 38.8, "dir_txt": "上行"}],
            "percentile": {},
        },
        "targets": {
            "t1": 46.0, "t2_engine": None, "wall_far": None, "ath": None,
            "sea_sky": False, "no_room_above": False,
        },
        "probe": {},
        "odds_recommend": {
            "entry": 39.0, "stop": 37.5, "risk": 1.5, "r1": 2.1,
            "r1_basis": "t1", "risk_atr": 0.8,
        },
    }


def test_battle_summary_surfaces_the_rule():
    a = _report_analysis()
    assert "hold_winners" in RR.build_oneline(a, {})
    assert "不要轻易卖出赢家" in RR.build_oneline(a, {})
    plan = RR.build_plan_rows(a)
    assert "赢家持有" in plan
    assert "hold_winners" in plan
    summary = RR.build_summary(a, {})
    assert "赢家持有" in summary
    assert "不要轻易卖出这一腿" in summary


def test_open_gate_never_justifies_a_sell():
    """卓易场景 (2026-10-29)：开仓三闸全红，但持仓结构未破。

    开仓硬闸（强度 WEAK / 筹码破位 / 形态 wait）是**新开仓过滤器**，
    不是**斩仓刀**。即使 facts 里塞入开仓类差信号，只要持仓结构 intact，
    review_sell 仍须判 wrong_sell（硬纠正），绝不能判 sell_stands。
    这条锁定「隔离」行为，防止未来把开仓闸误接入清仓判定。
    """
    facts = _leg(
        strength="WEAK",
        chip="破位下行中",
        open_signal="wait",
        open_gate_red=True,
    )
    # 结构 intact：收盘 40 > 支撑 37.5，无派发 / 无顶确认 / 均线未破。
    block = review_sell(facts, sold_px=40.0)
    assert block["verdict"] == "wrong_sell", (
        "开仓硬闸不能作为斩仓理由：结构未破时卖出必须被纠正"
    )
    assert block["broken"] == [], "broken 只认持仓结构，不认开仓信号"
    assert block["intact"] is True
    assert block["hard_gate"] is True
    assert block["reentry"] is not None
    assert block["reentry"]["rr"] >= 1.5
