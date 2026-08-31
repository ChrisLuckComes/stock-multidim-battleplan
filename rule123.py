#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
方向感知结构判定（延续 vs 反转）
================================
四种买法（当日只给一种，由 plan_entry 输出 mode）：
  1. platform_break 平台突破（优先T1）：近 3 根内收盘站上 R1 且放量，买突破位
  2. line_pullback 沿线回踩（优先T1）：沿着肉眼可见的线上升，回踩该线买；默认 P0→P1 上升趋势线，仅明显贴均线才改用该均线
  3. downtrend_tl_break 下降趋势线突破（次优先T2）：近 3 根内站上下降高点连线，买该线；仓位试错，过前高升级为平台突破
  4. impulse_pause 大阳后缩量回踩（次优先T2）：大阳线之后回踩缩量找买点
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
    Hs, Ls = [], []
    n = len(bars)
    for i in range(w, n - w):
        hh, ll = bars[i]["h"], bars[i]["l"]
        if all(bars[j]["h"] <= hh for j in range(i - w, i + w + 1) if j != i):
            Hs.append((i, hh))
        if all(bars[j]["l"] >= ll for j in range(i - w, i + w + 1) if j != i):
            Ls.append((i, ll))
    return Hs, Ls


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
    if len(bars) < 2:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    w = min(n, len(trs))
    return sum(trs[-w:]) / w


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
    "r1": "突破位R1",
    "yang_digest": "大阳调整区",
    "yang_gap": "缺口下沿",
}


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
    """最近 3 根都不下破此前回调低点（允许 0.1×ATR 噪声）。未满 3 根不算。
    要的是横盘控盘，不是天天阴跌再走 N 字。
    """
    n = len(bars)
    if n - 1 - yang_i < 3:
        return False
    pad = 0.1 * atr_v if atr_v else bars[-1]["c"] * 0.002
    for i in range(n - 3, n):
        prior = [bars[j]["l"] for j in range(yang_i + 1, i)]
        if not prior:
            continue
        if bars[i]["l"] < min(prior) - pad:
            return False
    return True


def yang_floor(bars, yang_i, atr_v):
    """防守位：普通大阳=当日低点；一字/振幅极小的跳空=缺口下沿（前收）。"""
    y = bars[yang_i]
    y_lo, y_hi = y["l"], y["h"]
    prev_c = bars[yang_i - 1]["c"] if yang_i > 0 else None
    has_gap = prev_c is not None and y["o"] > prev_c * 1.01
    y_range = y_hi - y_lo
    yi_zi = bool(has_gap and atr_v and y_range < 0.35 * atr_v)
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
    lows_after = [bars[j]["l"] for j in range(yang_i + 1, n)]
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
    pad = 0.35 * atr_v if atr_v else level * 0.005
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
        "invalidation": round(level - pad, 2),
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


def zone_at_level(level, atr_v, last_c, kind, ev, bars):
    z = _empty_zone()
    closes = [b["c"] for b in bars]
    z["vwap5"] = round(typical_vwap(bars, 5) or 0, 2) if typical_vwap(bars, 5) else None
    z["ma5"] = round(sma(closes, 5), 2) if sma(closes, 5) else None
    if level is None:
        return z
    pad = 0.35 * atr_v if atr_v else level * 0.005
    lo, hi = level - pad, level + pad
    if last_c and hi - lo < last_c * 0.002:
        mid = (lo + hi) / 2.0
        lo, hi = mid * 0.997, mid * 1.003
    z.update({
        "type": kind,
        "anchor": "r1" if "平台" in kind else "down_tl",
        "level": round(level, 2),
        "primary_lo": round(lo, 2),
        "primary_hi": round(hi, 2),
        "in_zone": True,
        "dist_atr": 0.0,
        "extended": False,
        "invalidation": round(level - pad, 2),
    })
    return z


