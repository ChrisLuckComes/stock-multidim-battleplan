#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
方向感知结构判定（延续 vs 反转）
================================
先判趋势方向，再决定 123 怎么用。

上升延续候选（收盘>SMA20 且 (EMA10>SMA20>SMA50 或 SMA20 上行) 且 HH/HL 还在）：
  - 不用底部 123 否决整笔
  - cond1 不作为门控
  - cond2 / HL = 回踩买点 A 的结构条件（还要 VWAP/0.382/EMA10，由 skill 再判）
  - cond3（收盘>R1）= Breakout 买点 B 的触发，需放量（RVOL>1.5）才算确认

下跌反转候选（收盘<SMA20，或 HH/HL 坏了）：
  - 走 Vic 底部 123 硬门控：①趋势线突破 ②更高低点 ③收盘过 R1
  - 三项全过才 recommend

方向不明（mixed）：只观察。

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


def classify_regime(last_c, ema10, sma20, sma50, sma20_up, hh, hl):
    above20 = sma20 is not None and last_c > sma20
    bull_stack = (
        ema10 is not None and sma20 is not None and sma50 is not None
        and ema10 > sma20 > sma50
    )
    up_ma = bull_stack or bool(sma20_up)
    if above20 and up_ma and hh and hl:
        return "continuation"
    if (sma20 is not None and last_c < sma20) or (not hh) or (not hl):
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

    c2 = hl
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

    regime = classify_regime(last_c, ema10, sma20, sma50, sma20_up, hh, hl)
    passed = sum([c1, c2, c3])
    setup = "wait"
    recommend = False
    note = ""

    if regime == "reversal":
        recommend = passed == 3
        if recommend:
            setup = "reversal_123"
            verdict = "反转·123符合"
        elif passed == 2:
            verdict = "反转·123部分符合"
        else:
            verdict = "反转·123不符合"
    elif regime == "continuation":
        r1_px = R1[1] if R1 else None
        above_r1 = r1_px is not None and last_c > r1_px
        below_r1 = r1_px is None or last_c <= r1_px
        structure_ok = hl and last_c > P1[1]
        vol_ok = rvol20 is None or rvol20 > 1.5
        if above_r1 and vol_ok:
            setup = "breakout"
            recommend = True
            verdict = "延续·突破触发"
            if rvol20 is None:
                note = "过 R1 但无量能数据，突破确认打折"
        elif above_r1:
            setup = "wait"
            verdict = "延续·过R1但量能不足"
            note = f"RVOL={round(rvol20, 2)} ≤ 1.5"
        elif structure_ok and below_r1:
            setup = "pullback"
            recommend = False
            verdict = "延续·回踩路径"
            note = "cond3 不作否决；买点 A 须再落在 VWAP/0.382/EMA10 且缩量"
        else:
            setup = "wait"
            verdict = "延续·等待结构"
    else:
        verdict = "方向不明·观察"
        note = "未同时满足 SMA20 上方 + 均线向上 + HH/HL"

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
        "hh": hh,
        "hl": hl,
        "ema10": rnd(ema10),
        "sma20": rnd(sma20),
        "sma50": rnd(sma50),
        "sma20_up": sma20_up,
        "rvol20": rnd(rvol20),
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
        for k in ["regime", "setup", "verdict", "recommend", "note",
                  "last", "last_date", "ema10", "sma20", "sma50", "sma20_up",
                  "hh", "hl", "rvol20", "P0", "P1", "R1",
                  "cond1_trendline_break", "cond1_note", "cond2_no_new_low",
                  "cond3_break_prior_high", "passed", "reason"]:
            if k in r:
                print(f"  {k}: {r[k]}")

    with open("rule123_out.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("\n-> rule123_out.json written")
