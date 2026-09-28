#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tape_review 的纯函数回归。不联网。"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from gates.tape_review import (  # noqa: E402
    close_pos, conclude, cumulate_flow, dedupe, fact_rows, flow_after_high,
    flow_at, minute_up_down, norm_date, parse_flow, parse_minute, parse_tick,
    reading, running_vwap, slice_by_date, verdict_window, _verdict_row,
)


def test_dedupe_drops_repeated_page():
    rows = ["09:30:01,10,1,0,2", "09:30:02,10.1,2,0,1"]
    assert dedupe(rows + rows) == rows


def test_tick_amount_is_price_times_hands_times_100():
    t = parse_tick("13:00:01,40.51,1367,1,2")
    assert t["side"] == "买"
    assert abs(t["amt"] - 40.51 * 1367 * 100) < 1


def test_flow_partition_and_pullback():
    line = "2026-09-28 15:00,31167628,-40151340,8983695,9766860,21400768"
    f = parse_flow(line)
    net = f["small"] + f["mid"] + f["large"] + f["super"]
    assert abs(net) < 50
    assert abs(f["large"] + f["super"] - f["main"]) < 50
    flows = [
        parse_flow("2026-09-28 13:02,100,0,0,60,40"),
        parse_flow("2026-09-28 13:30,70,0,0,40,30"),
        parse_flow("2026-09-28 15:00,90,0,0,50,40"),
    ]
    worst, when, drop = flow_after_high(flows, "13:02")
    assert when == "13:30"
    assert drop == 30
    assert worst == 70
    assert flow_at(flows, "13:02")["main"] == 100
    assert flow_at(flows, "13:10")["main"] == 100


def test_reading_labels():
    key, _ = reading(39.45, 38.12, 0.71, 0.27, 3e7, -4e7)
    assert key == "big_buy"
    key, _ = reading(35.0, 38.0, 0.2, 0.6, -1e7, 1e7)
    assert key == "supply"
    key, _ = reading(39.0, 38.0, 0.4, 0.2, 1e6, -1e6)
    assert key == "mixed"


def test_minute_up_down_and_position():
    minutes = [
        parse_minute("2026-09-28 09:30,10,10,10,10,100,1000,10"),
        parse_minute("2026-09-28 09:31,10,11,11,10,50,550,10.2"),
        parse_minute("2026-09-28 09:32,11,9,11,9,80,800,10.1"),
    ]
    up, dn = minute_up_down(minutes)
    assert up == 50
    assert dn == 80
    assert abs(close_pos(9, 11, 10) - 0.5) < 1e-9


def test_range_helpers():
    assert norm_date("20260814") == "2026-08-14"
    assert norm_date("2026-08-14") == "2026-08-14"
    bars = running_vwap([
        {"t": "2026-08-14", "o": 10, "h": 11, "l": 9, "c": 10, "v": 100, "amt": 100000},
        {"t": "2026-08-15", "o": 10, "h": 12, "l": 10, "c": 12, "v": 100, "amt": 110000},
    ])
    assert abs(bars[-1]["avg"] - 210000 / 20000) < 1e-9
    flows = cumulate_flow([
        {"t": "2026-08-14", "main": 100, "small": -100, "mid": 0, "large": 40, "super": 60},
        {"t": "2026-08-15", "main": -30, "small": 30, "mid": 0, "large": -30, "super": 0},
    ])
    assert flows[-1]["main"] == 70
    assert len(slice_by_date(bars, "2026-08-15", "2026-08-15")) == 1


def test_fact_and_verdict_share_reading():
    day = {
        "frame": "range", "pre": 28.25, "date": "2026-08-14 → 2026-09-28",
        "quote": {"f46": 28.30, "f170": 39.65, "f48": 6.2243e9},
        "indexes": {"上证指数": -2.63}, "bench_name": "科创50",
        "minutes": [
            {"t": "2026-08-14", "h": 29, "l": 27.67, "c": 28.24, "avg": 28.2},
            {"t": "2026-09-28", "h": 41.19, "l": 35.21, "c": 39.45, "avg": 35.82},
        ],
    }
    summary = {
        "hi": day["minutes"][-1], "lo": day["minutes"][0], "last": day["minutes"][-1],
        "up": 139, "dn": 100, "above": 209, "below": 100, "after_share": 0.0, "pos": 0.87,
        "bench_at_low": 0.0, "bench_at_high": -9.42, "bench_at_close": -9.42,
        "outer": None, "inner": None,
        "flow": {"main": 3.729e7, "super": 1.252e8, "large": -8.791e7, "mid": -2.474e7, "small": -1.256e7},
        "reading": "大单净买、小单净卖，收在均价上方，高点后成交没有占到一半",
        "session_done": True,
    }
    summary["conclusion"] = conclude(day, summary)["text"]
    facts = dict(fact_rows(day, summary))
    verdict = _verdict_row("区间", day, summary)
    assert facts["结论"] == verdict["verdict"]
    assert verdict["verdict"].startswith("趋势延续。能买，只接回踩，不追。")
    assert "高点在最后一根" in verdict["basis"]
    assert "超大单" in verdict["basis"]


def test_conclude_three_ways():
    def pack(close, avg, pos, after, main, chg, done=True):
        day = {
            "frame": "minute", "session_done": done, "bench_name": "科创50",
            "quote": {"f170": chg},
            "minutes": [{"t": "15:00", "h": 10, "l": 8, "c": close}],
        }
        summary = {
            "last": {"c": close, "avg": avg}, "hi": day["minutes"][0],
            "pos": pos, "after_share": after, "session_done": done,
            "bench_at_close": -1.0,
            "flow": {"main": main, "super": main},
        }
        return conclude(day, summary)

    up = pack(39.45, 35.82, 0.87, 0.0, 3.7e7, 39.65)
    assert up["label"] == "趋势延续"
    assert up["buy"] == "能买，只接回踩，不追"
    pull = pack(39.0, 38.0, 0.4, 0.2, 1e6, 1.0)
    assert pull["label"] == "正常回调"
    assert pull["buy"] == "能等回踩接，不追"
    sold = pack(47.9, 46.6, 0.84, 0.43, -6e6, 1.96)
    assert sold["label"] == "正常回调"
    assert sold["buy"] == "先不买"
    dist = pack(35.0, 38.0, 0.2, 0.6, -1e7, -2.0)
    assert dist["label"] == "派发"
    assert dist["buy"] == "不买"
    live = pack(35.0, 38.0, 0.2, 0.6, -1e7, -2.0, done=False)
    assert live["label"] == "未收盘"
    assert "不下派发" in live["buy"]


def test_verdict_window_defaults_to_20_days():
    spec = verdict_window()
    assert spec["mode"] == "recent"
    assert spec["n"] == 20
    assert spec["label"] == "近 20 日"
    named = verdict_window("2026-08-14", "2026-09-28")
    assert named["mode"] == "range"
    assert named["start"] == "2026-08-14"
    assert named["label"] == "2026-08-14 → 2026-09-28"
    try:
        verdict_window("2026-08-14", None)
        raise AssertionError("half range should fail")
    except ValueError:
        pass


if __name__ == "__main__":
    test_dedupe_drops_repeated_page()
    test_tick_amount_is_price_times_hands_times_100()
    test_flow_partition_and_pullback()
    test_reading_labels()
    test_minute_up_down_and_position()
    test_range_helpers()
    test_fact_and_verdict_share_reading()
    test_conclude_three_ways()
    test_verdict_window_defaults_to_20_days()
    print("ok 9")
