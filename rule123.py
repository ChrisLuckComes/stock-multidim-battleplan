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
import urllib.request, json, sys, datetime, re, time

HEADERS = {"User-Agent": "Mozilla/5.0"}


def is_ash(ticker: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", ticker.strip()))


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
    kd = fetch_json(url).get("data", {})
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
        q = fetch_json(
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


def get_bars(sym, data_file=None):
    if data_file:
        with open(data_file, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("bars", []), d.get("spot")
    if is_ash(sym):
        return bars_from_em(secid_of(sym))
    return bars_from_yahoo(sym)


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

_ASH_SESSIONS = ((9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60))


def is_live_bar(bars, market="ASH", now=None):
    """末根是否为今日未收盘 K 线（盘中量能/RVOL 不可信）。"""
    if not bars:
        return False
    now = now or datetime.datetime.now()
    if str(bars[-1]["d"])[:10] != now.strftime("%Y-%m-%d"):
        return False
    if market != "ASH":
        # 美股：简单按本地日历日未过 16:00 ET 近似；无时区库时只标当日末根
        return True
    t = now.hour * 60 + now.minute
    return any(a <= t < b for a, b in _ASH_SESSIONS)


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
    """现价高出买区上沿 >limit×ATR → 不追。"""
    if not atr_v or last_c is None or not z:
        return False
    hi = z.get("primary_hi")
    if hi is None:
        return False
    return (last_c - hi) / atr_v > limit


def living_demand(bars, ev):
    """沿线回踩的线：默认 = P0→P1 上升趋势线。
    仅当很明显贴着均线走（近 8 根至少 4 次触及）且趋势线离现价 >1.5×ATR 时，才改用该均线。
    不用 VWAP/R1 抢默认买区。
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
    if (
        p0i is not None and p1i is not None
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

    # 默认趋势线；均线只在「离趋势线很远 + 明显贴轨」时覆盖
    if walk is not None and (tl_res is None or tl_res["dist_atr"] > 1.5):
        return walk
    if tl_res is not None:
        return tl_res
    return walk


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
    """大阳后缩量回踩的升势过滤：收盘须在 P1 之上且未跌破 P0→P1 线。"""
    if p1p is None or last_c is None or last_c <= p1p or not c2:
        return False
    tl = rising_tl_level(bars, ev)
    if tl is not None and last_c < tl:
        return False
    return True


def is_yang_bar(bar, atr_v, prev=None):
    body = bar["c"] - bar["o"]
    rng = bar["h"] - bar["l"]
    # 一字涨停：实体为 0，但相对前收跳空，必须算大阳
    if body <= 0:
        if prev is None or not prev.get("c"):
            return False
        up = bar["c"] >= prev["c"] * 1.03
        tiny = rng <= max(bar["c"] * 0.003, 0.15 * (atr_v or bar["c"] * 0.01))
        return bool(up and tiny)
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
    if yang_i == n - 1:
        return {
            "state": "yang_today",
            "yang_d": y["d"],
            "yang_low": y_lo,
            "yang_high": y_hi,
            "floor": floor,
            "yi_zi": fl["yi_zi"],
        }
    lows_after = [bars[j]["c"] for j in range(yang_i + 1, n)]
    min_after = min(lows_after)
    pad = 0.15 * atr_v if atr_v else floor * 0.005
    if min_after < floor - pad:
        return {
            "state": "broke_yang_low",
            "yang_d": y["d"],
            "yang_low": y_lo,
            "yang_high": y_hi,
            "floor": floor,
            "yi_zi": fl["yi_zi"],
        }
    shrink = vol_at_recent_low(bars, yang_i)
    held = held_lows_3d(bars, yang_i, atr_v)
    days_after = n - 1 - yang_i
    in_range = zone_lo <= last_c <= (zone_hi + pad)
    above = last_c > zone_hi + (0.5 * atr_v if atr_v else 0)
    z = _empty_zone()
    z.update({
        "type": "大阳后缩量回踩(次优先T2)",
        "anchor": "yang_gap" if fl["yi_zi"] else "yang_digest",
        "level": round(floor, 2),
        "primary_lo": round(zone_lo, 2),
        "primary_hi": round(zone_hi, 2),
        "in_zone": bool(in_range),
        "dist_atr": round((last_c - zone_hi) / atr_v, 2) if atr_v else None,
        "extended": bool(above),
        "invalidation": round(floor - pad, 2),
        "yang_d": y["d"],
        "hits": n - 1 - yang_i,
        "yi_zi": fl["yi_zi"],
        "has_gap": fl["has_gap"],
        "gap_lo": round(fl["gap_lo"], 2) if fl["gap_lo"] is not None else None,
        "gap_hi": round(fl["gap_hi"], 2) if fl["gap_hi"] is not None else None,
        "held_3d": held,
    })
    extra = {
        "yang_d": y["d"],
        "yang_low": y_lo,
        "yang_high": y_hi,
        "floor": floor,
        "yi_zi": fl["yi_zi"],
    }
    if above:
        return {"state": "extended", "zone": z, **extra}
    if not shrink:
        return {"state": "no_shrink", "zone": z, **extra}
    if in_range:
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
        "primary_lo": None,
        "primary_hi": None,
        "in_zone": False,
        "dist_atr": None,
        "hits": 0,
        "extended": False,
        "invalidation": None,
        "vwap5": None,
        "ma5": None,
        "fail_lo": None,
        "fail_hi": None,
    }


def zone_from_demand(demand, bars, ev):
    """沿线回踩买区：半宽与 in_zone 门槛统一为 1.0×ATR（与 SKILL 贴线触发一致）。"""
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
    lo = min(demand.get("cluster_lo", level), level) - pad
    hi = max(demand.get("cluster_hi", level), level) + pad
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
    if len(flag_hs) < 2:
        # 枢轴不够时，用旗杆后分段最高点近似
        seg = bars[pe + 1:]
        if len(seg) < 4:
            return None
        mid = pe + 1 + len(seg) // 2
        h1 = max(range(pe + 1, mid + 1), key=lambda i: bars[i]["h"])
        h2 = max(range(mid, n), key=lambda i: bars[i]["h"])
        if h2 <= h1 or bars[h2]["h"] >= bars[h1]["h"]:
            return None
        flag_hs = [(h1, bars[h1]["h"]), (h2, bars[h2]["h"])]
    # 取最近两个下降高点
    pb = pa = None
    for k in range(len(flag_hs) - 1, 0, -1):
        i2, p2 = flag_hs[k]
        i1, p1 = flag_hs[k - 1]
        if p2 < p1 and i2 > i1:
            pb, pa = (i1, p1), (i2, p2)
            break
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


def zone_at_level(level, atr_v, last_c, kind, ev, bars):
    """突破类买区：半宽 0.35×ATR；in_zone/dist_atr/extended 按现价真算。"""
    z = _empty_zone()
    closes = [b["c"] for b in bars]
    v5 = typical_vwap(bars, 5)
    z["vwap5"] = round(v5, 2) if v5 is not None else None
    ma5 = sma(closes, 5)
    z["ma5"] = round(ma5, 2) if ma5 is not None else None
    if level is None:
        return z
    pad = 0.35 * atr_v if atr_v else level * 0.005
    lo, hi = level - pad, level + pad
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
        "primary_lo": round(lo, 2),
        "primary_hi": round(hi, 2),
        "dist_atr": round(dist, 2) if dist is not None else None,
        "in_zone": bool(dist is not None and -0.35 <= dist <= 1.0),
        "extended": bool(dist is not None and dist > 2.0),
        # 结构位=突破位本身；硬止损由 stop_plan 另给
        "invalidation": round(level, 2),
    })
    return z


HARD_STOP_TRIGGER = (
    "开盘已在硬止损下→开盘走；盘中破后3分钟仍在下或分钟线新低→市价走；"
    "1–2分钟收回且非放量加速阴→当毛刺"
)


def stop_plan(bars, mode, z, atr_v):
    """两档止损：结构（收盘破）+ 硬止损（锚三选一 − 0.10×ATR）。硬止损必须低于买区下沿。"""
    if not bars or not atr_v or not z or z.get("level") is None:
        return None
    y = bars[-1]
    body, rng = abs(y["c"] - y["o"]), y["h"] - y["l"]
    long_yang = (
        y["c"] > y["o"]
        and (body >= 0.8 * atr_v or rng >= 1.0 * atr_v or (y["o"] and body / y["o"] >= 0.03))
    )
    level = z["level"]
    buy_lo = z.get("primary_lo") if z.get("primary_lo") is not None else level
    gap = 0.10 * atr_v

    def _ensure_below_buy(hard, hard_anchor, fallback_anchor_px, fallback_name):
        if hard >= buy_lo:
            return round(fallback_anchor_px - gap, 2), fallback_name
        return round(hard, 2), hard_anchor

    if mode == "line_pullback":
        # 结构锚默认 MA5；贴轨例外用已选均线 level
        if z.get("anchor") in ("hl_trendline", None):
            anchor_px = z.get("ma5") if z.get("ma5") is not None else level
            struct_name, hard_name = "MA5(收盘破)", "MA5"
        else:
            anchor_px = level
            struct_name = f"{ANCHOR_LABEL.get(z.get('anchor'), z.get('anchor'))}(收盘破)"
            hard_name = ANCHOR_LABEL.get(z.get("anchor"), z.get("anchor"))
        hard = anchor_px - gap
        hard, hard_name = _ensure_below_buy(hard, hard_name, y["l"], "阳线下沿")
        return {
            "struct_anchor": struct_name,
            "struct": round(anchor_px, 2),
            "hard_anchor": hard_name,
            "hard": hard,
            "trigger": HARD_STOP_TRIGGER,
        }

    if mode == "impulse_pause":
        floor = level
        if z.get("yi_zi"):
            return {
                "struct_anchor": "缺口下沿/前收(收盘破)",
                "struct": round(floor, 2),
                "hard_anchor": "缺口下沿",
                "hard": round(floor - gap, 2),
                "trigger": HARD_STOP_TRIGGER,
            }
        hard = floor - gap
        return {
            "struct_anchor": "大阳低点(收盘破)",
            "struct": round(floor, 2),
            "hard_anchor": "阳线下沿",
            "hard": round(hard, 2),
            "trigger": HARD_STOP_TRIGGER,
        }

    # 平台 / W底 / 旗形 / 下降趋势线突破
    struct = round(level, 2)
    struct_name = f"{ANCHOR_LABEL.get(z.get('anchor'), z.get('anchor') or '突破位')}@{struct}(收盘破)"
    if long_yang:
        mid = (y["h"] + y["l"]) / 2.0
        hard, hard_name = _ensure_below_buy(mid - gap, "大阳中点", y["l"], "阳线下沿")
    else:
        hard, hard_name = _ensure_below_buy(y["l"] - gap, "突破阳下沿", y["l"], "阳线下沿")
    return {
        "struct_anchor": struct_name,
        "struct": struct,
        "hard_anchor": hard_name,
        "hard": hard,
        "trigger": HARD_STOP_TRIGGER,
    }


def targets(bars, mode, z, atr_v, entry, Hs):
    """目标1=最近未破摆动高/测量涨幅；目标2=区间更高阻力。附 rr_target1。"""
    if entry is None or not atr_v:
        return None
    resist = sorted({h for i, h in Hs if h > entry + 0.05 * atr_v})
    t1 = resist[0] if resist else round(entry + 2.0 * atr_v, 2)
    # 测量涨幅：平台/颈线突破用买区半高近似
    if mode in ("platform_break", "w_bottom_break") and z and z.get("level") is not None:
        measured = round(z["level"] + max(atr_v * 2.0, (z.get("primary_hi") or z["level"]) - (z.get("primary_lo") or z["level"])), 2)
        if measured > entry:
            t1 = min(t1, measured) if resist else measured
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
    tg = targets(bars, mode, z, atr_v, last_c, Hs or []) if mode != "wait" else None
    if tg:
        z["target1"] = tg["target1"]
        z["target2"] = tg["target2"]
        z["rr_target1"] = tg["rr_target1"]
    plan["buy_zone"] = z
    plan["stop_plan"] = sp
    plan["targets"] = tg
    return plan


def plan_entry(bars, ev):
    """当日只给一种 mode（含 W底/旗形突破）。"""
    last_c = bars[-1]["c"] if bars else None
    atr_v = atr14(bars)
    rvol = ev.get("rvol20")
    vol_ok = rvol is None or rvol > 1.5
    vol_shrink = rvol is None or rvol <= 1.2
    r1p = _px(ev.get("R1"))[1]
    plat = ev.get("platform") or ev.get("R1")
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
                f"收盘 {last_c:.2f} 高出突破位 {lv} 已 {d:.1f}×ATR（买区上沿 {hi}），"
                f"不追；等回踩 {hi} 一带或等新一轮缩量再站上"
            )
            mode, priority, setup, verdict, recommend, path = (
                "wait", None, "wait", "突破已延伸·等回踩", False, "wait",
            )
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
        return attach_stops_targets(result, bars, atr_v, Hs_all)

    plat_txt = f"{round(plat_p, 2)}" if plat_p is not None else "N/A"
    fresh_plat = plat_p is not None and last_c is not None and last_c > plat_p and n_above <= 3
    if fresh_plat and vol_ok:
        note = "过平台沿但无量能数据，突破确认打折" if rvol is None else ""
        if declining and days_above_tl <= 3:
            note = (note + "；" if note else "") + "下降趋势线同步突破，按平台突破（优先T1）执行"
        if r1p is not None and abs(plat_p - r1p) > 0.01:
            extra = f"活平台沿 {plat_txt}（123 R1={round(r1p, 2)} 已不作突破位）"
            note = (note + "；" if note else "") + extra
        z = zone_at_level(plat_p, atr_v, last_c, "平台突破(优先T1)", ev, bars)
        return pack("platform_break", 1, "breakout", "平台突破(优先T1)", True, note, z)
    if fresh_plat and not vol_ok:
        return pack(
            "wait", None, "wait", "平台突破·量能不足", False,
            f"近{n_above}根站上活平台沿 {plat_txt} 但 RVOL={round(rvol, 2)}≤1.5，不买假突破",
        )

    wpat = ev.get("w_bottom")
    if wpat is None:
        wpat = detect_w_bottom(bars, Hs_all, Ls_all, atr_v)
    if wpat and last_c is not None:
        neck = wpat["neckline"]
        n_neck = days_above_level(bars, neck)
        fresh_w = last_c > neck and 1 <= n_neck <= 3
        if fresh_w and wpat["l2"]["i"] < len(bars) - 1:
            neck_txt = round(neck, 2)
            if vol_ok:
                z = zone_at_level(neck, atr_v, last_c, "W底颈线突破(优先T1)", ev, bars)
                z["anchor"] = "w_neckline"
                note = (
                    f"W底颈线 {neck_txt}："
                    f"左底 {wpat['l1']['d']}@{round(wpat['l1']['price'], 2)} / "
                    f"右底 {wpat['l2']['d']}@{round(wpat['l2']['price'], 2)}；"
                    f"结构止损看颈线下"
                )
                if rvol is None:
                    note += "；无量能数据，确认打折"
                return pack(
                    "w_bottom_break", 1, "breakout", "W底颈线突破(优先T1)", True, note, z,
                )
            return pack(
                "wait", None, "wait", "W底颈线突破·量能不足", False,
                f"近{n_neck}根站上颈线 {neck_txt} 但 RVOL={round(rvol, 2)}≤1.5，不买假突破",
            )

    flag = ev.get("bull_flag")
    if flag is None:
        flag = detect_bull_flag(bars, Hs_all, atr_v)
    if flag and last_c is not None:
        f_tl = flag["tl_now"]
        f_days = flag["days_above_tl"]
        fresh_flag = last_c > f_tl and 1 <= f_days <= 3
        if fresh_flag:
            tl_txt = round(f_tl, 2)
            if vol_ok:
                z = zone_at_level(f_tl, atr_v, last_c, "旗形下降趋势线突破(优先T1)", ev, bars)
                z["anchor"] = "flag_tl"
                note = (
                    f"旗形突破：旗杆 {flag['pole_d0']}→{flag['pole_d1']} "
                    f"({round(flag['pole_lo'], 2)}→{round(flag['pole_hi'], 2)})，"
                    f"旗面下降趋势线 @{tl_txt}；结构止损看该线下"
                )
                if rvol is None:
                    note += "；无量能数据，确认打折"
                return pack(
                    "flag_tl_break", 1, "breakout", "旗形下降趋势线突破(优先T1)", True, note, z,
                )
            return pack(
                "wait", None, "wait", "旗形突破·量能不足", False,
                f"近{f_days}根站上旗面趋势线 {tl_txt} 但 RVOL={round(rvol, 2)}≤1.5，不买假突破",
            )

    line_wait_verdict = None
    line_wait_note = None
    if structure_ok and demand is not None:
        label = ANCHOR_LABEL.get(demand["anchor"], demand["anchor"])
        lv = round(demand["level"], 2)
        d_atr = demand["dist_atr"]
        if d_atr < 0:
            line_wait_verdict = "已跌破该线"
            line_wait_note = f"{label}@{lv}，收盘已在线下 {abs(d_atr):.2f}×ATR，不买"
        elif d_atr <= 1.0:
            rec = bool(vol_shrink)
            note = f"沿线回踩{label}@{lv}（近12根触及{demand['hits']}次）"
            if not vol_shrink:
                rec = False
                note += f"；量能偏大 RVOL={round(rvol, 2)}，等缩量尾盘"
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

    if uptrend:
        st = imp.get("state")
        y_lo = imp.get("yang_low")
        y_hi = imp.get("yang_high")
        y_d = imp.get("yang_d")
        floor = imp.get("floor") if imp.get("floor") is not None else y_lo
        yi_zi = bool(imp.get("yi_zi"))
        z = imp.get("zone") or _empty_zone()
        zone_txt = f"{round(z.get('primary_lo') or y_lo, 2)}-{round(z.get('primary_hi') or y_hi, 2)}"
        floor_txt = f"一字缺口下沿 {round(floor, 2)}" if yi_zi else f"大阳低点 {round(floor, 2)}"
        if st == "yang_today":
            return pack(
                "wait", None, "wait", "大阳当日不追", False,
                f"{y_d} 大阳，等缩到近期最低量、回踩进 {zone_txt} 再买，防守看{floor_txt}",
            )
        if st == "broke_yang_low":
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
                f"大阳后缩量回踩：{y_d} 阳线 {round(y_lo, 2)}-{round(y_hi, 2)}，"
                f"现价在区内、量已到近期最低，且近 3 根不再创新低。防守{floor_txt}。"
                f"次优先T2，试错仓"
                f"{gap_note}"
            )
            return pack(
                "impulse_pause", 2, "pullback", "大阳后缩量回踩(次优先T2)", True, note, z,
            )

    if flag is None:
        fresh_dtl = (
            declining and tl_now is not None and last_c is not None
            and last_c > tl_now and days_above_tl <= 3
        )
        if fresh_dtl and vol_ok:
            z = zone_at_level(tl_now, atr_v, last_c, "下降趋势线突破(次优先T2)", ev, bars)
            z["anchor"] = "down_tl"
            tgt_lv = plat_p if plat_p is not None else r1p
            tgt = f"目标1先看平台沿 {round(tgt_lv, 2)}" if tgt_lv else "目标1看最近前高"
            note = f"次优先T2，试错仓。未过前高则{tgt}；过前高升级为平台突破"
            if rvol is None:
                note += "；无量能数据，确认打折"
            return pack(
                "downtrend_tl_break", 2, "breakout", "下降趋势线突破(次优先T2)", True, note, z,
            )
        if fresh_dtl and not vol_ok:
            return pack(
                "wait", None, "wait", "下降趋势线突破·量能不足", False,
                f"RVOL={round(rvol, 2)}≤1.5，T2 也不买假突破",
            )

    if line_wait_note:
        return pack(
            "wait", None, "wait", line_wait_verdict, False, line_wait_note, bz_line,
        )
    if not structure_ok:
        return pack(
            "wait", None, "wait", "等待", False,
            "未形成平台/W底/旗形突破、沿线回踩、大阳后缩量回踩或下降趋势线突破",
        )
    return pack(
        "wait", None, "wait", "等待", False,
        "近端找不到被尊重的线，也没有大阳后缩量买点", _empty_zone(),
    )


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


def build_ev(bars, drop_live=False):
    """从日线构造 plan_entry 所需 ev（扫描器与 evaluate 共用）。

    drop_live=True 时丢弃今日未收盘末根。
    无有效活平台沿时不回落旧 R1（避坑）。
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
    if h_a and h_b:
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
        "cond3_break_prior_high": c3,
        "c2": c2,
        "cond2_no_new_low": c2,
        "P0": {"i": P0[0], "price": P0[1]},
        "P1": {"i": P1[0], "price": P1[1]},
        "R1": {"i": R1[0], "price": R1[1]} if R1 else None,
        "platform": plat,
        "w_bottom": w_bottom,
        "bull_flag": bull_flag,
        "down_tl": {
            "b": {"i": h_b[0], "price": h_b[1]},
            "a": {"i": h_a[0], "price": h_a[1]},
        } if (h_a and h_b) else None,
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
    bars, last_q = get_bars(sym, data_file)
    market = "ASH" if is_ash(str(sym).split(".")[0] if sym else "") or (
        data_file and str(sym).isdigit()
    ) else "ASH"
    # 盘中未收盘：默认禁止 recommend（量能不可判）；--eod 丢弃末根
    live = is_live_bar(bars, market=market)
    drop_live = bool(eod or False)
    ev, bars, meta = build_ev(bars, drop_live=drop_live)
    if ev is None:
        return {"sym": sym, "verdict": "N/A", "reason": meta.get("reason", "无法判定"), "last": last_q}

    if live and not drop_live:
        # 仍算出结构，但强制不买
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
    return out


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
            if k in ("buy_zone", "stop_plan", "targets", "w_bottom", "bull_flag", "P0", "P1", "R1", "platform"):
                print(f"  {k}: {v}")
            elif k not in ("note",) and not isinstance(v, (dict, list)):
                print(f"  {k}: {v}")
        if r.get("note"):
            print(f"  note: {r['note']}")
        print(f"  verdict: {r.get('verdict')}")

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(res if len(res) > 1 else res[0], f, ensure_ascii=False, indent=2, default=str)
        print(f"-> {out_path} written")