def plan_entry(bars, ev):
    """当日只给一种：platform_break / line_pullback / impulse_pause / downtrend_tl_break / wait。"""
    last_c = bars[-1]["c"] if bars else None
    atr_v = atr14(bars)
    rvol = ev.get("rvol20")
    vol_ok = rvol is None or rvol > 1.5
    vol_shrink = rvol is None or rvol <= 1.2
    r1p = _px(ev.get("R1"))[1]
    n_above = days_above_level(bars, r1p)
    c2 = bool(ev.get("c2") if ev.get("c2") is not None else ev.get("cond2_no_new_low"))
    p1p = _px(ev.get("P1"))[1]
    structure_ok = p1p is not None and c2 and last_c is not None and last_c > p1p
    demand = living_demand(bars, ev)
    bz_line = zone_from_demand(demand, bars, ev)
    bz_line["days_above_r1"] = n_above
    uptrend = still_uptrend(bars, ev, last_c, c2, p1p)
    imp = find_impulse_pause(bars, atr_v)

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

    def pack(mode, priority, setup, verdict, recommend, note, buy_zone=None, path=None):
        z = buy_zone if buy_zone is not None else bz_line
        if path is None:
            if mode in ("line_pullback", "impulse_pause"):
                path = "A"
            elif mode in ("platform_break", "downtrend_tl_break"):
                path = "B"
            else:
                path = "wait"
        z["path"] = path
        z["mode"] = mode
        z["priority"] = priority
        z["days_above_r1"] = n_above
        return {
            "path": path,
            "mode": mode,
            "priority": priority,
            "setup": setup,
            "verdict": verdict,
            "recommend": recommend,
            "note": note,
            "buy_zone": z,
            "days_above_r1": n_above,
        }

    # T1 平台突破：近 3 根才站上 R1
    fresh_plat = r1p is not None and last_c is not None and last_c > r1p and n_above <= 3
    if fresh_plat and vol_ok:
        note = "过 R1 但无量能数据，突破确认打折" if rvol is None else ""
        if declining and days_above_tl <= 3:
            note = (note + "；" if note else "") + "下降趋势线同步突破，按平台突破（优先T1）执行"
        z = zone_at_level(r1p, atr_v, last_c, "平台突破(优先T1)", ev, bars)
        return pack("platform_break", 1, "breakout", "平台突破(优先T1)", True, note, z)
    if fresh_plat and not vol_ok:
        return pack(
            "wait", None, "wait", "平台突破·量能不足", False,
            f"近{n_above}根站上R1但 RVOL={round(rvol, 2)}≤1.5，不买假突破",
        )

    # T1 沿线回踩：只在贴线（≤1×ATR）时占用当日；未到位/延伸则让给 T2
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

    # T2 大阳后缩量回踩（升势过滤；T1 未贴线时才轮到）
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
        # broke_yang_low / no_yang：这笔大阳作废或没有大阳，不占用当日

    # T2 下降趋势线突破
    fresh_dtl = (
        declining and tl_now is not None and last_c is not None
        and last_c > tl_now and days_above_tl <= 3
    )
    if fresh_dtl and vol_ok:
        z = zone_at_level(tl_now, atr_v, last_c, "下降趋势线突破(次优先T2)", ev, bars)
        z["anchor"] = "down_tl"
        tgt = f"目标1先看平台沿/R1 {round(r1p, 2)}" if r1p else "目标1看最近前高"
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
            "未形成平台突破、沿线回踩、大阳后缩量回踩或下降趋势线突破",
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
    above20 = sma20 is not None and last_c > sma20
    bull_stack = (
        ema10 is not None and sma20 is not None and sma50 is not None
        and ema10 > sma20 > sma50
    )
    up_ma = bull_stack or bool(sma20_up)
    # 结构完好：站在底部 P1 之上 且 自底部以来未再创新低（替代脆弱的两两 hl）
    structure_ok = (p1_px is not None and last_c > p1_px) and c2
    if above20 and up_ma and structure_ok:
        return "continuation"
    if (sma20 is not None and last_c < sma20) or (not c2):
        return "reversal"
    return "mixed"


