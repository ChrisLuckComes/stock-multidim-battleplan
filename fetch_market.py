#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用行情取数脚本（零外部 skill 依赖 · 兜底数据源）
================================================
当 skill 运行在**非 WorkBuddy 环境**（如 Cursor / 通用 AI Agent）时，
wb-finance-skill 不可用，Agent 应改用本脚本取数。

支持 A 股（东方财富）与美股（Yahoo v8 chart），输出统一 JSON 到 stdout，
供 rule123.py / gamma_gex.py / Agent 直接消费。

用法:
  python fetch_market.py 601233          # A股上海
  python fetch_market.py 688002          # A股科创板
  python fetch_market.py 000858          # A股深圳
  python fetch_market.py CF              # 美股
  python fetch_market.py CF --out cf.json

输出字段:
  ticker, market(ASH/US), name, spot, prev_close, open, high, low,
  volume, turnover, change_pct, pe_ttm, pb, market_cap, float_cap,
  bars[{d,o,h,l,c,v}], source
"""
import sys, json, re, urllib.request, datetime, time

HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_json(url, timeout=20, retries=3):
    last = None
    for n in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1.0 * (n + 1))
    raise last


def detect_market(ticker: str):
    """判断标的所属市场并返回 secid / symbol。"""
    ticker = ticker.strip().upper()
    # 显式后缀 .SS / .SZ / .BJ
    m = re.match(r"^(\d{6})\.(SS|SZ|BJ)$", ticker)
    if m:
        code, suffix = m.group(1), m.group(2)
        secid_map = {"SS": "1", "SZ": "0", "BJ": "0"}  # 北交所用 0 也通
        return "ASH", code, f"{secid_map[suffix]}.{code}"
    # 6 位数字 -> A 股
    if re.fullmatch(r"\d{6}", ticker):
        if ticker.startswith(("60", "68", "69")):
            return "ASH", ticker, f"1.{ticker}"
        else:
            return "ASH", ticker, f"0.{ticker}"
    # 否则视为美股
    return "US", ticker, ticker


def _flt(v):
    """东方财富部分字段以「分」为单位返回整数，这里统一转 float。"""
    if isinstance(v, (int, float)):
        return float(v)
    return v


def fetch_ash(secid):
    # 最新行情
    quote_url = (
        "https://push2.eastmoney.com/api/qt/stock/get"
        f"?secid={secid}&ut=fa5fd1943c7b386f172d6893dbfba10b&invt=2&fltt=2"
        "&fields=f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f116,f117,f162,f167,f168,f184,f189,f190"
    )
    q = fetch_json(quote_url).get("data", {})
    if not q:
        raise RuntimeError(f"东方财富未返回 {secid} 行情")

    def fv(key):
        return _flt(q.get(key))

    prev_close = fv("f60") or 0.0
    spot = fv("f43") or 0.0
    change_pct = round((spot - prev_close) / prev_close * 100, 2) if prev_close else None

    quote = {
        "ticker": q.get("f57", secid.split(".")[-1]),
        "market": "ASH",
        "name": q.get("f58", ""),
        "spot": spot,
        "prev_close": prev_close,
        "open": fv("f46"),
        "high": fv("f44"),
        "low": fv("f45"),
        "volume": fv("f47"),
        "turnover": fv("f48"),
        "change_pct": change_pct,
        "pe_ttm": fv("f162"),
        "pb": fv("f167"),
        "market_cap": fv("f116"),
        "float_cap": fv("f117"),
        "turnover_rate": fv("f168"),
        "amplitude": fv("f184"),
        "source": "eastmoney",
    }

    # 日线历史（130 根约 6 个月）
    kline_url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&klt=101&fqt=1&end=20500101&lmt=130"
        "&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    )
    kd = fetch_json(kline_url).get("data", {})
    bars = []
    for line in kd.get("klines", []):
        parts = line.split(",")
        if len(parts) < 6:
            continue
        bars.append({
            "d": parts[0],
            "o": float(parts[1]),
            "c": float(parts[2]),
            "h": float(parts[3]),
            "l": float(parts[4]),
            "v": float(parts[5]),
        })
    quote["bars"] = bars
    return quote


def fetch_stooq_bars(sym):
    """stooq 免费日线 CSV 兜底（Yahoo 不可达时使用）。"""
    url = f"https://stooq.com/q/d/l/?s={sym.lower()}.us&i=d"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode("utf-8")
    bars = []
    for line in text.strip().split("\n"):
        if not line or line.startswith("Date"):
            continue
        p = line.split(",")
        if len(p) < 6:
            continue
        try:
            bars.append({
                "d": p[0],
                "o": float(p[1]), "h": float(p[2]), "l": float(p[3]), "c": float(p[4]),
                "v": float(p[5] or 0),
            })
        except ValueError:
            continue
    return bars


def fetch_us(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=6mo&interval=1d"
        j = fetch_json(url)
        result = j.get("chart", {}).get("result", [None])[0]
        if not result:
            raise RuntimeError(f"Yahoo 未返回 {symbol} 数据")
        meta = result.get("meta", {})
        ts = result.get("timestamp", [])
        q = result.get("indicators", {}).get("quote", [{}])[0]
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
        spot = meta.get("regularMarketPrice")

        bars = []
        opens, highs, lows, closes, vols = (
            q.get("open", []), q.get("high", []), q.get("low", []), q.get("close", []), q.get("volume", []),
        )
        for i, t in enumerate(ts):
            o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], vols[i]
            if None in (o, h, l, c):
                continue
            bars.append({
                "d": datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
                "o": float(o), "h": float(h), "l": float(l), "c": float(c),
                "v": float(v or 0),
            })
        if bars:
            last = bars[-1]
            spot = spot if spot is not None else last["c"]
            prev_close = prev_close if prev_close is not None else (bars[-2]["c"] if len(bars) > 1 else last["o"])
        change_pct = round((spot - prev_close) / prev_close * 100, 2) if prev_close else None

        return {
            "ticker": symbol,
            "market": "US",
            "name": meta.get("shortName", meta.get("longName", symbol)),
            "spot": spot,
            "prev_close": prev_close,
            "open": bars[-1]["o"] if bars else None,
            "high": bars[-1]["h"] if bars else None,
            "low": bars[-1]["l"] if bars else None,
            "volume": bars[-1]["v"] if bars else None,
            "turnover": None,
            "change_pct": change_pct,
            "pe_ttm": None,
            "pb": None,
            "market_cap": None,
            "float_cap": None,
            "bars": bars,
            "source": "yahoo",
        }
    except Exception:
        # Yahoo 不可达 → stooq 兜底（仅有日线，spot 取末根收盘）
        bars = fetch_stooq_bars(symbol)
        if not bars:
            raise RuntimeError(f"Yahoo 与 stooq 均未返回 {symbol} 数据")
        last = bars[-1]
        prev_close = bars[-2]["c"] if len(bars) > 1 else last["o"]
        change_pct = round((last["c"] - prev_close) / prev_close * 100, 2)
        return {
            "ticker": symbol,
            "market": "US",
            "name": symbol,
            "spot": last["c"],
            "prev_close": prev_close,
            "open": last["o"], "high": last["h"], "low": last["l"], "volume": last["v"],
            "turnover": None,
            "change_pct": change_pct,
            "pe_ttm": None, "pb": None, "market_cap": None, "float_cap": None,
            "bars": bars,
            "source": "stooq",
        }


def main():
    if len(sys.argv) < 2:
        print("usage: python fetch_market.py <TICKER> [--out path.json]", file=sys.stderr)
        sys.exit(1)
    ticker = sys.argv[1]
    out_path = None
    if "--out" in sys.argv:
        out_path = sys.argv[sys.argv.index("--out") + 1]
    market, code, secid_or_sym = detect_market(ticker)
    try:
        data = fetch_ash(secid_or_sym) if market == "ASH" else fetch_us(secid_or_sym)
    except Exception as e:
        data = {"ticker": ticker, "market": market, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"# written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
