#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收盘复盘全量引擎跑批：先预热宽基缓存（腾讯 ifzq 501 时），再逐只 evaluate。

输出格式与 `rule123.py <codes> --out <path>` 完全一致（>1 只时为数组）。
"""
from __future__ import print_function

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import rule123  # noqa: E402
from out_cn.prewarm_broad import prewarm  # noqa: E402


def main():
    args = sys.argv[1:]
    out_path = "out_cn/out_tmp.json"
    codes = []
    i = 0
    while i < len(args):
        if args[i] == "--out":
            out_path = args[i + 1]
            i += 2
            continue
        codes.append(args[i])
        i += 1
    if not codes:
        print("no codes")
        return 1

    got = prewarm(quiet=True)
    print("预热宽基 %d 只：%s" % (len(got), ",".join(sorted(got))))

    res = []
    for s in codes:
        try:
            r = rule123.evaluate(s, None, eod=False)
        except Exception as e:
            r = {"sym": s, "verdict": "ERR", "reason": "%s: %s" % (type(e).__name__, e)}
        res.append(r)
        print("  %-8s %-8s rec=%-5s R=%-6s mode=%s" % (
            s, r.get("verdict"), r.get("recommend"), r.get("rr_t1"),
            (r.get("mode") or "-")))

    mdir = os.path.dirname(out_path)
    if mdir:
        os.makedirs(mdir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    print("-> %s written (%d)" % (out_path, len(res)))
    err = [r["sym"] for r in res if r.get("verdict") == "ERR"]
    print("ERR: %s" % (",".join(err) if err else "0"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
