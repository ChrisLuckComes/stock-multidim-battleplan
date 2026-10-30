#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""百普赛斯 301080 盘中实时量价盯盘（30秒粒度）
盯：现价 / 分时VWAP偏离 / 量比 / 五档买卖失衡 / 量能突变
信号：上破98.40(昨收) / 到99.00(目标) / 破97.80(开盘) / 破97.00(死线)
"""
import json, time, urllib.request, os, sys
from datetime import datetime

CODE = "sz301080"
BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "logs_watch_301080.txt")
INTERVAL = 30
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 80  # 默认 80*30s = 40分钟

PREV = 98.40
OPEN_REF = 97.85
KEY_UP = 98.40
TARGET = 99.00
DEAD = 97.00
BROKEN_OPEN = 97.80

def http(url, referer="http://finance.qq.com"):
    req = urllib.request.Request(url, headers={
        "Referer": referer,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    return urllib.request.urlopen(req, timeout=8).read()

def quote():
    raw = http("http://qt.gtimg.cn/q=%s" % CODE).decode("gbk", "ignore")
    f = raw.split("~")
    def g(i, d=0.0):
        try:
            return float(f[i])
        except Exception:
            return d
    bids = []
    asks = []
    for i in range(5):
        bids.append((g(9 + 2 * i), g(10 + 2 * i)))
        asks.append((g(19 + 2 * i), g(20 + 2 * i)))
    return dict(price=g(3), prev=g(4, PREV), open=g(5), vol=g(6),
                pct=g(32), high=g(33), low=g(34), amt=g(37, 0.0),
                liangbi=g(48), bids=bids, asks=asks)

def vwap():
    try:
        d = json.loads(http("http://web.ifzq.gtimg.cn/appstock/app/minute/query?code=%s" % CODE).decode("utf-8"))
        node = d["data"][CODE]["data"]["data"]
        last = node[-1].split()
        t, p, cum_vol, cum_amt = last[0], float(last[1]), float(last[2]), float(last[3])
        if cum_vol <= 0:
            return None, None, 0.0
        # 累计额单位判定：若 额/量 明显不是价格量级，则按万元计
        v = cum_amt / (cum_vol * 100)
        if v < p * 0.2 or v > p * 5:
            v = cum_amt * 10000.0 / (cum_vol * 100.0)
        return round(v, 3), t, cum_vol
    except Exception:
        return None, None, 0.0

def main():
    lines = []
    prev_vol = None
    deltas = []
    for r in range(ROUNDS):
        now = datetime.now().strftime("%H:%M:%S")
        try:
            q = quote()
        except Exception as e:
            lines.append("%s 取数失败 %s" % (now, str(e)[:40]))
            with open(LOG, "w", encoding="utf-8") as fp:
                fp.write("\n".join(lines) + "\n")
            time.sleep(INTERVAL)
            continue
        v, vt, cum_vol = vwap()
        dev = ""
        if v:
            dev = "%+.2f%%" % ((q["price"] / v - 1) * 100)
        bsum = sum(x[1] for x in q["bids"])
        asum = sum(x[1] for x in q["asks"])
        ratio = ""
        if asum > 0:
            ratio = "%.1fx" % (bsum / asum)
        dvol = 0.0
        if prev_vol is not None:
            dvol = cum_vol - prev_vol
            deltas.append(dvol)
        prev_vol = cum_vol
        avg_prev = (sum(deltas[-6:-1]) / max(1, len(deltas[-6:-1]))) if len(deltas) >= 2 else 0
        spike = ""
        if avg_prev > 0 and dvol > avg_prev * 3 and dvol > 200:
            spike = " ★量突变%.0f手(>3x)" % dvol

        sig = []
        if q["price"] >= TARGET:
            sig.append("【到目标99+】")
        if q["price"] >= KEY_UP:
            sig.append("【站上昨收98.40】")
        if q["price"] <= DEAD:
            sig.append("【★破死线97.00】")
        elif q["price"] <= BROKEN_OPEN:
            sig.append("【破开盘97.80】")

        line = "%s %7.2f %+6.2f%% VWAP=%s(偏%s) 量比=%s 买%.0f/卖%.0f手(%s) 时量%.0f手%s %s" % (
            now, q["price"], q["pct"], ("%.2f" % v) if v else "NA", dev or "-",
            ("%.2f" % q["liangbi"]) if q["liangbi"] else "-",
            bsum, asum, ratio or "-", dvol, spike, "".join(sig))
        lines.append(line)
        with open(LOG, "w", encoding="utf-8") as fp:
            fp.write("\n".join(lines) + "\n")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
