#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修 fallback_bars.py 用旧快照补出的重复尾 bar（2026-11-25 新增）。

背景：fallback_bars.py 的 --snap 默认写死 out_cn/quotes_20261123.json。
当日跑时会拿 11-23 的报价当"今天"补一根，产出 ...11-24,11-25,11-23 的乱序尾。
本脚本：① 剔除破坏日期单调的尾 bar；② 用新浪真实 hq 快照重建 spot；
③ 若末根不是今日且快照是今日，才补一根。
用法： python out_cn/fix_bars_tail.py 300759 688347 ...
"""
from __future__ import print_function

import json
import os
import sys
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}


def prefix_of(code):
    c = str(code)
    if c.startswith("92"):
        return "bj"
    if c.startswith(("6", "9")):
        return "sh"
    return "sz"


def snap(codes):
    out = {}
    for i in range(0, len(codes), 40):
        part = codes[i:i + 40]
        qs = ",".join(prefix_of(c) + c for c in part)
        req = urllib.request.Request("http://hq.sinajs.cn/list=" + qs, headers=HDR)
        txt = urllib.request.urlopen(req, timeout=12).read().decode("gbk", "ignore")
        for line in txt.strip().split("\n"):
            if '"' not in line:
                continue
            key = line.split("=")[0].strip().split("_")[-1]
            p = line.split('"')[1].split(",")
            if len(p) < 32 or not p[3]:
                continue
            out[key[2:]] = {
                "name": p[0], "o": float(p[1]), "prev": float(p[2]),
                "c": float(p[3]), "h": float(p[4]), "l": float(p[5]),
                "v": float(p[8]), "date": p[30],
            }
    return out


def main():
    codes = [a for a in sys.argv[1:] if not a.startswith("--")]
    sn = snap(codes)
    for code in codes:
        path = "out_cn/bars_%s.json" % code
        if not os.path.exists(path):
            print(code, "MISSING", path)
            continue
        d = json.load(open(path, encoding="utf-8"))
        bars = d["bars"]
        removed = 0
        while len(bars) > 2 and bars[-1]["d"] <= bars[-2]["d"]:
            bars.pop()
            removed += 1
        q = sn.get(code)
        patched = False
        if q and q["date"] and bars[-1]["d"] != q["date"]:
            bars.append({"d": q["date"], "o": q["o"], "c": q["c"],
                         "h": q["h"], "l": q["l"], "v": q["v"]})
            patched = True
        d["bars"] = bars
        d["spot"] = ({"name": q["name"], "last": q["c"], "prev_close": q["prev"],
                      "open": q["o"], "high": q["h"], "low": q["l"]} if q else d.get("spot"))
        json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        print("%-7s bars=%d removed=%d patch=%s tail=%s c=%.2f" % (
            code, len(bars), removed, patched, bars[-1]["d"], bars[-1]["c"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
