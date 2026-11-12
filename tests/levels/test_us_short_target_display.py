"""Target-price display and T1 gate after reverting PR22 execution gates.

These tests are about structure/conditional plans and same-source T1/R1·T2/R2.
They do not require minute paths, 120s windows, or frozen triggers.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gates"))

import us_short as U
from gates.us_short_watch import _fmt_etf_cand, _verdict, brief_line


def _bars():
    """Synthetic daily path with two swing lows under a broken close. Not market history."""
    bars = []
    day = datetime(2026, 6, 1)
    # Climb, drop to 90 (T2 region), bounce, drop to 94 (T1 region), bounce, close 100
    seq = ([80 + i for i in range(20)]
           + [100, 98, 95, 92, 90, 93, 96, 98, 97, 95, 94, 96, 99, 101, 100]
           + [100 + (i % 3) * 0.2 for i in range(20)])
    # After enough history, pin last close at 96 so a prior swing high/support sits above
    # and two lows (90, 94) sit below — a typical broken structure.
    seq = []
    px = 100.0
    for i in range(40):
        px = 100 + i * 0.4
        bars.append(dict(d=(day + timedelta(days=i)).strftime("%Y-%m-%d"),
                         o=px - 0.3, h=px + 1.0, l=px - 1.0, c=px, v=1000))
    # swing low around 90, then 94, then rally to 110, break back to 100
    extras = [
        (110, 111, 90, 91),
        (91, 96, 90.5, 95),
        (95, 100, 94, 99),
        (99, 101, 94.2, 100),
        (100, 112, 99, 110),
        (110, 111, 99, 100),
    ]
    for j, (o, h, l, c) in enumerate(extras):
        bars.append(dict(d=(day + timedelta(days=40 + j)).strftime("%Y-%m-%d"),
                         o=o, h=h, l=l, c=c, v=1000))
    return bars


def test_structure_plan_emits_conditional_levels_without_minute_path():
    bars = _bars()
    pl = U.plan_levels(bars, bars[-1]["c"])
    assert pl["t1"] is not None and pl["t2"] is not None
    assert pl["t2"] <= pl["t1"] + 1e-9
    obj = U.short_objectives(bars[-1]["c"], pl["stop"] if pl["stop"] > bars[-1]["c"]
                             else bars[-1]["c"] + 0.30 * pl["atr"], pl["t1"], pl["t2"])
    text = U.format_short_objectives("破位延续空", obj, near_wall=pl["t1"])
    assert "T1 " in text and "/R1 " in text and "T2 " in text and "/R2 " in text
    assert "目标S2" not in text
    assert "近墙" in text and "不是自动止盈" in text
    # No observations / frozen trigger required to state the plan.
    assert obj["entry"] and obj["stop"] and obj["target1"] and obj["target2"]


def test_gate_uses_t1_not_far_t2():
    # T1 RR = 1.0 (below gate), T2 RR = 3.0 (would falsely pass if used as the gate).
    entry, stop, t1, t2 = 100.0, 104.0, 96.0, 88.0
    obj = U.short_objectives(entry, stop, t1, t2)
    assert obj["rr1"] == 1.0 and obj["rr2"] == 3.0
    assert obj["rr"] == obj["rr1"]
    card = {
        "sym": "TEST", "close": entry, "error": None,
        "ctx_name": "头肩顶", "ctx_alive": True, "ctx_age": 3, "ctx_grade": "A",
        "main_cands": [dict(name="破位延续空", **obj)],
        "rr_ladder": [(1.5, 102.4)],
        "etf": {"code": "TESTQ"},
    }
    tier, why = _verdict(card)
    assert tier == "不推荐"
    assert "RR(T1)" in why and "1.00" in why
    assert "3.00" not in why  # must not advertise the T2 RR as the failing/passing figure


def test_t1_at_gate_still_qualifies_when_t2_is_farther():
    entry, stop, t1, t2 = 100.0, 104.0, 94.0, 88.0
    obj = U.short_objectives(entry, stop, t1, t2)
    assert obj["rr1"] == 1.5 and obj["rr2"] == 3.0
    card = {
        "sym": "TEST", "close": entry, "error": None,
        "ctx_name": "头肩顶", "ctx_alive": True, "ctx_age": 3, "ctx_grade": "A",
        "main_cands": [dict(name="破位延续空", **obj)],
        "rr_ladder": [(1.5, entry)],
        "etf": {"code": "TESTQ"},
    }
    tier, why = _verdict(card)
    assert tier.startswith("推荐")
    assert "候选位" in why


def test_display_and_etf_line_are_same_source_t1_t2():
    obj = U.short_objectives(100.0, 104.0, 94.0, 88.0)
    text = U.format_short_objectives("破位延续空", obj, near_wall=94.0)
    assert "T1 94.00/R1 1.50" in text
    assert "T2 88.00/R2 3.00" in text
    assert "远目标RR" not in text
    line = _fmt_etf_cand(dict(name="破位延续空", **obj), {"code": None}, 100, 10, 2)
    assert "T1 94.00/R1 1.50" in line
    assert "T2 88.00/R2 3.00" in line
    assert "目标S2" not in line
    # Old fixtures that only have target + rr still render a T1 line.
    legacy = _fmt_etf_cand(
        {"name": "反抽R1不过空", "entry": 110.0, "stop": 115.0, "target": 80.0,
         "rr": (110.0 - 80.0) / 5.0},
        {"code": "TESTS"}, 100.0, 10.0, 2.0, t1_und=80.0, t2_und=75.0)
    assert "R1 " in legacy and "临界入场" not in legacy
    card = {
        "sym": "TEST", "date": "2026-11-05", "close": 100.0, "ref_close": 100.0,
        "mode": "反抽结构空", "verdict": "推荐·等到位",
        "ctx_name": "头肩顶", "ctx_alive": True, "ctx_age": 5, "ctx_grade": "A",
        "main_cands": [{"name": "反抽R1不过空", "entry": 110.0, "stop": 115.0,
                        "target": 80.0, "rr": (110.0 - 80.0) / 5.0,
                        "rr_far": None, "rr_basis": "T1"}],
        "rr_ladder": [(3.0, 125.0), (1.5, 110.0)],
        "etf": {"code": "TESTS"}, "t1": 80.0, "t2": 75.0, "chase": [], "deep": None,
        "signal": None,
    }
    txt = brief_line(card)
    assert "临界入场" in txt and "现价未到" in txt
    assert "T1 " in txt and "T2 " in txt


def test_cli_prints_both_targets_and_not_t2_as_the_sole_objective(monkeypatch, capsys):
    bars = _bars()
    last = bars[-1]["c"]
    monkeypatch.setattr(U.R, "bars_from_us",
                        lambda sym: (bars, last, {"session": "Overnight", "source": "offline_test",
                                                  "as_of": "2026-11-11 23:00:00"}))
    monkeypatch.setattr(sys, "argv", ["us_short.py", "SNDK"])
    U.main()
    text = capsys.readouterr().out
    assert "T1 " in text and "R1 " in text and "T2 " in text and "R2 " in text
    assert "目标S2" not in text
    assert "不是自动止盈" in text
    assert "空点候选" in text
