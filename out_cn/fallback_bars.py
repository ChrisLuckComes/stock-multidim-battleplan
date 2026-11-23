#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""东财 502 兜底：新浪日线 + hq 快照补当日 → rule123 --data 用。

用法： python out_cn/fallback_bars.py 600233 301178 ...
产出： out_cn/bars_<code>.json  {"bars":[{d,o,h,l,c,v}], "spot":{...}}
只负责落盘，跑引擎仍由调用方 rule123 --data 完成。
"""
from __future__ import print_function

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bars_source  # noqa: E402


def prefix_of(code):
    c = str(code)
    if c.startswith("92"):
        return "bj"
    if c.startswith(("6", "9")):
        return "sh"
    return "sz"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    snap_path = "out_cn/quotes_20261123.json"
    if "--snap" in sys.argv:
        snap_path = sys.argv[sys.argv.index("--snap") + 1]
    snap = {}
    if os.path.exists(snap_path):
        snap = json.load(open(snap_path, encoding="utf-8"))
    out = {}
    for code in args:
        pre = prefix_of(code)
        bars, src, notes = bars_source.ash_bars(pre, code, n=160)
        if not bars:
            print("%-8s FAILED %s" % (code, notes))
            continue
        key = (pre if pre != "bj" else "bj") + code
        q = snap.get(key) or snap.get(pre + code) or snap.get(code)
        last_d = bars[-1]["d"]
        patched = False
        if q and q.get("date") and q["date"] != last_d and q.get("c"):
            bars.append({"d": q["date"], "o": q["o"], "c": q["c"],
                         "h": q["h"], "l": q["l"], "v": q["v"]})
            patched = True
        spot = None
        if q:
            spot = {"name": q.get("name"), "last": q.get("c"),
                    "prev_close": q.get("prev"), "open": q.get("o"),
                    "high": q.get("h"), "low": q.get("l")}
        path = "out_cn/bars_%s.json" % code
        json.dump({"bars": bars, "spot": spot}, open(path, "w", encoding="utf-8"),
                  ensure_ascii=False)
        out[code] = path
        print("%-8s src=%-6s bars=%d last=%s patched_today=%s %s" % (
            code, src, len(bars), bars[-1]["d"], patched,
            "; ".join(notes)[:80]))
    json.dump(out, open("out_cn/bars_map_20261123.json", "w", encoding="utf-8"),
              ensure_ascii=False)
    print("-> out_cn/bars_map_20261123.json (%d)" % len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
