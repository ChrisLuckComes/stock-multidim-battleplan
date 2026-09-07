# -*- coding: utf-8 -*-
"""活平台沿回归：P0–P1 的 R1 不是今天的突破位；突破阳高点也不是新沿。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rule123 import atr14, evaluate, living_platform, pivots

DATA = Path(__file__).resolve().parent / "data"


def _run(code):
    return evaluate(code, str(DATA / f"{code}.json"))


def _platform_of(bars):
    Hs, _ = pivots(bars)
    return living_platform(bars, Hs, atr14(bars))


def test_shandong_gold_not_stale_r1():
    r = _run("600547")
    plat = r["platform"]["price"]
    r1 = r["R1"]["price"]
    assert r1 == 32.55, r1
    assert abs(plat - 38.14) < 0.05, plat
    assert r["last"] < plat
    assert r["mode"] != "platform_break"
    assert r["verdict"] != "平台突破·量能不足"


def test_runtu_not_inner_shelf():
    r = _run("002440")
    plat = r["platform"]["price"]
    r1 = r["R1"]["price"]
    assert r1 == 13.34, r1
    assert abs(plat - 14.22) < 0.05, plat
    assert r["last"] < plat
    assert r["mode"] != "platform_break"
    assert r["verdict"] != "平台突破·量能不足"


def test_unigroup_not_far_ath():
    r = _run("000938")
    plat = r["platform"]["price"]
    r1 = r["R1"]["price"]
    assert r1 == 41.68, r1
    assert abs(plat - 41.68) < 0.05, plat
    assert plat < 45.0


def test_raytron_breakout_stays_on_179():
    bars = json.loads((DATA / "688002.json").read_text(encoding="utf-8"))["bars"]
    r = _run("688002")
    assert abs(r["platform"]["price"] - 179.0) < 0.05, r["platform"]
    extra = [
        {"d": "2026-09-01", "o": 183, "h": 186.5, "l": 178, "c": 181, "v": 15e6},
        {"d": "2026-09-02", "o": 180, "h": 184, "l": 177, "c": 180, "v": 12e6},
        {"d": "2026-09-03", "o": 179.5, "h": 183, "l": 176, "c": 179.8, "v": 11e6},
    ]
    acc = list(bars)
    for i, x in enumerate(extra, 1):
        acc = acc + [x]
        plat = _platform_of(acc)
        if i <= 2:
            assert plat is not None, i
            assert abs(plat["price"] - 179.0) < 0.05, (i, plat)
        if plat is not None:
            assert plat["price"] < 185, (i, plat)


if __name__ == "__main__":
    test_shandong_gold_not_stale_r1()
    test_runtu_not_inner_shelf()
    test_unigroup_not_far_ath()
    test_raytron_breakout_stays_on_179()
    print("ok")
