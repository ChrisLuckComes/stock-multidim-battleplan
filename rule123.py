#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
方向感知结构判定（延续 vs 反转）
================================
六种买法（当日只给一种，由 plan_entry 输出 mode）：
  1. platform_break 平台突破（优先T1）：近 3 根内收盘站上活平台沿且放量，买突破位
     （活沿见 living_platform；123 的 R1 仍是 P0–P1 反应高，二者不是同一个东西）
  2. w_bottom_break W底颈线突破（优先T1）：双底后收盘站上颈线且放量，买颈线
  3. flag_tl_break 旗形下降趋势线突破（优先T1）：主升旗杆后的下降整理旗面，收盘站上旗面下降趋势线且放量
  4. line_pullback 沿线回踩（优先T1）：沿着肉眼可见的线上升，回踩该线买；默认 P0→P1 上升趋势线，仅明显贴均线才改用该均线
  5. impulse_pause 大阳后缩量回踩（次优先T2）：大阳线之后回踩缩量找买点
  6. downtrend_tl_break 下降趋势线突破（次优先T2）：下跌段反转，近 3 根内站上下降高点连线；与旗形区分，无旗杆则才用本模式
  wait = 没有可执行模式，或距买位 >2×ATR

HH 两两比较不作门控。cond2 = 自 P1 未再创新低。不用 MA60。

