#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全市场 A 股方向感知扫描。

买区/模式一律走 rule123.build_ev + plan_entry（含 living_platform）。
tier1/tier2 仅用于排序展示；具体买卖点以 mode/recommend 为准。
"""
import json, time, sys, os, concurrent.futures as cf, urllib.request, threading
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from rule123 import build_ev, plan_entry, atr14, is_live_bar

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


def analyze(code, name, prefix):
    bars = sina_kline(prefix, code)
    if not bars or len(bars) < 60:
        return None
    # 盘中未收盘：不出票，避免半日量污染全市场表
    if is_live_bar(bars, market="ASH"):
        return None
    ev, bars, meta = build_ev(bars, drop_live=False)
    if ev is None:
        return None
    plan = plan_entry(bars, ev)
    z = plan.get("buy_zone") or {}
    spot = bars[-1]["c"]
    H = [b["h"] for b in bars]
    hi52 = max(H)
    plat = meta.get("plat")
    R1 = meta.get("R1")
    P1 = meta.get("P1")
    platform_px = plat["price"] if plat else None
    # 目标优先用 plan_entry 产出
    tg = plan.get("targets") or {}
    T1 = tg.get("target1")
    T2 = tg.get("target2")
    if T1 is None:
        peak_after = max(H[R1[0]:]) if R1 and R1[0] < len(H) else max(H[-20:])
        T1 = round(peak_after, 2)
        T2 = round(hi52, 2)
        if abs(T2 - T1) < 1e-6:
            atr_v = meta.get("atr_v") or atr14(bars) or spot * 0.02
            T2 = round(T1 + 2.0 * atr_v, 2)
    sp = plan.get("stop_plan") or {}
    stop = sp.get("hard") or sp.get("struct") or z.get("hard_stop") or z.get("struct_stop") or z.get("invalidation")
    struct_stop = sp.get("struct") or z.get("struct_stop")
    hard_stop = sp.get("hard") or z.get("hard_stop")
    # 预备突破单（buy-stop 埋伏）：与当日 mode 独立并存，先到先做。
    # 全市场表里这一列比 buy_zone 更可执行 —— A 股用户盯不住盘中，只能靠挂单。
    pb = plan.get("pre_breakout") or {}
    if not pb:
        pb = None
    # 候选：recommend 或 tier 结构信号
    c2, c3 = meta["c2"], meta["c3"]
    sma20 = meta["sma20"]
    price_above20 = bool(sma20 and spot > sma20)
    passed3 = bool(c2 and c3 and price_above20)
    regime = meta["regime"]
    if "ST" in name or "退" in name:
        candidate, tier = False, "none"
    else:
        candidate = bool(plan.get("recommend") or passed3 or (regime == "continuation" and price_above20))
        if plan.get("recommend") and plan.get("priority") == 1:
            tier = "tier1"
        elif plan.get("recommend"):
            tier = "tier2"
        elif passed3:
            tier = "tier1"
        elif regime == "continuation" and price_above20:
            tier = "tier2"
        else:
            tier = "none"
    return dict(
        code=code, name=name, spot=round(spot, 2), regime=regime, tier=tier,
        passed3=passed3, c1=meta["c1"], c2=c2, c3=c3,
        R1=round(R1[1], 2) if R1 else None,
        platform=round(platform_px, 2) if platform_px is not None else None,
        P1=round(P1[1], 2) if P1 else None,
        ma5=z.get("ma5"),
        ma20=round(sma20, 2) if sma20 else None,
        buy_type=z.get("type"),
        support_lo=z.get("primary_lo"),
        support_hi=z.get("primary_hi"),
        stop=stop,
        struct_stop=struct_stop,
        hard_stop=hard_stop,
        hard_anchor=sp.get("hard_anchor") or z.get("hard_anchor"),
        stop_warning=sp.get("warning") or z.get("stop_warning"),
        # 两档止损的「执行口径」字段：报表要能说清哪条腿不需要盯盘（2026-09-18）
        struct_exec=sp.get("struct_exec") or z.get("struct_exec"),
        hard_exec=sp.get("hard_exec") or z.get("hard_exec"),
        hard_dist_atr=sp.get("hard_dist_atr") if sp.get("hard_dist_atr") is not None else z.get("hard_dist_atr"),
        hard_noise=bool(sp.get("hard_noise") or z.get("hard_noise")),
        buy_lo_adjusted=bool(z.get("buy_lo_adjusted")),
        path=plan.get("path"), mode=plan.get("mode"), priority=plan.get("priority"),
        recommend=plan.get("recommend"),
        verdict=plan.get("verdict"),
        pre_breakout=pb,
        anchor=z.get("anchor"),
        vwap5=z.get("vwap5"), in_zone=z.get("in_zone"),
        chase_only=bool(z.get("chase_only")),
        T1=T1, T2=T2, rr_target1=tg.get("rr_target1"),
        rvol=round(meta["rvol20"], 2) if meta.get("rvol20") else None,
        dd_from_high=round((spot / hi52 - 1) * 100, 1),
        candidate=candidate, market=prefix,
    )


def main():
    codes = json.load(open("scan_all/codes.json", encoding="utf-8"))
    out = open("scan_all/results.jsonl", "w", encoding="utf-8")
    done = 0
    skipped = 0
    cands = Counter()
    lock = threading.Lock()

    def work(item):
        try:
            r = analyze(item["c"], item["name"], item["prefix"])
        except Exception:
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
