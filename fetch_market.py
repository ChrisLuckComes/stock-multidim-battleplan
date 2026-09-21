#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用行情取数脚本（零外部 skill 依赖 · 兜底数据源）
================================================
当 skill 运行在**非 WorkBuddy 环境**（如 Cursor / 通用 AI Agent）时，
wb-finance-skill 不可用，Agent 应改用本脚本取数。

支持 A 股（东方财富）与美股（Yahoo v8 chart），输出统一 JSON 到 stdout，
供 rule123.py / Agent 直接消费。

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
import sys, json, re, urllib.request, datetime, time, os
import concurrent.futures as cf

HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_json(url, timeout=20, retries=2):
    last = None
    for n in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            if n + 1 < retries:
                time.sleep(0.3 * (n + 1))
    raise last


def fetch_json_fallback(url, timeout=20, retries=2):
    """东财优先走已验证可用的 HTTP，失败后再回退原协议。"""
    primary = url
    fallback = None
    if url.startswith("https://") and ".eastmoney.com/" in url:
        primary = "http://" + url[8:]
        fallback = url
    elif url.startswith("https://"):
        fallback = "http://" + url[8:]
    try:
        return fetch_json(primary, timeout=timeout, retries=retries)
    except Exception as e1:
        if fallback is None:
            raise
        try:
            return fetch_json(fallback, timeout=timeout, retries=1)
        except Exception as e2:
            raise RuntimeError(
                f"primary={type(e1).__name__}:{str(e1)[:50]} | "
                f"fallback={type(e2).__name__}:{str(e2)[:50]}")


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
    # 日线历史（130 根约 6 个月）
    kline_url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&klt=101&fqt=1&end=20500101&lmt=130"
        "&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    )
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        quote_f = ex.submit(fetch_json_fallback, quote_url)
        kline_f = ex.submit(fetch_json_fallback, kline_url)
        q = quote_f.result().get("data", {})
        kd = kline_f.result().get("data", {})
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


# ---------------------------------------------------------------------------
# 美股三源：Nasdaq 官方 API（主）→ Yahoo v8 → stooq CSV
# ---------------------------------------------------------------------------
NASDAQ_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nasdaq.com/",
}


