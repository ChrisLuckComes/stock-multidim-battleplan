# -*- coding: utf-8 -*-
"""活平台沿回归：P0–P1 的 R1 不是今天的突破位。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rule123 import evaluate

DATA = Path(__file__).resolve().parent / "data"


def _run(code):
    return evaluate(code, str(DATA / f"{code}.json"))


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


if __name__ == "__main__":
    test_shandong_gold_not_stale_r1()
    test_runtu_not_inner_shelf()
    test_unigroup_not_far_ath()
    print("ok")
