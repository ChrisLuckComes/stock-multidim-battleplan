#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
123 趋势法则判定（Victor Sperandeo《专业投机原理》/ Trader Vic 1-2-3）
==================================================================
源于道氏理论：上升趋势=更高的高点+更高的低点；下降趋势反之。
多头（买入）1-2-3 底部/中继确认三条件：
  ① 趋势线被突破  → 下跌(或回调)段的下降趋势连线被收盘价站上
  ② 不再创出新低  → 最近摆动低点 P1 高于前一个摆动低点 P0（更高低点）
  ③ 穿越前期高点  → 最新收盘价 > P0~P1 之间的前期反应高点 R1
三项全过 = 符合123（可推荐）；任一不过 = 不符合（仅观察/不推荐）。

数据：Yahoo v8 chart（6mo 日线，零密钥）。仅作结构判定，非投资建议。
"""
import urllib.request, json, sys, datetime

def get_ohlc(sym, range_="6mo"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={range_}&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    j = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
    r = j["chart"]["result"][0]
    ts = r["timestamp"]; q = r["indicators"]["quote"][0]
    bars = []
    for i, t in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        bars.append({
            "d": datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
            "o": o, "h": h, "l": l, "c": c, "v": q["volume"][i],
        })
    return bars, r["meta"].get("regularMarketPrice")

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
    # 线性插值：过点 p_b=(i_b,y_b) 与 p_a=(i_a,y_a) 在 idx 处的值
    i_b, y_b = p_b; i_a, y_a = p_a
    if i_a == i_b:
        return y_a
    return y_b + (y_a - y_b) * (idx - i_b) / (i_a - i_b)

def evaluate(sym):
    bars, last_q = get_ohlc(sym)
    Hs, Ls = pivots(bars, w=3)
    if len(Ls) < 2 or len(Hs) < 2:
        return {"sym": sym, "verdict": "N/A", "reason": "枢轴点不足，无法判定", "last": last_q}
    # 最近两个摆动低点
    P1 = Ls[-1]; P0 = Ls[-2]
    # P0~P1 之间的前期反应高点 R1（index 在 P0,P1 之间的最大 H）
    R1_cands = [h for h in Hs if P0[0] < h[0] < P1[0]]
    R1 = max(R1_cands, key=lambda x: x[1]) if R1_cands else None
    # P1 之前的两个摆动高点（下跌段连线）
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
    # 条件② 不再创新低
    c2 = P1[1] > P0[1]
    # 条件③ 穿越前期高点
    c3 = (R1 is not None) and (last_c > R1[1])
    # 条件① 趋势线被突破
    c1 = False; tl_note = "N/A"
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
    syms = sys.argv[1:] or ["CF", "LLY", "MU", "TEM", "RVMD"]
    res = []
    for s in syms:
        try:
            r = evaluate(s)
        except Exception as e:
            r = {"sym": s, "verdict": "ERR", "reason": f"{type(e).__name__}: {e}"}
        res.append(r)
        print(f"=== {r['sym']} ===")
        for k in ["verdict", "last", "last_date", "P0", "P1", "R1",
                 "cond1_trendline_break", "cond1_note", "cond2_no_new_low",
                 "cond3_break_prior_high", "passed", "recommend", "reason"]:
            if k in r:
                print(f"  {k}: {r[k]}")
    # 写 json 供 HTML 注入
    with open("rule123_out.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("\n-> rule123_out.json written")