数据：A 股东财 / 美股 Yahoo / --data 喂 fetch_market.py JSON。
"""
import urllib.request, json, sys, datetime, re, time, os

HEADERS = {"User-Agent": "Mozilla/5.0"}
NASDAQ_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nasdaq.com/",
}


def fetch_json_nasdaq(url, timeout=20, retries=2):
    """Nasdaq 官方 API 需要完整浏览器头 + Referer，否则 403。"""
    last = None
    for n in range(retries):
        try:
            req = urllib.request.Request(url, headers=NASDAQ_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except Exception as e:
            last = e
            if n + 1 < retries:
                time.sleep(0.3 * (n + 1))
    raise last


def _num_q(s):
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


def is_ash(ticker: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", ticker.strip()))


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


def bars_from_yahoo(sym, range_="6mo"):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={range_}&interval=1d"
        j = fetch_json(url)
        r = j["chart"]["result"][0]
        ts = r["timestamp"]
        q = r["indicators"]["quote"][0]
        bars = []
        for i, t in enumerate(ts):
            o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            if None in (o, h, l, c):
                continue
            bars.append({
                "d": datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).strftime("%Y-%m-%d"),
                "o": o, "h": h, "l": l, "c": c, "v": q["volume"][i] or 0,
            })
        return bars, r["meta"].get("regularMarketPrice")
    except Exception:
        bars = fetch_stooq_bars(sym)
        if not bars:
            raise RuntimeError(f"Yahoo 与 stooq 均未返回 {sym} 数据")
        return bars, bars[-1]["c"]


def bars_from_em(secid, lmt=130):
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&klt=101&fqt=1&end=20500101&lmt={lmt}"
        "&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    )
    kd = fetch_json_fallback(url).get("data", {})
    bars = []
    for line in kd.get("klines", []):
        p = line.split(",")
        if len(p) < 6:
            continue
        bars.append({
            "d": p[0], "o": float(p[1]), "c": float(p[2]),
            "h": float(p[3]), "l": float(p[4]), "v": float(p[5]),
        })
    # 最新价取末根
    last_q = None
    try:
        q = fetch_json_fallback(
            "https://push2.eastmoney.com/api/qt/stock/get"
            f"?secid={secid}&ut=fa5fd1943c7b386f172d6893dbfba10b&invt=2&fltt=2"
            "&fields=f43,f57,f58"
        ).get("data", {})
        last_q = float(q.get("f43")) if q.get("f43") else None
    except Exception:
        pass
    return bars, last_q


def secid_of(ticker: str):
    ticker = ticker.strip()
    if ticker.startswith(("60", "68", "69")):
        return f"1.{ticker}"
    return f"0.{ticker}"


def bars_from_nasdaq(sym, days=400):
    """Nasdaq 官方 API：历史日线 + 实时/盘前快照 → (bars, spot)。

    实测要点（2026-09-16）：
      · historical 只含「已收盘交易日」，盘中不会出现当日 bar；
        故 spot 必须来自 /info 的 primaryData（盘前/盘中实时），不能取 bars[-1].c。
      · marketStatus 由 Nasdaq 直接给出（Pre-Market/Open/Closed/After-Hours），
        免去自行判断夏令时/冬令时。
      · 不要用 /chart 的 previousClose 当昨收——它会滞后一整天（实测停在 9/14）。
    """
    s = sym.upper()
    today = datetime.date.today()
    frm = today - datetime.timedelta(days=days)
    j = fetch_json_nasdaq(
        f"https://api.nasdaq.com/api/quote/{s}/historical"
        f"?assetclass=stocks&fromdate={frm:%Y-%m-%d}&todate={today:%Y-%m-%d}&limit=300"
    )
    rows = ((j.get("data") or {}).get("tradesTable") or {}).get("rows") or []
    bars = []
    for r in rows:
        try:
            mm, dd, yy = str(r.get("date", "")).split("/")
        except ValueError:
            continue
        o, h, l, c = (_num_q(r.get("open")), _num_q(r.get("high")),
                      _num_q(r.get("low")), _num_q(r.get("close")))
        if None in (o, h, l, c):
            continue
        bars.append({"d": f"{yy}-{mm}-{dd}", "o": o, "h": h, "l": l, "c": c,
                     "v": _num_q(r.get("volume")) or 0.0})
    bars.sort(key=lambda b: b["d"])                      # 旧 → 新
    if not bars:
        raise RuntimeError(f"Nasdaq 未返回 {sym} 日线")

    spot, meta = None, {"session": "", "as_of": None, "prev_close": None}
    try:
        info = (fetch_json_nasdaq(
            f"https://api.nasdaq.com/api/quote/{s}/info?assetclass=stocks"
        ).get("data") or {})
        pdat = info.get("primaryData") or {}
        sdat = info.get("secondaryData") or {}
        spot = _num_q(pdat.get("lastSalePrice"))
        meta = {
            "session": info.get("marketStatus") or "",
            "as_of": pdat.get("lastTradeTimestamp"),
            "prev_close": _num_q(sdat.get("lastSalePrice")),
        }
    except Exception:
        pass
    return bars, (spot if spot is not None else bars[-1]["c"]), meta


_EM_US_CACHE = {}
_YAHOO_PROXY = None


def em_us_secid(sym):
    """东财美股 secid 前缀探测：105=纳斯达克、106=纽交所（结果缓存）。"""
    s = sym.upper()
    if s in _EM_US_CACHE:
        return _EM_US_CACHE[s]
    for pre in ("105", "106"):
        try:
            j = fetch_json_fallback(
                f"http://push2his.eastmoney.com/api/qt/stock/kline/get"
                f"?secid={pre}.{s}&klt=101&fqt=1&end=20500101&lmt=2"
                f"&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56")
            if ((j.get("data") or {}).get("klines") or []):
                _EM_US_CACHE[s] = f"{pre}.{s}"
                return _EM_US_CACHE[s]
        except Exception:
            continue
    raise RuntimeError(f"东财未识别美股 {sym} 的交易所前缀（105/106 均无数据）")


def bars_from_em_us(sym, klt=101, lmt=130):
    """东财美股 K 线（走 http 通道，见 fetch_json_fallback）。

    实测（2026-09-16）：东财 HTTPS 被中间设备阻断、HTTP 明文可通；
    且这是目前唯一提供「美股分钟级带量 K 线」的可用源——
    Nasdaq /chart 只有稀疏价格点（无量），新浪/腾讯美股分钟线均已失效。
      · klt=101 日线（d 形如 2026-09-15）
      · klt=5   五分钟（d 形如 2026-09-16 04:00，**北京时间**）
    """
    secid = em_us_secid(sym)
    url = (f"http://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
           f"&klt={klt}&fqt=1&end=20500101&lmt={lmt}"
           f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58")
    d = (fetch_json_fallback(url).get("data") or {})
    kl = d.get("klines") or []
    if not kl:
        raise RuntimeError(f"东财美股未返回 {sym} klt={klt} 数据")
    bars = []
    for line in kl:
        p = line.split(",")
        if len(p) < 6:
            continue
        # f51 时间 / f52 开 / f53 收 / f54 高 / f55 低 / f56 量
        bars.append({"d": p[0], "o": float(p[1]), "c": float(p[2]),
                     "h": float(p[3]), "l": float(p[4]), "v": float(p[5])})
    return bars, None, {"session": "", "as_of": None, "prev_close": None}


def _proxy_list():
    """美股分钟线的代理候选。Yahoo 直连 403，实测需走本地代理。

    不读 HTTP_PROXY/HTTPS_PROXY —— 沙箱会注入一个假代理（实测 57189 返回 502），
    用显式的 WB_US_PROXY 覆盖，否则按常见客户端端口逐个探。
    """
    out = []
    if _YAHOO_PROXY:
        out.append(_YAHOO_PROXY)
    env = os.environ.get("WB_US_PROXY")
    if env and env not in out:
        out.append(env)
    for p in (7897, 7890, 7891, 10809, 1080):
        proxy = f"http://127.0.0.1:{p}"
        if proxy not in out:
            out.append(proxy)
    return out


def fetch_json_proxy(url, proxy=None, timeout=20):
    """经代理（或直连）取 JSON。"""
    if proxy:
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        op = urllib.request.build_opener()
    req = urllib.request.Request(url, headers=NASDAQ_HEADERS)
    with op.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def bars_from_yahoo_min(sym, interval="5m", range_="1d"):
    """Yahoo 美股分钟线（带量）——美股盘中的**第二源**。

    实测（2026-09-16）：直连 query1.finance.yahoo.com 返回 403；
    经本地代理（Clash 类 7897）可通，返回带量 OHLC（5 分钟 78/79 根带量）。
    东财美股分钟线是唯一另一源，一旦被限流（批量取数后实测整站拒连）就没得用，
    故此处保留降级链。

    时间戳统一转成**北京时间**字符串 "YYYY-MM-DD HH:MM"，与东财口径一致，
    以便 us_trade_date / us_offset_min 通用。
    """
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym.upper()}"
           f"?interval={interval}&range={range_}&includePrePost=false")
    global _YAHOO_PROXY
    errs = []
    for proxy in _proxy_list():
        try:
            j = fetch_json_proxy(url, proxy if proxy else None)
        except Exception as e:
            errs.append(f"{proxy or 'direct'}={type(e).__name__}")
            continue
        res = ((j.get("chart") or {}).get("result") or [])
        if not res:
            errs.append(f"{proxy}=empty")
            continue
        r0 = res[0]
        ts = r0.get("timestamp") or []
        if not ts:
            errs.append(f"{proxy}=no_ts")
            continue
        q = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
        o_, h_, l_, c_, v_ = (q.get("open") or [], q.get("high") or [],
                              q.get("low") or [], q.get("close") or [],
                              q.get("volume") or [])
        bars = []
        for i, t in enumerate(ts):
            if i >= len(c_):
                break
            oo, hh, ll, cc = (o_[i] if i < len(o_) else None,
                              h_[i] if i < len(h_) else None,
                              l_[i] if i < len(l_) else None, c_[i])
            if None in (oo, hh, ll, cc):
                continue
            bars.append({
                "d": datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M"),
                "o": float(oo), "h": float(hh), "l": float(ll), "c": float(cc),
                "v": float(v_[i] if i < len(v_) and v_[i] else 0),
            })
        if not bars:
            errs.append(f"{proxy}=no_bars")
            continue
        _YAHOO_PROXY = proxy
        return bars, None, {"session": "", "as_of": None, "prev_close": None,
                            "source": "yahoo_min", "proxy": proxy}
    raise RuntimeError(f"Yahoo 分钟线 {sym} 全部失败 → " + " | ".join(errs))


def bars_from_us(sym):
    """美股取数：Nasdaq（带盘前/市场状态）→ 东财 http → Yahoo（含 stooq 兜底）。
    统一返回 (bars, spot, meta)。"""
    errs = []
    for name, fn in (("nasdaq", bars_from_nasdaq),
                     ("eastmoney", bars_from_em_us),
                     ("yahoo", bars_from_yahoo)):
        try:
            out = fn(sym)
        except Exception as e:
            errs.append(f"{name}={type(e).__name__}:{str(e)[:60]}")
            continue
        if len(out) == 2:                       # yahoo 返回 (bars, spot)
            bars, spot = out
            return bars, spot, None
        return out
    raise RuntimeError(f"美股 {sym} 全部数据源失败 → " + " | ".join(errs))


def get_bars(sym, data_file=None):
    """→ (bars, spot, meta)。meta 目前只有美股会填（session/as_of/prev_close）。"""
    if data_file:
        with open(data_file, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("bars", []), d.get("spot"), None
    if is_ash(sym):
        bars, last_q = bars_from_em(secid_of(sym))
        return bars, last_q, None
    return bars_from_us(sym)


def pivots(bars, w=3):
    """摆动高低点。允许并列（<=/>=），同簇内只保留更极端的一根，避免一字连板导致 R1 空。"""
    Hs, Ls = [], []
    n = len(bars)
    for i in range(w, n - w):
        hh, ll = bars[i]["h"], bars[i]["l"]
        if all(bars[j]["h"] <= hh for j in range(i - w, i + w + 1) if j != i):
            Hs.append((i, hh))
        if all(bars[j]["l"] >= ll for j in range(i - w, i + w + 1) if j != i):
            Ls.append((i, ll))

    def _dedup(ps, higher_is_better):
        out = []
        for i, p in ps:
            if out and i - out[-1][0] < w:
                better = p > out[-1][1] if higher_is_better else p < out[-1][1]
                if better:
                    out[-1] = (i, p)
                continue
            out.append((i, p))
        return out

    return _dedup(Hs, True), _dedup(Ls, False)


def line_val(p_b, p_a, idx):
    i_b, y_b = p_b
    i_a, y_a = p_a
    if i_a == i_b:
        return y_a
    return y_b + (y_a - y_b) * (idx - i_b) / (i_a - i_b)


def sma(closes, n):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def ema(closes, n):
    if len(closes) < n:
        return None
    k = 2.0 / (n + 1)
    e = sum(closes[:n]) / n
    for i in range(n, len(closes)):
        e = closes[i] * k + e * (1 - k)
    return e


def typical_vwap(bars, n):
    sl = bars[-n:] if len(bars) >= n else bars
    num = 0.0
    den = 0.0
    for b in sl:
        v = float(b.get("v") or 0)
        num += (b["h"] + b["l"] + b["c"]) / 3.0 * v
        den += v
    if den <= 0:
        return None
    return num / den


def sma_at(closes, n, i):
    if i + 1 < n:
        return None
    return sum(closes[i + 1 - n:i + 1]) / n


def ema_series(closes, n):
    out = [None] * len(closes)
    if len(closes) < n:
        return out
    k = 2.0 / (n + 1)
    e = sum(closes[:n]) / n
    out[n - 1] = e
    for i in range(n, len(closes)):
        e = closes[i] * k + e * (1 - k)
        out[i] = e
    return out


def atr14(bars, n=14):
    """Wilder ATR（与通达信/东财口径一致）。首值用前 n 根 TR 简单均值，其后递推。"""
    if len(bars) < 2:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < n:
        return sum(trs) / len(trs) if trs else None
    atr = sum(trs[:n]) / n
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr


def avwap_from(bars, start_i):
    series = [None] * len(bars)
    num = 0.0
    den = 0.0
    for i in range(start_i, len(bars)):
        b = bars[i]
        v = float(b.get("v") or 0)
        num += (b["h"] + b["l"] + b["c"]) / 3.0 * v
        den += v
        if den > 0:
            series[i] = num / den
    return series


def _px(obj):
    if not obj:
        return None, None
    if isinstance(obj, dict):
        return obj.get("i"), obj.get("price")
    return obj[0], obj[1]


def days_above_level(bars, level):
    if level is None:
        return 0
    n = 0
    for b in reversed(bars):
        if b["c"] > level:
            n += 1
        else:
            break
    return n


def living_platform(bars, Hs, atr_v, lookback=60, press_atr=2.5):
    """活平台沿：正在顶的、或刚被收盘打穿的那条阻力，不是突破阳的最高价。

    经典 R1 只取 P0–P1 之间最高，会漏 P1 之后的新沿、P0 之前仍压着的前高。
    但也不能把刚突破那根的高点（睿创 185）当成新 R1：近 3 根窗口里沿仍是被破的那条（179）。

    规则：只用已确认摆动高点（不含近端未走完枢轴、不含当日上影）。
    该高点之后有过收盘站上，就不再当未破沿。
    若近 3 根刚破了一条更低的沿，突破窗口内的新高全部丢掉（那是冲高，不是平台）。
    有更高的未破沿（闰土 14.22 vs 内沿 13.34）仍用未破沿。
    否则近 3 根刚破的沿就是活沿。远处 ATH 用 press_atr 滤掉。
    """
    n = len(bars)
    if n < 5 or not atr_v or atr_v <= 0:
        return None
    last_c = bars[-1]["c"]
    start = max(0, n - lookback)
    cands = [(i, h) for i, h in Hs if i >= start]
    if not cands:
        return None

    pressing = []
    fresh = []
    for i, h in cands:
        n_ab = days_above_level(bars, h)
        closed_after = any(bars[j]["c"] > h for j in range(i + 1, n))
        if (not closed_after) and h >= last_c and (h - last_c) <= press_atr * atr_v:
            pressing.append((i, h, n_ab))
        elif last_c > h and 1 <= n_ab <= 3:
            fresh.append((i, h, n_ab))

    if fresh:
        top_h = max(h for _, h, _ in fresh)
        win_start = n - days_above_level(bars, top_h)
        pressing = [(i, h, n_ab) for i, h, n_ab in pressing if i < win_start]

    if pressing:
        first = min(h for _, h, _ in pressing)
        shelf = [(i, h, n_ab) for i, h, n_ab in pressing if h <= first + 0.35 * atr_v]
        i, h, n_ab = max(shelf, key=lambda x: x[1])
        return {"i": i, "price": h, "days_above": n_ab, "kind": "pressing"}

    if fresh:
        i, h, n_ab = max(fresh, key=lambda x: x[1])
        return {"i": i, "price": h, "days_above": n_ab, "kind": "fresh_break"}
    return None


def count_tags(bars, level_at, atr_v, lookback=12, k=0.5):
    if not atr_v or atr_v <= 0:
        return 0, None
    n = len(bars)
    start = max(0, n - lookback)
    hits = 0
    last_dist = None
    band = k * atr_v
    for i in range(start, n):
        lv = level_at(i)
        if lv is None:
            continue
        lo, c = bars[i]["l"], bars[i]["c"]
        if min(abs(lo - lv), abs(c - lv)) <= band:
            hits += 1
        if i == n - 1:
            last_dist = c - lv
    return hits, last_dist


ANCHOR_LABEL = {
    "hl_trendline": "上升趋势线",
    "ema10": "EMA10",
    "sma20": "SMA20",
    "ma5": "MA5",
    "avwap_p1": "锚定VWAP",
    "platform_lip": "活平台沿",
    "r1": "突破位R1",  # 兼容旧字段；平台突破请用 platform_lip
    "w_neckline": "W底颈线",
    "flag_tl": "旗形下降趋势线",
    "yang_digest": "大阳调整区",
    "yang_gap": "缺口下沿",
    "down_tl": "下降趋势线",
}

# A股开盘至收盘（含午休）：当日 K 线未走完，量能不可信
_ASH_LIVE = (9 * 60 + 30, 15 * 60)


def is_live_bar(bars, market="ASH", now=None):
    """末根是否为今日未收盘 K 线（盘中量能/RVOL 不可信）。"""
    if not bars:
        return False
    now = now or datetime.datetime.now()
    if str(bars[-1]["d"])[:10] != now.strftime("%Y-%m-%d"):
        return False
    if market != "ASH":
        # 美股：当日末根即视为 live（无时区库时保守拦截）
        return True
    t = now.hour * 60 + now.minute
    return _ASH_LIVE[0] <= t < _ASH_LIVE[1]


def in_ash_session(date_str=None, now=None):
    """现在是否处于 A 股交易时段（09:30–15:00，含午休）。

    与 `is_live_bar` 分工不同：后者问「日线末根要不要丢」，本函数问「现在是不是盘中」。
    新浪日线在盘中**不返回当日半根**（10:23 取到的末根仍是昨天），两者必然不一致；
    合成一个判断会把盘中误报成【已收盘】，从而跳过盘中确认、错给次日预案。
    `date_str` 传实时快照的日期，用来排除非交易日（休市时快照停在上一交易日）。
    """
    now = now or datetime.datetime.now()
    if date_str and str(date_str)[:10] != now.strftime("%Y-%m-%d"):
        return False
    t = now.hour * 60 + now.minute
    return _ASH_LIVE[0] <= t < _ASH_LIVE[1]


# 美股「未收盘」时段：只有常规盘中才把今日 bar 合成进结构。
# 盘前/盘后成交稀疏，用它们充当「收盘站上」会造出新的假突破 —— 不合成。
# Closed → 日线本身已完整，合成反而会造出重复的一根。
_US_INTRADAY_SESSION = ("open",)
_US_SESSION_MIN = 390                    # 常规时段 09:30–16:00 ET
_US_OPEN_MIN = 9 * 60 + 30
_MON = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def us_session_clock(meta):
    """从 Nasdaq 快照的 `as_of`（'Sep 17, 2026 12:19 PM ET'）解出 (ET 日期, ET 分钟)。

    无时区库时不去自己换算夏令时 —— Nasdaq 已经把 ET 算好写进字符串，
    直接解析它，比拿北京时间减固定偏移稳。返回 (None, None) 表示解不出。
    """
    if not meta:
        return None, None
    m = re.search(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4}) (\d{1,2}):(\d{2}) (AM|PM)",
                  str(meta.get("as_of") or ""))
    if not m:
        return None, None
    mon, day, yr, hh, mi, ap = m.groups()
    mo = _MON.get(mon)
    if not mo:
        return None, None
    h = int(hh) % 12 + (12 if ap == "PM" else 0)
    return f"{int(yr):04d}-{mo:02d}-{int(day):02d}", h * 60 + int(mi)


def project_session_volume(v, meta):
    """盘中累计量 → 预计全日量（量比口径）。

    半天的累计量直接比整日均量必然偏低：INTC 2026-09-17 12:26 ET 已成交 85.5M，
    rvol20 却算出 0.94 被标成「非放量」，而按已走时段折算约 2.1× = 真放量。
    低标放量会错误地压低仓位，故按 A 股量比习惯折算。取不到时钟则原样返回。
    """
    _, clk = us_session_clock(meta)
    if not v or clk is None:
        return v
    elapsed = min(max(clk - _US_OPEN_MIN, 1), _US_SESSION_MIN)
    return v * _US_SESSION_MIN / float(elapsed)


def intraday_bar(sym, meta, bars, fetch=None):
    """合成「今日盘中未收盘」的一根 K 线（美股）。

    背景（2026-09-17 INTC）：Nasdaq historical **只含已收盘交易日**，盘中取到的
    末根仍是昨日。原实现把 spot 只当一个附带字段，结构依旧按昨收算 —— 于是突破
    当天引擎完全看不见突破：模式停在昨收的 line_pullback，买区（99.45~104.03）
    整段落在突破位 106.69 之下，输出只剩「等回踩」。用户实况：「突破了平台，
    却让我 104.03 买」＝ 这个口径错位。

    今日 o/h/l/c 用 5 分钟线聚合。`v_raw` = 当日累计量（与 Nasdaq primaryData 的
    volume 实测吻合，INTC 9/17 12:19 ET：84.57M vs 84.56M）；`v` = 按已走时段折算的
    预计全日量（量比口径），避免半天量比整日均量被误读成「缩量」。

    仅在「常规盘中（session=Open）+ 日线尚未含当日」时才合成。盘前/盘后成交稀疏，
    不参与；已收盘或拿不到数据也返回 None。
    """
    if not bars or not meta:
        return None
    if str(meta.get("session") or "").strip().lower() not in _US_INTRADAY_SESSION:
        return None
    d_et, _ = us_session_clock(meta)
    if not d_et or str(bars[-1]["d"])[:10] >= d_et:
        return None                     # 拿不到交易日，或日线已含当日
    fn = fetch or (lambda s: bars_from_yahoo_min(s)[0])
    try:
        m5 = fn(sym) or []
    except Exception:
        return None
    m5 = [x for x in m5 if x.get("o") is not None and x.get("c") is not None]
    if not m5:
        return None
    hs = [x["h"] for x in m5 if x.get("h") is not None]
    ls = [x["l"] for x in m5 if x.get("l") is not None]
    if not hs or not ls:
        return None
    v_raw = sum(x.get("v") or 0 for x in m5)
    return {
        "d": d_et,
        "o": m5[0]["o"],
        "h": max(hs),
        "l": min(ls),
        "c": m5[-1]["c"],
        "v": project_session_volume(v_raw, meta),   # 量比口径，见函数注释
        "v_raw": v_raw,
        "live": True,                   # 标记：未收盘，量能为折算值
    }


def merge_intraday_bar(sym, bars, meta, market="US", fetch=None):
    """把今日盘中未收盘 bar 并入日线序列 → (bars2, live_bar|None)。

    并入后 build_ev / plan_entry 会自然地把「今日站上平台沿」算成 fresh_break，
    买区随之从昨收的回踩位刷到突破位 —— 这是「不再永远等回踩」的关键。
    量能字段缺 A 股（东财日线盘中不返回当日半根，meta 为空）→ 自动不合成。
    `fetch` 仅供测试注入 5 分钟线，生产走默认取数。
    """
    if not bars or market == "ASH":
        return bars, None
    b = intraday_bar(sym, meta, bars, fetch=fetch)
    if not b:
        return bars, None
    return bars + [b], b


def is_yizi(bar, prev_c, atr_v):
    """真一字/振幅极小跳空：有缺口 + 振幅 tiny + 实体≈0。光脚大阳天然排除。"""
    if prev_c is None:
        return False
    has_gap = bar["o"] > prev_c * 1.01
    y_range = bar["h"] - bar["l"]
    body = abs(bar["c"] - bar["o"])
    tiny = y_range <= max(bar["c"] * 0.003, 0.15 * (atr_v or bar["c"] * 0.01))
    return bool(has_gap and tiny and body <= 0.03 * bar["c"])


def too_far_from_zone(z, atr_v, last_c, limit=2.0):
    """现价高出买位（level/突破位）>limit×ATR → 不追。与 SKILL「离买位 >2×ATR」对齐，不随买区带宽漂移。"""
    if not atr_v or last_c is None or not z:
        return False
    lv = z.get("level")
    if lv is None:
        return False
    return (last_c - lv) / atr_v > limit


def living_demand(bars, ev):
    """沿线回踩的线：默认 = P0→P1 上升趋势线。
    当该上升趋势线不可用（加速线豁免或 P0/P1 点数不足）时，才以明显贴轨的均线（EMA10/MA5/SMA20，
    近 8 根至少 4 次触及）兜底；趋势线可用时优先趋势线。不用 VWAP/R1 抢默认买区。
    """
    atr_v = atr14(bars)
    if not atr_v:
        return None
    last_i = len(bars) - 1
    closes = [b["c"] for b in bars]
    p0i, p0p = _px(ev.get("P0"))
    p1i, p1p = _px(ev.get("P1"))

    def pack(name, level, hits, dist):
        if level is None or dist is None:
            return None
        if dist < -0.4 * atr_v:
            return None
        return {
            "anchor": name,
            "level": level,
            "hits": hits,
            "dist_atr": dist / atr_v,
            "atr": atr_v,
            "cluster_lo": level,
            "cluster_hi": level,
        }

    tl_res = None
    slope = tl_slope_atr(bars, ev, atr_v)
    tl_steep = slope is not None and slope > TL_SLOPE_MAX_ATR
    # 加速线不作回踩锚：斜率超过 TL_SLOPE_MAX_ATR 时，这条线外推后必然「追上」
    # 价格，把横盘误报成跌破（2026-09-18 BE）。此时直接跳过它，让均线锚有机会。
    if (
        not tl_steep
        and p0i is not None and p1i is not None
        and p1p is not None and p0p is not None
        and p1p > p0p and p1i != p0i
    ):
        def tl(i, _a=(p0i, p0p), _b=(p1i, p1p)):
            return line_val(_a, _b, i)
        hits, dist = count_tags(bars, tl, atr_v)
        tl_res = pack("hl_trendline", tl(last_i), hits, dist)

    walk = None
    e10 = ema_series(closes, 10)

    def try_walk(name, level_at):
        hits, dist = count_tags(bars, level_at, atr_v, lookback=8, k=0.5)
        if hits < 4:
            return None
        return pack(name, level_at(last_i), hits, dist)

    for name, fn in (
        ("ema10", lambda i: e10[i]),
        ("ma5", lambda i: sma_at(closes, 5, i)),
        ("sma20", lambda i: sma_at(closes, 20, i)),
    ):
        w = try_walk(name, fn)
        if w is None:
            continue
        if walk is None or w["hits"] > walk["hits"]:
            walk = w

    # 默认趋势线；趋势线不可用（加速线豁免/点数不足）时以均线锚兜底，不再用 1.5×ATR 归一化阈值卡切换
    out = tl_res if tl_res is not None else walk
    if out is not None:
        # 诊断字段：供 note 说明「为什么没用 P0→P1 线」
        out["tl_steep"] = tl_steep
        out["tl_slope_atr"] = slope
    return out


def rising_tl_level(bars, ev):
    p0i, p0p = _px(ev.get("P0"))
    p1i, p1p = _px(ev.get("P1"))
    if (
        p0i is None or p1i is None or p0p is None or p1p is None
        or p1p <= p0p or p1i == p0i
    ):
        return None
    return line_val((p0i, p0p), (p1i, p1p), len(bars) - 1)


def still_uptrend(bars, ev, last_c, c2, p1p):
    """大阳后缩量回踩的升势过滤：收盘须在 P1 之上且未跌破 P0→P1 线。

    加速线豁免（2026-09-18 BE）：斜率 > TL_SLOPE_MAX_ATR 的 P0→P1 线，每天自行
    抬升的幅度已接近标的日波动，外推后「追上」价格是必然，不代表升势走坏 ——
    此时不以该线作否决，升势只由 P1 与 c2 把关（宁松勿错杀）。
    """
    if p1p is None or last_c is None or last_c <= p1p or not c2:
        return False
    _slope = tl_slope_atr(bars, ev, atr14(bars))
    if _slope is not None and _slope > TL_SLOPE_MAX_ATR:
        return True
    tl = rising_tl_level(bars, ev)
    if tl is not None and last_c < tl:
        return False
    return True


# 短均线锚：弱票阴跌/横盘也会反复蹭 MA5，肉眼是下跌趋势，脚本却给 line_pullback。
MA_WALK_ANCHORS = frozenset({"ma5", "ema10", "sma20"})

# P0→P1 上升趋势线的斜率上限（单位：×ATR/根）。
# 超过此值的线是「加速轨迹线」而非「支撑线」：它每天自行抬升的幅度已接近标的的
# 正常日波动，外推时会「追上」价格 —— 只要价格横盘一两根就自动变成「跌破」，
# 与价格是否真的走弱无关。此时该线既不作回踩锚，也不作否决依据。
#
# 2026-09-18 BE 实例：P0=9/1 低 197.50 → P1=9/14 低 249.05，斜率 6.44 元/根
# = 0.341×ATR/天（34 只样本第 94 百分位）。外推到 9/18 = 274.83；而按昨线
# 268.38 看，价格 269.64 仍在线上方 —— 外推一天就翻转了「是否跌破」的结论。
# 当日「跌破」的 5.19 元里，6.44 元是线自己涨上去的。
#
# 阈值 0.25 取自实测分布的 90 分位（50%:0.008 / 75%:0.088 / 90%:0.242）。
TL_SLOPE_MAX_ATR = 0.25


def tl_slope_atr(bars, ev, atr_v):
    """P0→P1 上升趋势线的斜率，单位 ×ATR/根。无有效线时返回 None。"""
    if not atr_v or atr_v <= 0:
        return None
    p0i, p0p = _px(ev.get("P0"))
    p1i, p1p = _px(ev.get("P1"))
    if (
        p0i is None or p1i is None or p0p is None or p1p is None
        or p1p <= p0p or p1i == p0i
    ):
        return None
    return (p1p - p0p) / (p1i - p0i) / atr_v


def ma_anchor_trend_ok(bars, ev, demand):
    """短均线锚的沿线回踩趋势闸门（2026-09-17 科华数据 002335 反例）。

    hl_trendline（摆动上升趋势线）不受限——那才是肉眼可见的升势沿线。
    ma5 / ema10 / sma20 必须满足其一，否则 recommend 强制 False：
      1. regime == continuation；或
      2. 收盘 > SMA20。
    """
    if demand is None:
        return True
    if demand.get("anchor") not in MA_WALK_ANCHORS:
        return True
    if ev.get("regime") == "continuation":
        return True
    if not bars:
        return False
    closes = [b["c"] for b in bars]
    s20 = sma_at(closes, 20, len(bars) - 1)
    last_c = bars[-1]["c"]
    if s20 is not None and last_c > s20:
        return True
    return False


def reversal_yang_ok(bars, imp, p1p, last_c, atr_v):
    """反转态下仍允许「大阳后缩量回踩」的放宽门控（2026-09-16 决定松绑）。

    背景：原门控 still_uptrend 要求 c2（自 P1 以来未创新低）。但强势票最常见的
    启动形态恰恰是「先破位洗盘 → 一根放量大阳收复 P1」——此时 c2=False 却已站上
    P1，结果整套大阳回踩分支被跳过，真信号整段丢弃（ILMN 9/15：+6.76%、量 1.70 倍、
    收 222.29 站上 P1 206.82，却只得到 wait 且买区为空）。

    放宽必须带防护，四条同时成立才认（任一不满足就退回原门控，宁缺勿滥）：
      1. 基准日收盘站上 P1 —— 结构与 structure_ok 同口径，破位尚未修复的不认；
      2. 大阳当日放量 —— 量 ≥ 1.5× 其前 20 日均量，无量的阳线不认；
      3. 大阳实体足够强 —— body ≥ 1.2×ATR 或 涨幅 ≥ 4%（弱阳不足以扭转破位）；
      4. 未回吐到大阳体下半部 —— 守住「反转仍有效」，跌破中点即视为失败。
    """
    if p1p is None or last_c is None or last_c <= p1p:
        return False
    yi = imp.get("yang_i")
    if yi is None or yi < 0 or yi >= len(bars):
        return False
    y = bars[yi]
    # 2) 放量：相对大阳前的 20 日均量
    pre = [float(b.get("v") or 0) for b in bars[max(0, yi - 20):yi]]
    pre = [v for v in pre if v > 0]
    if not pre:
        return False
    avg = sum(pre) / len(pre)
    y_vol = float(y.get("v") or 0)
    if y_vol <= 0 or avg <= 0 or y_vol < 1.5 * avg:
        return False
    # 3) 实体强度
    body = y["c"] - y["o"]
    pct = body / y["o"] if y["o"] else 0.0
    if (not atr_v or body < 1.2 * atr_v) and pct < 0.04:
        return False
    # 4) 未回吐到大阳体下半部
    mid = (y["l"] + y["h"]) / 2.0
    if last_c < mid:
        return False
    return True


def is_yang_bar(bar, atr_v, prev=None):
    body = bar["c"] - bar["o"]
    rng = bar["h"] - bar["l"]
    # 一字涨停：实体≈0，且须相对前收跳空（SKILL.md）；无缺口的一字/准一字不算大阳
    if body <= 0:
        if prev is None or not prev.get("c"):
            return False
        tiny = rng <= max(bar["c"] * 0.003, 0.15 * (atr_v or bar["c"] * 0.01))
        if not tiny:
            return False
        return bool(bar["c"] >= prev["c"] * 1.03)
    pct = body / bar["o"] if bar["o"] else 0
    strong = pct >= 0.03 or (atr_v and (body >= 0.8 * atr_v or rng >= 1.0 * atr_v))
    if not strong:
        return False
    if rng > 0 and (bar["c"] - bar["l"]) / rng < 0.55:
        return False
    return True


def vol_at_recent_low(bars, yang_i):
    """缩量：大阳之后这波调整里，末根量已到近期最低（允许 10% 容差），且低于大阳当日量。"""
    y_vol = float(bars[yang_i].get("v") or 0)
    last_v = float(bars[-1].get("v") or 0)
    after = [float(b.get("v") or 0) for b in bars[yang_i + 1:]]
    pos = [v for v in after if v > 0]
    if last_v <= 0 or not pos:
        return False
    min_v = min(pos)
    if last_v > min_v * 1.1:
        return False
    if y_vol > 0 and last_v >= y_vol:
        return False
    return True


def held_lows_3d(bars, yang_i, atr_v):
    """最近 3 根收盘都不下破「窗口之前」的回调最低收盘（允许 0.1×ATR）。未满 3 根不算。
    基准固定在 n-3 之外，禁止棘轮把阴跌误判成控盘。用收盘而非最低价。
    """
    n = len(bars)
    if n - 1 - yang_i < 3:
        return False
    pad = 0.1 * atr_v if atr_v else bars[-1]["c"] * 0.002
    prior = [bars[j]["c"] for j in range(yang_i + 1, n - 3)]
    ref = min(prior) if prior else bars[yang_i]["c"]
    return min(bars[j]["c"] for j in range(n - 3, n)) >= ref - pad


def yang_floor(bars, yang_i, atr_v):
    """防守位：普通大阳=当日低点；真一字/振幅极小跳空=缺口下沿（前收）。"""
    y = bars[yang_i]
    y_lo, y_hi = y["l"], y["h"]
    prev_c = bars[yang_i - 1]["c"] if yang_i > 0 else None
    has_gap = prev_c is not None and y["o"] > prev_c * 1.01
    yi_zi = is_yizi(y, prev_c, atr_v)
    if yi_zi:
        return {
            "yi_zi": True,
            "has_gap": True,
            "floor": prev_c,
            "zone_lo": prev_c,
            "zone_hi": y_hi,
            "gap_lo": prev_c,
            "gap_hi": y["o"],
        }
    return {
        "yi_zi": False,
        "has_gap": bool(has_gap),
        "floor": y_lo,
        "zone_lo": y_lo,
        "zone_hi": y_hi,
        "gap_lo": prev_c if has_gap else None,
        "gap_hi": y["o"] if has_gap else None,
    }


def find_impulse_pause(bars, atr_v):
    """最近一根大阳线之后的缩量回踩。返回 state + 买区字段。"""
    n = len(bars)
    last = bars[-1]
    last_c = last["c"]
    start = max(0, n - 10)
    yang_i = None
    for i in range(n - 1, start - 1, -1):
        prev = bars[i - 1] if i > 0 else None
        if is_yang_bar(bars[i], atr_v, prev):
            yang_i = i
            break
    if yang_i is None:
        return {"state": "no_yang"}
    y = bars[yang_i]
    y_lo, y_hi = y["l"], y["h"]
    fl = yang_floor(bars, yang_i, atr_v)
    floor, zone_lo, zone_hi = fl["floor"], fl["zone_lo"], fl["zone_hi"]
    # 两件事必须分开：
    #   大阳体 [zone_lo, zone_hi]（普通大阳=低点~高点）是「未走坏的有效性边界」——
    #     用来判断现价是否还在调整区、是否已延伸；
    #   买区/下单带 [buy_lo, buy_hi] 是「贴防守位 floor 单边向上 1.0×ATR」，
    #     与突破类买区同构。大阳体常宽达 2~2.5×ATR，把它当买区等于没有买区
    #     （挂在 19.09 和挂在 20.90 是两笔完全不同的交易），无法执行。
    body_lo, body_hi = zone_lo, zone_hi
    band = 1.0 * atr_v if atr_v else (body_hi - body_lo)
    buy_lo = floor
    buy_hi = min(body_hi, floor + band) if band > 0 else body_hi
    if buy_hi < buy_lo:
        buy_hi = buy_lo
    pad = 0.15 * atr_v if atr_v else floor * 0.005

    def _z(extra=None):
        zz = _empty_zone()
        zz.update({
            "type": "大阳后缩量回踩(次优先T2)",
            "anchor": "yang_gap" if fl["yi_zi"] else "yang_digest",
            "level": round(floor, 2),
            "primary_lo": round(buy_lo, 2),
            "primary_hi": round(buy_hi, 2),
            "in_zone": bool((buy_lo - pad) <= last_c <= (buy_hi + pad)),
            "dist_atr": round((last_c - buy_hi) / atr_v, 2) if atr_v else None,
            "extended": bool(last_c > body_hi + (0.5 * atr_v if atr_v else 0)),
            "invalidation": round(floor - pad, 2),
            "yang_d": y["d"],
            "hits": n - 1 - yang_i,
            "yi_zi": fl["yi_zi"],
            "has_gap": fl["has_gap"],
            "gap_lo": round(fl["gap_lo"], 2) if fl["gap_lo"] is not None else None,
            "gap_hi": round(fl["gap_hi"], 2) if fl["gap_hi"] is not None else None,
            "body_lo": round(body_lo, 2),
            "body_hi": round(body_hi, 2),
        })
        if extra:
            zz.update(extra)
        return zz

    extra = {
        "yang_i": yang_i,
        "yang_d": y["d"],
        "yang_low": y_lo,
        "yang_high": y_hi,
        "floor": floor,
        "yi_zi": fl["yi_zi"],
    }
    if yang_i == n - 1:
        # 大阳当日也要带 zone：否则调用方只能回退到别的买区/大阳体，
        # 计划卡上会出现两个互相矛盾的买区。
        return {"state": "yang_today", "zone": _z(), **extra}
    lows_after = [bars[j]["c"] for j in range(yang_i + 1, n)]
    min_after = min(lows_after)
    if min_after < floor - pad:
        return {"state": "broke_yang_low", "zone": _z(), **extra}
    shrink = vol_at_recent_low(bars, yang_i)
    held = held_lows_3d(bars, yang_i, atr_v)
    days_after = n - 1 - yang_i
    z = _z({"held_3d": held})
    if z["extended"]:
        return {"state": "extended", "zone": z, **extra}
    if not shrink:
        return {"state": "no_shrink", "zone": z, **extra}
    # 进买区只看下单带（贴防守位 1.0×ATR），不再用大阳体 —— 否则回踩到大阳中部
    # 就算「到位」，等于在大阳体内任意价位都能买。
    if z["in_zone"]:
        if days_after < 3:
            return {"state": "need_3d", "zone": z, **extra}
        if not held:
            return {"state": "still_falling", "zone": z, **extra}
        return {"state": "digest", "zone": z, **extra}
    return {"state": "waiting_back", "zone": z, **extra}


def _empty_zone():
    return {
        "type": "非候选",
        "path": "wait",
        "anchor": None,
        "level": None,
        "base": None,
        "gap_hi": None,
        "primary_lo": None,
        "primary_hi": None,
        "body_lo": None,
        "body_hi": None,
        "in_zone": False,
        "dist_atr": None,
        "hits": 0,
        "extended": False,
        "chase_only": False,
        "invalid": False,
        "invalid_reason": None,
        "invalidation": None,
        "vwap5": None,
        "ma5": None,
        "fail_lo": None,
        "fail_hi": None,
    }


# 回踩买区下沿相对锚的容差（×ATR）。
#
# 2026-09-18 SNDK：旧版买区是「锚 ±1.0×ATR」双侧对称，下沿落在锚下方整整 1 个 ATR。
# 那是**破线之后**的区域，本不该挂买单；更要命的是它必然低于任何「锚 −0.10×ATR」的
# 硬止损，于是 stop_plan 每次都判「买区与止损锚冲突」→ 把买区下沿抬到硬止损之上。
# 当硬止损锚（MA5）本身高过现价时，抬完的买区整个飞到现价上方：
#   SNDK 9/16 收 1519.97，回踩锚=上升趋势线 1492.76，输出的买区却是 1580.16~1604.72
#   （在收盘上方 60 元），结构止损 1585.76 还高过次日开盘 1564.53 = 买入即止损。
# 下沿只留毛刺余量（0.05×ATR），让「锚 −0.10×ATR」的硬止损天然落在买区下沿之下，
# 不再触发那条抬买区的分支；上沿保留 1.0×ATR，与 in_zone 的贴线门槛一致。
PULLBACK_ZONE_PAD_LO_ATR = 0.05
PULLBACK_ZONE_PAD_HI_ATR = 1.0


def zone_from_demand(demand, bars, ev):
    """沿线回踩买区：下沿=锚 −0.05×ATR（毛刺），上沿=锚 +1.0×ATR（与贴线门槛一致）。"""
    closes = [b["c"] for b in bars]
    last_c = closes[-1]
    ma5 = sma(closes, 5)
    v5 = typical_vwap(bars, 5)
    ma20 = sma(closes, 20)
    r1 = ev.get("R1", {}).get("price") if isinstance(ev.get("R1"), dict) else None
    out = _empty_zone()
    out["vwap5"] = round(v5, 2) if v5 is not None else None
    out["ma5"] = round(ma5, 2) if ma5 is not None else None
    if not demand:
        return out
    atr_v = demand.get("atr") or atr14(bars)
    level = demand["level"]
    pad = 1.0 * atr_v if atr_v else level * 0.01
    # 下沿：只留毛刺余量（见 PULLBACK_ZONE_PAD_LO_ATR 注释）。cluster 只在锚之上时
    # 才可能抬高上沿，下沿恒以锚为基准，不再整体下探 1 个 ATR。
    lo = min(demand.get("cluster_lo", level), level) - PULLBACK_ZONE_PAD_LO_ATR * pad
    hi = max(demand.get("cluster_hi", level), level) + PULLBACK_ZONE_PAD_HI_ATR * pad
    if last_c and hi - lo < last_c * 0.002:
        mid = (lo + hi) / 2.0
        lo, hi = mid * 0.997, mid * 1.003
    tagging = demand["dist_atr"] <= 1.0
    extended = demand["dist_atr"] > 2.0
    label = ANCHOR_LABEL.get(demand["anchor"], demand["anchor"])
    kind = f"延伸等回踩·{label}" if extended else f"回踩(A)·{label}"
    out.update({
        "type": kind,
        "path": "wait" if (extended or not tagging) else "A",
        "anchor": demand["anchor"],
        "level": round(level, 2),
        "primary_lo": round(lo, 2),
        "primary_hi": round(hi, 2),
        "in_zone": bool(tagging and not extended),
        "dist_atr": round(demand["dist_atr"], 2),
        "hits": demand["hits"],
        "extended": extended,
        # invalidation 兼容旧字段：结构位=该线本身（收盘破）；硬止损见 stop_plan
        "invalidation": round(level, 2),
    })
    if ev.get("cond3_break_prior_high") and r1:
        out["fail_lo"] = round(r1 * 0.99, 2)
        out["fail_hi"] = round((ma20 * 1.01 if ma20 else r1 * 1.01), 2)
    return out


def days_above_line(bars, p_b, p_a):
    n = 0
    for i in range(len(bars) - 1, -1, -1):
        if bars[i]["c"] > line_val(p_b, p_a, i):
            n += 1
        else:
            break
    return n


def detect_w_bottom(bars, Hs, Ls, atr_v, lookback=55):
    """W 底：近端两个近似等深的摆动低点 + 中间反弹高作颈线。

    返回 {l1, l2, neckline, neck_i, span} 或 None。不负责判定是否已突破。
    """
    n = len(bars)
    if n < 15 or not atr_v or atr_v <= 0 or len(Ls) < 2:
        return None
    start = max(0, n - lookback)
    lows = [(i, p) for i, p in Ls if i >= start]
    if len(lows) < 2:
        return None
    best = None
    for a in range(len(lows) - 1):
        for b in range(a + 1, len(lows)):
            i1, p1 = lows[a]
            i2, p2 = lows[b]
            span = i2 - i1
            if span < 5 or span > 40:
                continue
            depth = abs(p2 - p1)
            if depth > max(0.5 * atr_v, p1 * 0.03):
                continue
            # 右底不低于左底太多（允许略低，但不能破成单边下跌）
            if p2 < p1 - 0.35 * atr_v:
                continue
            neck_i, neck = None, None
            for j in range(i1 + 1, i2):
                if neck is None or bars[j]["h"] > neck:
                    neck, neck_i = bars[j]["h"], j
            # 也允许用两底之间已确认摆动高
            mid_hs = [(i, h) for i, h in Hs if i1 < i < i2]
            if mid_hs:
                hi = max(mid_hs, key=lambda x: x[1])
                if neck is None or hi[1] >= neck:
                    neck_i, neck = hi[0], hi[1]
            if neck is None or neck_i is None:
                continue
            rise = neck - max(p1, p2)
            if rise < max(0.8 * atr_v, max(p1, p2) * 0.03):
                continue
            # 右底之后、突破前，价格应主要在颈线之下活动
            after = bars[i2 + 1: n]
            if not after:
                continue
            closed_above = sum(1 for x in after if x["c"] > neck)
            if closed_above > 3:
                continue
            score = rise / atr_v - depth / atr_v + min(span, 20) / 20.0
            cand = {
                "l1": {"i": i1, "price": p1, "d": bars[i1]["d"]},
                "l2": {"i": i2, "price": p2, "d": bars[i2]["d"]},
                "neckline": neck,
                "neck_i": neck_i,
                "neck_d": bars[neck_i]["d"],
                "span": span,
                "score": score,
            }
            if best is None or score > best["score"]:
                best = cand
    return best


def local_highs(bars, w=2):
    """±w 滚动窗内的局部高点（比 pivots 更宽松）。

    必须比 pivots(w=3) 宽松：**单调下移段内 w=3 根本不产生枢轴高**。
    TEM 2026-08-27 高 71.89 因左侧 08-24 高 72.02 更高而被 w=3 丢弃，于是
    「下降高点连线」取不到锚点 —— 而 71.89 正是肉眼那条斜线的起点之一。
    """
    n = len(bars)
    out = []
    for i in range(w, n - w):
        h = bars[i]["h"]
        if all(bars[j]["h"] <= h for j in range(i - w, i + w + 1) if j != i):
            out.append((i, h))
    ded = []
    for i, h in out:
        if ded and i - ded[-1][0] < w:
            if h > ded[-1][1]:
                ded[-1] = (i, h)
            continue
        ded.append((i, h))
    return ded


def detect_down_trendline(bars, atr_v, start_i=None, lookback=30, w=2,
                          touch_k=0.35, viol_w=1.5, min_touch=2, max_viol=3):
    """下降趋势线：近端高点依次下移，选「被最多触碰、被最少收盘穿越」的那条。

    为什么不能用「P1 之前的两个枢轴高」（旧口径）：TEM 2026-09 的 P1 是 09-02
    低 60.06，其前两个枢轴高是 08-12@57.03 与 08-21@72.96 —— 那是一条**上升**
    线，外推到末根得到 97.99→111.65（而当期价格只有 60-70），declining=False，
    于是 downtrend_tl_break 永不触发。该画的其实是 08-21@72.96 → 09-02@67.10
    （下移、9 次触碰），即肉眼那条斜线。

    本函数只负责把线画对；突破判定（last_c > tl_now 且 days_above_tl ≤ 3）与
    量能门控交给调用方。锚点不含末根（末根是待判突破的那根，不能当摆动高）。
    """
    n = len(bars)
    if n < 12 or not atr_v or atr_v <= 0:
        return None
    lo_i = max(start_i if start_i is not None else 0, n - lookback)
    cand = [(i, h) for i, h in local_highs(bars, w) if i >= lo_i and i <= n - w - 1]
    if len(cand) < 2:
        return None
    best = None
    for a in range(len(cand)):
        for b in range(a + 1, len(cand)):
            i1, y1 = cand[a]
            i2, y2 = cand[b]
            if y2 >= y1 or i2 - i1 < w:
                continue
            p_b, p_a = (i1, y1), (i2, y2)
            touch = sum(
                1 for k in range(i1, n)
                if abs(bars[k]["h"] - line_val(p_b, p_a, k)) <= touch_k * atr_v
            )
            # 突破前的收盘穿越（末根不算：末根正是要判突破的那一根）
            viol = sum(
                1 for k in range(i1, n - 1)
                if bars[k]["c"] > line_val(p_b, p_a, k)
            )
            score = touch - viol_w * viol
            if best is None or score > best["score"]:
                best = {
                    "i1": i1, "y1": y1, "i2": i2, "y2": y2,
                    "touch": touch, "viol": viol, "score": score,
                }
    if best is None:
        return None
    if best["touch"] < min_touch or best["viol"] > max_viol:
        return None
    # 线的右端必须够近：远期化石线即使统计好看也不是「当期」结构
    if best["i2"] < n - 20:
        return None
    pb, pa = (best["i1"], best["y1"]), (best["i2"], best["y2"])
    tl_now = line_val(pb, pa, n - 1)
    if tl_now is None:
        return None
    return {
        "pb": {"i": best["i1"], "price": round(best["y1"], 4), "d": bars[best["i1"]]["d"]},
        "pa": {"i": best["i2"], "price": round(best["y2"], 4), "d": bars[best["i2"]]["d"]},
        "tl_now": tl_now,
        "days_above_tl": days_above_line(bars, pb, pa),
        "touches": best["touch"],
        "viol": best["viol"],
        "score": best["score"],
        "dist_atr": (bars[-1]["c"] - tl_now) / atr_v,
    }


def detect_bull_flag(bars, Hs, atr_v, lookback=45):
    """上升旗形：先有旗杆（短促大涨），再有下降高点连成的旗面。

    返回 {pole_start, pole_end, pb, pa, tl_now, days_above_tl} 或 None。
    """
    n = len(bars)
    if n < 20 or not atr_v or atr_v <= 0:
        return None
    start = max(1, n - lookback)
    best_pole = None
    # 旗杆：3–12 根内净涨幅够大，终点是局部高点
    for end in range(start + 2, n - 3):
        for length in range(3, 13):
            s = end - length + 1
            if s < start:
                break
            lo = min(bars[j]["l"] for j in range(s, end + 1))
            hi = bars[end]["h"]
            # 终点应接近区间最高
            if hi < max(bars[j]["h"] for j in range(s, end + 1)) * 0.985:
                continue
            gain = hi - lo
            pct = gain / lo if lo else 0
            if gain < 2.0 * atr_v and pct < 0.08:
                continue
            # 旗杆后至少还剩 4 根做旗面
            if n - 1 - end < 4:
                continue
            score = gain / atr_v + pct * 10
            if best_pole is None or score > best_pole["score"]:
                best_pole = {
                    "start": s, "end": end, "lo": lo, "hi": hi,
                    "score": score, "d0": bars[s]["d"], "d1": bars[end]["d"],
                }
    if best_pole is None:
        return None
    pe = best_pole["end"]
    # 旗面高点：旗杆结束后的下降摆动高（至少两个，后高低于前高）
    flag_hs = [(i, h) for i, h in Hs if i > pe]
    pb = pa = None
    if len(flag_hs) >= 2:
        for k in range(len(flag_hs) - 1, 0, -1):
            i2, p2 = flag_hs[k]
            i1, p1 = flag_hs[k - 1]
            if p2 < p1 and i2 > i1:
                pb, pa = (i1, p1), (i2, p2)
                break
    if pb is None or pa is None:
        # 枢轴不够（下移段内 w=3 不产生枢轴高）→ 用近端局部高点检出。
        # 旧实现取「旗杆后右半段最高点」近似，突破那根自己会成为右半段最高点，
        # 线被拖到突破价、days_above_tl 归零 —— 形态在突破当天自我消失
        # （TEM 2026-09-15：9/14 线=62.18 正确，9/15 跳到 68.94、days_above=0）。
        alt = detect_down_trendline(bars, atr_v, start_i=pe + 1)
        if alt is not None:
            pb = (alt["pb"]["i"], alt["pb"]["price"])
            pa = (alt["pa"]["i"], alt["pa"]["price"])
        else:
            seg = bars[pe + 1:]
            if len(seg) < 4:
                return None
            mid = pe + 1 + len(seg) // 2
            hi_end = max(mid + 1, n - 1)        # 右段最高点候选不含末根
            h1 = max(range(pe + 1, mid + 1), key=lambda i: bars[i]["h"])
            h2 = max(range(mid, hi_end), key=lambda i: bars[i]["h"])
            if bars[h2]["h"] >= bars[h1]["h"]:
                return None
            pb, pa = (h1, bars[h1]["h"]), (h2, bars[h2]["h"])
    if pb is None or pa is None:
        return None
    # 旗面通常 3–20 根；更长是通道不是旗。回撤不超过旗杆 2/3。
    flag_len = n - 1 - pe
    if flag_len > 20:
        return None
    flag_low = min(x["l"] for x in bars[pe + 1:])
    if best_pole["hi"] - flag_low > 0.66 * (best_pole["hi"] - best_pole["lo"]):
        return None
    tl_now = line_val(pb, pa, n - 1)
    if tl_now is None:
        return None
    # 旗面期间多数收盘应在旗杆高点之下、旗杆中点之上（未彻底破位）
    mid_pole = (best_pole["lo"] + best_pole["hi"]) / 2.0
    flag_bars = bars[pe + 1:]
    if not flag_bars:
        return None
    below_pole_hi = sum(1 for x in flag_bars if x["c"] < best_pole["hi"]) / len(flag_bars)
    above_mid = sum(1 for x in flag_bars if x["l"] > mid_pole * 0.98) / len(flag_bars)
    if below_pole_hi < 0.6:
        return None
    if above_mid < 0.35:
        return None
    return {
        "pole_start": best_pole["start"],
        "pole_end": pe,
        "pole_lo": best_pole["lo"],
        "pole_hi": best_pole["hi"],
        "pole_d0": best_pole["d0"],
        "pole_d1": best_pole["d1"],
        "pb": {"i": pb[0], "price": pb[1], "d": bars[pb[0]]["d"]},
        "pa": {"i": pa[0], "price": pa[1], "d": bars[pa[0]]["d"]},
        "tl_now": tl_now,
        "days_above_tl": days_above_line(bars, pb, pa),
        "mid_pole": mid_pole,
        "flag_len": flag_len,
    }


def key_break_level(bars, atr_v, last_c, lo=0.20, hi=1.5):
    """上方最近的可突破位 K（供盘中「关键位突破」通道使用）。

    只回答「盘中站上哪个价算突破成立」，不回答「买区在哪」。

    候选一 近期整理上沿 —— bars[-4:-1] 的最高价（天然 ≥2 根之前形成，
           所以不会把「昨天刚创的高点」当成阻力，从而变成追加速段）。
           必要性：ILMN 2026-09-15 突破的正是 9/9 高点 212.18，
           而 9/9 左侧更高（9/8 的 219.45），进不了 pivots。
    候选二 摆动前高     —— pivots 上方最近的高点。

    过滤：距现价必须落在 [lo, hi]×ATR。太近（<0.2ATR）没有突破意义，
    太远（>1.5ATR）当日不可及。取**较低**者——更近的阻力才是先要打的位。
    """
    if not atr_v or atr_v <= 0:
        return None
    n = len(bars)
    out = []
    if n >= 4:
        seg = bars[-4:-1]
        best = max(seg, key=lambda b: b["h"])
        if best["h"] > last_c:
            out.append({"kind": "近期整理上沿", "level": best["h"], "date": best["d"]})
    try:
        Hs, _ = pivots(bars[:-1], 3)
        ups = sorted({h for _, h in Hs if h > last_c})
        if ups:
            out.append({"kind": "摆动前高", "level": ups[0], "date": None})
    except Exception:
        pass
    valid = [c for c in out if lo * atr_v <= (c["level"] - last_c) <= hi * atr_v]
    if not valid:
        return None
    k = min(valid, key=lambda x: x["level"])
    k["level"] = round(k["level"], 2)
    k["dist_atr"] = round((k["level"] - last_c) / atr_v, 2)
    return k


def zone_at_level(level, atr_v, last_c, kind, ev, bars):
    """突破类买区：自突破位【单边向上】，带宽 1.0×ATR。

    下沿不低于突破位——本模式的定义就是「收盘站上突破位」，在尚未突破的价位挂买单
    自相矛盾；同时保证硬止损（锚 −0.10×ATR）恒落在买区下沿之下，不会出现
    「按买区挂单成交，成交价却已在硬止损下方」。
    跳空突破例外：突破根跳空越过突破位时，买区基准上移到缺口上沿（= 突破根低点），
    买区不得落进缺口（SKILL「不买回补缺口」，石头科技 8/25 例）。
    dist / extended / 不追高闸门的基准恒为突破位本身，不随买区基准漂移。
    """
    z = _empty_zone()
    closes = [b["c"] for b in bars]
    v5 = typical_vwap(bars, 5)
    z["vwap5"] = round(v5, 2) if v5 is not None else None
    ma5 = sma(closes, 5)
    z["ma5"] = round(ma5, 2) if ma5 is not None else None
    if level is None:
        return z
    band = 1.0 * atr_v if atr_v else level * 0.01
    base = level
    gap_hi = None
    if len(bars) >= 2 and atr_v:
        y, prev_c = bars[-1], bars[-2]["c"]
        # 跳空越过突破位：缺口上沿（= 突破根低点）才是实际买点
        if y["o"] > prev_c + 0.2 * atr_v and y["l"] >= prev_c and y["l"] > level:
            gap_hi = round(y["l"], 2)
            base = y["l"]
    lo, hi = base, base + band
    if last_c and hi - lo < last_c * 0.002:
        mid = (lo + hi) / 2.0
        lo, hi = mid * 0.997, mid * 1.003
    if "W底" in kind or "颈线" in kind:
        anchor = "w_neckline"
    elif "旗形" in kind:
        anchor = "flag_tl"
    elif "平台" in kind:
        anchor = "platform_lip"
    else:
        anchor = "down_tl"
    dist = None
    if last_c is not None and atr_v:
        dist = (last_c - level) / atr_v
    z.update({
        "type": kind,
        "anchor": anchor,
        "level": round(level, 2),
        "base": round(base, 2),
        "gap_hi": gap_hi,
        "primary_lo": round(lo, 2),
        "primary_hi": round(hi, 2),
        "dist_atr": round(dist, 2) if dist is not None else None,
        "in_zone": bool(last_c is not None and lo <= last_c <= hi),
        "extended": bool(dist is not None and dist > 2.0),
        # 结构位=突破位本身；硬止损由 stop_plan 另给
        "invalidation": round(level, 2),
    })
    return z


HARD_STOP_TRIGGER = (
    "开盘已在硬止损下→开盘走；盘中破后3分钟仍在下或分钟线新低→市价走；"
    "1–2分钟收回且非放量加速阴→当毛刺"
)

# 硬止损距买区下沿不足这么多 ATR 时，等于把止损塞进单日噪声带：名义上有止损，
# 实际一次正常波动就被扫掉。此时若同族还有**更宽**的合法锚，降级过去（2026-09-18）。
NOISE_ROOM_ATR = 0.25

# 噪声带只在**突破类**成立（2026-09-18 收尾）。分家的理由是可执行价不同：
#   突破类：买区下沿 = 突破位本身，是真实成交价。硬止损塞在它下面 <0.25×ATR，
#           就是「一次正常波动即扫」= 名义止损。
#   回踩类（line_pullback / impulse_pause）：买区下沿只是理想成交价，实际成交在线上，
#           主风控是**收盘破线**的结构止损；硬止损本就是 0.10×ATR 的毛刺滤网。
#           用同一个阈值去量它，全市场 44 个候选里会误报 15 个（都是 line_pullback），
#           警告随即退化成噪声。故回踩类只报 hard_dist_atr，不报 hard_noise。
BREAKOUT_MODES = ("platform_break", "w_bottom_break",
                  "flag_tl_break", "downtrend_tl_break")

# 两档止损的执行口径。用户侧约束：不能盯整场 + 券商不支持条件单时，「盘中轨」根本
# 执行不了 —— 必须让输出自己说清楚哪条腿不需要盯盘，别让人以为有保护。
STRUCT_EXEC = "收盘口径 — 不需要盯盘：次日开盘/晨起按收盘价判定，收盘破即走"
HARD_EXEC = ("盘中口径 — 需要盯盘或券商条件单；两者都没有时此腿不可执行，"
             "届时保护只剩收盘轨（应改用更低限价买入，用买价替代止损）")

# SKILL 硬止损锚名；禁止静默改写成「买区下沿」
SKILL_HARD_ANCHORS = ("阳线下沿", "大阳中点", "MA5", "缺口下沿")


def stop_plan(bars, mode, z, atr_v):
    """两档止损：结构（收盘破）+ 硬止损（SKILL 合法锚 − 0.10×ATR）。

    铁律一：锚名与数值严格绑定 —— hard 恒等于「锚价 − gap」。禁止为迁就买区把数值
            悄悄下移：那会让报告上「锚=大阳中点」配一个离中点好几个 ATR 的价。
    铁律二：硬止损必须落在买区下沿之下。若 SKILL 的合法锚都做不到（买区与锚冲突），
            如实挂 stop_warning，并把买区下沿抬到硬止损之上 —— 让路的是买区，
            不是止损数值，也不是锚名。
    铁律零（2026-09-18 SNDK）：**任何止损锚都必须低于现价**。锚价 ≥ 现价 = 买入的那一刻
            就已经在止损之下（买入即止损），这个锚不是"宽一点/紧一点"的问题，是
            根本不可用 —— 必须先剔除，再谈铁律一/二。剔除后无处可去的，如实不给止损，
            由 attach_stops_targets 的总闸门撤销 recommend，绝不靠抬买区去迁就。
            触发场景：SNDK 9/14~9/16，价格从 1764 跌到 1519，MA5=1676/1634/1586
            全在收盘价之上，旧代码仍拿它当「收盘破」的结构止损。
    """
    if not bars or not atr_v or not z or z.get("level") is None:
        return None
    z.pop("stop_warning", None)
    z.pop("buy_lo_adjusted", None)
    y = bars[-1]
    body, rng = abs(y["c"] - y["o"]), y["h"] - y["l"]
    long_yang = (
        y["c"] > y["o"]
        and (body >= 0.8 * atr_v or rng >= 1.0 * atr_v or (y["o"] and body / y["o"] >= 0.03))
    )
    level = z["level"]
    buy_lo = z.get("primary_lo") if z.get("primary_lo") is not None else level
    gap = 0.10 * atr_v
    last_c = y["c"]

    def _usable(px):
        """铁律零：锚价必须低于现价，否则买入即止损。"""
        return px is not None and px < last_c

    def _rej_txt(rejected):
        return "、".join(f"{n}@{round(p, 2)}" for n, p in rejected)

    def _resolve(cands):
        """cands 已按 SKILL 优先级排序 [(锚名, 锚价)]。
        取第一个「锚价 − gap < 买区下沿」的锚；数值恒 = 锚价 − gap。
        返回 (锚名, 锚价, hard, warning, hard_note)。

        2026-09-18：在「已通过」的锚里，若首选那条距买区下沿 < NOISE_ROOM_ATR×ATR
        （止损被塞进单日噪声带，名义有止损、实际等于没有），且同族存在**更宽**的合法锚，
        就降级到更宽那条。铁律一不变（数值恒 = 该锚价 − gap）、铁律二不变（仍在买区下沿
        之下），只是把「先到先得」换成「噪声带内让位给更宽的合法锚」。
        触发场景：突破后隔夜/次日入场 —— 买区已上移，大阳中点算出的止损只距沿 0.03×ATR，
        等于没有止损（INTC 2026-09-17 实例）。

        2026-09-18 铁律零：进入本函数前先把「锚价 ≥ 现价」的候选剔除，并在 hard_note
        里写明剔除原因 —— 不让「买入即止损」的锚混进铁律一/二的取舍。
        """
        first = None
        ok = []
        rejected = []
        usable_n = 0
        for name, px in cands:
            if px is None:
                continue
            h = px - gap
            if not _usable(px):
                rejected.append((name, px))
                continue
            usable_n += 1
            if h < buy_lo:
                ok.append((name, px, h))
            # first 只在**可用**锚里取最低的那条：warn 分支要把买区抬到硬止损之上，
            # 若这里留着一个「买入即止损」的锚，抬完的买区会整个飞到现价上方
            # （SNDK 2026-09-16 就是这么来的）。
            if first is None or h < first[2]:
                first = (name, px, h)
        rej_note = (
            f"已剔除不低于现价 {round(last_c, 2)} 的锚 {_rej_txt(rejected)}（买入即止损）"
            if rejected else None
        )
        if ok:
            name, px, h = ok[0]
            room = buy_lo - h
            if room >= NOISE_ROOM_ATR * atr_v or len(ok) == 1:
                return name, px, h, None, rej_note
            wname, wpx, wh = min(ok, key=lambda t: t[2])
            if wname == name:
                return name, px, h, None, rej_note
            return wname, wpx, wh, None, (
                (rej_note + "；" if rej_note else "")
                + f"首选锚 {name}@{round(px, 2)} − gap 得硬止损 {round(h, 2)}，距买区下沿 "
                f"{round(buy_lo, 2)} 仅 {round(room / atr_v, 2)}×ATR（落在单日噪声带内）"
                f"→ 按同族合法锚降级到更宽的 {wname}@{round(wpx, 2)}，硬止损 "
                f"{round(wh, 2)}（距买区下沿 {round((buy_lo - wh) / atr_v, 2)}×ATR）"
            )
        if first is None:
            return None, None, None, None, None
        name, px, h = first
        if usable_n == 0:
            return None, None, None, (
                f"SKILL 合法锚（{_rej_txt([(n, p) for n, p in cands if p is not None])}）"
                f"全不低于现价 {round(last_c, 2)} —— 买入即止损，本档不给出硬止损"
            ), rej_note
        return name, px, h, (
            f"SKILL 合法锚（{' / '.join(str(c[0]) for c in cands)}）中最低的 "
            f"{name}@{round(px, 2)} − gap = {round(h, 2)}，仍不低于买区下沿 "
            f"{round(buy_lo, 2)}；买区与止损锚冲突 —— 已把买区下沿上抬到硬止损之上"
            f"（可执行价位以 primary_lo 为准）"
        ), rej_note

    def _finish(struct_name, struct, hard_name, hard, warn, note=None):
        # 结构止损自身就在现价之上 = 买入即止损。这里不给任何止损，交总闸门撤销
        # 整单 —— 不能靠抬买区去迁就一个错误的锚（铁律零）。
        if struct is None or not _usable(struct):
            return {
                "struct_anchor": struct_name,
                "struct": round(struct, 2) if struct is not None else None,
                "hard_anchor": None,
                "hard": None,
                "trigger": HARD_STOP_TRIGGER,
                "struct_exec": STRUCT_EXEC,
                "hard_exec": HARD_EXEC,
                "hard_dist_atr": None,
                "hard_noise": False,
                "warning": (
                    f"结构止损锚 {struct_name}@{round(struct, 2)} 不低于现价 "
                    f"{round(last_c, 2)} —— 买入即止损，该锚当前不可用"
                ),
            }
        if hard is None:
            # SKILL 合法锚全部不可用：只保留结构止损那一档，并如实说明没有硬止损
            return {
                "struct_anchor": struct_name,
                "struct": round(struct, 2),
                "hard_anchor": None,
                "hard": None,
                "trigger": HARD_STOP_TRIGGER,
                "struct_exec": STRUCT_EXEC,
                "hard_exec": HARD_EXEC,
                "hard_dist_atr": None,
                "hard_noise": False,
                "hard_note": note,
                "warning": warn or "无可用硬止损锚 —— 保护只剩收盘轨（结构止损）",
            }
        if warn:
            z["stop_warning"] = warn
            # last_c 用外层 stop_plan 的那个（此处不可再赋值，否则会把上面两个
            # 提前返回分支里的 last_c 变成未绑定的局部变量）
            new_lo = round(hard + 0.05 * atr_v, 2)
            hi = z.get("primary_hi")
            if hi is None or hi <= new_lo:
                hi = round(new_lo + 0.5 * atr_v, 2)
            z["primary_lo"], z["primary_hi"] = new_lo, hi
            z["buy_lo_adjusted"] = True
            z["in_zone"] = bool(new_lo <= last_c <= hi)
        lo_now = z.get("primary_lo")
        room = (lo_now - hard) if lo_now is not None else None
        out = {
            "struct_anchor": struct_name,
            "struct": round(struct, 2),
            "hard_anchor": hard_name,
            "hard": round(hard, 2),
            "trigger": HARD_STOP_TRIGGER,
            "struct_exec": STRUCT_EXEC,
            "hard_exec": HARD_EXEC,
            "hard_dist_atr": round(room / atr_v, 2) if room is not None else None,
            "hard_noise": bool(room is not None and room < NOISE_ROOM_ATR * atr_v
                               and mode in BREAKOUT_MODES),
        }
        if note:
            out["hard_note"] = note
        if z.get("stop_warning"):
            out["warning"] = z["stop_warning"]
        return out

    if mode == "line_pullback":
        # 结构锚 = 回踩锚本身（2026-09-18 SNDK 修正）。
        #
        # 旧代码在 anchor=hl_trendline 时把结构止损换成 MA5，出发点是「均线比外推的
        # 趋势线更实」。但 MA5 是**滞后**的：价格急跌时 MA5 还停在高处 —— SNDK 从
        # 9/9 的 1764 一路跌到 9/16 的 1519，MA5 依次是 1676 / 1634 / 1586，全都
        # **高过当日收盘**。拿它当「收盘破」的结构止损，等于买入那一刻就已触发
        # （买入即止损）；随后 stop_plan 还会把买区抬到硬止损之上，输出一个整个
        # 位于现价上方的买区（9/16：收 1519.97，买区 1580.16~1604.72）。
        # 回踩单的定义就是「回踩到这条线买、收盘破这条线走」，止损锚必须是这条线
        # 本身。MA5 只在该线已不可用（价格跌到线下）时作备选，且同样须过铁律零。
        _an = z.get("anchor")
        _label = ANCHOR_LABEL.get(_an, _an) if _an else "回踩线"
        struct_px, struct_name = level, f"{_label}(收盘破)"
        if not _usable(struct_px):
            _ma5 = z.get("ma5")
            if _usable(_ma5):
                struct_px, struct_name = _ma5, "MA5(收盘破)"
        # 硬止损：主锚 MA5。只有在 MA5 不可用（高过现价 = 买入即止损）或缺失时，
        # 才退到「阳线下沿」—— 不为了"更宽"而平白换锚：MA5 能用时回踩类的紧止损
        # 本来就是设计好的毛刺滤网（见 test_pullback_tight_stop_is_not_noise_flagged）。
        # MA5 不可用时仍把它一并传入，好让铁律零把剔除原因留在 hard_note 里。
        _cands = []
        _ma5 = z.get("ma5")
        if _ma5 is not None and _usable(_ma5):
            _cands.append(("MA5", _ma5))
        else:
            if _ma5 is not None:
                _cands.append(("MA5", _ma5))
            _cands.append(("阳线下沿", y["l"]))
        name, px, hard, warn, note = _resolve(_cands)
        return _finish(struct_name, struct_px, name or "MA5", hard, warn, note)

    if mode == "impulse_pause":
        floor = level
        if z.get("yi_zi"):
            name, px, hard, warn, note = _resolve([("缺口下沿", floor)])
            return _finish("缺口下沿/前收(收盘破)", floor, name or "缺口下沿", hard, warn, note)
        name, px, hard, warn, note = _resolve([("阳线下沿", floor)])
        return _finish("大阳低点(收盘破)", floor, name or "阳线下沿", hard, warn, note)

    if mode == "ma_reclaim_break":
        # T0（2026-09-20 补分支）：z 由 _apply_t0 构造 —— level 是**上方**的「过昨高」
        # 触发价，不是可当突破位/支撑位的水平位。走下面的通用分支会把 struct 设成
        # level（高于现价）→ 被铁律零剔除 → hard=None；调用方（probe_intraday.
        # room_and_cap）再兜底 level−0.10×ATR，就得到一个**高于现价**的「止损」
        # （301335 实测 25.72 > 现价 25.43，预案单因此打出「风险为负 / 不做」）。
        # T0 的结构止损与硬止损是同一个价（= 买区下沿 itself），锚名/数值已在
        # buy_zone 里算好，此处原样透传，不顺带做任何买区上抬。
        _hs = z.get("hard_stop")
        if _hs is None:
            _hs = z.get("hard")
        if _hs is None:
            _hs = z.get("struct_stop")
        if _hs is None:
            return None
        _an = z.get("hard_anchor") or z.get("struct_anchor") or "实体中点"
        return {
            "struct_anchor": z.get("struct_anchor") or f"{_an}@{round(_hs, 2)}（收盘破）",
            "struct": round(_hs, 2),
            "hard_anchor": _an,
            "hard": round(_hs, 2),
            "trigger": HARD_STOP_TRIGGER,
            "struct_exec": STRUCT_EXEC,
            "hard_exec": HARD_EXEC,
            "hard_dist_atr": z.get("hard_dist_atr"),
            "hard_noise": False,
            "stop_basis": z.get("stop_basis"),
            "t0": True,
        }

    # 平台 / W底 / 旗形 / 下降趋势线突破
    struct = round(level, 2)
    struct_name = f"{ANCHOR_LABEL.get(z.get('anchor'), z.get('anchor') or '突破位')}@{struct}(收盘破)"
    if long_yang:
        # SKILL:198 长阳用中点；若中点算出的止损落进买区、或落进单日噪声带（距买区下沿
        # < NOISE_ROOM_ATR×ATR，等于没有止损），按同族合法锚降级到阳线下沿
        cands = [("大阳中点", (y["h"] + y["l"]) / 2.0), ("阳线下沿", y["l"])]
    else:
        cands = [("阳线下沿", y["l"])]
    name, px, hard, warn, note = _resolve(cands)
    return _finish(struct_name, struct, name or cands[0][0], hard, warn, note)


def targets(bars, mode, z, atr_v, entry, Hs):
    """目标1=最近未破摆动高/测量涨幅；目标2=当前取数窗口内更高阻力（非强制 52 周）。附 rr_target1。"""
    if entry is None or not atr_v:
        return None
    resist = sorted({h for i, h in Hs if h > entry + 0.05 * atr_v})
    t1 = resist[0] if resist else round(entry + 2.0 * atr_v, 2)
    # 测量涨幅：平台/颈线突破用买区半高近似
    if mode in ("platform_break", "w_bottom_break") and z and z.get("level") is not None:
        measured = round(z["level"] + max(atr_v * 2.0, (z.get("primary_hi") or z["level"]) - (z.get("primary_lo") or z["level"])), 2)
        if measured > entry:
            t1 = min(t1, measured) if resist else measured
    # 区间高 = 传入 bars 窗口内最高（常见约 130 根），不是严格 250 日/52 周高
    hi_all = max(b["h"] for b in bars)
    t2 = hi_all if hi_all > t1 + 0.2 * atr_v else round(t1 + 2.0 * atr_v, 2)
    if abs(t2 - t1) < 0.15 * atr_v:
        t2 = round(t1 + 2.0 * atr_v, 2)
    hard = (z or {}).get("hard_stop") or (z or {}).get("hard")
    if isinstance(hard, dict):
        hard = hard.get("hard")
    risk = (entry - hard) if hard is not None else None
    rr = round((t1 - entry) / risk, 2) if risk and risk > 0 else None
    return {
        "target1": round(t1, 2),
        "target2": round(t2, 2),
        "rr_target1": rr,
    }


def attach_stops_targets(plan, bars, atr_v, Hs):
    """给 plan / buy_zone 挂上两档止损与目标。"""
    z = plan.get("buy_zone") or {}
    mode = plan.get("mode") or "wait"
    last_c = bars[-1]["c"] if bars else None
    sp = stop_plan(bars, mode, z, atr_v) if mode != "wait" and plan.get("recommend") else None
    if sp is None and mode != "wait" and z.get("level") is not None:
        sp = stop_plan(bars, mode, z, atr_v)
    if sp:
        z["struct_stop"] = sp["struct"]
        z["struct_anchor"] = sp["struct_anchor"]
        z["hard_stop"] = sp["hard"]
        z["hard_anchor"] = sp["hard_anchor"]
        z["hard_trigger"] = sp["trigger"]
        z["invalidation"] = sp["struct"]  # 兼容：结构止损
        z["hard"] = sp["hard"]
        # 执行口径与噪声带标记（2026-09-18）：让「哪条腿不需要盯盘」随买区一路带出去，
        # 否则 CLI/报表只有一个价，用户会以为硬止损随时生效。
        z["struct_exec"] = sp["struct_exec"]
        z["hard_exec"] = sp["hard_exec"]
        z["hard_dist_atr"] = sp["hard_dist_atr"]
        z["hard_noise"] = sp["hard_noise"]
        if sp.get("hard_note"):
            z["hard_note"] = sp["hard_note"]
            plan["hard_note"] = sp["hard_note"]
        if sp.get("warning"):
            z["stop_warning"] = sp["warning"]
            plan["stop_warning"] = sp["warning"]
        # 总闸门（铁律零的最后一道防线，2026-09-18 SNDK）：
        # 止损 ≥ 现价 = 买入即止损，无论它是怎么算出来的都撤销整单。
        # 之前同类错误的共同点是「锚点逻辑正确 ≠ 锚点数值可用」，单点修补总会
        # 有下一个锚踩坑，所以在出口统一拦一道：recommend=True 必须自带一个
        # 真正位于现价之下的止损。
        if plan.get("recommend"):
            bad = [
                f"{nm} {round(v, 2)}"
                for nm, v in (("结构止损", sp.get("struct")), ("硬止损", sp.get("hard")))
                if v is not None and last_c is not None and v >= last_c
            ]
            if bad:
                plan["recommend"] = False
                plan["stop_above_price"] = True
                plan["verdict"] = "止损锚在现价之上·买入即止损·不接"
                _prev = plan.get("note") or ""
                plan["note"] = (_prev + "；" if _prev else "") + (
                    f"{'、'.join(bad)} 均不低于现价 {round(last_c, 2)} —— 买入即止损"
                    f"（买进去就已经在止损之下），该锚当前不可用"
                    f"（多为价格急跌、均线滞后所致），本单撤销"
                )
    tg = targets(bars, mode, z, atr_v, last_c, Hs or []) if mode != "wait" else None
    if tg:
        z["target1"] = tg["target1"]
        z["target2"] = tg["target2"]
        z["rr_target1"] = tg["rr_target1"]
        if tg.get("rr_target1") is not None and tg["rr_target1"] < 1.0:
            # 突破类单：头顶最近的枢轴高会天然压低静态赔率（SDGR 2026-09-15
            # rr=0.17），这是结构失真，不代表机会变差。旧措辞「赔率差」会让
            # 人在趋势启动第一天就放弃，故单列口径。
            if mode in ("platform_break", "w_bottom_break", "flag_tl_break",
                        "downtrend_tl_break"):
                rr_note = (
                    f"静态赔率 rr_target1={tg['rr_target1']}<1.0 属突破初期失真"
                    f"（头顶最近枢轴高天然压住赔率），机会未变差；"
                    f"趋势单用移动止损（MA5/大阳中点）替代固定目标"
                )
            else:
                rr_note = f"盈亏比 rr_target1={tg['rr_target1']}<1.0，结构有效但赔率差"
            prev = plan.get("note") or ""
            plan["note"] = f"{prev}；{rr_note}" if prev else rr_note
            v = plan.get("verdict") or ""
            if v and "赔率" not in v and mode not in (
                "platform_break", "w_bottom_break", "flag_tl_break",
                "downtrend_tl_break",
            ):
                plan["verdict"] = f"{v}·赔率偏弱"
    plan["buy_zone"] = z
    plan["stop_plan"] = sp
    plan["targets"] = tg
    return plan


def pre_breakout_order(bars, ev, atr_v, plat, last_c, rvol):
    """预备突破单（buy-stop 埋伏）：价格逼近「未被收盘打穿」的活平台沿时给出。

    补 SDGR 2026-09-14 缺口：当日活平台沿 21.40 距收盘仅 0.64×ATR（kind=pressing），
    而 plan_entry 只输出了「大阳回踩 18.84-19.86」——该回踩从未发生，次日直接跳空
    越过买区，整段 +46% 被完整错过。买点就是突破位本身（不属追高），buy-stop 需
    触发才成交、假突破自动不成交，故与「不追高」闸门不冲突。

    只在：pressing 平台 / 距沿 ≤1.2×ATR / 非极度缩量 / 非下跌段且站上 MA20 时给出。

    ★ 2026-09-19 拓宽（用户「预判突破买」实证后定）：原闸门要求 kind == "pressing"，
    这只覆盖「价格正贴着沿」的形态。但用户要的是**T-1 就挂好、次日触发**，
    需要在「尚未贴近、但方向已明」时就给埋单位，否则 T-1 无单可挂 → 只能次日顶着买。
    实测（赛分 688758 / 康龙 688621 / TEM / ILMN / SDGR / INTC）：
      顶着买（T 日收盘）→ T+1 盘中破止损率 49~52%
      预判埋伏（T 触发价）→ T+1 盘中破止损率 2~13%
    → 故放宽为「kind ∈ {pressing, near, broken}」且距沿放宽到 1.2×ATR（不变），
      即：只要平台沿在头上且不太远，就给埋单位。极远端（>1.2×ATR）仍不给，
      因为埋伏单离触发太远=挂在那里长期不成交，占注意力无收益。
    """
    if not plat:
        return None
    if plat.get("kind") not in ("pressing", "near", "broken"):
        return None
    if not atr_v or atr_v <= 0 or last_c is None:
        return None
    lv = plat.get("price")
    if lv is None or lv <= last_c:
        return None
    dist_atr = (lv - last_c) / atr_v
    if dist_atr > 1.2:
        return None
    if rvol is not None and rvol < 0.8:
        return None
    # 结构守卫：不埋伏明确下跌段。此处**不能用 c2**（自 P1 未创新低）——
    # 反转初期 c2 必为 False（SDGR 9/14 正是如此，9/11 低 18.63 < P1 19.13），
    # 用它会把本信号要覆盖的场景整个挡掉。改用 regime + MA20。
    if ev.get("regime") == "downtrend":
        return None
    ma20 = sma([b["c"] for b in bars], 20)
    if ma20 is not None and last_c < ma20:
        return None
    trigger = round(lv + 0.05 * atr_v, 2)
    ma5 = sma([b["c"] for b in bars], 5)
    floors = [lv - 0.5 * atr_v]
    if ma5 is not None:
        floors.append(ma5 - 0.10 * atr_v)
    hard = round(max(f for f in floors if f < trigger), 2)
    from_d = bars[plat["i"]]["d"] if plat.get("i") is not None else None
    return {
        "order": "buy-stop",
        "level": round(lv, 2),
        "trigger": trigger,
        "hard_stop": hard,
        "risk_per_share": round(trigger - hard, 2),
        "dist_atr": round(dist_atr, 2),
        "platform_from": from_d,
        # 埋伏单 = 独立于当日 mode 的「优先 T1」入口（2026-09-19 用户定级）。
        # 机制：买价更低 → 1R 更小 → 同预算可开仓位更大（赛分 9-14 埋伏 vs 9-15 顶着买 = 1.46 倍）。
        # 实测优势集中在 T+1 市场：A 股顶着买 T+1 被扫 49~52%，埋伏买 2~13%。
        # 美股（T+0）优势小但仍不劣 → 全局 T1。
        "priority": 1,
        "tier": "T1",
        "setup_kind": "pre_breakout",
        "role": "primary_entry",
        "cushion_note": (
            "★ 买点低于突破确认日收盘价：触发成交后当日收盘多半已带浮盈垫，"
            "垫子就是次日（A股 T+1）低开的缓冲；这是「预判买」相对「顶着买」的核心优势"
        ),
        "note": (
            f"上方活平台沿 {round(lv, 2)}（{from_d}）未破，距收盘 {dist_atr:.2f}×ATR；"
            f"挂 buy-stop {trigger} 埋伏，触发即突破确认。硬止损 {hard}"
            f"（跌回平台沿下方=假突破）；突破后按移动止损管理，不设固定目标。"
            f"此为埋伏单，与当日回踩买点并存、先到先做"
        ),
    }


def pre_breakout_line_order(bars, ev, atr_v, last_c, rvol=None):
    """斜线预备突破单：价格贴在**下降趋势线**下方时，在线上方挂 buy-stop。

    补 ILMN / TEM 2026-09 缺口（用户 2026-09-17 指正：这两个都该按「画斜线突破
    下降趋势线」处理，而不是水平平台）。平台版挂水平沿，这里挂下移斜线 ——
    触发价必须取**下一根**的线值：线在下移，用当日线值会把单子挂高。

    买点仍是突破位本身（不是追高）：buy-stop 需真被触发才成交，假突破自动不成交，
    故与「不追高」闸门不冲突。

    止损给线下 1.0×ATR：ILMN 20 笔样本显示 0.55×ATR 的贴沿止损有 94% 在 10 日内
    被扫后又涨回（错杀），1.0×ATR 才是期望最优，故不沿用平台版的 0.5×ATR。
    """
    if not atr_v or atr_v <= 0 or last_c is None:
        return None
    dl = ev.get("down_tl") or {}
    if dl.get("src") != "local_highs":
        return None
    a, b = dl.get("a"), dl.get("b")
    if not a or not b or a["i"] == b["i"]:
        return None
    pb, pa = (b["i"], b["price"]), (a["i"], a["price"])
    if pa[1] >= pb[1]:                       # 必须是下移线
        return None
    nxt = len(bars)                          # 下一根的索引：触发价按下一根的线值
    line_next = line_val(pb, pa, nxt)
    if line_next is None or last_c >= line_next:
        return None                          # 已站上 → 交给突破类模式，不挂埋伏单
    dist_atr = (line_next - last_c) / atr_v
    if dist_atr > 1.2:                       # 离得太远，埋伏无意义
        return None
    if ev.get("regime") == "downtrend":
        return None                          # 明确下跌段不接飞刀（反转态才埋伏）
    trigger = round(line_next + 0.05 * atr_v, 2)
    if trigger <= last_c:
        return None
    hard = round(line_next - 1.0 * atr_v, 2)
    from_txt = (f"{bars[b['i']]['d']}高{b['price']:.2f}→"
                f"{bars[a['i']]['d']}高{a['price']:.2f}")
    return {
        "order": "buy-stop",
        "anchor": "down_tl",
        "level": round(line_next, 2),
        "trigger": trigger,
        "hard_stop": hard,
        "risk_per_share": round(trigger - hard, 2),
        "dist_atr": round(dist_atr, 2),
        "line_from": from_txt,
        "priority": 1,
        "tier": "T1",
        "setup_kind": "pre_breakout",
        "role": "primary_entry",
        "cushion_note": (
            "★ 买点低于突破确认日收盘价：触发成交后当日收盘多半已带浮盈垫，"
            "垫子就是次日（A股 T+1）低开的缓冲"
        ),
        "note": (
            f"下降趋势线（{from_txt}）下移中，下一根线值 {round(line_next, 2)}，"
            f"收盘在线下 {dist_atr:.2f}×ATR；挂 buy-stop {trigger} 于线上方埋伏，"
            f"触发即「画斜线突破」确认。硬止损 {hard}（线下1×ATR=假突破）；"
            f"突破后按移动止损管理，不设固定目标。此为埋伏单，与当日买点并存、先到先做"
        ),
    }


def find_ma_reclaim_bar(bars):
    """★ 2026-09-20 新增：定位「**最近的突破均线的日K**」。

    ── 用户原话（德邦科技 688035 案例）──────────────────────────────
      「78.23 好像也没有比昨天收盘价低多少啊，而且 **9-18 这么小的 K 线实体，
        为啥要取它的中点，当然是取 9-16 的中点**，这是代码的问题，
        应该找**最近的突破均线的日K**。」
      「能买的低成本当然比追着买要强。」

    ── 旧实现为什么错（无条件下 `bars[-1]` 取 K 线锚）──────────────
    突破日之后若又跟了 1~2 根**小实体**，最后一根的中点就贴在收盘价上：

      | 德邦 688035 | 实体 | 实体/ATR | 中点 | 距收盘 |
      |---|---|---|---|---|
      | 09-16 真突破日 | 6.68 | **1.39×** | **74.32** | −5.3% |
      | 09-18 最后一根 | 0.52 | **0.11×** | 78.23 | **−0.33%** |

    ⇒ 用 78.23 当止损：距收盘仅 0.33%（0.05×ATR），一次正常波动就扫 = **等于没有止损**；
      用它当回踩买点：比收盘只便宜 0.33%，**根本不是「低成本」**，与用户
      「能买低成本就不追着买」的原则相悖。
    ⇒ 真正的结构锚在**把股价拉上全部均线的那根**（德邦 09-16，实体 1.39×ATR）。

    ── 定义 ──────────────────────────────────────────────────────
    从最后一根往回找，**当前这段「收盘 > MA5 且 > MA10 且 > MA20」连续段的启动根**，
    即最近一次「由下向上收复全部均线」的那根 K。要求该根 > 0（否则说明历史起点
    就在均线上方，定位不到突破日，此时退回旧口径 `bars[-1]`）。

    ⚠️ 与八案例的兼容性：赛分/纳微/成都/科泰/浩瀚/东富龙/康龙 的**信号日当天就是突破日**
    （`back == 0`），本函数返回最后一根 → 输出数值与旧实现**逐字相同**，回归不受影响。
    """
    n = len(bars)
    if n < 21:
        return None
    closes = [b["c"] for b in bars]

    def _above(i):
        if i < 20:
            return False
        c = closes[i]
        for w in (5, 10, 20):
            if c <= sum(closes[i - w + 1:i + 1]) / w:
                return False
        return True

    if not _above(n - 1):
        return None
    j = n - 1
    while j - 1 >= 0 and _above(j - 1):
        j -= 1
    if j <= 0:
        return None          # 定位不到突破日（历史全程在均线上方）
    return j


def ma_reclaim_break(bars, ev, atr_v, last_c, res_win=25):
    """★ T0 买法：均线收复后「过昨高」买（2026-09-20 用户定稿，优先级高于 T1 买突破）。

    用户自研战法，八个实证案例（均为真实走势）：
      赛分科技 688758 2026-09-15 信号 → 09-16 过昨高买
      纳微科技 688690 2026-09-08 信号 → 09-09 过昨高买（→ 9/18 收 47.20）
      康龙化成 300759 2026-08-04 信号 → 08-05 过昨高 / 08-06 回踩买（用户实盘买 40 卖 47）
              ⚠ 2026-09-20 用户澄清：「康龙这个买法买的是**缩量回踩**，不用纠结锚」
                 —— 康龙这道单属于「缩量回踩」体系，**不是** T0 过昨高体系。
                 故「康龙 42.66 的锚归属」不是待办，无需再审。
                 列在 T0 案例里只是因为 08-04 那天形态同时满足 T0 的判据。
      东富龙   300171 2026-09-17 信号 → 09-18 过昨高（演示，用户未入场）
      成都先导 688222 2026-09-16 信号 → 09-17 过昨高 37.19 买 → 09-18 收 +7.48%
      科泰电源 300153 2026-09-16 信号 → 09-17 **跳空高开直接买 26.01** → 09-18 收 +11.38%
              ★ 八案例中唯一「D1 开盘已在触发价之上」的样本（开盘买，无需等触发）
              ★ 也是唯一 ma_aligned=True（均线多头排列）的样本
              ★ 实体/ATR 仅 0.72x（很弱的小阳）照样成立 → **实体大小不是判据**
      浩瀚深度 688292 2026-09-16 信号 → 09-17 过昨高 18.50 买 → **09-18 收 23.02 = +20.02%（科创板 20cm 涨停）**
              ★ 最极致的一个：触发价 18.50 → 止损 18.24 只差 0.26（1.41%），两天走 +24.43% = **17.38R**
              ★ D0 涨幅仅 +0.66%（几乎平盘）却成立 → **再次证明「大阳」不是必要条件**
              ★ 平台度 100%、收盘位 94% —— 真正的判据是「贴着平台沿收强」
              ★ 用户自述是「回踩收绿跌回收盘价买 19.18」→ 实测那是**次优价**：
                比触发价 18.50 贵 +3.68%，R 从 17.38 掉到 4.09。**P1 触发价才是最优入场**。
      美股 AI 制药共振 TEM / SDGR / ILMN（2026-09-14~15 同日给信号，用户未买到）
      ── 反面案例 ──
      联瑞新材 688300 2026-08-28 被平台度闸门拦下（见下 ①-B）

      ── ★ 港股（2026-09-20 用户提出，**最重要的「重复信号」样本**）───────────
      联想集团 00992.HK —— 同一条规则、同一只票、相隔 15 个交易日给出**两次**信号，
      第一次被扫、第二次吃到整段主升浪。**这是"一次成功不了得来几次"的实证**：

        · 第一次 D0=07-09  C24.040 (+7.71%)  mode=downtrend_tl_break  平台度 84%
          触发 24.30 / 止损 23.27（实体中点）/ 计划风险 4.24%
          D1=07-10 **跳空 +8.82%**（开 26.160 vs 昨收 24.040）→ 按规则用**开盘价 26.160** 入场
          ⇒ 止损距入场 = **11.05%**，1R = 2.890 元
          D1 当天从开盘往下杀 −6.57%，**07-13 一根 −4.82% 直接扫掉** → **−1.00R**

        · 第二次 D0=07-31  C23.860 (+9.75%)  mode=ma_reclaim_break     平台度 96%
          触发 24.32 / 止损 23.68（实体中点）/ 计划风险 2.63%
          D1=08-03 **平开**（开 23.880，+0.08%）→ 挂触发价成交在 **24.320**
          ⇒ 止损距入场 = **2.63%**，1R = 0.640 元（只有第一次的 22%）
          D1 日内最大回撤仅 −1.34% → 止损纹丝不动
          持有 30 根：**+32.15% = +12.22R**（峰值 08-14 达 **+18.50R**），
          含 **08-13 +20.18%**（29.040 → 34.900，量能 3.9 倍）

        ⇒ **两次合计 +11.22R。** 若因第一次被扫就放弃这只票/这条规则，
          第二次那 +12.22R 整段拿不到。

      ★★ 机制（本案例最重要的产出）：**跳空把入场价推高，止损线却不动**
         两次的触发价（24.30 / 24.32）与止损锚（都是实体中点）几乎一样，
         差别**全在 D1 怎么开盘**：
           · 07-09 跳空 +8.82% → 入场被抬到 26.160 → 1R 从计划的 3.20%
             被撑大到 **11.05%（4.20 倍）** → 同一条止损线从「保险」变成「随手被扫」
           · 07-31 平开 +0.08% → 入场就在触发价上 → 1R 保持 **2.63%**
         ⇒ **「挂触发价等触发」永远优于「跳空后追开盘价」**
           —— 与浩瀚深度案例（17.38R vs 4.09R）是同一个结论的再次验证。

      ★ 事前唯一可见的差异（**只能当人工复核项，不可写成硬闸门**）：
        **D0-1 的形态** —— 07-09 前面是 07-08 +6.90%（**连续第二天大涨**，
        两天累涨 15.1% = 情绪透支，D1 高开就是最后一口气）；
        07-31 前面是 07-30 −5.40% 且**收在振幅下沿 22%**（刚被砸过洗过，
        07-31 的 +9.75% 是「调整后第一根放量长阳」= 重新启动）。
        ⚠ 此模式在 8 个正案例里**无一致性证据** → 不设阈值、只提示人工复核。

      ★ 港股数据通道（**引擎目前不支持**）：`fetch_us` 对港股代码三源全失败；
        东财 `push2his` 港股（`secid=116.00992`）可用但**易触发限流 RemoteDisconnected**；
        新浪港股返回 `Service not valid`；Yahoo 隧道 502。
        **可用源 = 腾讯 `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=hk00992,day,,,320,qfq`**
        （返回 `data.hk00992.qfqday|day`，字段顺序 日期/开/收/高/低/量）。

    ── ★★ 八案例的共性提炼（2026-09-20，这是规则的真正内核）──────
    八案例的「实体/ATR」从 **0.22x 到 2.43x**、「D0 涨幅」从 **+0.66% 到 +14.05%**，
    跨度极大 → **「大阳线」不是判据**，D0 是平盘小阳（浩瀚 +0.66%）照样成立。
    真正一致的只有三条：
      ① 收盘站上全部均线（八案例全中）
      ② 平台度 ≥50%（实测 60%~100%，反面案例联瑞仅 44%）
      ③ **收盘位置在当日振幅高位**（八案例除赛分 41% 外全部 ≥61%，
         浩瀚 94% / 科泰 90% / 成都 90% / 纳微 91%）
    ⇒ **内核 = 「贴着平台沿、收在当日最高附近」**，而不是「涨得多」。
       涨得多（成都 +14%）与涨得少（浩瀚 +0.66%）都能成立，
       因为两者都是「收盘贴在平台沿上、没有上影抛压」。

    ── 判据（2026-09-20 定稿）────────────────────────────────────────
    D0（信号日）：
      ① 收盘站上 **MA5、MA10、MA20 全部三条**
         ★ 用户定：「不做均线下方的股票」—— 均线下方=还有套牢盘压着，压力更大。
         实测（23 只 A 股全历史，P1 口径）：≥2 条 → 胜率 32.4% / 硬止损 12.7%；
         全部三条 → 胜率 37.2% / 硬止损 9.9%。C/D/E（加 MA60/120/250）无进一步改善。
         ★ 2026-09-20 用户再次强调：「收盘价格要站上所有均线，**跟排列无关**」
           —— 只判「站上/未站上」，MA5>MA10>MA20 的多头排列**不作条件**。
      ② 上方有阻力位：resistance = 近 res_win 根最高价（不含当日）
         要求 resistance >= D0.close（在上方）
         且 (resistance - D0.close)/D0.close <= 板块涨停上限
           主板（60/00 开头） 10% ／ 创业板(30)、科创板(68) 20%
         ★ 用户定：「0-20%，当然低一些更好」。实测**越贴墙越好**：
           0~2%（贴墙）  胜率 50.0% 均R +0.80 收益 +2.08% 硬止损  5.3%
           2~10%（半路） 胜率 29.1% 均R -0.05 收益 -0.21% 硬止损 12.6%
           → 故输出按距离分档标注，贴墙者标 T0-primary。
         ★ 2026-09-20 窗口 20 → **25** 根：成都先导 688222 的锚 41.78 在 08-13，
           距 09-16 信号日为 **24 个交易根**，20 根窗口刚好切掉它，引擎误抓 08-20 的
           40.57。25 根即可覆盖。用户原话「60根太多了」→ 仍未回到 60。

    D1（次日）：
      ★★ 「昨高」= **D0 的最高价 `D0.high`（含上影线插针）**，不是 D0 收盘价。
         （2026-09-20 用户提问「昨高指的是收盘价还是最高价」→ 已定稿为**最高价**。）
         判据也一致：P1 分支是 `D1.high > D0.high`，全程比的是 high，不是 close。
         为何必须是最高价：突破的定义是「越过昨天全部成交的价位」，
         D0 的上影线里挂着真实抛压；用收盘价当触发线等于把 2/3 的墙拆掉。
         实证七案例（`_yesterday_high.txt`）——若误用昨收，触发价会**全线大幅下移**：
           浩瀚 18.50 → 18.43（−0.38%）｜ 科泰 25.58 → 25.44（−0.55%）
           纳微 41.87 → 41.40（−1.12%）｜ 康龙 40.43 → 39.92（−1.26%）
           成都 37.19 → 36.62（−1.53%）｜ 赛分 29.02 → 28.30（**−2.48%**）
           东富龙 14.79 → 14.36（**−2.91%**）
         ⇒ 误用昨收 = 提前买入 = D0 那根上影线的抛压还没被消化就进场。
      P1 过昨高买   D1.high > D0.high  →  entry = max(D1.open, D0.high)
                    ★ 用户定：「不是开盘过高，而是次日任意时点过高都买」
                    开盘已过高则用开盘价，否则用触发价 D0.high（盯盘挂单）
      P3 尾盘买     D1.high <= D0.high →  entry = D1.close（尾盘定夺，破位不买）
                    ★ 用户定：「越不过昨日高点代表今天调整预期…尾盘买是最佳选择，破位就不买了」
                    ★ 注意 P3 的入场价是 **D1 收盘**，那是「没追到时的补救价」，
                      不是把「昨高」定义成收盘价 —— 两个 close 是不同的东西，别混。

    止损锚 ★ 2026-09-20 口径扩展（用户定：「取全部锚中离触发价最近」）：
      候选 = { MA5, MA10, MA20, **突破日均K** 实体中点 (o+c)/2, 突破日低点 }
      取 **低于触发价且离触发价最近** 的一条。
      ★★ 2026-09-20 二次修正（德邦科技 688035 案例，用户定）：
        K 线锚的**取K对象**从「最后一根 bars[-1]」改为「**最近的突破均线的日K**」
        （= 当前「收盘站上全部均线」连续段的启动根，见 `find_ma_reclaim_bar`）。
        理由：德邦 9-16 是真突破日（实体 1.39×ATR），9-17/9-18 只是跟涨，
        9-18 实体仅 0.11×ATR → 它的中点 78.23 距收盘只 0.33%，**既不是止损也不是低成本买点**。
        用户原话：「9-18 这么小的 K 线实体，为啥要取它的中点，当然是取 9-16 的中点，
        应该找最近的突破均线的日K。」「能买的低成本当然比追着买要强。」
        ⚠ 八案例（赛分/纳微/成都/科泰/浩瀚/东富龙/康龙）信号日**当天即突破日**，
           `back == 0` → 输出与旧口径逐字相同，不受影响。
      ── 为什么加入 K 线锚 ──
      成都先导 09-16 是一根 +14.05% 的大阳，把 MA5/MA10 远远甩在下方：
        MA20 33.26（风险 10.57%）< MA5 32.60（12.34%）< MA10 32.33（13.07%）
      均线锚全部偏宽、且被「>8% 做不了」闸门拦下。而 **D0 实体中点 34.40 只有 7.50%**：
        → 大阳的实体中点 = 「获利盘兑现压力」的分界，是比均线更贴切的结构锚。
      ── 为什么旧口径排除 MA5 而现在放开 ──
      旧注释说「MA5 长期贴价，必落在 0.25×ATR 噪声带内」。但在**大阳突破日**，
      恰恰相反：MA5 被大阳拉高后反而**离得远**，而它的方向仍是「最贴近价格」。
      以「离触发价最近」这一条单一规则统一处理，不再对 MA5 做先验排除
      —— 噪声带风险由 `NOISE_ROOM_ATR` 检查在更上层兜底。

    ── 为什么是 T0（用户 2026-09-20 定级）────────────────────────────
    与 T1「买突破」的区别：T1 在**位已破之后**追；本 setup 在**位未破之前**、均线已
    收复、贴着墙蓄势时进场。同一道墙，买点低一截、止损窄一截 → 盈亏比更高。
    即用户最早那条「预判埋伏优于顶着买」的同一逻辑，但锚更明确（昨日高点 + 前方阻力位）。
    执行提示：P1 触发价在现价上方，用户券商无 buy-stop/条件单 → **需盯盘手动触发**；
              P3 买点在下方 → 可预挂限价。两者用户都能执行（自述「我会盯盘，做好战术就行」）。

    ── ⚠️ 七案例 vs 全样本回测的背离（2026-09-20 实测，务必记住）──────
    976 笔 T0 成交样本分档（越界即止，20 根后收盘计浮盈，R 截断到 [-1,+5]）：
      D0 实体/ATR  0.7~1.0x（科泰地带）n=120 被扫 83.3% 均R **-0.16**
                   1.5~2.5x           n= 38 被扫 73.7% 均R **+0.24**
      单股风险     0~2%（赛分地带）   n=432 被扫 95.8% 均R **-0.75**
                   5~8%               n= 67 被扫 68.7% 均R **+0.24**
      D0 振幅      3~5%（科泰地带）   n=373 被扫 93.0% 均R **-0.63**
    「科泰特征」严格匹配样本 n=28 → 被扫 92.9%、均R **-0.57**，但它实际走出 +11.38%。
    ⇒ **两级推论（不可混淆）**：
       ① 对选股层：用户收益**不是**来自这些技术参数，而来自基本面+板块+题材筛选
          —— 这正是无选股层回测缺的那一层，**回测系统性低估用户**。
       ② 对参数调优：**禁止**照这批数据改规则（加实体门槛/放宽止损会得到另一个系统）。
          真正常态要盯的判据是 **止损不要太窄**（0~2% 被扫 95.8%），
          但对策是**主动放弃太窄的单子**，不是把止损改宽。
    """
    if not bars or len(bars) < 25 or not atr_v or atr_v <= 0 or last_c is None:
        return None

    closes = [b["c"] for b in bars]
    ma5, ma10, ma20 = sma(closes, 5), sma(closes, 10), sma(closes, 20)
    if ma5 is None or ma10 is None or ma20 is None:
        return None
    # ① 全部三条均线之上（用户定：不做均线下方的股票）
    if not (last_c > ma5 and last_c > ma10 and last_c > ma20):
        return None

    # ② 阻力位 ★ 2026-09-20 双锚并列，**取更高者**（用户定）：
    #   锚1 = 近 res_win 根最高价（不含当日）—— 纯最高，含长上影插针
    #   锚2 = 同窗口内的**枢轴高**（左右各 2 根都低于它）中 ≥ 收盘价 的**最高**那个
    #   两者通常是**同一个日线平台上的两个测点** → 平台沿 = **更高者**。
    #   ── 为什么不是「取近者」（我 2026-09-20 一度按取近者实现，被用户纠正）──
    #   取近者会把「平台内一个较低的次级高点」当成墙，等于把墙画低了：
    #     康龙 08-04：枢轴高 40.96 vs 窗口最高 42.00 —— 两者同属 7~8 月那个平台，
    #     墙是 42.00（用户实盘认的锚），不是 40.96。
    #     赛分 09-15：枢轴高 28.33 vs 窗口最高 31.97 —— 同属平台，墙应取更高者。
    #   用户原话：「这两个高点应该取更高者，因为都是属于同一个日线平台」。
    #   ⇒ 取更高者同时解决两件事：① 不把墙画低（赔率不虚高）；
    #     ② 长上影插针若确实是平台的一部分，也自然被包含进来。
    seg = bars[-(res_win + 1):-1] if len(bars) >= res_win + 1 else bars[:-1]
    if not seg:
        return None
    res_i = max(range(len(seg)), key=lambda k: seg[k]["h"])
    res_max = seg[res_i]["h"]                 # 锚1：窗口内最高
    res_max_from = seg[res_i]["d"]

    # 锚2：枢轴高（左右各 2 根都 <= 它），取 >= last_c 的**最高**者
    _ph = []
    for _i in range(2, len(seg) - 2):
        _b = seg[_i]
        _okl = (seg[_i - 1]["h"] <= _b["h"] and seg[_i - 2]["h"] <= _b["h"])
        _okr = (seg[_i + 1]["h"] <= _b["h"] and seg[_i + 2]["h"] <= _b["h"])
        if _okl and _okr and _b["h"] >= last_c:
            _ph.append((_b["h"], _b["d"]))
    res_piv = res_piv_from = None
    if _ph:
        _ph.sort(key=lambda x: x[0])
        res_piv, res_piv_from = _ph[-1]        # 最高者

    # 两者取**更高者**作主锚（同一平台 → 平台沿是高那个）
    if res_piv is not None and res_piv > res_max:
        resistance, res_from, res_kind = res_piv, res_piv_from, "枢轴高"
        resistance_alt, resistance_alt_from = res_max, res_max_from
    else:
        resistance, res_from, res_kind = res_max, res_max_from, "窗口最高"
        resistance_alt, resistance_alt_from = (res_piv, res_piv_from) if res_piv is not None else (None, None)

    if resistance < last_c:
        return None
    # ③ ★ 2026-09-20 新增「左侧必须有锚（平台成形）」闸门 —— 用户定
    #   用户原话（联瑞新材 688300 案例）：
    #     「联瑞新材要看大阳线次日入场那一天左侧的锚，**根本就没有锚啊，平台都没有形成**，
    #       最高都跑到 297.31 了，这种肯定不能做了」
    #   背景：联瑞 08-27 是一根 +20% 涨停大阳（收 186.24），08-28/08-31 连续 PASS 全部均线，
    #     但**左侧是 6 月 297.31 崩塌到 8-03 的 98.78（−66.8%）后的报复性反弹**，
    #     08-28 当日已冲到左侧 25 根最高之上（距最高 −1.9%），头上没有任何可参照的阻力位
    #     —— 所谓「阻力位 202.88」是它自己那根反弹的高点，不是左侧的墙。
    #   ── 本质：T0 需要「左侧平台 → 前方前高 → 次日过昨高」三段结构 ──
    #     联瑞缺第一段（左侧无平台）。用户第 4 条原理：「这个规则成立的前提是买突破的前置
    #     setup，否则任意一个站上所有均线我都去试，没有意义」——联瑞就是那个反例。
    #   ── 判据：平台度 = 窗口内收盘落在「中位价 ±10%」的根数占比 ──
    #     实测六案例（`_plat2.txt`）：
    #       赛分 100% / 东富龙 100% / 纳微 76% / 成都先导 72% / 康龙 60%  ← 五个正案例
    #       联瑞 44%                                                  ← 用户判「不能做」
    #     阈值 **50%**（用户定）：高于联瑞 44%、低于正案例最低 60%，留余量。
    #   ── 与「距最高」的关系 ──
    #     联瑞距窗口最高 −1.9%（已冲出左侧区间）；五个正案例 +5.2%~+14.1%（头上有墙）。
    #     平台度已能区分两者，故「距最高 > 0」不单独设闸门，仅记录 `above_range` 供复盘。
    _seg33 = seg
    _cs33 = sorted(b["c"] for b in _seg33)
    _mid33 = _cs33[len(_cs33) // 2]
    _plat_deg = (sum(1 for b in _seg33 if abs(b["c"] - _mid33) / _mid33 <= 0.10)
                 / len(_seg33) * 100)
    _hi33 = max(b["h"] for b in _seg33)
    _above_range = last_c > _hi33          # 价格已冲出左侧窗口区间
    if _plat_deg < 50.0:
        return None

    gap_pct = (resistance - last_c) / last_c * 100
    # 板块涨跌幅上限：主板 10% / 创业板(30)、科创板(68) 20%。
    # 优先取 ev 里的 ticker（build_ev 调用方注入）；取不到再退回振幅推断。
    _tk = str(ev.get("ticker") or ev.get("symbol") or ev.get("code") or "")
    # ★ 市场判据（2026-09-20 新增，用于 note 的「尾盘」措辞分支）：
    #   A 股 6 位纯数字 → 有尾盘（14:57 集合竞价前定夺）
    #   美股/港股 → 无「尾盘 14:57」概念，措辞改为「收盘前定夺（ET 15:45–16:00）」
    #   ⚠ 用户 2026-09-20 已定：美股不做盘中盯守（致富证券无 buy-stop，且盘中在北京深夜），
    #     故美股措辞必须避免让人误以为要「守着盘到尾盘」。
    _is_cn = bool(re.fullmatch(r"\d{6}", _tk.strip())) if _tk.strip() else None
    if _tk:
        _c6 = _tk[-6:] if len(_tk) >= 6 else _tk
        cap = 20.0 if _c6.startswith(("30", "68")) else 10.0
        board_src = f"code:{_c6}"
    else:
        cap = 20.0 if _is_dual_crea(bars) else 10.0
        board_src = "振幅推断"
    if gap_pct > cap:
        return None

    # D1 尚未发生 —— 本函数在 D0 收盘时给「次日执行方案」
    d0 = bars[-1]
    trigger = round(d0["h"], 2)          # ★ 触发价 = 昨日**最高价**（不是收盘价，2026-09-20 定稿）

    # 止损锚 ★ 2026-09-20 口径扩展（用户定：「取全部锚中离触发价最近」）：
    #   候选 = { MA5, MA10, MA20, D0 实体中点, D0.low }
    #   取「低于触发价且离触发价最近」的一条。
    #   ── 旧口径只取 MA10/MA20 且排除 MA5，在大阳突破日会失效 ──
    #   成都先导 09-16（+14.05% 大阳）实测：
    #     MA20 33.26（风险10.57%）< MA5 32.60（12.34%）< MA10 32.33（13.07%）
    #     → 均线锚全部偏宽（大阳把均线甩在下方），被 >8% 闸门拦下；
    #     而 **D0 实体中点 34.40 只有 7.50%**，是「获利盘兑现压力」的分界，更贴切。
    #   ── 为什么放开 MA5 ──
    #   旧注释称「MA5 长期贴价必落噪声带内」，但**在大阳日恰好相反**：MA5 被拉高后
    #   反而离得远。统一用「离触发价最近」单一规则处理，不再对 MA5 先验排除；
    #   噪声带风险由上层 `NOISE_ROOM_ATR` 检查兜底。
    # ★★ K 线锚取「**最近的突破均线的日K**」（2026-09-20 用户定，德邦科技 688035 案例）
    #   原实现无条件用 `bars[-1]`，当突破日之后又走 1~2 根**小实体**时，
    #   最后一根的中点会贴在收盘价上（德邦 9-18 实体仅 0.11×ATR → 中点 78.23
    #   vs 收 78.49，只差 0.33%）—— 既不是有意义的止损，也不是「低成本」买点。
    #   改为锚定「把股价拉上全部均线的那根」（德邦 9-16，实体 1.39×ATR）。
    #   `back == 0`（最后一根即突破日）时行为与旧实现**逐字相同**，八案例不受影响。
    _kb_i = find_ma_reclaim_bar(bars)
    kb = bars[_kb_i] if _kb_i is not None else d0
    kb_back = (len(bars) - 1 - _kb_i) if _kb_i is not None else 0
    st = None
    st_name = None
    _mid = (kb["o"] + kb["c"]) / 2.0
    _mid_nm = "实体中点" if kb_back == 0 else "突破日实体中点"
    _low_nm = "大阳低点" if kb_back == 0 else "突破日低点"
    _body_atr_kb = abs(kb["c"] - kb["o"]) / atr_v if atr_v else None
    for _nm, v in (("MA5", ma5), ("MA10", ma10), ("MA20", ma20),
                   (_mid_nm, _mid), (_low_nm, kb["l"])):
        if v is None or v >= trigger:
            continue
        if st is None or v > st:
            st, st_name = v, _nm
    if st is None:
        return None
    hard = round(st, 2)
    risk_pct_v = (trigger - hard) / trigger * 100
    # 风险闸门：超 8% 的止损在小账户上做不了仓位管理（用户「小亏」框架）。
    # 保留信号但标注超限，由消费方决定半仓/放弃 —— 不静默丢弃。
    risk_over = risk_pct_v > 8.0

    # 分档：贴墙(≤2%) / 半路(>2%)
    close_to_wall = gap_pct <= 2.0
    tier = "T0" if close_to_wall else "T0"
    grade = "贴墙" if close_to_wall else "半路"
    dist_atr = (resistance - last_c) / atr_v

    # 买点前置幅度：过昨高买 vs 现价
    prem_pct = (trigger - last_c) / last_c * 100

    exec_hint = (
        f"★ 需盯盘：触发价 {trigger} 在现价 {round(last_c, 2)} 上方 {prem_pct:.2f}%，"
        f"券商无 buy-stop/条件单，只能盘中手动打（A股 T+0 买入当日不可卖，"
        f"但 A 股本身 T+1 交割，买入即锁定至次日）"
    )

    return {
        "setup_kind": "ma_reclaim_break",
        "mode": "ma_reclaim_break",
        "tier": tier,
        "priority": 1,
        "role": "primary_entry",
        "path": "B",
        "grade": grade,                    # 贴墙 / 半路
        "order": "buy-on-break",
        "level": trigger,                  # 触发价 = 昨日**最高价** D0.high
        "trigger": trigger,
        "hard_stop": hard,
        "risk_per_share": round(trigger - hard, 2),
        "risk_pct": round(risk_pct_v, 2),
        "risk_atr": round((trigger - hard) / atr_v, 2) if atr_v else None,
        "stop_anchor": st_name,
        # ★ K 线锚溯源（2026-09-20 新增）：K 线锚取自哪一根、距今几根、那根的实体有多大。
        #   消费方（报表/probe/复盘）据此判断「这个止损是不是贴在小实体上」。
        "kanchor_date": kb["d"],
        "kanchor_back": kb_back,
        "kanchor_mid": round(_mid, 2),
        "kanchor_low": round(kb["l"], 2),
        "kanchor_body_atr": round(_body_atr_kb, 2) if _body_atr_kb is not None else None,
        "last_bar_body_atr": round(abs(d0["c"] - d0["o"]) / atr_v, 2) if atr_v else None,
        "risk_over_limit": risk_over,
        "resistance": round(resistance, 2),
        "resistance_from": res_from,
        "resistance_kind": res_kind,          # 枢轴高 / 窗口最高（双锚取近者）
        "resistance_alt": round(resistance_alt, 2) if resistance_alt else None,
        "resistance_alt_from": resistance_alt_from,
        # ★ 左侧平台质量（2026-09-20 新增，联瑞新材案例）：< 50% 直接不出 T0
        "plateau_degree": round(_plat_deg, 1),
        "above_range": bool(_above_range),
        "dist_to_wall_pct": round(gap_pct, 2),
        "dist_to_wall_atr": round(dist_atr, 2),
        "board_cap_pct": cap,
        "board_src": board_src,
        # ★ 均线排列**不作闸门**（2026-09-20 用户定）：纳微 9-08 突破时均线也未走顺
        #   （MA10/MA20 仍纠缠），后面还调了几天，但不影响最终结果。仅记录供复盘。
        "ma_aligned": bool(ma5 > ma10 > ma20),
        # ★ 板块共振（用户 2026-09-20 指出：这是真正提高胜率的维度）：
        #   「对板块强度有要求，能提高胜率，大部分是同时启动的」。
        #   实证：TEM/SDGR/ILMN（AI制药）+ 赛分/纳微 于 2026-09-14~15 同日共振。
        #   引擎单票无法判定板块 —— 该字段由批处理层（watch_cn / scanner 聚合）注入，
        #   此处只占位，避免消费方 KeyError。
        "sector_resonance": None,
        "ma_state": {
            "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2),
        },
        "exec": exec_hint,
        # ★ 市场分支（2026-09-20 新增）：A 股才有「尾盘 14:57」；美股/港股措辞改写。
        #   且用户已定美股不做盘中盯守 → 明说「睡前挂单 / 次日补判」，不写「守到尾盘」。
        "alt_tail_entry": (
            {
                "condition": "D1.high <= D0.high（未过昨高）",
                "entry": "D1 收盘价（尾盘 14:57 定夺，破位不买）",
                "note": (
                    "越不过昨日高点 = 今日调整预期，尾盘买最佳，破位就不买。"
                    "买点在下方 → 可预挂限价单；无执行难度"
                ),
            }
            if _is_cn is not False else
            {
                "condition": "D1.high <= D0.high（未过昨高）",
                "entry": "D1 收盘价（收盘前定夺，破位不买）",
                "note": (
                    "越不过昨日高点 = 今日调整预期，收盘前买最佳，破位就不买。"
                    "★ 美股无「尾盘 14:57」概念，且盘中在北京深夜 → "
                    "按用户口径【不熬夜】：睡前把单子处理完，或只记录、次日按实际走势补判，"
                    "不做盘中盯守。"
                ),
            }
        ),
        "note": (
            f"【T0·均线收复+过昨高】站上全部均线（MA5 {round(ma5,2)} / "
            f"MA10 {round(ma10,2)} / MA20 {round(ma20,2)}），"
            f"上方阻力位 {round(resistance,2)}（{res_from}·{res_kind}）距收盘 "
            f"{gap_pct:.2f}%（{dist_atr:.2f}×ATR，{grade}）"
            + (f"，另锚 {round(resistance_alt,2)}（{resistance_alt_from}）"
               if resistance_alt else "")
            + f"，左侧平台度 {_plat_deg:.0f}%"
            + f"—— 距墙分档仅作**参考**（贴墙≤2% / 半路2~10% / 远端>10%）："
            f"用户五个实证案例的距墙为 5.2%~14.1%（多数在 10%~14%），"
            f"而无选股层的全样本回测里「半路」期望≈0 —— 差距来自板块/题材/基本面筛选，"
            f"故**不因距墙远而否定信号**，只标注位置供人工取舍。"
            f"次日过昨高 {trigger} 即买（开盘已过高用开盘价）；"
            + ("不过则尾盘定夺。" if _is_cn is not False else
               "不过则【收盘前定夺】（美股无尾盘 14:57；按用户口径不熬夜——"
               "睡前处理完或次日补判）。")
            + f"止损锚 {st_name} {hard}"
            f"（风险 {risk_pct_v:.2f}%）。"
            + (f" ★ K线锚取自**突破均线那天 {kb['d']}**"
               f"（实体中点 {round(_mid, 2)} / 低点 {round(kb['l'], 2)}，"
               f"实体 {_body_atr_kb:.2f}×ATR），不是最后一根 —— 突破日之后第 {kb_back} 根"
               f"的实体只有 {abs(d0['c'] - d0['o']) / atr_v:.2f}×ATR，"
               f"其中点贴在收盘价上、不构成结构位。"
               if kb_back > 0 else "")
            + (f" ⚠ 风险 {risk_pct_v:.2f}%>8%，小账户难做仓位管理，"
               f"建议降为半仓或改做更贴墙的标的" if risk_over else "")
        ),
    }


def _is_dual_crea(bars):
    """判断是否创业板/科创板（20% 涨跌幅）—— 用代码前缀兜底。

    真实代码由调用方在 ev/scan 层注入；此处无法从 bars 拿到代码时，
    退化为按振幅判断：近 60 根最大单日涨幅 >10.5% 即视为 20% 板。
    """
    try:
        code = (bars[0].get("code") or bars[-1].get("code") or "")
        if isinstance(code, str) and code:
            c = code[-6:] if len(code) >= 6 else code
            return c.startswith(("30", "68"))
    except Exception:
        pass
    # 兜底：按历史单日涨幅推断
    seg = bars[-60:] if len(bars) >= 60 else bars
    for k in range(1, len(seg)):
        pc = seg[k - 1]["c"]
        if pc and (seg[k]["h"] - pc) / pc > 0.105:
            return True
    return False


def plan_entry(bars, ev):
    """当日只给一种 mode（含 W底/旗形突破）。"""
    last_c = bars[-1]["c"] if bars else None
    atr_v = atr14(bars)
    rvol = ev.get("rvol20")
    vol_ok = rvol is None or rvol > 1.5   # 2026-09-17 起不再作突破类闸门（仅保留供参考/提示）
    vol_shrink = rvol is None or rvol <= 1.2
    # 价格型突破确认（2026-09-17 用户定：RVOL 不是硬闸门，「价格说明一切」）。
    # 站上关键位的确认改看价格本身：收盘落在当日振幅上半区（排除上影插针式站上），
    # 且收盘不低于前收（非下跌日）。量能降级为提示，不再一票否决。
    _lb = bars[-1] if bars else {}
    _prev_c = bars[-2]["c"] if len(bars) > 1 else None
    _rng = (_lb.get("h", 0) - _lb.get("l", 0)) if bars else 0
    price_conf = bool(
        bars and _lb.get("c") is not None
        and (_rng <= 0 or (_lb["c"] - _lb["l"]) / _rng >= 0.5)
        and (_prev_c is None or _lb["c"] >= _prev_c)
    )

    def vol_note():
        """量能降级为提示：放量=加分项；无量只说明「非放量突破」，按价格结构确认。"""
        if rvol is None:
            return "；无量能数据，确认打折"
        if rvol > 1.5:
            return f"；放量确认 RVOL={round(rvol, 2)}"
        return (f"；无量（RVOL={round(rvol, 2)}≤1.5）——按价格结构确认（阳线、收盘上半区），"
                f"属非放量突破，仓位打折")

    # 宽幅强阳：实体 ≥1.2×ATR 且收盘落在振幅上半区。无量时的**价格替代确认**，
    # 用于平台/W底突破（下降趋势线家族已完全按价格确认，不设量能闸门）。
    strong_bar = bool(
        price_conf and atr_v and _lb.get("o") is not None
        and (_lb["c"] - _lb["o"]) >= 1.2 * atr_v
    )
    r1p = _px(ev.get("R1"))[1]
    plat = ev.get("platform")  # 不回落旧 R1；无活平台沿则不做平台突破
    plat_p = _px(plat)[1]
    n_above = days_above_level(bars, plat_p)
    n_above_r1 = days_above_level(bars, r1p)
    c2 = bool(ev.get("c2") if ev.get("c2") is not None else ev.get("cond2_no_new_low"))
    p1p = _px(ev.get("P1"))[1]
    structure_ok = p1p is not None and c2 and last_c is not None and last_c > p1p
    demand = living_demand(bars, ev)
    bz_line = zone_from_demand(demand, bars, ev)
    bz_line["days_above_r1"] = n_above_r1
    bz_line["days_above_platform"] = n_above
    uptrend = still_uptrend(bars, ev, last_c, c2, p1p)
    imp = find_impulse_pause(bars, atr_v)
    # 大阳回踩门控（2026-09-16 松绑）：原只认 uptrend，导致「反转态中放量大阳收复 P1」
    # 这一典型启动形态被整段丢弃。放宽为 uptrend 或 reversal_yang_ok（后者自带四条防护）。
    gate_imp, gate_src = uptrend, ("uptrend" if uptrend else None)
    if not gate_imp and imp.get("state") != "no_yang":
        if reversal_yang_ok(bars, imp, p1p, last_c, atr_v):
            gate_imp, gate_src = True, "reversal_yang"
    Hs_all, Ls_all = pivots(bars, w=3)

    dtl = ev.get("down_tl")
    tl_now = None
    declining = False
    days_above_tl = 0
    pb = pa = None
    if dtl and dtl.get("a") and dtl.get("b"):
        pb = (dtl["b"]["i"], dtl["b"]["price"])
        pa = (dtl["a"]["i"], dtl["a"]["price"])
        if pb[0] != pa[0]:
            declining = pa[1] < pb[1]
            tl_now = line_val(pb, pa, len(bars) - 1)
            days_above_tl = days_above_line(bars, pb, pa)

    breakout_modes = (
        "platform_break", "w_bottom_break", "flag_tl_break", "downtrend_tl_break",
    )

    def pack(mode, priority, setup, verdict, recommend, note, buy_zone=None, path=None):
        z = buy_zone if buy_zone is not None else bz_line
        # 标注放宽来源，避免「反转态买点」被误当成常规升势回踩
        if gate_src == "reversal_yang" and "大阳" in str(note):
            note = "【反转态·放量大阳·已放宽】" + note
        if path is None:
            if mode in ("line_pullback", "impulse_pause"):
                path = "A"
            elif mode in breakout_modes:
                path = "B"
            else:
                path = "wait"
        if recommend and mode in breakout_modes and too_far_from_zone(z, atr_v, last_c):
            lv = z.get("level")
            hi = z.get("primary_hi")
            d = (last_c - lv) / atr_v if (atr_v and lv is not None and last_c is not None) else None
            note = (
                f"收盘 {last_c:.2f} 高出买位 {lv} 已 {d:.1f}×ATR（>2），"
                f"不追；等回踩 {hi} 一带或等新一轮缩量再站上"
            )
            mode, priority, setup, verdict, recommend, path = (
                "wait", None, "wait", "突破已延伸·等回踩", False, "wait",
            )
        elif (
            recommend
            and mode in breakout_modes
            and atr_v
            and last_c is not None
            and z.get("level") is not None
            and z.get("in_zone") is False
        ):
            # 现价出买区、但仍在「不追高」闸门（2×ATR）之内。三档语义分开：
            #   上沿之上（出买区 ≤2×ATR）→ 仍可执行，但只能挂回踩单，禁止市价追（recommend 保持 True）
            #   下沿之下                 → 突破未成立，不接（recommend=False）
            # 买区位（≤1.0×ATR）与可执行闸门（≤2.0×ATR）是两件事，不得用前者卡死后者。
            lv = z["level"]
            lo = z.get("primary_lo")
            hi = z.get("primary_hi")
            d = (last_c - lv) / atr_v
            if hi is not None and last_c > hi:
                note = (
                    f"现价 {last_c:.2f} 高出买位 {lv} 已 {d:.1f}×ATR，已出买区上沿 {hi}（≤2×ATR 仍可执行）；"
                    f"只能挂回踩单在 {hi} 一带，禁止市价追"
                )
                verdict = f"突破已延伸·只挂回踩单 {hi}"
                z["chase_only"] = True
                # 强突破次日多不给回踩：SDGR 2026-09-15 RVOL=3.85，次日
                # 23.29 跳空越过买区上沿 22.59，之后再未回落 —— 只挂回踩单＝整段吃不到
                # （原回踩单 22.59 从未成交）。故对强突破额外开一条**次日限价**次优路径，
                # 代价用「半仓＋更紧止损」对冲，且不越「不追高」那条线：
                #   上限 = 闸门边界 level+2×ATR（与不追高同一条线，不越线）
                #   止损 = 突破阳 (H+L)/2（比结构止损紧）
                #   仅次日有效，不成交即作废
                # 触发口径（2026-09-17 用户定 RVOL 不作硬闸门）：放量 **或** 价格型
                # 宽幅强阳（实体≥1.2ATR、收盘上半区，见 strong_bar）—— 无量强突破同样
                # 不给回踩机会，只认 RVOL 会系统性错过它们。
                _vol_strong = rvol is not None and rvol >= 2.5
                if atr_v and (_vol_strong or strong_bar):
                    yang = bars[-1]
                    chase_stop = round((yang["h"] + yang["l"]) / 2, 2)
                    if chase_stop >= last_c:
                        chase_stop = round(last_c - 0.5 * atr_v, 2)
                    chase_cap = round(lv + 2.0 * atr_v, 2)
                    why = (f"RVOL={rvol:.2f}≥2.5" if _vol_strong
                           else f"实体{(yang['c'] - yang['o']) / atr_v:.2f}ATR 宽幅强阳")
                    z["next_day_chase"] = {
                        "limit": chase_cap,
                        "size_ratio": 0.5,
                        "stop": chase_stop,
                        "valid_for": "仅次日",
                        "note": (
                            f"强突破（{why}）次日多不回踩："
                            f"若次日开盘 ≤{chase_cap}，可**半仓**限价执行，"
                            f"止损收紧到突破阳中点 {chase_stop}；只此一天，不成交即作废"
                        ),
                    }
                    note += (
                        f"；**强突破例外**（{why}）：次日开盘 ≤{chase_cap} "
                        f"可半仓限价执行，止损收紧至突破阳中点 {chase_stop}，仅次日有效"
                    )
                    verdict = f"突破已延伸·只挂回踩单 {hi} 或次日半仓追（强突破）"
            elif lo is not None and last_c < lo:
                note = (
                    f"现价 {last_c:.2f} 低于买位 {lv} {abs(d):.1f}×ATR，已跌破买区下沿 {lo}；"
                    f"突破未成立，不接"
                )
                verdict = f"跌破买区下沿 {lo}·不接"
                recommend = False
            else:
                note = f"现价 {last_c:.2f} 未落入买区 {lo}-{hi}（距买位 {d:+.1f}×ATR）"
                verdict = "未进买区"
                recommend = False
        z["path"] = path
        z["mode"] = mode
        z["priority"] = priority
        z["days_above_r1"] = n_above_r1
        z["days_above_platform"] = n_above
        result = {
            "path": path,
            "mode": mode,
            "priority": priority,
            "setup": setup,
            "verdict": verdict,
            "recommend": recommend,
            "note": note,
            "buy_zone": z,
            "days_above_r1": n_above_r1,
            "days_above_platform": n_above,
        }
        result = attach_stops_targets(result, bars, atr_v, Hs_all)
        # 收盘正顶未破的位 → 附「预备突破单」，与当日 mode 独立并存。
        # 两种挂法：水平平台沿（平台版）/ 下移斜线（下降趋势线版）；同时够格取更近的。
        pb_plat = pre_breakout_order(bars, ev, atr_v, plat, last_c, rvol)
        pb_line = pre_breakout_line_order(bars, ev, atr_v, last_c, rvol)
        cands = [x for x in (pb_plat, pb_line) if x]
        if cands:
            result["pre_breakout"] = min(cands, key=lambda x: x["dist_atr"])
            # 埋伏单是独立 T1 入口，须与当日买点争夺「先成交者」。按成交价高低定先后：
            # 买点在上方（突破/追高类）而埋伏单在下方 → 埋伏单先触发，它才是实际首选。
            if mode in breakout_modes:
                pb_px = result["pre_breakout"].get("trigger")
                zz = z.get("level") if isinstance(z, dict) else None
                if pb_px is not None and zz is not None and pb_px <= zz:
                    result["pre_breakout"]["beats_current_mode"] = True
            if len(cands) == 2:
                other = max(cands, key=lambda x: x["dist_atr"])
                result["pre_breakout"]["note"] += (
                    f"；另有"
                    f"{'活平台沿' if other.get('anchor') != 'down_tl' else '下降趋势线'}"
                    f" {other['level']} 的埋伏单同样够格（距 {other['dist_atr']}×ATR），先到先做"
                )
        # ★ T0 均线收复+过昨高：**总是**计算并挂在结果上（供股池复盘/盯盘方案消费）。
        # 纯增量，不改变当日 mode —— 它回答「次日怎么挂单」，不回答「在哪买」。
        # 提升规则（2026-09-20，用户在 pack() 出口统一做，不再打分支补丁）：
        #   mode=="wait"               → T0 接管（原本无买点，它是唯一入口）
        #   recommend==False           → T0 接管（原买法自身已否，T0 仍有独立方案；
        #                                如东富龙 9-17 line_pullback「赔率偏弱」）
        #   mode=="impulse_pause" 等
        #     「大阳后」家族（赛分 9-15「大阳后未满三日」/ 纳微 9-08「大阳当日不追」）
        #                              → T0 接管。★ 这两条是 T1 的纪律，与 T0 天然冲突：
        #                                T0 要的正是「大阳次日的过昨高」，不是等回踩。
        #   其他 recommend==True 的形态  → 保留原 mode，T0 仅并列挂载（先到先做）
        if t0:
            result["ma_reclaim"] = t0
            result["tier_t0"] = "T0"
            _takeover = (
                result.get("mode") == "wait"
                or not result.get("recommend")
                or result.get("mode") in ("impulse_pause",)
                or "大阳" in str(result.get("verdict") or "")
            )
            if _takeover:
                result = _t0_takeover(result, t0)
        return result

    # ★★ T0 入口：均线收复 + 过昨高（2026-09-20 用户定级，优先级**高于** T1 买突破）
    #
    # 设计（2026-09-20 修正）：T0 是「**执行方案**」，不是「形态」。它回答的是
    # 「次日怎么挂单」，而形态回答「在哪买」。故它**不抢标**其他 mode：
    #   ① 无条件计算，挂在 result["ma_reclaim"]（股池复盘/盯盘方案用，纯增量）
    #   ② 仅当其他所有 mode 都判为 wait（当日无买点）时，才把 mode 提升为
    #      ma_reclaim_break —— 此时它是**唯一入口**，按用户定级给 T0。
    #
    # 为什么不能无条件抢标（2026-09-20 回归发现）：T0 判据（站上全部均线 + 上方
    # 有阻力位）比「反转态大阳」「平台突破」宽，放在最前面 return 会吃掉大量本该
    # 走其他路径的样本 —— test_review_fixes::test_reversal_yang_gate_relaxes
    # 断言 gate=="reversal_yang" 却拿到 T0 即为实证。
    #
    # T0 自带的买区：触发价 = 昨日高点，止损锚 = MA10/MA20 中更近的那条。
    # ★ 不能传 bz_line（那是「回踩(A)·上升趋势线」买区）—— 会把趋势线位
    #   （常高于现价）喂给 stop_plan，触发「止损锚在现价之上·买入即止损」误判
    #   （2026-09-20 康龙 8-04 实测：结构止损被算成趋势线 40.44 > 现价 39.92）。
    t0 = ma_reclaim_break(bars, ev, atr_v, last_c)
    _t0z = None
    if t0:
        _t0z = {
            "level": t0["trigger"],
            "primary_lo": round(t0["hard_stop"], 2),
            "primary_hi": t0["trigger"],
            "in_zone": True,
            "type": f"T0·均线收复+过昨高（{t0['grade']}·距墙{t0['dist_to_wall_pct']}%）",
            "anchor": "ma_reclaim",
            "counter": t0["resistance"],
            "struct_stop": t0["hard_stop"],
            "struct_anchor": f"{t0['stop_anchor']}@{t0['hard_stop']}（收盘破）",
            "hard_stop": t0["hard_stop"],
            "hard_anchor": t0["stop_anchor"],
            "invalidation": t0["hard_stop"],
            "hard": t0["hard_stop"],
            # ★ 2026-09-20 口径同步（同上）：不再是「MA10/MA20 排除 MA5」。
            "stop_basis": (
                "{MA5, MA10, MA20, 突破日均K 实体中点, 突破日低点} 中"
                "「< 触发价且离触发价最近」的一条"
                "（K 线锚取「最近的突破均线的日K」，非最后一根）"
            ),
            "struct_exec": STRUCT_EXEC,
        }

    plat_txt = f"{round(plat_p, 2)}" if plat_p is not None else "N/A"
    fresh_plat = plat_p is not None and last_c is not None and last_c > plat_p and n_above <= 3
    # 2026-09-17 用户定「价格说明一切」：突破类不再以量能作一票否决闸门。
    # 放量（vol_ok）/宽幅强阳（strong_bar）都只是加分确认，最终一律要过价格确认
    # price_conf —— 收盘落在当日振幅上半区且不低于前收，排除长上影插针式站上。
    if fresh_plat and not price_conf:
        return pack(
            "wait", None, "wait", "平台突破·价格未确认", False,
            f"近{n_above}根站上活平台沿 {plat_txt}，但收盘落在当日振幅下半区"
            f"（或收低于前收）= 上影插针式站上，不算突破" + vol_note(),
            _empty_zone(),
        )
    if fresh_plat and (vol_ok or strong_bar):
        note = ""
        if declining and days_above_tl <= 3:
            note = "下降趋势线同步突破，按平台突破（优先T1）执行"
        if r1p is not None and abs(plat_p - r1p) > 0.01:
            extra = f"活平台沿 {plat_txt}（123 R1={round(r1p, 2)} 已不作突破位）"
            note = (note + "；" if note else "") + extra
        note = (note + "；" if note else "") + vol_note().lstrip("；")
        z = zone_at_level(plat_p, atr_v, last_c, "平台突破(优先T1)", ev, bars)
        return pack("platform_break", 1, "breakout", "平台突破(优先T1)", True, note, z)
    if fresh_plat and not (vol_ok or strong_bar):
        # 量能不足且非宽幅强阳 = 疑似假突破。但老罗 2026-09-17 定「强上加强」：
        # 平台破位与沿线主升两结构互证 —— 优先级高于单独任何一路，
        # 直接按沿线回踩给出买点。收盘已跌破该线（dist_atr<0）则不豁免，仍 wait。
        boost = (
            structure_ok
            and demand is not None
            and demand["hits"] >= 4
            and 0.0 <= demand["dist_atr"] <= 1.0
            and ma_anchor_trend_ok(bars, ev, demand)
        )
        if boost:
            label = ANCHOR_LABEL.get(demand["anchor"], demand["anchor"])
            lv = round(demand["level"], 2)
            rec = bool(vol_shrink)
            note = (
                f"【强上加强】近{n_above}根站上活平台沿 {plat_txt} 但 RVOL="
                f"{round(rvol, 2) if rvol is not None else 'N/A'}≤1.5 且非宽幅强阳；"
                f"同时沿{label}@{lv}主升（触及{demand['hits']}次、"
                f"距线 {demand['dist_atr']:.2f}×ATR）→ 两结构互证，按沿线回踩买"
            )
            if not vol_shrink:
                note += f"；量能偏大 RVOL={round(rvol, 2)}，等缩量尾盘"
            bz_line["type"] = f"强上加强·沿线回踩·{label}"
            return pack("line_pullback", 1, "pullback", "强上加强(平台+沿线主升)", rec, note, bz_line)
        # 上面 price_conf 闸门已过 → 价格本身成立，只是「未放量」。
        # 2026-09-17 用户定「价格说明一切」：非放量突破不再判假突破「不买」
        # —— 那正是 ILMN 踏空的成因。给买区，但明确打折为半仓/试错仓。
        z = zone_at_level(plat_p, atr_v, last_c, "平台突破(优先T1)", ev, bars)
        note = (
            f"近{n_above}根站上活平台沿 {plat_txt}；价格已确认（收盘在当日振幅上半区、"
            f"不低于前收），但量能未放（RVOL="
            f"{round(rvol, 2) if rvol is not None else 'N/A'}）→ 非放量突破："
            f"半仓/试错仓，次日不破当日低点再加"
            + vol_note()
        )
        return pack("platform_break", 1, "breakout", "平台突破(优先T1)·非放量", True, note, z)

    wpat = ev.get("w_bottom")
    if wpat is None:
        wpat = detect_w_bottom(bars, Hs_all, Ls_all, atr_v)
    if wpat and last_c is not None:
        neck = wpat["neckline"]
        n_neck = days_above_level(bars, neck)
        fresh_w = last_c > neck and 1 <= n_neck <= 3
        if fresh_w and wpat["l2"]["i"] < len(bars) - 1:
            neck_txt = round(neck, 2)
            if price_conf:
                z = zone_at_level(neck, atr_v, last_c, "W底颈线突破(优先T1)", ev, bars)
                z["anchor"] = "w_neckline"
                note = (
                    f"W底颈线 {neck_txt}："
                    f"左底 {wpat['l1']['d']}@{round(wpat['l1']['price'], 2)} / "
                    f"右底 {wpat['l2']['d']}@{round(wpat['l2']['price'], 2)}；"
                    f"结构止损看颈线下" + vol_note()
                )
                return pack(
                    "w_bottom_break", 1, "breakout", "W底颈线突破(优先T1)", True, note, z,
                )
            return pack(
                "wait", None, "wait", "W底颈线突破·价格未确认", False,
                f"近{n_neck}根站上颈线 {neck_txt}，但收盘落在当日振幅下半区"
                f"（或收低于前收）= 上影插针式站上，不算突破" + vol_note(),
                _empty_zone(),
            )

    flag = ev.get("bull_flag")
    if flag is None:
        flag = detect_bull_flag(bars, Hs_all, atr_v)
    if flag and last_c is not None:
        f_tl = flag["tl_now"]
        f_days = flag["days_above_tl"]
        fresh_flag = last_c > f_tl and 1 <= f_days <= 3
        if fresh_flag and price_conf:
            tl_txt = round(f_tl, 2)
            z = zone_at_level(f_tl, atr_v, last_c, "旗形下降趋势线突破(优先T1)", ev, bars)
            z["anchor"] = "flag_tl"
            note = (
                f"旗形突破：旗杆 {flag['pole_d0']}→{flag['pole_d1']} "
                f"({round(flag['pole_lo'], 2)}→{round(flag['pole_hi'], 2)})，"
                f"旗面下降趋势线 @{tl_txt}；结构止损看该线下" + vol_note()
            )
            return pack(
                "flag_tl_break", 1, "breakout", "旗形下降趋势线突破(优先T1)", True, note, z,
            )

    # 下降趋势线突破（与旗形同族：都是斜线突破）。必须放在「回踩类」分支之前 ——
    # 2026-09-17 用户指正 TEM / ILMN：线已被收盘站上是**已发生的突破事实**，
    # 不该被「等回踩」的预案（大阳当日不追 / 调整未缩量 / 等回调整区）覆盖掉。
    # ILMN 9/15 RVOL=1.70 本已过量能闸门，却被「大阳当日不追」抢先返回，
    # 线突破根本没被评估 → 9/15 开盘 208.95 直接跳过整条旗面线（踏空）。
    # 有旗形时不用泛化 downtrend_tl_break 抢标（test_flag_blocks_generic_dtl 明确此规则）：
    # 旗形与斜线同族，旗形已在上面优先评估；此处只在「无旗形」时才由泛化斜线出手。
    # TEM 9/15 那种「旗面线已离价过远」的情形，由 9/10-9/11 的斜线预备突破单
    # 与 9/14 的旗形信号覆盖，不靠抢标解决。
    if flag is None:
        fresh_dtl = (
            declining and tl_now is not None and last_c is not None
            and last_c > tl_now and days_above_tl <= 3
        )
        if fresh_dtl and price_conf:
            z = zone_at_level(tl_now, atr_v, last_c, "下降趋势线突破(次优先T2)", ev, bars)
            z["anchor"] = "down_tl"
            tgt_lv = plat_p if plat_p is not None else r1p
            tgt = f"目标1先看平台沿 {round(tgt_lv, 2)}" if tgt_lv else "目标1看最近前高"
            note = f"次优先T2，试错仓。未过前高则{tgt}；过前高升级为平台突破" + vol_note()
            return pack(
                "downtrend_tl_break", 2, "breakout", "下降趋势线突破(次优先T2)", True, note, z,
            )

    line_wait_verdict = None
    line_wait_note = None
    if structure_ok and demand is not None:
        label = ANCHOR_LABEL.get(demand["anchor"], demand["anchor"])
        lv = round(demand["level"], 2)
        d_atr = demand["dist_atr"]
        # 加速线透明度：引擎跳过了 P0→P1 线（斜率超限），必须说明原因，
        # 否则用户会问「为什么没提那条趋势线」——2026-09-18 BE 的诉求点。
        _acc = ""
        if demand.get("tl_steep"):
            _s = demand.get("tl_slope_atr") or 0.0
            _acc = (
                f"；P0→P1 线斜率 {_s:.2f}×ATR/根（>{TL_SLOPE_MAX_ATR}，加速线）"
                f"外推会追上价格，已不作锚"
            )
        if d_atr < 0:
            line_wait_verdict = "已跌破该线"
            line_wait_note = f"{label}@{lv}，收盘已在线下 {abs(d_atr):.2f}×ATR，不买" + _acc
        elif d_atr <= 1.0:
            rec = bool(vol_shrink)
            note = f"沿线回踩{label}@{lv}（近12根触及{demand['hits']}次）"
            if not ma_anchor_trend_ok(bars, ev, demand):
                rec = False
                note += (
                    f"；短均线锚·趋势偏弱（regime={ev.get('regime')}，"
                    f"非continuation且收盘未站上SMA20），不做"
                )
            elif not vol_shrink:
                rec = False
                note += f"；量能偏大 RVOL={round(rvol, 2)}，等缩量尾盘"
            note += _acc
            bz_line["type"] = f"沿线回踩(优先T1)·{label}"
            return pack("line_pullback", 1, "pullback", "沿线回踩(优先T1)", rec, note, bz_line)
        elif d_atr > 2.0:
            line_wait_verdict = "主升延伸·等回踩"
            line_wait_note = (
                f"沿{label}主升，现价距线 {d_atr:.1f}×ATR（>2），不追。"
                f"等回踩 {lv} 或等下一次放量新高"
            )
        else:
            line_wait_verdict = "沿线靠近未到位"
            line_wait_note = (
                f"距{label} {d_atr:.1f}×ATR，未落入 "
                f"{bz_line.get('primary_lo')}-{bz_line.get('primary_hi')}"
            )

    if gate_imp and imp.get("state") != "no_yang":
        st = imp.get("state")
        y_lo = imp.get("yang_low")
        y_hi = imp.get("yang_high")
        y_d = imp.get("yang_d")
        floor = imp.get("floor") if imp.get("floor") is not None else y_lo
        yi_zi = bool(imp.get("yi_zi"))
        z = imp.get("zone") or _empty_zone()
        z["gate"] = gate_src
        z["state"] = st
        z["yang_i"] = imp.get("yang_i")
        if gate_src == "reversal_yang":
            z["relaxed"] = True
            z["relaxed_reason"] = (
                f"反转态（自 P1 曾创新低、c2=False）中 {y_d} 放量大阳收复 P1，"
                f"已按放宽门控放行"
            )
        # 不允许回退到大阳体：那会把「未走坏的有效性边界」印成买区，
        # 宽度可达 2.5×ATR，人无从下单。无买区就如实写 N/A。
        if z.get("primary_lo") is not None and z.get("primary_hi") is not None:
            zone_txt = f"{round(z['primary_lo'], 2)}-{round(z['primary_hi'], 2)}"
        else:
            zone_txt = "N/A"
        floor_txt = f"一字缺口下沿 {round(floor, 2)}" if yi_zi else f"大阳低点 {round(floor, 2)}"
        if st == "yang_today":
            return pack(
                "wait", None, "wait", "大阳当日不追", False,
                f"{y_d} 大阳，等缩到近期最低量、回踩进 {zone_txt} 再买，防守看{floor_txt}",
                z,
            )
        if st == "broke_yang_low":
            z["invalid"] = True
            z["invalid_reason"] = f"{y_d} 大阳后收盘跌破防守位 {round(floor, 2)}"
            # 作废态必须清空下单带：留着 primary_lo/primary_hi 会让消费方
            # （probe 打印、扫描表「买区」列）原样显示一个区间，看上去像挂单价位，
            # 而 verdict 其实是「作废」—— 这正是「看到买区、实际没有买点」的误判源。
            # invalidation（防守位）保留：次级观察位/复盘仍需知道防线在哪。
            z["primary_lo"] = None
            z["primary_hi"] = None
            z["in_zone"] = False
            return pack(
                "wait", None, "wait", "大阳低点已破·作废", False,
                f"{y_d} 大阳后收盘跌破防守位 {round(floor, 2)}，本笔大阳设置作废",
                z,
            )
        if st == "extended":
            return pack(
                "wait", None, "wait", "大阳后仍在延伸", False,
                f"{y_d} 大阳高 {round(y_hi, 2)}，现价尚未缩量回到调整区，不追",
                z,
            )
        if st == "no_shrink":
            return pack(
                "wait", None, "wait", "调整未缩量", False,
                f"{y_d} 大阳后仍在调整区，但量能未缩到这波调整的近期最低",
                z,
            )
        if st == "need_3d":
            return pack(
                "wait", None, "wait", "大阳后未满三日", False,
                f"{y_d} 大阳后不足 3 根，先看低点是否稳住、量是否缩到近期最低",
                z,
            )
        if st == "still_falling":
            return pack(
                "wait", None, "wait", "回踩仍在创新低", False,
                f"{y_d} 大阳后缩量了，但近 3 根仍在创新低，偏自由落体，等连续三日低点不再下移",
                z,
            )
        if st == "waiting_back":
            return pack(
                "wait", None, "wait", "等回调整区", False,
                f"等价格回到 {y_d} 买区 {zone_txt}，防守看{floor_txt}",
                z,
            )
        if st == "digest":
            gap_note = ""
            if z.get("has_gap") and not yi_zi:
                gap_note = (
                    f"；有跳空 {z.get('gap_lo')}-{z.get('gap_hi')}，"
                    f"买缺口上沿/大阳低点，不买回补缺口"
                )
            elif yi_zi:
                gap_note = f"；一字板，买回踩缺口，防守缺口下沿 {round(floor, 2)}"
            note = (
                f"大阳后缩量回踩：{y_d} 阳线 {round(y_lo, 2)}-{round(y_hi, 2)}（大阳体），"
                f"现价在买区 {zone_txt} 内、量已到近期最低，且近 3 根不再创新低。防守{floor_txt}。"
                f"次优先T2，试错仓"
                f"{gap_note}"
            )
            return pack(
                "impulse_pause", 2, "pullback", "大阳后缩量回踩(次优先T2)", True, note, z,
            )

    if line_wait_note:
        return pack(
            "wait", None, "wait", line_wait_verdict, False, line_wait_note, bz_line,
        )
    if not structure_ok:
        # 结构不成立（未站上 P1，或自 P1 曾创新低）→ 没有任何买法。
        # 此处原先不传 buy_zone，pack() 便回退到 bz_line（活平台沿线位），
        # 于是「等待」的票照样打印出一个可挂单区间 —— 与 note「未形成任何买法」
        # 自相矛盾，也是「看到买区、其实没有买点」的误判源。
        # 与下方「近端找不到线」分支保持同口径：无买法就给空区。
        return pack(
            "wait", None, "wait", "等待", False,
            "未形成平台/W底/旗形突破、沿线回踩、大阳后缩量回踩或下降趋势线突破",
            _empty_zone(),
        )
    return pack(
        "wait", None, "wait", "等待", False,
        "近端找不到被尊重的线，也没有大阳后缩量买点", _empty_zone(),
    )


def _t0_takeover(result, t0):
    """★ T0 接管（2026-09-20）：当日原买法为 wait / 不推荐 / 「大阳后」家族时，
    由 T0「均线收复+过昨高」接管为首选入口。

    用户定级：T0 优先于 T1 买突破（盈亏比最高）。但 T0 是**执行方案**不是形态，
    故只在原买法「本身没给出可执行买点」时才接管 mode —— 不抢平台突破/W底/旗形
    等 recommend=True 的强形态的标（抢标会破坏既有判定，见 test_review_fixes 回归）。

    为何「大阳后」家族必须让位：`impulse_pause`/「大阳当日不追」/「大阳后未满三日」
    都是 T1 的纪律（等缩量回踩），而 T0 要的正是「大阳次日过昨高即买」——
    两者对同一根大阳给出相反指令，T0 优先级更高故须覆盖。
    实证：赛分 9-15（大阳后未满三日）、纳微 9-08（大阳当日不追），若不让位则
    T0 全部被压成 wait，用户举的两个案例都出不来。
    """
    z = {
        "level": t0["trigger"],
        "primary_lo": round(t0["hard_stop"], 2),
        "primary_hi": t0["trigger"],
        "in_zone": True,
        "type": f"T0·均线收复+过昨高（{t0['grade']}·距墙{t0['dist_to_wall_pct']}%）",
        "anchor": "ma_reclaim",
        "counter": t0["resistance"],
        "struct_stop": t0["hard_stop"],
        "struct_anchor": f"{t0['stop_anchor']}@{t0['hard_stop']}（收盘破）",
        "hard_stop": t0["hard_stop"],
        "hard_anchor": t0["stop_anchor"],
        "invalidation": t0["hard_stop"],
        "hard": t0["hard_stop"],
        # ★ 2026-09-20 口径同步：止损锚已从「MA10/MA20 排除 MA5」放宽为
        #   「{MA5, MA10, MA20, 实体中点, D0.low} 中 < 触发价且最近者」。
        #   此处原为旧口径文本，会让渲染层与 t0["stop_anchor"] 自相矛盾（正帆/成都实测踩到）。
        "stop_basis": (
            "{MA5, MA10, MA20, 突破日均K 实体中点, 突破日低点} 中"
            "「< 触发价且离触发价最近」的一条"
            "（K 线锚取「最近的突破均线的日K」，非最后一根）"
        ),
        "struct_exec": STRUCT_EXEC,
    }
    # ★ 保留被接管路径的元数据（不丢信息）：原「反转态大阳」「大阳后未满三日」等
    #   判定仍是事实，只是执行方案换成 T0。report/scanner 若按旧 key 读取不会 KeyError。
    _prev_z = result.get("buy_zone") or {}
    _prev_mode = result.get("mode")
    _prev_verdict = result.get("verdict")
    result["mode"] = "ma_reclaim_break"
    result["priority"] = 1
    result["setup"] = "breakout"
    result["path"] = "B"
    result["verdict"] = (f"T0·均线收复+过昨高（贴墙·距墙{t0['dist_to_wall_pct']}%）"
                         if t0["grade"] == "贴墙"
                         else f"T0·均线收复+过昨高（半路·距墙{t0['dist_to_wall_pct']}%）")
    result["recommend"] = True
    result["prev_verdict"] = _prev_verdict
    result["t0_superseded_mode"] = _prev_mode
    result["note"] = t0["note"]
    result["exec"] = t0["exec"]
    result["alt_tail_entry"] = t0["alt_tail_entry"]
    result["tier"] = "T0"
    result["grade"] = t0["grade"]
    result["buy_zone"] = z
    # ★ 同步覆盖 stop_plan 对象：渲染层（watch_cn / scan_all 报表）读的是
    #   plan["stop_plan"]，只改 buy_zone 会让「止损」那一行仍显示 stop_plan 按
    #   未知 mode 算出的锚（正帆 9-18 实测：T0 说 MA20 65.5，渲染层却显示 63.40·锚MA5）。
    result["stop_plan"] = {
        "struct": t0["hard_stop"],
        "struct_anchor": f"{t0['stop_anchor']}@{t0['hard_stop']}（收盘破）",
        "hard": t0["hard_stop"],
        "hard_anchor": t0["stop_anchor"],
        "trigger": None,
        "struct_exec": STRUCT_EXEC,
        "hard_exec": HARD_EXEC,
        "hard_dist_atr": t0.get("risk_atr"),
        "hard_noise": False,
        "stop_basis": z["stop_basis"],
        "t0": True,
    }
    # 把被接管路径的 gate/relaxed/state 透传到 T0 买区，供测试与复盘追溯
    for _k in ("gate", "relaxed", "relaxed_reason", "state", "yang_i", "gate_src"):
        if _prev_z.get(_k) is not None:
            z[f"prev_{_k}"] = _prev_z[_k]
    result.pop("stop_above_price", None)
    result.pop("stop_warning", None)
    return result


def buy_zone(ev, bars):
    """兼容扫描器：买区由 plan_entry 的活需求给出，禁止硬编码均线底座。"""
    if not bars:
        return _empty_zone()
    return plan_entry(bars, ev)["buy_zone"]


def classify_regime(last_c, ema10, sma20, sma50, sma20_up, hh, hl, c2, p1_px):
    # hh/hl 保留参数兼容扫描器旧调用，不参与门控
    _ = (hh, hl)
    above20 = sma20 is not None and last_c > sma20
    bull_stack = (
        ema10 is not None and sma20 is not None and sma50 is not None
        and ema10 > sma20 > sma50
    )
    up_ma = bull_stack or bool(sma20_up)
    structure_ok = (p1_px is not None and last_c > p1_px) and c2
    if above20 and up_ma and structure_ok:
        return "continuation"
    if (sma20 is not None and last_c < sma20) or (not c2):
        return "reversal"
    return "mixed"


def build_ev(bars, drop_live=False, ticker=None):
    """从日线构造 plan_entry 所需 ev（扫描器与 evaluate 共用）。

    drop_live=True 时丢弃今日未收盘末根。
    无有效活平台沿时不回落旧 R1（避坑）。
    ticker：可选，标的代码（如 '300171'）。用于判断板块涨跌幅上限
            （主板 10% / 创业板·科创板 20%），T0 的阻力位距离闸门需要它。
            不传则由 T0 内部按历史振幅推断（不可靠，仅为兼容旧调用）。
    """
    if drop_live and bars and is_live_bar(bars):
        bars = bars[:-1]
    if not bars or len(bars) < 30:
        return None, bars, {"reason": "K线不足"}
    Hs, Ls = pivots(bars, w=3)
    if len(Ls) < 2 or len(Hs) < 2:
        return None, bars, {"reason": "枢轴点不足，无法判定"}
    P1 = Ls[-1]
    P0 = next((p for p in reversed(Ls[:-1]) if P1[0] - p[0] >= 3), None)
    if P0 is None:
        return None, bars, {"reason": "摆动低点过于密集，结构不可判"}
    R1_cands = [h for h in Hs if P0[0] < h[0] < P1[0]]
    R1 = max(R1_cands, key=lambda x: x[1]) if R1_cands else None
    Hs_before = [h for h in Hs if h[0] < P1[0]]
    h_a = Hs_before[-1] if Hs_before else None
    h_b = Hs_before[-2] if len(Hs_before) >= 2 else None
    last_i = len(bars) - 1
    last_c = bars[-1]["c"]
    closes = [b["c"] for b in bars]
    vols = [float(b.get("v") or 0) for b in bars]
    atr_v = atr14(bars)
    ema10 = ema(closes, 10)
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    sma20_prev = sma(closes[:-5], 20) if len(closes) >= 25 else None
    sma20_up = sma20 is not None and sma20_prev is not None and sma20 > sma20_prev
    hh = Hs[-1][1] > Hs[-2][1]
    hl = P1[1] > P0[1]
    vol_base = sma(vols[:-1], 20) if len(vols) > 20 else None
    last_v = vols[-1] if vols else 0
    rvol20 = (last_v / vol_base) if vol_base else None
    p1_px = P1[1]
    lows_since_p1 = [b["l"] for b in bars[P1[0]:]]
    c2 = min(lows_since_p1) >= p1_px - 1e-9
    c3 = (R1 is not None) and (last_c > R1[1])
    c1 = False
    tl_note = "N/A"
    # 下降趋势线：条件1（站上下降趋势线）与 downtrend_tl_break 共用此来源。
    # 旧口径取「P1 之前两个枢轴高」：TEM 2026-09 取到 08-12@57.03 与 08-21@72.96
    # —— 那是一条**上升**线，外推到末根 97.99→111.65（当期价格仅 60-70），
    # 于是 declining=False、c1 永远 False。优先用近端下移局部高点检出
    # （见 detect_down_trendline），检出失败才退回旧口径。
    dline = detect_down_trendline(bars, atr_v)
    if dline is not None:
        tl_at_last = dline["tl_now"]
        c1 = last_c > tl_at_last
        tl_note = (
            f"下降趋势线@末根≈{round(tl_at_last, 2)}"
            f"（连 {dline['pb']['d']}高{dline['pb']['price']:.2f}→"
            f"{dline['pa']['d']}高{dline['pa']['price']:.2f}，"
            f"触及{dline['touches']}次/线上收盘{dline['viol']}根）"
        )
    elif h_a and h_b:
        tl_at_last = line_val(h_b, h_a, last_i)
        c1 = last_c > tl_at_last
        tl_note = (
            f"下跌段趋势线@末根≈{round(tl_at_last, 2)}"
            f"（连 {bars[h_b[0]]['d']}高{bars[h_b[0]]['h']:.2f}→{bars[h_a[0]]['d']}高{h_a[1]:.2f}）"
        )
    regime = classify_regime(last_c, ema10, sma20, sma50, sma20_up, hh, hl, c2, p1_px)
    passed = sum([c1, c2, c3])
    plat = living_platform(bars, Hs, atr_v)
    w_bottom = detect_w_bottom(bars, Hs, Ls, atr_v)
    bull_flag = detect_bull_flag(bars, Hs, atr_v)
    ev = {
        "regime": regime,
        "passed": passed,
        "rvol20": rvol20,
        "ticker": ticker,          # T0 判定板块涨跌幅上限用（主板10%/双创20%）
        "cond3_break_prior_high": c3,
        "c2": c2,
        "cond2_no_new_low": c2,
        "P0": {"i": P0[0], "price": P0[1]},
        "P1": {"i": P1[0], "price": P1[1]},
        "R1": {"i": R1[0], "price": R1[1]} if R1 else None,
        "platform": plat,
        "w_bottom": w_bottom,
        "bull_flag": bull_flag,
        "down_tl": (
            {
                "b": {"i": dline["pb"]["i"], "price": dline["pb"]["price"]},
                "a": {"i": dline["pa"]["i"], "price": dline["pa"]["price"]},
                "src": "local_highs",
                "touches": dline["touches"],
                "viol": dline["viol"],
            }
            if dline is not None else (
                {
                    "b": {"i": h_b[0], "price": h_b[1]},
                    "a": {"i": h_a[0], "price": h_a[1]},
                    "src": "pivots_before_p1",
                } if (h_a and h_b) else None
            )
        ),
    }
    meta = {
        "Hs": Hs, "Ls": Ls, "P0": P0, "P1": P1, "R1": R1,
        "h_a": h_a, "h_b": h_b, "last_c": last_c, "last_i": last_i,
        "ema10": ema10, "sma20": sma20, "sma50": sma50, "sma20_up": sma20_up,
        "hh": hh, "hl": hl, "rvol20": rvol20, "atr_v": atr_v,
        "c1": c1, "c2": c2, "c3": c3, "tl_note": tl_note,
        "regime": regime, "passed": passed,
        "plat": plat, "w_bottom": w_bottom, "bull_flag": bull_flag,
    }
    return ev, bars, meta


def evaluate(sym, data_file=None, eod=False):
    bars, last_q, qmeta = get_bars(sym, data_file)
    _us = qmeta or {}                       # 美股：session/as_of/prev_close
    sym_code = str(sym).split(".")[0] if sym else ""
    market = "ASH" if (is_ash(sym_code) or (data_file and sym_code.isdigit())) else "US"
    # 盘中：先把今日未收盘 bar 并入再判结构。不并的话突破当天引擎仍按昨收算，
    # 模式/买区整体滞后一天 —— 就是「已破平台却只给回踩价」的成因（INTC 9/17）。
    bars_closed = bars
    bars, live_bar = (bars, None) if eod else merge_intraday_bar(
        sym, bars, qmeta, market)
    stale_plan = None
    if live_bar is not None:
        ev_stale, _, _ = build_ev(bars_closed, ticker=sym_code)
        if ev_stale is not None:
            stale_plan = plan_entry(bars_closed, ev_stale)
    # 盘中未收盘：默认禁止 recommend（量能不可判）；--eod 丢弃末根
    live = is_live_bar(bars, market=market)
    drop_live = bool(eod or False)
    ev, bars, meta = build_ev(bars, drop_live=drop_live, ticker=sym_code)
    if ev is None:
        return {"sym": sym, "verdict": "N/A", "reason": meta.get("reason", "无法判定"), "last": last_q}

    if live and not drop_live and live_bar is None:
        # 仍算出结构，但强制不买。注意：能吃上真实盘中 OHLC 时（live_bar 非空）
        # 不走这里 —— 那条路径给得出可执行结构，见下方「盘中口径」段。
        plan = plan_entry(bars, ev)
        plan["recommend"] = False
        plan["verdict"] = "盘中数据未收盘，量能不可判"
        plan["note"] = (
            "末根为未走完的当日 K 线：缩量/RVOL 全部不可信。"
            "请在 14:57 后重跑，或加 --eod 只吃到昨日收盘。"
        )
        out_live = {
            "sym": sym,
            "last": round(bars[-1]["c"], 2),
            "last_date": bars[-1]["d"],
            "intraday": True,
            "mode": plan["mode"],
            "verdict": plan["verdict"],
            "recommend": False,
            "note": plan["note"],
            "buy_zone": plan["buy_zone"],
            "stop_plan": plan.get("stop_plan"),
            "targets": plan.get("targets"),
            "spot_quote": last_q,
            "session": _us.get("session") or None,
            "as_of": _us.get("as_of"),
        }
        return out_live

    plan = plan_entry(bars, ev)
    setup = plan["setup"]
    recommend = plan["recommend"]
    note = plan["note"]
    verdict = plan["verdict"]
    plat = meta["plat"]
    last_c = meta["last_c"]
    if (
        plat and plat.get("kind") == "pressing"
        and last_c is not None and last_c <= plat["price"]
        and plan["mode"] == "wait"
    ):
        prefix = f"活平台沿 {round(plat['price'], 2)} 未破"
        if prefix not in (note or ""):
            note = f"{prefix}。{note}" if note else prefix
    if plat is None:
        extra = "无有效活平台沿（不回落旧 R1 作突破位）"
        if extra not in (note or ""):
            note = f"{extra}。{note}" if note else extra

    def rnd(v):
        if v is None:
            return None
        return round(v, 2)

    P0, P1, R1 = meta["P0"], meta["P1"], meta["R1"]
    h_a, h_b = meta["h_a"], meta["h_b"]
    w_bottom, bull_flag = meta["w_bottom"], meta["bull_flag"]
    out = {
        "sym": sym,
        "last": rnd(last_c),
        "last_date": bars[-1]["d"],
        "spot_quote": last_q,
        "session": _us.get("session") or None,
        "as_of": _us.get("as_of"),
        "P0": {"i": P0[0], "d": bars[P0[0]]["d"], "price": rnd(P0[1])},
        "P1": {"i": P1[0], "d": bars[P1[0]]["d"], "price": rnd(P1[1])},
        "regime": meta["regime"],
        "setup": setup,
        "path": plan["path"],
        "mode": plan["mode"],
        "priority": plan["priority"],
        "hh": meta["hh"],
        "hl": meta["hl"],
        "ema10": rnd(meta["ema10"]),
        "sma20": rnd(meta["sma20"]),
        "sma50": rnd(meta["sma50"]),
        "sma20_up": meta["sma20_up"],
        "rvol20": rnd(meta["rvol20"]),
        "atr14": rnd(meta["atr_v"]),
        "days_above_r1": plan["days_above_r1"],
        "days_above_platform": plan["days_above_platform"],
        "cond1_trendline_break": meta["c1"],
        "cond1_note": meta["tl_note"],
        "cond2_no_new_low": meta["c2"],
        "cond3_break_prior_high": meta["c3"],
        "passed": meta["passed"],
        "verdict": verdict,
        "recommend": recommend,
        "note": note,
        "intraday": False,
        "stop_plan": plan.get("stop_plan"),
        "targets": plan.get("targets"),
    }
    if R1:
        out["R1"] = {"i": R1[0], "d": bars[R1[0]]["d"], "price": rnd(R1[1])}
    if plat:
        out["platform"] = {
            "i": plat["i"],
            "d": bars[plat["i"]]["d"],
            "price": rnd(plat["price"]),
            "kind": plat.get("kind"),
            "days_above": plat.get("days_above"),
        }
    if w_bottom:
        out["w_bottom"] = {
            "l1": {**w_bottom["l1"], "price": rnd(w_bottom["l1"]["price"])},
            "l2": {**w_bottom["l2"], "price": rnd(w_bottom["l2"]["price"])},
            "neckline": rnd(w_bottom["neckline"]),
            "neck_d": w_bottom.get("neck_d"),
            "span": w_bottom.get("span"),
        }
    if bull_flag:
        out["bull_flag"] = {
            "pole_d0": bull_flag.get("pole_d0"),
            "pole_d1": bull_flag.get("pole_d1"),
            "pole_lo": rnd(bull_flag.get("pole_lo")),
            "pole_hi": rnd(bull_flag.get("pole_hi")),
            "tl_now": rnd(bull_flag.get("tl_now")),
            "days_above_tl": bull_flag.get("days_above_tl"),
            "pb": bull_flag.get("pb"),
            "pa": bull_flag.get("pa"),
        }
    if h_a and h_b:
        out["trendline_at_last"] = rnd(line_val(h_b, h_a, meta["last_i"]))
    out["buy_zone"] = plan["buy_zone"]

    # ---- 盘中（未收盘）口径 -------------------------------------------------
    # live_bar 非空 = 今日未收盘 K 线已并入结构。买区/模式随之刷到突破位，
    # 不再是昨收口径的回踩位；但仍要声明「盘中价非收盘价」并给出尾盘复核时点。
    if live_bar is not None:
        _clk = us_session_clock(_us)[1]
        _tail = _clk is not None and _clk >= 15 * 60 + 45
        out["intraday"] = True
        out["intraday_bar"] = {
            "d": live_bar["d"], "o": rnd(live_bar["o"]), "h": rnd(live_bar["h"]),
            "l": rnd(live_bar["l"]), "c": rnd(live_bar["c"]), "v": live_bar.get("v"),
        }
        out["confirm_at"] = "尾盘（北京时间 03:45–04:00 / ET 15:45–16:00）"
        out["verdict"] = "盘中·" + str(out.get("verdict") or "")
        out["note"] = (
            f"【盘中口径·未收盘】{_us.get('as_of') or ''} 现价 {rnd(live_bar['c'])}"
            f"（今日 {rnd(live_bar['o'])}/{rnd(live_bar['h'])}/{rnd(live_bar['l'])}）；"
            f"已并入今日未收盘 K 线，结构/买区按盘中刷新（非昨收口径）。"
            + ("已到尾盘时段，可按现价定夺。" if _tail else
               "盘中价非收盘价：以尾盘复核为准，收盘仍成立才动手。")
            + "今日量为盘中累计按已走时段折算的预计全日量（量比口径），含外推成分。"
            f" ｜ {note or ''}"
        )

    _pbsrc = (stale_plan or plan).get("pre_breakout")
    if _pbsrc:
        pb = dict(_pbsrc)
        if live_bar is not None:
            _trig = pb.get("trigger")
            _h, _o = live_bar.get("h"), live_bar.get("o")
            if _trig is not None and _h is not None and _h >= _trig:
                pb["triggered"] = True
                pb["fill_px"] = rnd(_o if (_o is not None and _o >= _trig) else _trig)
                pb["status"] = (
                    f"已触发（今日最高 {rnd(_h)} ≥ 触发价 {_trig}）→ 该埋伏单已成交，"
                    f"按计划持有，不再回头等回踩")
            elif _trig is not None:
                pb["triggered"] = False
                pb["status"] = f"未触发（今日最高 {rnd(_h)} < 触发价 {_trig}）→ 继续挂着"
        out["pre_breakout"] = pb
        _t = pb.get("trigger")
        if pb.get("triggered"):
            out["verdict"] = f"命中预案单 {pb.get('fill_px')}｜{out.get('verdict')}"
        elif _t is not None and "预备突破单" not in str(out.get("verdict") or ""):
            out["verdict"] = f"预备突破单 {_t}｜{out.get('verdict')}"
    return out


def _market_dir_for_sym(sym):
    """按标的判定市场子目录：首字符为数字(含北交所/科创板/主板6位代码)→out_cn，字母→out_us。"""
    if sym is None:
        return None
    s = str(sym).strip()
    if not s:
        return None
    return "out_cn" if s[0].isdigit() else "out_us"


def resolve_out_path(out_path, syms):
    """若 --out 给的是裸文件名(无目录分隔)，按市场自动重定向到 out_cn/ 或 out_us/；
    若已带目录或无法判定市场，则保持原样。"""
    if not out_path:
        return out_path
    if os.path.basename(out_path) != out_path:
        return out_path  # 已含目录，不重定向
    # 裸文件名优先从文件名解析 sym（约定 out_<SYM>[_eod].json）
    base = out_path
    name = os.path.splitext(base)[0]
    cand = name[len("out_"):] if name.startswith("out_") else name
    for suf in ("_eod2", "_eod"):
        if cand.endswith(suf):
            cand = cand[: -len(suf)]
            break
    mdir = _market_dir_for_sym(cand)
    if mdir is None and syms:
        mdir = _market_dir_for_sym(syms[0])  # 回退：用首个标的判定
    if mdir is None:
        return out_path
    return os.path.join(mdir, base)


if __name__ == "__main__":
    args = sys.argv[1:]
    syms, data_map = [], {}
    eod = False
    out_path = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--data":
            spec = args[i + 1]
            i += 2
            if "=" in spec:
                k, v = spec.split("=", 1)
                data_map[k.upper()] = v
            else:
                if syms:
                    data_map[syms[-1].upper()] = spec
            continue
        if a == "--eod":
            eod = True
            i += 1
            continue
        if a == "--out":
            out_path = args[i + 1]
            i += 2
            continue
        syms.append(a)
        i += 1
    if not syms:
        syms = ["CF", "LLY", "MU", "TEM", "RVMD"]

    res = []
    for s in syms:
        df = data_map.get(s.upper())
        try:
            r = evaluate(s, df, eod=eod)
        except Exception as e:
            r = {"sym": s, "verdict": "ERR", "reason": f"{type(e).__name__}: {e}"}
        res.append(r)
        print(f"=== {s} ===")
        for k, v in r.items():
            if k in ("buy_zone", "stop_plan", "targets", "w_bottom", "bull_flag", "P0", "P1", "R1", "platform", "pre_breakout"):
                print(f"  {k}: {v}")
            elif k not in ("note",) and not isinstance(v, (dict, list)):
                print(f"  {k}: {v}")
        if r.get("note"):
            print(f"  note: {r['note']}")
        print(f"  verdict: {r.get('verdict')}")

    out_path = resolve_out_path(out_path, syms)
    if out_path:
        mdir = os.path.dirname(out_path)
        if mdir:
            os.makedirs(mdir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(res if len(res) > 1 else res[0], f, ensure_ascii=False, indent=2, default=str)
        print(f"-> {out_path} written")
