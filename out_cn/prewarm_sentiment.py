#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""情绪分数据源兜底（东财 push2/push2his 502 时用）。

东财 push2（指数快照/资金/宽度）与 push2his（指数日线）全主机 502 时，
market_sentiment 首维取数即抛错。本脚本**不改任何打分逻辑与映射常数**，
只把两个取数函数换成新浪通道后调用官方 analyze()：

  get_index_snapshot → 新浪 hs_a 全市场列表按 sh/sz 分组计数（涨/跌/平、成交额）
  get_index_bars     → 新浪指数日线（amt 用「成交量 × 当日市场均价」近似，仅用于量比）

⚠ 已知缺口：主力净额（f62）无替代源 ⇒ 资金维按 50 兜底，分数为上界。
用法：python out_cn/prewarm_sentiment.py [--out-dir data]
"""
from __future__ import print_function

import argparse
import json
import os
import sys
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from gates import market_sentiment as ms  # noqa: E402

HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
SINA_LIST = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData?page={page}&num=100&sort=symbol&asc=1&node=hs_a")


def _get(url, timeout=20, encoding="utf-8"):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(encoding, "ignore")


def fetch_all_a():
    """拉全市场列表，返回 {'sh': {...}, 'sz': {...}} 聚合计数与成交额/成交量。"""
    agg = {"sh": {"adv": 0, "dec": 0, "flat": 0, "amt": 0.0, "vol": 0.0},
           "sz": {"adv": 0, "dec": 0, "flat": 0, "amt": 0.0, "vol": 0.0}}
    page, total = 1, None
    while True:
        txt = _get(SINA_LIST.format(page=page))
        txt = txt.strip()
        if not txt or txt == "null":
            break
        arr = json.loads(txt)
        if not arr:
            break
        for it in arr:
            sym = (it.get("symbol") or "").lower()
            mk = sym[:2]
            if mk not in agg:
                continue
            pct = float(it.get("changepercent") or 0)
            g = agg[mk]
            if pct > 0:
                g["adv"] += 1
            elif pct < 0:
                g["dec"] += 1
            else:
                g["flat"] += 1
            g["amt"] += float(it.get("amount") or 0)
            g["vol"] += float(it.get("volume") or 0)
        total = (total or 0) + len(arr)
        if len(arr) < 100:
            break
        page += 1
        if page > 80:
            break
    print(f"# 新浪全市场样本 {total} 只（沪 {agg['sh']['adv']+agg['sh']['dec']+agg['sh']['flat']}"
          f" / 深 {agg['sz']['adv']+agg['sz']['dec']+agg['sz']['flat']}）", file=sys.stderr)
    return agg


def sina_index_daily(sym, n=70):
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20t=/"
           "CN_MarketDataService.getKLineData?symbol=" + sym +
           "&scale=240&ma=no&datalen=" + str(n))
    txt = _get(url)
    i, j = txt.find("("), txt.rfind(")")
    if i < 0 or j < 0:
        return []
    raw = txt[i + 1:j].strip().strip(";")
    for k in ("day", "open", "high", "low", "close", "volume"):
        raw = raw.replace(k + ":", '"' + k + '":')
    out = []
    for b in json.loads(raw):
        out.append({"d": str(b["day"])[:10], "o": float(b["open"]),
                    "h": float(b["high"]), "l": float(b["low"]),
                    "c": float(b["close"]), "v": float(b["volume"])})
    return out


def sina_quotes(syms):
    txt = _get("https://hq.sinajs.cn/list=" + ",".join(syms), encoding="gbk")
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
                        "h": float(p[4]), "l": float(p[5]), "v": float(p[8])}
        except Exception:      # noqa: BLE001
            pass
    return out


def build(agg):
    """构造东财口径的 secid 快照 + 指数日线。"""
    q = sina_quotes(["sh000001", "sz399001", "sz399006"])
    mkt = {"1.000001": ("sh", "sh000001"), "0.399001": ("sz", "sz399001")}
    snap = {}
    for secid, (mk, sym) in mkt.items():
        g = agg[mk]
        s = q.get(sym) or {}
        prev = s.get("prev") or 0
        chg = ((s.get("c", 0) / prev - 1) * 100) if prev else 0.0
        snap[secid] = {"f104": g["adv"], "f105": g["dec"], "f106": g["flat"],
                       "f6": g["amt"], "f62": 0.0, "f3": round(chg, 2),
                       "f2": s.get("c")}
    s6 = q.get("sz399006") or {}
    prev6 = s6.get("prev") or 0
    snap["0.399006"] = {"f104": 0, "f105": 0, "f106": 0, "f6": 0.0, "f62": 0.0,
                        "f3": round(((s6.get("c", 0) / prev6 - 1) * 100) if prev6 else 0.0, 2),
                        "f2": s6.get("c")}

    # 成交量→成交额 的近似系数（按当日市场均价），保证量比与成交量比一致
    factor = {}
    for secid, (mk, _sym) in mkt.items():
        g = agg[mk]
        factor[secid] = (g["amt"] / g["vol"]) if g["vol"] else 1.0

    cache = {}
    today = ms.beijing_now().strftime("%Y-%m-%d")

    def bars_of(secid):
        if secid in cache:
            return cache[secid]
        sym = {"1.000001": "sh000001", "0.399001": "sz399001",
               "0.399006": "sz399006"}[secid]
        f = factor.get(secid)
        rows = sina_index_daily(sym)
        bars = []
        for b in rows:
            amt = b["v"] * (f if f else 1.0)
            bars.append({"d": b["d"], "o": b["o"], "c": b["c"],
                         "h": b["h"], "l": b["l"], "v": b["v"], "amt": amt})
        # 新浪指数日线 15:35 前后才更新当日 bar ⇒ 缺当日就用 hq 快照补一根
        if sym in q and (not bars or bars[-1]["d"] != today):
            s = q[sym]
            v = s["v"] or 0.0
            bars.append({"d": today, "o": s["o"], "c": s["c"],
                         "h": s["h"], "l": s["l"], "v": v,
                         "amt": v * (f if f else 1.0)})
        cache[secid] = bars
        return bars

    return snap, bars_of


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(_ROOT, "data"))
    a = ap.parse_args()

    agg = fetch_all_a()
    snap, bars_of = build(agg)

    ms.get_index_snapshot = lambda: snap
    ms.get_index_bars = lambda secid, lmt=60: bars_of(secid)

    r = ms.analyze()
    r["source_note"] = ("东财 push2/push2his 502 ⇒ 宽度取新浪全市场计数、指数日线取新浪日线、"
                        "成交额序列以成交量×当日均价近似；主力净额无替代源，资金维按 50 兜底，"
                        "总分为上界。打分逻辑与映射常数未改动。")
    sd = str(r.get("score_date") or r["asof"][:10]).replace("-", "")
    p = os.path.join(a.out_dir, f"sentiment_{sd}.json")
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2)
    print(f"# 落盘 {p}", file=sys.stderr)
    print(ms.render(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
