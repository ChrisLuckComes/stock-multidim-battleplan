#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""pre_runup.py 回归测试：python tests/screen/test_pre_runup.py"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'gates')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import pre_runup as pr  # noqa: E402


def mk(closes, start_day=1, lows=None):
    """造一段日线，day 从 1 递增；lows 省略时用收盘价代替。"""
    out = []
    for i, c in enumerate(closes):
        out.append({
            "d": f"2026-09-{start_day + i:02d}",
            "o": c, "h": c,
            "l": (lows[i] if lows else c),
            "c": c, "v": 100.0,
        })
    return out


# ── normalize：两种来源格式都要能吃 ──────────────────────────────
def test_normalize_dict():
    bars = pr.normalize([{"d": "2026-09-01", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 9}])
    assert bars[0]["c"] == 1.5 and bars[0]["d"] == "2026-09-01"


def test_normalize_dict_alt_keys():
    bars = pr.normalize([{"date": "2026-09-01", "open": 1, "high": 2, "low": 0.5,
                          "close": 1.5, "volume": 9}])
    assert bars[0]["c"] == 1.5 and bars[0]["l"] == 0.5


def test_normalize_list():
    bars = pr.normalize([["2026-09-01", 1, 2, 0.5, 1.5, 9]])
    assert bars[0]["c"] == 1.5 and bars[0]["v"] == 9


def test_normalize_rejects_unknown():
    try:
        pr.normalize([42])
    except TypeError:
        return
    raise AssertionError("非 dict/list 的 bar 应该报 TypeError")


def test_normalize_empty():
    assert pr.normalize([]) == [] and pr.normalize(None) == []


# ── prefix_of ────────────────────────────────────────────────────
def test_prefix():
    assert pr.prefix_of("688428") == "sh"
    assert pr.prefix_of("603067") == "sh"
    assert pr.prefix_of("000001") == "sz"
    assert pr.prefix_of("300404") == "sz"


# ── pick_base：取「公告日之前」最后一根 ──────────────────────────
def test_pick_base_excludes_notice_day():
    bars = mk([10, 11, 12, 13])          # 09-01 ~ 09-04
    t = pr.pick_base(bars, "20260904")   # 公告日 = 09-04
    assert bars[t]["d"] == "2026-09-03"  # 基准 = 09-03（公告前一日）


def test_pick_base_non_trading_day():
    bars = mk([10, 11, 12])              # 09-01 ~ 09-03
    t = pr.pick_base(bars, "20260905")   # 公告日在周末
    assert bars[t]["d"] == "2026-09-03"


def test_pick_base_default_last():
    bars = mk([10, 11, 12])
    assert pr.pick_base(bars, None) == 2


def test_pick_base_out_of_range():
    bars = mk([10, 11, 12])
    assert pr.pick_base(bars, "20260901") is None


# ── runup ────────────────────────────────────────────────────────
def test_runup_value():
    bars = mk([100, 100, 100, 100, 100, 110])
    assert abs(pr.runup(bars, 5, 5) - 0.10) < 1e-9


def test_runup_missing_history():
    bars = mk([100, 110])
    assert pr.runup(bars, 1, 5) is None


def test_runup_zero_base():
    bars = mk([0, 100])
    assert pr.runup(bars, 1, 1) is None


# ── grade：三档边界 ──────────────────────────────────────────────
def test_grade_strong_by_d5():
    assert pr.grade(0.10, 0.0)[0].startswith("★★★")


def test_grade_strong_by_d10():
    assert pr.grade(0.0, 0.15)[0].startswith("★★★")


def test_grade_moderate_by_d5():
    assert pr.grade(0.06, 0.0)[0].startswith("★★☆")


def test_grade_moderate_by_d10():
    assert pr.grade(0.0, 0.10)[0].startswith("★★☆")


def test_grade_none():
    assert pr.grade(0.0599, 0.0999)[0].startswith("★☆☆")


def test_grade_handles_none():
    assert pr.grade(None, None)[0].startswith("★☆☆")


def test_grade_boundary_just_below_strong():
    assert pr.grade(0.0999, 0.1499)[0].startswith("★★☆")


# ── 实证钉死：诺诚 688428 / 2026-09-24 礼来公告 ──────────────────
def test_nuocheng_regression():
    """公告前 5 日 +16.09% / 10 日 +15.61% ⇒ 强抢跑。数字变了说明取数口径被改动。"""
    r5, r10 = 0.16093152589502946, 0.15610938040844569
    assert pr.grade(r5, r10)[0].startswith("★★★")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"  FAIL {name}: {e}")
    print(f"\n{'全部通过' if not fails else f'{fails} 项失败'}")
    raise SystemExit(1 if fails else 0)