def _nasdaq_json(url):
    req = urllib.request.Request(url, headers=NASDAQ_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _num(s):
    """'$377.67' / '1,014,540' / '+0.43' / '-1.21%' → float；NA/空 → None。"""
    if s is None:
        return None
    t = (str(s).strip().replace("$", "").replace(",", "")
         .replace("%", "").replace("+", ""))
    if t in ("", "--", "-", "NA", "N/A", "null", "None"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def nasdaq_bars(sym, days=400):
    """Nasdaq 官方历史日线（只含已收盘交易日）→ [{d,o,h,l,c,v}] 旧→新。"""
    today = datetime.date.today()
    frm = today - datetime.timedelta(days=days)
    j = _nasdaq_json(f"https://api.nasdaq.com/api/quote/{sym}/historical"
                     f"?assetclass=stocks&fromdate={frm:%Y-%m-%d}"
                     f"&todate={today:%Y-%m-%d}&limit=300")
    rows = ((j.get("data") or {}).get("tradesTable") or {}).get("rows") or []
    bars = []
    for r in rows:
        try:
            mm, dd, yy = str(r.get("date", "")).split("/")
        except ValueError:
            continue
        o, h, l, c = (_num(r.get("open")), _num(r.get("high")),
                      _num(r.get("low")), _num(r.get("close")))
        if None in (o, h, l, c):
            continue
        bars.append({"d": f"{yy}-{mm}-{dd}", "o": o, "h": h, "l": l, "c": c,
                     "v": _num(r.get("volume")) or 0.0})
    bars.sort(key=lambda b: b["d"])                      # 旧 → 新
    if not bars:
        raise RuntimeError(f"Nasdaq 未返回 {sym} 历史日线")
    return bars


def fetch_us_nasdaq(symbol):
    """Nasdaq 官方 API 主源：历史日线 + 实时/盘前快照。

    坑位（实测 2026-09-16）：
      · /chart 的 previousClose 会滞后一整天（本例停在 9/14 的 382.29），
        昨收必须取 secondaryData.lastSalePrice（= 上一完整交易日收盘）。
      · historical 只含已收盘交易日，盘中不会出现当日 bar；
        故 open/high/low/volume 是「最近一个已收盘交易日」的口径，
        盘中真实价以 spot + session + as_of 三个字段表达。
      · marketStatus 由 Nasdaq 直接给出（Pre-Market/Open/Closed/After-Hours），
        免去自己判断夏令时/冬令时。
    """
    sym = symbol.upper()
    info = {}
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        bars_f = ex.submit(nasdaq_bars, sym)
        info_f = ex.submit(
            _nasdaq_json,
            f"https://api.nasdaq.com/api/quote/{sym}/info?assetclass=stocks")
        bars = bars_f.result()
        try:
            info = (info_f.result().get("data") or {})
        except Exception:
            info = {}
    pdat = info.get("primaryData") or {}
    sdat = info.get("secondaryData") or {}
    mstatus = info.get("marketStatus") or ""

    spot = _num(pdat.get("lastSalePrice"))
    prev_close = _num(sdat.get("lastSalePrice"))          # ← 唯一可靠昨收
    if prev_close is None:
        prev_close = bars[-2]["c"] if len(bars) > 1 else bars[-1]["o"]
    if spot is None:                                      # 接口降级 → 退化为末根收盘
        spot = bars[-1]["c"]
        mstatus = mstatus or "Closed"

    change_pct = _num(pdat.get("percentageChange"))
    if change_pct is None and prev_close:
        change_pct = round((spot - prev_close) / prev_close * 100, 2)

    last = bars[-1]                                       # 最近一个已收盘交易日
    quote = {
        "ticker": sym,
        "market": "US",
        "name": info.get("companyName") or sym,
        "exchange": info.get("exchange"),
        "session": mstatus,                               # Pre-Market/Open/Closed/After-Hours
        "as_of": pdat.get("lastTradeTimestamp") or f"Closed at {last['d']}",
        "is_realtime": bool(pdat.get("isRealTime")),
        "spot": spot,
        "prev_close": prev_close,
        "bid": _num(pdat.get("bidPrice")),
        "ask": _num(pdat.get("askPrice")),
        "open": last["o"], "high": last["h"], "low": last["l"], "volume": last["v"],
        "turnover": None,
        "change_pct": change_pct,
        "pe_ttm": None, "pb": None, "market_cap": None, "float_cap": None,
        "bars": bars,
        "source": "nasdaq",
    }

    # 盘前/盘后明细：盘前量对判断「开盘会不会跳空」最有用
    mt = {"Pre-Market": "pre", "After-Hours": "after"}.get(mstatus)
    if mt:
        try:
            et = (_nasdaq_json(f"https://api.nasdaq.com/api/quote/{sym}/extended-trading"
                               f"?assetclass=stocks&markettype={mt}").get("data") or {})
            rows = ((et.get("infoTable") or {}).get("rows") or [])
            if rows:
                r0 = rows[0]
                quote["extended"] = {
                    "session": mstatus,
                    "last": _num(str(r0.get("consolidated", "")).split()[0]),
                    "volume": _num(r0.get("volume")),
                    "high": _num(str(r0.get("highPrice", "")).split()[0]),
                    "low": _num(str(r0.get("lowPrice", "")).split()[0]),
                }
        except Exception:
            pass
    return quote


def fetch_us_yahoo(symbol):
    """Yahoo v8 chart（备用源）。"""
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
            "d": datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).strftime("%Y-%m-%d"),
            "o": float(o), "h": float(h), "l": float(l), "c": float(c),
            "v": float(v or 0),
        })
    if not bars:
        raise RuntimeError(f"Yahoo 返回空 bars：{symbol}")
    last = bars[-1]
    spot = spot if spot is not None else last["c"]
    # Yahoo meta 的 chartPreviousClose 偶尔 stale（如返回数月前的价），优先用 bars[-2] 的收盘
    bars_prev_close = bars[-2]["c"] if len(bars) > 1 else last["o"]
    if prev_close is None or (prev_close > 0 and abs(prev_close - bars_prev_close) / prev_close > 0.05):
        prev_close = bars_prev_close
    change_pct = round((spot - prev_close) / prev_close * 100, 2) if prev_close else None

    return {
        "ticker": symbol,
        "market": "US",
        "name": meta.get("shortName", meta.get("longName", symbol)),
        "session": "Closed",
        "as_of": None,
        "spot": spot,
        "prev_close": prev_close,
        "open": last["o"], "high": last["h"], "low": last["l"], "volume": last["v"],
        "turnover": None,
        "change_pct": change_pct,
        "pe_ttm": None, "pb": None, "market_cap": None, "float_cap": None,
        "bars": bars,
        "source": "yahoo",
    }


