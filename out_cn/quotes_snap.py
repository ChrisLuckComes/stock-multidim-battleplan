#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量取当日行情（新浪 hq 快照，一次全部标的）。

用法： python out_cn/quotes_snap.py 002941 300827 ... [--json out.json]
输出： 代码 名称 开 昨收 收 高 低 涨幅% 振幅% 量(股) 额(元)
"""
from __future__ import print_function

import json
import sys
import urllib.request

HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}

IDX = {"000001": "sh000001", "399006": "sz399006", "000852": "sh000852",
       "000905": "sh000905", "000300": "sh000300", "000688": "sh000688"}


def to_sina(code):
    c = str(code).strip()
    if c.lower().startswith(("sh", "sz", "bj")):
        return c.lower()
    if c in IDX:
        return IDX[c]
    if c.startswith(("6", "9")) and not c.startswith("92"):
        return "sh" + c
    if c.startswith("92"):
        return "bj" + c
    return "sz" + c


def fetch(codes):
    syms = [to_sina(c) for c in codes]
    out = {}
    for i in range(0, len(syms), 30):
        chunk = syms[i:i + 30]
        url = "https://hq.sinajs.cn/list=" + ",".join(chunk)
        req = urllib.request.Request(url, headers=HDR)
        with urllib.request.urlopen(req, timeout=15) as r:
            txt = r.read().decode("gbk", "ignore")
        for line in txt.splitlines():
            if "hq_str_" not in line or '"' not in line:
                continue
            sym = line.split("hq_str_")[1].split("=")[0]
            p = line.split('"')[1].split(",")
            if len(p) < 32:
                continue
            try:
                prev = float(p[2])
                last = float(p[3])
                out[sym] = {"name": p[0], "o": float(p[1]), "prev": prev,
                            "c": last, "h": float(p[4]), "l": float(p[5]),
                            "v": float(p[8]), "amt": float(p[9]), "date": p[30],
                            "chg": (last / prev - 1) * 100 if prev else 0.0,
                            "amp": (float(p[4]) - float(p[5])) / prev * 100 if prev else 0.0}
            except Exception:
                pass
    return out


def main():
    args = sys.argv[1:]
    out_path = ""
    if "--json" in args:
        i = args.index("--json")
        out_path = args[i + 1]
        args = args[:i] + args[i + 2:]
    codes = [a for a in args if not a.startswith("--")]
    q = fetch(codes)
    for c in codes:
        s = to_sina(c)
        d = q.get(s)
        if not d:
            print("%-8s ERR" % c)
            continue
        print("%-8s %-10s 开%8.2f 昨%8.2f 收%8.2f 高%8.2f 低%8.2f %+6.2f%% 振%5.2f%% 量%.0f 额%.0f %s" % (
            c, d["name"], d["o"], d["prev"], d["c"], d["h"], d["l"],
            d["chg"], d["amp"], d["v"], d["amt"], d["date"]))
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(q, f, ensure_ascii=False, indent=1)
        print("-> %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
