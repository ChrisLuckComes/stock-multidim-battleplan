#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预热宽基缓存（腾讯 ifzq 501 时的兜底手法）。

腾讯 web.ifzq.gtimg.cn 不可用 ⇒ market_regime.fetch_index_bars 返回 None
⇒ 宽基闸门 broad_state=unknown、market_pulse 判 unknown。
本脚本用「新浪指数日线 + 新浪 hq 快照补当日」构造 bars，写入
market_regime._CACHE，再调用 market_pulse.pulse_for 与 index_regime。

用法：
  python out_cn/prewarm_broad.py                # 打印指数明细 + 脉搏
  python out_cn/prewarm_broad.py --json-pulse data/pulse_cn_YYYYMMDD.json
"""
from __future__ import print_function

import argparse
import datetime
import json
import os
import sys
import time
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}

INDICES = ["sh000001", "sz399006", "sh000852", "sh000688"]


def _get(url, timeout=15, encoding="utf-8"):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(encoding, "ignore")


def sina_index_daily(sym, n=70):
    """新浪指数日线 → [{d,o,h,l,c,v}]（旧→新）。volume 单位＝股。"""
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20t=/"
           "CN_MarketDataService.getKLineData?symbol=" + sym +
           "&scale=240&ma=no&datalen=" + str(n))
    txt = _get(url)
    i, j = txt.find("("), txt.rfind(")")
    if i < 0 or j < 0:
        return None
    raw = txt[i + 1:j].strip().strip(";")
    for k in ("day", "open", "high", "low", "close", "volume"):
        raw = raw.replace(k + ":", '"' + k + '":')
    arr = json.loads(raw)
    out = []
    for b in arr:
        out.append({"d": str(b["day"])[:10],
                    "o": float(b["open"]), "h": float(b["high"]),
                    "l": float(b["low"]), "c": float(b["close"]),
                    "v": float(b["volume"])})
    return out


def sina_snapshot(syms):
    """hq 快照 → {sym: {o,prev,c,h,l,v,date}}（指数无需除 100，volume 单位＝股）。"""
    url = "https://hq.sinajs.cn/list=" + ",".join(syms)
    txt = _get(url, encoding="gbk")
    out = {}
    for line in txt.splitlines():
        if "hq_str_" not in line:
            continue
        sym = line.split("hq_str_")[1].split("=")[0]
        body = line.split('"')[1] if '"' in line else ""
        p = body.split(",")
        if len(p) < 32:
            continue
        try:
            out[sym] = {"o": float(p[1]), "prev": float(p[2]), "c": float(p[3]),
                        "h": float(p[4]), "l": float(p[5]),
                        "v": float(p[8]), "date": p[30]}
        except Exception:
            pass
    return out


def build_bars(sym, n=70, snap=None):
    bars = sina_index_daily(sym, n)
    if not bars:
        return None
    if snap and sym in snap:
        s = snap[sym]
        today = s["date"]
        if today and bars[-1]["d"] != today and s["c"] > 0:
            bars.append({"d": today, "o": s["o"], "h": s["h"],
                         "l": s["l"], "c": s["c"], "v": s["v"]})
    return bars


def prewarm(syms=None, n=70, quiet=False):
    from gates import market_regime as MR
    syms = syms or INDICES
    snap = sina_snapshot(syms)
    now = time.time()
    got = {}
    for sym in syms:
        bars = build_bars(sym, n, snap)
        if bars:
            MR._CACHE[sym] = (now, bars)
            got[sym] = bars
    if not quiet:
        for sym in syms:
            b = got.get(sym)
            if not b:
                print(sym, "ERR")
                continue
            print(sym, "n=%d" % len(b), "last=%s c=%.2f" % (b[-1]["d"], b[-1]["c"]))
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-pulse", default="")
    ap.add_argument("--json-out", default="")
    a = ap.parse_args()

    got = prewarm()
    from gates import market_regime as MR
    from gates import market_pulse as MP

    print("\n== index_regime ==")
    for sym, b in got.items():
        closes = [x["c"] for x in b]
        print(sym, MR.index_regime(b), "MA10=%.2f MA20=%.2f" % (
            MR.sma(closes, 10) or 0, MR.sma(closes, 20) or 0))

    print("\n== market_pulse(cn) ==")
    p = MP.pulse_for(market="cn")
    print(json.dumps(p, ensure_ascii=False, indent=2, default=str))
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=2, default=str)
        print("已写", a.json_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