def fetch_us_stooq(symbol):
    """stooq 日线兜底（仅日线，spot 取末根收盘）。"""
    bars = fetch_stooq_bars(symbol)
    if not bars:
        raise RuntimeError(f"stooq 未返回 {symbol} 日线")
    last = bars[-1]
    prev_close = bars[-2]["c"] if len(bars) > 1 else last["o"]
    change_pct = round((last["c"] - prev_close) / prev_close * 100, 2)
    return {
        "ticker": symbol,
        "market": "US",
        "name": symbol,
        "session": "Closed",
        "as_of": None,
        "spot": last["c"],
        "prev_close": prev_close,
        "open": last["o"], "high": last["h"], "low": last["l"], "volume": last["v"],
        "turnover": None,
        "change_pct": change_pct,
        "pe_ttm": None, "pb": None, "market_cap": None, "float_cap": None,
        "bars": bars,
        "source": "stooq",
    }


def fetch_us(symbol):
    """美股取数：Nasdaq → Yahoo → stooq，逐源降级并汇总失败原因。"""
    errs = []
    for name, fn in (("nasdaq", fetch_us_nasdaq),
                     ("yahoo", fetch_us_yahoo),
                     ("stooq", fetch_us_stooq)):
        try:
            return fn(symbol)
        except Exception as e:
            errs.append(f"{name}={type(e).__name__}:{str(e)[:70]}")
    raise RuntimeError(f"美股 {symbol} 三源全失败 → " + " | ".join(errs))


def resolve_out_path(out_path, market):
    """若 --out 给的是裸文件名(无目录分隔)，按市场重定向到 out_cn/(ASH) 或 out_us/(其他)；
    若已带目录，则保持原样。"""
    if not out_path:
        return out_path
    if os.path.basename(out_path) != out_path:
        return out_path
    mdir = "out_cn" if market == "ASH" else "out_us"
    return os.path.join(mdir, out_path)


def main():
    if len(sys.argv) < 2:
        print("usage: python fetch_market.py <TICKER> [--out path.json]", file=sys.stderr)
        sys.exit(1)
    ticker = sys.argv[1]
    out_path = None
    if "--out" in sys.argv:
        out_path = sys.argv[sys.argv.index("--out") + 1]
    market, code, secid_or_sym = detect_market(ticker)
    out_path = resolve_out_path(out_path, market)
    try:
        data = fetch_ash(secid_or_sym) if market == "ASH" else fetch_us(secid_or_sym)
    except Exception as e:
        data = {"ticker": ticker, "market": market, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if out_path:
        dir_ = os.path.dirname(os.path.abspath(out_path))
        if dir_:
            os.makedirs(dir_, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"# written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
