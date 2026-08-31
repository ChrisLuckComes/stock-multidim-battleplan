#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
123 趋势法则判定（Victor Sperandeo《专业投机原理》/ Trader Vic 1-2-3）
======================================================================
源于道氏理论：上升趋势=更高的高点+更高的低点；下降趋势反之。
多头（买入）1-2-3 底部/中继确认三条件：
  ① 趋势线被突破  → 下跌(或回调)段的下降趋势连线被收盘价站上
  ② 不再创出新低  → 最近摆动低点 P1 高于前一个摆动低点 P0（更高低点）
  ③ 穿越前期高点  → 最新收盘价 > P0~P1 之间的前期反应高点 R1
三项全过 = 符合123（可推荐）；任一不过 = 不符合（仅观察/不推荐）。

数据源（自动识别，零外部 skill 依赖）：
  - A 股（6 位数字代码）走东方财富日线 API
  - 美股（字母代码）走 Yahoo v8 chart
  - 也可通过 --data path.json 直接喂入 fetch_market.py 输出（通用环境首选）
输出：统一 JSON 到 stdout（同时写入 rule123_out.json 便于 HTML 注入）。

用法:
  python rule123.py 601233            # A股
  python rule123.py CF LLY MU         # 美股批量
  python rule123.py CF --data cf.json # 用 fetch_market.py 的产出
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
                "d": datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
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

    out = {
        "sym": sym, "last": round(last_c, 2), "last_date": bars[-1]["d"],
        "P0": {"i": P0[0], "d": bars[P0[0]]["d"], "price": round(P0[1], 2)},
        "P1": {"i": P1[0], "d": bars[P1[0]]["d"], "price": round(P1[1], 2)},
    }
    if R1:
        out["R1"] = {"i": R1[0], "d": bars[R1[0]]["d"], "price": round(R1[1], 2)}
    c2 = P1[1] > P0[1]
    c3 = (R1 is not None) and (last_c > R1[1])
    c1 = False
    tl_note = "N/A"
    if h_a and h_b:
        tl_at_last = line_val(h_b, h_a, last_i)
        c1 = last_c > tl_at_last
        tl_note = f"下跌段趋势线@末根≈{round(tl_at_last,2)}（连 {bars[h_b[0]]['d']}高{bars[h_b[0]]['h']:.2f}→{bars[h_a[0]]['d']}高{h_a[1]:.2f}）"
        out["trendline_at_last"] = round(tl_at_last, 2)
    out["cond1_trendline_break"] = c1
    out["cond2_no_new_low"] = c2
    out["cond3_break_prior_high"] = c3
    out["cond1_note"] = tl_note
    passed = sum([c1, c2, c3])
    out["passed"] = passed
    out["verdict"] = "符合" if passed == 3 else ("部分符合" if passed == 2 else "不符合")
    out["recommend"] = (passed == 3)
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
        for k in ["verdict", "last", "last_date", "P0", "P1", "R1",
                  "cond1_trendline_break", "cond1_note", "cond2_no_new_low",
                  "cond3_break_prior_high", "passed", "recommend", "reason"]:
            if k in r:
                print(f"  {k}: {r[k]}")

    with open("rule123_out.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("\n-> rule123_out.json written")
