#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证同花顺 K 线接口（d.10jqka.com.cn/v6/line）能否作为日线备用源。

校验三件事：
  1. 字段语义（日期/开/高/低/收/量/额/换手率）
  2. 复权口径 vs 我们本地东财缓存（同一天收盘价、成交额、换手率是否对得上）
  3. 覆盖长度（/last.js 只给最近 140 根，试分年接口能否回补更长）

用法：python probe_ths_kline_2026-10-31.py [002881]
"""
import glob
import gzip
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

H = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://stockpage.10jqka.com.cn/",
    "Accept-Encoding": "gzip",
}


def get(url, timeout=15):
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=H),
                                 timeout=timeout).read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "ignore")


def ths_line(code, kind="last"):
    """kind: last(最近140) / year(分年)"""
    u = "https://d.10jqka.com.cn/v6/line/hs_%s/01/%s.js" % (code, kind)
    txt = get(u)
    m = re.search(r"\((\{.*\})\)", txt, re.S)
    if not m:
        return None
    d = json.loads(m.group(1))
    rows = []
    for line in (d.get("data") or "").split(";"):
        p = line.split(",")
        if len(p) < 8 or len(p[0]) != 8:
            continue
        try:
            rows.append({
                "d": "%s-%s-%s" % (p[0][:4], p[0][4:6], p[0][6:8]),
                "o": float(p[1]), "h": float(p[2]), "l": float(p[3]), "c": float(p[4]),
                "v": float(p[5]) / 100.0,          # 股→手（对齐东财口径）
                "amt": float(p[6]),
                "turnover": float(p[7]) if p[7] else 0.0,
            })
        except (ValueError, IndexError):
            continue
    return {"meta": {k: d.get(k) for k in ("num", "total", "start", "name", "rt")},
            "bars": rows}


def load_em_cache(code):
    """读本地东财口径缓存（有 amt+turnover）。"""
    for f in glob.glob("data/cache/scr_%s.json" % code):
        d = json.load(open(f, encoding="utf-8"))
        b = d.get("bars") or []
        if b and "turnover" in b[0]:
            return b
    return []


def main():
    code = (sys.argv[1] if len(sys.argv) > 1 else "002881").strip()[-6:]
    print("=" * 78)
    print("同花顺 K 线备用源验证  code=%s  %s" % (code, time.strftime("%Y-%m-%d %H:%M:%S")))
    print("=" * 78)

    t0 = time.time()
    r = ths_line(code)
    print("\n[1] /last.js 耗时 %.2fs  meta=%s  根数=%d"
          % (time.time() - t0, r["meta"], len(r["bars"])))
    if r["bars"]:
        print("    首根:", r["bars"][0])
        print("    末根:", r["bars"][-1])

    # 2) vs 东财缓存对拍
    em = load_em_cache(code)
    print("\n[2] vs 本地东财缓存（%d 根）" % len(em))
    if not em:
        print("    ⚠ 无东财缓存，跳过对拍")
    else:
        emd = {b["d"]: b for b in em}
        common = [b for b in r["bars"] if b["d"] in emd]
        print("    重叠交易日: %d" % len(common))
        if common:
            print("    %-12s %8s %10s %10s %10s"
                  % ("日期", "收盘", "额(亿)", "换手%", "额偏差%"))
            for b in (common[:2] + common[-3:]):
                e = emd[b["d"]]
                ea = e.get("amt") or 0
                dv = (b["amt"] / ea - 1) * 100 if ea else float("nan")
                print("    %-12s %8.2f %10.2f %10.3f %10.2f"
                      % (b["d"], b["c"], b["amt"] / 1e8, b["turnover"], dv))
            # 统计
            dc = [(b["c"] / emd[b["d"]]["c"] - 1) * 100 for b in common if emd[b["d"]].get("c")]
            da = [(b["amt"] / emd[b["d"]]["amt"] - 1) * 100 for b in common
                  if emd[b["d"]].get("amt")]
            print("    收盘价偏差: max|%.2f%%|  成交额偏差: max|%.2f%%|"
                  % (max(abs(x) for x in dc) if dc else 0,
                     max(abs(x) for x in da) if da else 0))

    # 3) 能否回补更长
    print("\n[3] 分年接口（回补更长历史）")
    for kind in ("year/2026", "year/2025"):
        try:
            t1 = time.time()
            rr = ths_line(code, kind)
            n = len(rr["bars"]) if rr else 0
            print("    %-12s 根数=%-4d 耗时%.2fs  范围 %s ~ %s"
                  % (kind, n, time.time() - t1,
                     rr["bars"][0]["d"] if n else "-",
                     rr["bars"][-1]["d"] if n else "-"))
        except Exception as e:
            print("    %-12s FAIL %s: %s" % (kind, type(e).__name__, str(e)[:60]))


if __name__ == "__main__":
    main()