def evaluate(sym, data_file=None):
    bars, last_q = get_bars(sym, data_file)
    Hs, Ls = pivots(bars, w=3)
    if len(Ls) < 2 or len(Hs) < 2:
        return {"sym": sym, "verdict": "N/A", "reason": "枢轴点不足，无法判定", "last": last_q}
    P1 = Ls[-1]
    P0 = Ls[-2]
    R1_cands = [h for h in Hs if P0[0] < h[0] < P1[0]]
    R1 = max(R1_cands, key=lambda x: x[1]) if R1_cands else None
    Hs_before = [h for h in Hs if h[0] < P1[0]]
    h_a = Hs_before[-1] if Hs_before else None
    h_b = Hs_before[-2] if len(Hs_before) >= 2 else None
    last_i = len(bars) - 1
    last_c = bars[-1]["c"]
    closes = [b["c"] for b in bars]
    vols = [float(b.get("v") or 0) for b in bars]
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

    # cond2 (Vic 123 原意): 自底部 P1 以来未再创新低（非两两 hl 比较）
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
    plan = plan_entry(bars, {
        "regime": regime,
        "passed": passed,
        "rvol20": rvol20,
        "cond3_break_prior_high": c3,
        "c2": c2,
        "cond2_no_new_low": c2,
        "P0": {"i": P0[0], "price": P0[1]},
        "P1": {"i": P1[0], "price": P1[1]},
        "R1": {"i": R1[0], "price": R1[1]} if R1 else None,
        "down_tl": {
            "b": {"i": h_b[0], "price": h_b[1]},
            "a": {"i": h_a[0], "price": h_a[1]},
        } if (h_a and h_b) else None,
    })
    setup = plan["setup"]
    recommend = plan["recommend"]
    note = plan["note"]
    verdict = plan["verdict"]

    def rnd(v):
        if v is None:
            return None
        return round(v, 2)

    out = {
        "sym": sym,
        "last": rnd(last_c),
        "last_date": bars[-1]["d"],
        "P0": {"i": P0[0], "d": bars[P0[0]]["d"], "price": rnd(P0[1])},
        "P1": {"i": P1[0], "d": bars[P1[0]]["d"], "price": rnd(P1[1])},
        "regime": regime,
        "setup": setup,
        "path": plan["path"],
        "mode": plan["mode"],
        "priority": plan["priority"],
        "hh": hh,
        "hl": hl,
        "ema10": rnd(ema10),
        "sma20": rnd(sma20),
        "sma50": rnd(sma50),
        "sma20_up": sma20_up,
        "rvol20": rnd(rvol20),
        "atr14": rnd(atr14(bars)),
        "days_above_r1": plan["days_above_r1"],
        "cond1_trendline_break": c1,
        "cond1_note": tl_note,
        "cond2_no_new_low": c2,
        "cond3_break_prior_high": c3,
        "passed": passed,
        "verdict": verdict,
        "recommend": recommend,
        "note": note,
    }
    if R1:
        out["R1"] = {"i": R1[0], "d": bars[R1[0]]["d"], "price": rnd(R1[1])}
    if h_a and h_b:
        out["trendline_at_last"] = rnd(line_val(h_b, h_a, last_i))
    out["buy_zone"] = plan["buy_zone"]
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    syms, data_map = [], {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--data":
            # --data sym=path.json 或 --data path.json（对最后一个 sym 生效）
            spec = args[i + 1]
            i += 2
            if "=" in spec:
                k, v = spec.split("=", 1)
                data_map[k.upper()] = v
            else:
                if syms:
                    data_map[syms[-1].upper()] = spec
            continue
        syms.append(a)
        i += 1
    if not syms:
        syms = ["CF", "LLY", "MU", "TEM", "RVMD"]

    res = []
    for s in syms:
        df = data_map.get(s.upper())
        try:
            r = evaluate(s, df)
        except Exception as e:
            r = {"sym": s, "verdict": "ERR", "reason": f"{type(e).__name__}: {e}"}
        res.append(r)
        print(f"=== {r['sym']} ===")
        for k in ["regime", "mode", "priority", "setup", "path", "verdict", "recommend", "note",
                  "last", "last_date", "ema10", "sma20", "sma50", "sma20_up",
                  "hh", "hl", "rvol20", "atr14", "days_above_r1",
                  "P0", "P1", "R1", "buy_zone",
                  "cond1_trendline_break", "cond1_note", "cond2_no_new_low",
                  "cond3_break_prior_high", "passed", "reason"]:
            if k in r:
                print(f"  {k}: {r[k]}")

    with open("rule123_out.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("\n-> rule123_out.json written")
