#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全市场 A 股 方向感知 123 扫描。
对 codes.json 每只拉 Sina 140 日日线 → 判 123 + 短线买区（VWAP5/MA5）→ 增量写 results.jsonl。
tier1 = Sperandeo 123 完整(passed3)；tier2 = 上升延续(continuation)回踩买点；其余非候选。
"""
import json, time, math, sys, os, concurrent.futures as cf, urllib.request, threading
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from rule123 import buy_zone

UA = "Mozilla/5.0"
REF = "https://finance.sina.com.cn/"

def sina_kline(prefix, code, n=140, tries=3):
    sym = prefix + code
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=5&datalen={n}")
    last = None
    for t in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REF})
            with urllib.request.urlopen(req, timeout=15) as r:
                arr = json.loads(r.read().decode("utf-8"))
            if not arr:
                return None
            return [{"d": k["day"], "o": float(k["open"]), "c": float(k["close"]),
                     "h": float(k["high"]), "l": float(k["low"]), "v": float(k["volume"])}
                    for k in arr]
        except Exception as e:
            last = e
            time.sleep(0.4 * (t + 1))
    return None

def sma(a, w):
    return sum(a[-w:]) / w if len(a) >= w else None

def pivots(bars, w=5):
    Hs, Ls = [], []
    n = len(bars)
    for i in range(w, n - w):
        hi = max(bars[j]['h'] for j in range(i - w, i + w + 1))
        lo = min(bars[j]['l'] for j in range(i - w, i + w + 1))
        if bars[i]['h'] == hi:
            Hs.append((i, bars[i]['h']))
        if bars[i]['l'] == lo:
            Ls.append((i, bars[i]['l']))
    def dedup(ps):
        if not ps:
            return []
        out = [ps[0]]
        for p in ps[1:]:
            if p[0] - out[-1][0] >= w:
                out.append(p)
        return out
    return dedup(Hs), dedup(Ls)

def analyze(code, name, prefix):
    bars = sina_kline(prefix, code)
    if not bars or len(bars) < 60:
        return None
    C = [b['c'] for b in bars]; H = [b['h'] for b in bars]; L = [b['l'] for b in bars]
    n = len(C); spot = C[-1]
    ma20 = sma(C, 20); ma50 = sma(C, 50); ma60 = sma(C, 60); ma120 = sma(C, 120) if n >= 120 else sma(C, n)
    Hs, Ls = pivots(bars, w=5)
    rhs = [h for h in Hs if h[0] >= n - 120]
    rls = [l for l in Ls if l[0] >= n - 120]
    R1 = rhs[-1] if rhs else None
    P1 = rls[-1] if rls else None
    P0 = rls[-2] if len(rls) >= 2 else None
    hh = len(rhs) >= 2 and rhs[-1][1] > rhs[-2][1]
    hl = bool(P0 and P1 and P1[1] > P0[1])
    c2 = bool(P1 and min(L[P1[0]:]) >= P1[1] - 1e-6)
    cond1 = xtrend = None
    if len(rhs) >= 2:
        a, b = rhs[-2], rhs[-1]
        slope = (b[1] - a[1]) / (b[0] - a[0])
        xtrend = a[1] + slope * (n - 1 - a[0])
        cond1 = spot > xtrend
    c3 = bool(R1 and spot > R1[1])
    up_struct = bool(ma20 and ma60 and ma20 > ma60 and (ma120 and ma60 > ma120))
    price_above20 = bool(ma20 and spot > ma20)
    breakout_confirmed = bool(c3 and c2 and price_above20)
    if breakout_confirmed:                       # 收盘站 R1 + 未创新低 + 价在MA20上 = 突破确认
        regime = "breakout"
    elif up_struct and price_above20:
        regime = "continuation"
    elif (ma20 and ma60 and ma20 < ma60) or (not price_above20 and not c2):
        regime = "downtrend"
    else:
        regime = "mixed"
    passed3 = breakout_confirmed                 # 底座突破用 c2+c3 判定，不卡趋势线(cond1)
    # 短线买区 = plan_entry 四种模式之一
    ma5 = sma(C, 5); ma10 = sma(C, 10)
    vols = [b['v'] for b in bars]
    rvol = vols[-1] / (sum(vols[-20:]) / 20) if len(vols) >= 20 and vols[-1] else None
    ev = {
        "regime": "continuation" if regime in ("continuation", "breakout") else regime,
        "passed": 3 if passed3 else 0,
        "rvol20": rvol,
        "cond3_break_prior_high": c3,
        "c2": c2,
        "cond2_no_new_low": c2,
        "R1": {"price": R1[1], "i": R1[0]} if R1 else None,
        "P1": {"price": P1[1], "i": P1[0]} if P1 else None,
        "P0": {"price": P0[1], "i": P0[0]} if P0 else None,
        "down_tl": {
            "b": {"i": rhs[-2][0], "price": rhs[-2][1]},
            "a": {"i": rhs[-1][0], "price": rhs[-1][1]},
        } if len(rhs) >= 2 else None,
    }
    z = buy_zone(ev, bars)
    buy_type = z.get("type")
    support_lo = z.get("primary_lo")
    support_hi = z.get("primary_hi")
    stop = z.get("invalidation") or (round(P1[1], 2) if P1 else None)
    fail_lo = z.get("fail_lo")
    fail_hi = z.get("fail_hi")
    fail_stop = None
    peak_after = max(H[R1[0]:]) if R1 and R1[0] < n else max(H[-20:])
    hi52 = max(H)
    T1 = round(max(peak_after, rhs[-1][1] if rhs else (R1[1] if R1 else spot * 1.1)), 2)
    T2 = round(hi52, 2)
    candidate = passed3 or (regime == "continuation" and price_above20)
    # ST/*ST/退市股一律不作为候选
    if "ST" in name or "退" in name:
        candidate, tier = False, "none"
    else:
        tier = "tier1" if passed3 else ("tier2" if (regime == "continuation" and price_above20) else "none")
    return dict(
        code=code, name=name, spot=round(spot, 2), regime=regime, tier=tier,
        passed3=passed3, c1=cond1, c2=c2, c3=c3,
        R1=round(R1[1], 2) if R1 else None,
        P1=round(P1[1], 2) if P1 else None,
        ma5=round(ma5, 2) if ma5 else None,
        ma10=round(ma10, 2) if ma10 else None,
        ma20=round(ma20, 2) if ma20 else None,
        ma50=round(ma50, 2) if ma50 else None,
        ma60=round(ma60, 2) if ma60 else None,
        ma120=round(ma120, 2) if ma120 else None,
        buy_type=buy_type, support_lo=support_lo, support_hi=support_hi, stop=stop,
        path=z.get("path"), mode=z.get("mode"), priority=z.get("priority"),
        anchor=z.get("anchor"),
        vwap5=z.get("vwap5"), in_zone=z.get("in_zone"),
        fail_lo=fail_lo, fail_hi=fail_hi, fail_stop=fail_stop,
        T1=T1, T2=T2, rvol=round(rvol, 2) if rvol else None,
        dd_from_high=round((spot / hi52 - 1) * 100, 1),
        candidate=candidate, market=prefix,
    )

def main():
    codes = json.load(open("scan_all/codes.json", encoding="utf-8"))
    out = open("scan_all/results.jsonl", "w", encoding="utf-8")
    done = 0; skipped = 0; cands = Counter()
    lock = threading.Lock()

    def work(item):
        try:
            r = analyze(item["c"], item["name"], item["prefix"])
        except Exception as e:
            r = None
        time.sleep(0.03)
        return r

    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(work, c): c for c in codes}
        for fut in cf.as_completed(futs):
            r = fut.result()
            with lock:
                if r is None:
                    skipped += 1
                else:
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
                    out.flush()
                    if r["candidate"]:
                        cands[r["tier"]] += 1
                done += 1
                if done % 200 == 0:
                    print(f"进度 {done}/{len(codes)} 候选(t1/t2)={cands['tier1']}/{cands['tier2']} 跳过{skipped}", flush=True)
    print(f"完成 扫描{done} 候选 tier1={cands['tier1']} tier2={cands['tier2']} 跳过{skipped}", flush=True)

if __name__ == "__main__":
    main()
