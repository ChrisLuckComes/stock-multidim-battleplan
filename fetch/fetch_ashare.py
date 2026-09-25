#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股日线抓取器（新浪行情，可出网）：输出与 rule123.py 兼容的 bars JSON。
用法: python fetch_ashare.py <code> [--out path] [--n 130]
code: 6位A股代码。例: 002557 600872 688001
输出 JSON: {ticker, name, market, bars:[{d,o,h,l,c,v}], spot, prev_close, high, low, source}
注: 新浪日K为不复权收盘价；用于价格结构/趋势研判足够，分红缺口可能含噪。
"""
import sys, json, urllib.request, urllib.parse, argparse

def sina_symbol(code: str) -> str:
    code = code.strip()
    if code.startswith(("6", "9")):      # 上交所 600/601/603/688/900
        return "sh" + code
    return "sz" + code                    # 深交所 000/002/300/003

def fetch_kline(code, n=140):
    sym = sina_symbol(code)
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData?" + urllib.parse.urlencode(
               {"symbol": sym, "scale": "240", "ma": "5", "datalen": str(n)}))
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
    with urllib.request.urlopen(req, timeout=25) as r:
        arr = json.loads(r.read().decode("utf-8"))
    bars = []
    for k in arr:
        bars.append({"d": k["day"], "o": float(k["open"]), "c": float(k["close"]),
                     "h": float(k["high"]), "l": float(k["low"]), "v": float(k["volume"])})
    return bars

def fetch_quote(code):
    sym = sina_symbol(code)
    url = f"http://hq.sinajs.cn/list={sym}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
    with urllib.request.urlopen(req, timeout=20) as r:
        line = r.read().decode("gbk", "ignore")
    parts = line.split('"')[1].split(",")
    if len(parts) < 32:
        return {"name": sym, "spot": None}
    return {"name": parts[0], "open": float(parts[1]), "prev_close": float(parts[2]),
            "spot": float(parts[3]), "high": float(parts[4]), "low": float(parts[5])}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("code")
    ap.add_argument("--out", default=None)
    ap.add_argument("--n", type=int, default=140)
    args = ap.parse_args()
    code = args.code
    bars = fetch_kline(code, n=args.n)
    q = fetch_quote(code)
    spot = q.get("spot") or bars[-1]["c"]
    out = {
        "ticker": code, "name": q.get("name", code), "market": "ASH",
        "bars": bars, "spot": spot, "prev_close": q.get("prev_close"),
        "high": q.get("high"), "low": q.get("low"), "source": "sina",
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"OK {code} {out['name']} | bars={len(bars)} 末根={bars[-1]['d']} 收={bars[-1]['c']} spot={spot}")
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
