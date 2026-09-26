# -*- coding: utf-8 -*-
"""周线、月线沿线上涨的两段回测。不联网。

周线：源杰科技 688498，2025-06-06 至 2026-06-26。
  周线收盘不破 10 周线。引擎用日线同一套成线判据，55 周里 50 周是沿线或新高，
  前 5 周（到 2025-07-04）还在刚收复或中间态。

月线：松发股份 603268，2025-07 起。
  月线收盘不破 5 月线。引擎每个月都是沿线或新高。
月线：南亚新材 688519，2025-02 至 2026-06 见顶。
  月线收盘不破 5 月线。引擎除 2025-02 为中间态外，各月都是沿线或新高。2026-07 收盘才破。

运行：python tests/entry/test_ride_frames.py
"""
import json
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import rule123 as R

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
UP = ("line_ride", "new_high")


def load(code):
    with open(os.path.join(FIX, code + "_bars.json"), encoding="utf-8") as f:
        return json.load(f)["bars"]


def sma_at(closes, n, i):
    if i + 1 < n:
        return None
    return sum(closes[i + 1 - n:i + 1]) / n


def ride_at(daily, end_date, frame):
    cut = [b for b in daily if str(b["d"])[:10] <= end_date]
    folded = R.fold_bars(cut, frame)
    return R.ma_ride_state(folded, R.atr14(folded), frame=frame)


class TestYuanjieWeek(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = load("688498")
        cls.weeks = R.fold_bars(cls.bars, "week")
        cls.closes = [w["c"] for w in cls.weeks]

    def test_close_never_breaks_10week(self):
        broke = []
        for i, w in enumerate(self.weeks):
            if w["d"] < "2025-06-06" or w["d"] > "2026-06-26":
                continue
            ma = sma_at(self.closes, 10, i)
            if ma is not None and w["c"] < ma:
                broke.append(w["d"])
        self.assertEqual(broke, [])

    def test_engine_sees_the_ride_after_the_first_weeks(self):
        early = []
        late_miss = []
        seen = 0
        n = 0
        for w in self.weeks:
            if w["d"] < "2025-06-06" or w["d"] > "2026-06-26":
                continue
            n += 1
            st = ride_at(self.bars, w["d"], "week")
            state = None if st is None else st["state"]
            if state in UP:
                seen += 1
            elif w["d"] <= "2025-07-04":
                early.append((w["d"], state))
            else:
                late_miss.append((w["d"], state))
        self.assertEqual(n, 55)
        self.assertEqual(seen, 50)
        self.assertEqual(len(early), 5)
        self.assertEqual(late_miss, [])


class TestSongfaMonth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = load("603268")
        cls.months = R.fold_bars(cls.bars, "month")
        cls.closes = [m["c"] for m in cls.months]

    def test_close_never_breaks_5month_since_2025_07(self):
        broke = []
        for i, m in enumerate(self.months):
            if m["d"] < "2025-07-01":
                continue
            ma = sma_at(self.closes, 5, i)
            if ma is not None and m["c"] < ma:
                broke.append(m["d"])
        self.assertEqual(broke, [])

    def test_engine_sees_every_month(self):
        miss = []
        n = 0
        for m in self.months:
            if m["d"] < "2025-07-01":
                continue
            n += 1
            st = ride_at(self.bars, m["d"], "month")
            state = None if st is None else st["state"]
            if state not in UP:
                miss.append((m["d"], state))
        self.assertGreaterEqual(n, 15)
        self.assertEqual(miss, [])


class TestNanyaMonth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = load("688519")
        cls.months = R.fold_bars(cls.bars, "month")
        cls.closes = [m["c"] for m in cls.months]

    def _in_window(self, d):
        return "2025-02-01" <= d <= "2026-06-30"

    def test_close_never_breaks_5month_until_june_2026(self):
        broke = []
        for i, m in enumerate(self.months):
            if not self._in_window(m["d"]):
                continue
            ma = sma_at(self.closes, 5, i)
            if ma is not None and m["c"] < ma:
                broke.append(m["d"])
        self.assertEqual(broke, [])

    def test_july_2026_close_breaks(self):
        for i, m in enumerate(self.months):
            if not m["d"].startswith("2026-07"):
                continue
            ma = sma_at(self.closes, 5, i)
            self.assertLess(m["c"], ma)
            return
        self.fail("没有 2026-07 月线")

    def test_engine_sees_every_month_after_february(self):
        early = []
        late_miss = []
        for m in self.months:
            if not self._in_window(m["d"]):
                continue
            st = ride_at(self.bars, m["d"], "month")
            state = None if st is None else st["state"]
            if state in UP:
                continue
            if m["d"].startswith("2025-02"):
                early.append(state)
            else:
                late_miss.append((m["d"], state))
        self.assertEqual(early, ["mixed"])
        self.assertEqual(late_miss, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
