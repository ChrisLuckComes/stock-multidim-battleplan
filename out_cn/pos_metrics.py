#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""池内个股位置指标：距60日低点% / MA20乖离% / 60日区间位置 / 当日振幅。"""
from __future__ import print_function

import json
import sys

sys.path.insert(0, "C:/Users/luoyu/.workbuddy/skills/stock-multidim-battleplan")
import rule123  # noqa: E402


def main():
    codes = sys.argv[1:]
    out = {}
    for c in codes:
        try:
            bars, _spot, _meta = rule123.get_bars(c)
        except Exception as e:
            print(c, "ERR", e)
            continue
        if not bars:
            print(c, "NOBARS")
            continue
        last = bars[-1]["c"]
        lows = [b["l"] for b in bars[-60:]]
        highs = [b["h"] for b in bars[-60:]]
        lo60, hi60 = min(lows), max(highs)
        ma20 = sum(b["c"] for b in bars[-20:]) / 20.0
        pos = (last - lo60) / (hi60 - lo60) if hi60 > lo60 else 0.5
        out[c] = {
            "last": last, "date": bars[-1]["d"],
            "d60": (last / lo60 - 1) * 100,
            "bias20": (last / ma20 - 1) * 100,
            "pos60": pos * 100,
            "lo60": lo60, "hi60": hi60, "ma20": ma20,
        }
        print("%-7s last=%7.2f d60=%+6.1f%% bias20=%+6.2f%% pos60=%5.1f%% (lo60=%.2f hi60=%.2f ma20=%.2f)" % (
            c, last, out[c]["d60"], out[c]["bias20"], out[c]["pos60"], lo60, hi60, ma20))
    with open("out_cn/pos_20261103.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
